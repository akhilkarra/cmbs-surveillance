"""Shared services against a real Postgres. Skipped without DATABASE_URL."""

import datetime as dt
import subprocess
import sys
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.access.roles import Action, Role
from cmbs.access.service import (
    PermissionRefused,
    UnknownUser,
    authorize,
    ensure_user,
    roles_of,
)
from cmbs.audit.service import entries_for_subject, record
from cmbs.audit.tables import AuditEntry
from cmbs.checks.service import flags_for, raise_flag
from cmbs.db import engine
from cmbs.deals.service import by_name, get_or_create
from cmbs.filings.service import ConflictingFiling, register_filing, store_file
from cmbs.filings.tables import Filing
from cmbs.runs.service import RunAlreadyFinished, finish_run, start_run
from cmbs.runs.tables import Run
from cmbs.settings.service import NoSettings, OutOfOrderVersion, in_force, store_version
from cmbs.settings.values import Settings
from tests.conftest import needs_db

pytestmark = needs_db


def test_authorize_returns_the_acting_role(seeded):
    assert authorize(seeded, "morgan", Action.PROPOSE_SETTING).role is Role.QUANT
    assert authorize(seeded, "alex", Action.DECIDE_REVIEW).role is Role.ANALYST


def test_a_refusal_is_raised_and_audited_with_user_and_time(seeded):
    clock.set_now(dt.datetime(2026, 10, 6, 14, 30, tzinfo=dt.UTC))
    with pytest.raises(PermissionRefused, match="^pm may not decide review items$"):
        authorize(seeded, "pat", Action.DECIDE_REVIEW)
    seeded.rollback()  # the caller's work is abandoned; the refusal record must survive
    entry = seeded.scalars(select(AuditEntry)).one()
    assert (entry.user_name, entry.outcome, entry.reason) == (
        "pat",
        "refused",
        "pm may not decide review items",
    )
    assert entry.at == dt.datetime(2026, 10, 6, 14, 30, tzinfo=dt.UTC)


def test_unknown_users_are_refused_and_the_attempt_is_audited(seeded):
    with pytest.raises(UnknownUser, match="^no user named 'nobody'$"):
        authorize(seeded, "nobody", Action.READ_PANEL)
    seeded.rollback()
    entry = seeded.scalars(select(AuditEntry)).one()
    assert (entry.user_name, entry.outcome, entry.reason) == (
        "nobody",
        "refused",
        "no user named 'nobody'",
    )


def test_ensure_user_sets_exactly_the_given_roles(seeded):
    ensure_user(seeded, "quinn", {Role.QUANT, Role.RISK})
    assert roles_of(seeded, "quinn") == {Role.QUANT, Role.RISK}
    ensure_user(seeded, "quinn", {Role.RISK})
    assert roles_of(seeded, "quinn") == {Role.RISK}


def test_audit_entries_cannot_be_updated_or_deleted(db):
    record(db, user="alex", action="decide", subject="loan", subject_key="F1/2")
    db.commit()
    for statement in ("UPDATE app.audit_entry SET action = 'x'", "DELETE FROM app.audit_entry"):
        with pytest.raises(DBAPIError, match="the audit log is append-only"):
            db.execute(text(statement))
        db.rollback()
    assert db.scalars(select(AuditEntry)).one().action == "decide"


def test_subject_history_is_newest_first(db):
    for hour, after in ((9, "first"), (11, "second")):
        clock.set_now(dt.datetime(2026, 9, 30, hour, tzinfo=dt.UTC))
        record(db, user="alex", action="decide", subject="loan", subject_key="F1/2", after=after)
    record(db, user="alex", action="decide", subject="loan", subject_key="F1/7", after="other")
    assert [e.after for e in entries_for_subject(db, "loan", "F1/2")] == ["second", "first"]


def test_settings_in_force_follow_approval_time(seeded):
    assert in_force(seeded, dt.datetime(2026, 9, 20, tzinfo=dt.UTC)).version == 7
    store_version(
        seeded,
        version=8,
        values=Settings(dscr_alert_threshold=Decimal("1.05")),
        proposed_by="quinn",
        approved_by="rae",
        approved_at=dt.datetime(2026, 10, 1, tzinfo=dt.UTC),
    )
    before = in_force(seeded, dt.datetime(2026, 9, 30, tzinfo=dt.UTC))
    after = in_force(seeded, dt.datetime(2026, 10, 2, tzinfo=dt.UTC))
    assert (before.version, before.values.dscr_alert_threshold) == (7, Decimal("1.00"))
    assert (after.version, after.values.dscr_alert_threshold) == (8, Decimal("1.05"))


def test_a_version_is_in_force_from_the_instant_it_is_approved(seeded):
    approved = dt.datetime(2026, 9, 15, tzinfo=dt.UTC)
    assert in_force(seeded, approved).version == 7
    with pytest.raises(NoSettings):
        in_force(seeded, approved - dt.timedelta(microseconds=1))


def test_in_force_defaults_to_now(seeded):
    clock.set_now(dt.datetime(2026, 10, 27, tzinfo=dt.UTC))
    assert in_force(seeded).version == 7


def test_no_settings_before_the_first_approval(seeded):
    with pytest.raises(NoSettings, match=r"^no settings approved by 2026-01-01T00:00:00\+00:00$"):
        in_force(seeded, dt.datetime(2026, 1, 1, tzinfo=dt.UTC))


@pytest.mark.parametrize(
    ("version", "approved_at"),
    [
        (7, dt.datetime(2026, 10, 1, tzinfo=dt.UTC)),
        (6, dt.datetime(2026, 10, 1, tzinfo=dt.UTC)),
        (8, dt.datetime(2026, 9, 14, tzinfo=dt.UTC)),
    ],
)
def test_versions_must_follow_the_latest_in_number_and_time(seeded, version, approved_at):
    with pytest.raises(OutOfOrderVersion, match="does not follow version 7"):
        store_version(
            seeded,
            version=version,
            values=Settings(),
            proposed_by="quinn",
            approved_by="rae",
            approved_at=approved_at,
        )


def test_settings_round_trip_every_field(seeded):
    values = Settings(
        dscr_alert_threshold=Decimal("1.05"),
        abstention_low=Decimal("0.40"),
        abstention_high=Decimal("0.60"),
        transfer_date_tolerance_periods=1,
        model="jev-1.15.0",
        prompt_version=4,
        zero_data_retention=True,
    )
    store_version(
        seeded,
        version=8,
        values=values,
        proposed_by="quinn",
        approved_by="rae",
        approved_at=dt.datetime(2026, 10, 1, tzinfo=dt.UTC),
    )
    assert in_force(seeded, dt.datetime(2026, 10, 1, tzinfo=dt.UTC)).values == values


def test_runs_take_a_number_and_finish_once(db):
    clock.set_now(dt.datetime(2026, 10, 27, 9, tzinfo=dt.UTC))
    run = start_run(db, kind="refresh", started_by="olly", run_id=42)
    assert (run.id, run.status) == (42, "running")
    clock.set_now(dt.datetime(2026, 10, 27, 9, 4, tzinfo=dt.UTC))
    finish_run(db, run, status="succeeded", summary={"deals": 2})
    assert run.ended_at == dt.datetime(2026, 10, 27, 9, 4, tzinfo=dt.UTC)
    with pytest.raises(ValueError, match="cannot finish as 'running'"):
        finish_run(db, run, status="running", summary={})


def test_a_finished_run_keeps_its_outcome(db):
    run = start_run(db, kind="refresh", started_by="olly")
    finish_run(db, run, status="succeeded", summary={"deals": 2})
    with pytest.raises(RunAlreadyFinished, match=r"already finished as 'succeeded'"):
        finish_run(db, run, status="failed", summary={"error": "late"})
    assert (run.status, run.summary) == ("succeeded", {"deals": 2})


def test_unknown_statuses_cannot_finish_a_run(db):
    run = start_run(db, kind="refresh", started_by="olly")
    with pytest.raises(ValueError, match="cannot finish as 'done'"):
        finish_run(db, run, status="done", summary={})


def test_pinned_run_numbers_never_collide_with_later_runs(db):
    assert start_run(db, kind="refresh", started_by="olly", run_id=42).id == 42
    assert start_run(db, kind="refresh", started_by="olly").id == 43


def test_pinning_a_low_number_on_a_fresh_database_still_advances(db):
    assert start_run(db, kind="refresh", started_by="olly", run_id=1).id == 1
    assert start_run(db, kind="backfill", started_by="quinn").id == 2


def test_deals_are_registered_once_and_learn_their_cik(db):
    first = get_or_create(db, "BANK 2018-BNK13")
    again = get_or_create(db, "BANK 2018-BNK13", cik=1744707)
    assert first.id == again.id and again.cik == 1744707
    assert by_name(db, "BANK 2018-BNK13") is first
    assert by_name(db, "no such deal") is None


def test_flags_keep_both_sides_per_loan(db):
    deal = get_or_create(db, "Fixture Trust 2026-F1")
    raise_flag(
        db,
        deal_id=deal.id,
        loan="4",
        check="report_tape_reconciliation",
        message="report says delinquent, tape says current",
        sides={"report": "delinquent", "tape": "current"},
    )
    other = get_or_create(db, "Fixture Trust 2026-F2")
    raise_flag(db, deal_id=other.id, loan="4", check="text_vs_tape", message="other deal")
    (flag,) = flags_for(db, deal_id=deal.id, loan="4")
    assert flag.sides == {"report": "delinquent", "tape": "current"}
    assert flags_for(db, deal_id=deal.id, loan="5") == []
    assert [f.message for f in flags_for(db, deal_id=other.id, loan="4")] == ["other deal"]


ACCESSION = "0001888524-26-017705"
FILED = dt.date(2026, 9, 29)


def register_sept(db, filing_date: dt.date = FILED, **optional):
    """The September 2026 ABS-EE of BANK 2018-BNK13, plus any optional facts."""
    return register_filing(
        db, accession=ACCESSION, cik=1744707, form="ABS-EE", filing_date=filing_date, **optional
    )


def test_a_filing_is_registered_once(db):
    assert register_sept(db) is register_sept(db)


def test_later_registrations_fill_in_missing_facts(db):
    deal = get_or_create(db, "BANK 2018-BNK13", cik=1744707)
    register_sept(db)
    filing = register_sept(
        db,
        deal_id=deal.id,
        report_date=dt.date(2026, 9, 17),
        period=dt.date(2026, 9, 1),
        primary_document="wcm18b13_absee-202609.htm",
    )
    assert (filing.deal_id, filing.report_date, filing.period, filing.primary_document) == (
        deal.id,
        dt.date(2026, 9, 17),
        dt.date(2026, 9, 1),
        "wcm18b13_absee-202609.htm",
    )


def test_registered_facts_cannot_change(db):
    register_sept(db, period=dt.date(2026, 9, 1))
    with pytest.raises(ConflictingFiling, match="already has period"):
        register_sept(db, period=dt.date(2026, 8, 1))
    with pytest.raises(ConflictingFiling, match="different facts"):
        register_sept(db, filing_date=dt.date(2026, 9, 30))


def test_files_are_stored_once_with_their_hash(db):
    register_sept(db)
    stored = store_file(db, accession=ACCESSION, name="exh_102.xml", content=b"<assets/>")
    assert (stored.size, len(stored.sha256)) == (9, 64)
    assert store_file(db, accession=ACCESSION, name="exh_102.xml", content=b"<assets/>") is stored
    with pytest.raises(ConflictingFiling, match="other content"):
        store_file(db, accession=ACCESSION, name="exh_102.xml", content=b"<changed/>")


def test_concurrent_approvals_queue_instead_of_both_passing_the_order_check(seeded):
    """A second writer waits for the first; it cannot read a stale latest version."""

    def approve(db: Session, version: int, day: int) -> None:
        store_version(
            db,
            version=version,
            values=Settings(),
            proposed_by="quinn",
            approved_by="rae",
            approved_at=dt.datetime(2026, 10, day, tzinfo=dt.UTC),
        )

    with Session(engine.engine()) as first, Session(engine.engine()) as second:
        approve(first, 8, 2)  # holds the ledger lock until it commits
        second.execute(text("SET LOCAL lock_timeout = '200ms'"))
        with pytest.raises(OperationalError, match="lock timeout"):
            approve(second, 9, 1)
        second.rollback()
        first.commit()
        with pytest.raises(OutOfOrderVersion, match="does not follow version 8"):
            approve(second, 9, 1)


def test_a_fresh_process_can_use_one_service_without_importing_the_others(db_url):
    """Foreign keys across features resolve even when only one feature's service is imported."""
    code = (
        "import datetime as dt\n"
        "from cmbs.db import engine\n"
        "from cmbs.filings.service import register_filing\n"
        "with engine.session() as db:\n"
        "    register_filing(db, accession='a', cik=1, form='10-D',"
        " filing_date=dt.date(2026, 9, 29))\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False
    )
    assert result.stdout.strip() == "ok", result.stderr[-400:]


def test_a_fact_filled_in_by_another_session_is_not_overwritten(db):
    register_sept(db)
    db.commit()
    with Session(engine.engine()) as stale, Session(engine.engine()) as other:
        # Loaded while the period is still unknown. Keep a reference: the session holds loaded
        # objects weakly, and the race needs the stale copy to still be there.
        held = stale.get(Filing, ACCESSION)
        assert held is not None and held.period is None
        register_sept(other, period=dt.date(2026, 9, 1))
        other.commit()
        with pytest.raises(ConflictingFiling, match="already has period"):
            register_sept(stale, period=dt.date(2026, 8, 1))


def test_a_run_finished_by_another_session_cannot_be_finished_again(db):
    started = start_run(db, kind="refresh", started_by="olly")
    db.commit()
    with Session(engine.engine()) as stale, Session(engine.engine()) as other:
        held = stale.get(Run, started.id)  # kept: the race needs the stale copy
        assert held is not None and held.status == "running"
        fresh = other.get(Run, started.id)
        assert fresh is not None
        finish_run(other, fresh, status="succeeded", summary={"deals": 2})
        other.commit()
        with pytest.raises(RunAlreadyFinished, match="already finished as 'succeeded'"):
            finish_run(stale, held, status="failed", summary={"error": "late"})


def test_a_refusal_is_recorded_even_when_the_callers_pool_is_exhausted(seeded, db_url):
    """The refusal record never waits on the caller's connection pool."""
    tiny = create_engine(db_url, pool_size=1, max_overflow=0, pool_timeout=1)
    try:
        with Session(tiny) as caller:
            caller.execute(text("SELECT 1"))  # the pool's only connection is now in use
            with pytest.raises(PermissionRefused, match="^pm may not decide review items$"):
                authorize(caller, "pat", Action.DECIDE_REVIEW)
    finally:
        tiny.dispose()
    entry = seeded.scalars(select(AuditEntry).where(AuditEntry.outcome == "refused")).one()
    assert (entry.user_name, entry.reason) == ("pat", "pm may not decide review items")
