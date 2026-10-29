"""Rebuilding the panel from raw filings, and as-of exports, against a database."""

import csv
import datetime as dt
import io
from decimal import Decimal

import polars as pl
import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError

from cmbs import clock
from cmbs.access.service import PermissionRefused
from cmbs.audit.tables import AuditEntry
from cmbs.domain import Period
from cmbs.export import service
from cmbs.export.service import (
    EXPORT_COLUMNS,
    CorruptRawFile,
    DayNotOver,
    NothingToRebuild,
    UnknownDeal,
    UnknownFormat,
    export_panel,
    rebuild,
)
from cmbs.export.tables import PanelExport
from cmbs.filings.tables import FilingFile
from cmbs.settings.service import NoSettings, store_version
from cmbs.tape.tables import LoanPeriod, LoanTape
from cmbs.testing.bdd import in_session
from cmbs.testing.world import BASELINE_SETTINGS, FIXTURE_DEAL
from tests.conftest import needs_db
from tests.fixtures.refresh.offline import offline_fixture  # noqa: F401
from tests.test_routine_world import backfill, wait_for_review

pytestmark = needs_db

JUL, AUG, SEP = Period(2026, 7), Period(2026, 8), Period(2026, 9)
EXPORT_DAY = dt.datetime(2026, 10, 7, 9, 0, tzinfo=dt.UTC)


@pytest.fixture
def loaded(seeded):
    backfill(in_session, JUL, SEP)
    return seeded


def _count(table) -> int:
    return in_session(lambda db: db.scalar(select(func.count()).select_from(table))) or 0


def _rows(content: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(content.decode())))


# ---------------------------------------------------------------------------- rebuild


def test_the_rebuilt_panel_is_identical_and_nothing_changes(loaded):
    before = in_session(
        lambda db: [(r.loan, r.period, r.ending_balance) for r in db.scalars(select(LoanPeriod))]
    )
    found = rebuild("quinn", deal=FIXTURE_DEAL)
    assert found.identical, found.text()
    assert (found.deals, found.tapes, found.rows) == ([FIXTURE_DEAL], 3, 21)
    assert found.stored_checksum == found.rebuilt_checksum
    assert len(found.stored_checksum) == 64
    assert found.text() == (
        f"rebuilt 21 loan rows of 1 deal from 3 stored tapes: identical, checksum "
        f"{found.stored_checksum}"
    )
    after = in_session(
        lambda db: [(r.loan, r.period, r.ending_balance) for r in db.scalars(select(LoanPeriod))]
    )
    assert after == before
    entry = in_session(
        lambda db: db.scalars(select(AuditEntry).where(AuditEntry.action == service.REBUILD)).one()
    )
    assert (entry.user_name, entry.role, entry.after, entry.subject_key) == (
        "quinn",
        "quant",
        "identical",
        FIXTURE_DEAL,
    )
    assert entry.detail is not None
    assert entry.detail["rows"] == 21
    assert entry.detail["stored_checksum"] == entry.detail["rebuilt_checksum"]
    # The whole panel (every deal with a tape) rebuilds the same way.
    every = rebuild("olly")
    assert (every.deals, every.rows, every.stored_checksum) == (
        [FIXTURE_DEAL],
        21,
        found.stored_checksum,
    )


def test_a_stored_row_that_differs_from_its_raw_file_is_found(loaded):
    def tamper(db):
        db.execute(text("ALTER TABLE silver.loan_period DISABLE TRIGGER ALL"))
        db.execute(
            update(LoanPeriod)
            .where(LoanPeriod.loan == "7", LoanPeriod.period == SEP.first_day)
            .values(ending_balance=Decimal("8999999.99"))
        )

    in_session(tamper)
    found = rebuild("quinn", deal=FIXTURE_DEAL)
    assert not found.identical
    assert found.stored_checksum != found.rebuilt_checksum
    assert [(d.key[3], d.column, d.stored, d.rebuilt) for d in found.differences] == [
        ("7", "ending_balance", Decimal("8999999.99"), Decimal("9000000.00"))
    ]
    assert (
        found.text()
        .splitlines()[0]
        .endswith(
            f"1 differences, stored checksum {found.stored_checksum}, "
            f"rebuilt {found.rebuilt_checksum}"
        )
    )
    after = in_session(
        lambda db: db.scalar(select(AuditEntry.after).where(AuditEntry.action == service.REBUILD))
    )
    assert after == "1 differences"


def test_a_corrupt_raw_file_is_refused(loaded):
    def corrupt(db):
        tape = db.scalars(select(LoanTape).order_by(LoanTape.period)).first()
        db.execute(
            update(FilingFile)
            .where(FilingFile.accession == tape.accession)
            .values(content=b"<assetData/>")
        )
        return tape

    tape = in_session(corrupt)
    with pytest.raises(CorruptRawFile) as refused:
        rebuild("quinn")
    assert str(refused.value) == (
        f"the stored file {tape.accession}/{tape.file_name} does not match its SHA-256"
    )


def test_rebuild_refusals(seeded):
    with pytest.raises(PermissionRefused):
        rebuild("pat")
    with pytest.raises(NothingToRebuild) as empty:
        rebuild("quinn")
    assert str(empty.value) == "no loan tape is stored"
    with pytest.raises(UnknownDeal) as unknown:
        rebuild("quinn", deal="No Such Trust")
    assert str(unknown.value) == "no deal named 'No Such Trust'"
    from tests.test_routine_world import register

    in_session(register)
    with pytest.raises(NothingToRebuild) as none:
        rebuild("quinn", deal=FIXTURE_DEAL)
    assert str(none.value) == f"no loan tape of {FIXTURE_DEAL} is stored"
    refused = in_session(
        lambda db: db.scalars(
            select(AuditEntry.reason).where(AuditEntry.outcome == "refused")
        ).all()
    )
    assert len(refused) == 4


def test_a_whole_panel_rebuild_takes_one_deal_at_a_time(seeded, monkeypatch):
    """While one deal is rebuilt, another deal is not locked: a load of it could go ahead."""
    from cmbs.db import engine
    from cmbs.deals.service import by_name
    from cmbs.deals.tables import Deal

    second = "Fixture Trust 2026-F2"
    backfill(in_session, SEP, SEP)
    backfill(in_session, SEP, SEP, deal=second)
    ids = in_session(lambda db: {name: by_name(db, name).id for name in (FIXTURE_DEAL, second)})  # pyright: ignore[reportOptionalMemberAccess]
    free: list[str] = []
    load = service.load_tape

    def loading(db, *, deal, **kwargs):
        other = second if deal == FIXTURE_DEAL else FIXTURE_DEAL
        with engine.session() as probe:
            probe.get(Deal, ids[other], with_for_update={"nowait": True})
            free.append(other)
        return load(db, deal=deal, **kwargs)

    monkeypatch.setattr(service, "load_tape", loading)
    found = rebuild("quinn")
    assert found.identical, found.text()
    assert (found.deals, found.tapes, found.rows) == ([FIXTURE_DEAL, second], 2, 14)
    assert free == [second, FIXTURE_DEAL]
    keys = in_session(
        lambda db: db.scalars(
            select(AuditEntry.subject_key)
            .where(AuditEntry.action == service.REBUILD)
            .order_by(AuditEntry.id)
        ).all()
    )
    assert keys == [FIXTURE_DEAL, second]


# ---------------------------------------------------------------------------- export


def test_an_as_of_export_holds_only_what_was_known(loaded):
    clock.set_now(EXPORT_DAY)
    made = in_session(lambda db: export_panel(db, "quinn", as_of=dt.date(2026, 9, 28)))
    rows = _rows(made.content)
    assert sorted({row["period"] for row in rows}) == ["2026-07-01", "2026-08-01"]
    assert made.rows == len(rows) == 14
    assert list(rows[0]) == list(EXPORT_COLUMNS)
    # The comments were extracted on 2026-10-01: not known on 2026-09-28.
    assert {row["comment_borrower_request"] for row in rows} == {""}
    assert made.file_name == (
        f"panel_as-of-2026-09-28_settings-7_jev-1.14.0_prompt-3_export-{made.number}.csv"
    )
    assert made.text() == (
        f"export {made.number}: 14 rows as of 2026-09-28, settings version 7, jev-1.14.0, "
        f"prompt version 3; {made.file_name}, SHA-256 {made.sha256}"
    )


def test_an_export_carries_extracted_fields_with_their_status(loaded):
    clock.set_now(EXPORT_DAY)
    in_session(lambda db: wait_for_review(db, field="workout_strategy"))
    made = in_session(
        lambda db: export_panel(db, "quinn", as_of=dt.date(2026, 10, 6), deal=FIXTURE_DEAL)
    )
    rows = {(row["loan"], row["period"]): row for row in _rows(made.content)}
    loan_2 = rows[("2", "2026-09-01")]
    assert loan_2["comment_accession"] == "0009900001-26-000418"
    assert (loan_2["comment_model"], loan_2["comment_prompt_version"]) == ("jev-1.14.0", "3")
    assert (
        loan_2["comment_borrower_request"],
        loan_2["comment_borrower_request_status"],
        loan_2["comment_borrower_request_sentence"],
        loan_2["comment_borrower_request_by"],
    ) == ("maturity extension", "auto-accepted", "3", "")
    assert (loan_2["comment_workout_strategy"], loan_2["comment_workout_strategy_status"]) == (
        "",
        "needs review",
    )
    assert loan_2["comment_dscr"] == "0.71"
    assert loan_2["ending_balance"] == "30000000.00"
    assert loan_2["workout_strategy"] == rows[("2", "2026-08-01")]["workout_strategy"]
    assert rows[("1", "2026-09-01")]["comment_accession"] == ""
    assert made.file_name.startswith("fixture-trust-2026-f1_as-of-2026-10-06_settings-7_")


def test_exports_are_recorded_audited_and_append_only(loaded):
    clock.set_now(EXPORT_DAY)
    made = in_session(lambda db: export_panel(db, "quinn", as_of=dt.date(2026, 10, 6)))
    stored = in_session(lambda db: db.get_one(PanelExport, made.number))
    assert (
        stored.exported_by,
        stored.exported_at,
        stored.as_of,
        stored.deal_id,
        stored.format,
        stored.file_name,
        stored.row_count,
        stored.sha256,
        stored.settings_version,
        stored.model,
        stored.prompt_version,
    ) == (
        "quinn",
        EXPORT_DAY,
        dt.date(2026, 10, 6),
        None,
        "csv",
        made.file_name,
        21,
        made.sha256,
        7,
        "jev-1.14.0",
        3,
    )
    import hashlib

    assert hashlib.sha256(made.content).hexdigest() == made.sha256
    entry = in_session(
        lambda db: db.scalars(select(AuditEntry).where(AuditEntry.action == service.EXPORT)).one()
    )
    assert (entry.user_name, entry.subject, entry.subject_key, entry.after) == (
        "quinn",
        "export",
        str(made.number),
        made.file_name,
    )
    assert entry.detail == {
        "as_of": "2026-10-06",
        "settings_version": "7",
        "model": "jev-1.14.0",
        "prompt_version": "3",
        "exported_by": "quinn",
        "export": str(made.number),
        "rows": 21,
        "sha256": made.sha256,
        "format": "csv",
    }
    with pytest.raises(DBAPIError, match="append-only"):
        in_session(lambda db: db.execute(update(PanelExport).values(row_count=0)))
    with pytest.raises(DBAPIError, match="append-only"):
        in_session(lambda db: db.execute(text("DELETE FROM app.panel_export")))


def test_the_same_export_again_has_the_same_hash(loaded):
    clock.set_now(EXPORT_DAY)
    first = in_session(lambda db: export_panel(db, "quinn", as_of=dt.date(2026, 9, 30)))
    again = in_session(lambda db: export_panel(db, "quinn", as_of=dt.date(2026, 9, 30)))
    assert first.sha256 == again.sha256
    assert first.number != again.number


def test_a_parquet_export_keeps_types_and_names_its_provenance(loaded):
    clock.set_now(EXPORT_DAY)
    made = in_session(
        lambda db: export_panel(db, "quinn", as_of=dt.date(2026, 10, 6), fmt="parquet")
    )
    assert made.file_name.endswith(f"_export-{made.number}.parquet")
    frame = pl.read_parquet(io.BytesIO(made.content))
    assert frame.height == 21
    assert frame.schema["ending_balance"] == pl.Decimal(38, 2)
    assert frame.schema["period"] == pl.Date()
    balances = frame.filter(pl.col("loan") == "2")["ending_balance"].to_list()
    assert balances == [Decimal("30000000.00")] * 3
    metadata = pl.read_parquet_metadata(io.BytesIO(made.content))
    assert {key: metadata[key] for key in ("as_of", "settings_version", "model")} == {
        "as_of": "2026-10-06",
        "settings_version": "7",
        "model": "jev-1.14.0",
    }
    assert metadata["prompt_version"] == "3"


def test_the_settings_named_are_those_in_force_on_the_as_of_date(loaded):
    clock.set_now(EXPORT_DAY)
    in_session(
        lambda db: store_version(
            db,
            version=8,
            values=BASELINE_SETTINGS.model_copy(update={"prompt_version": 4}),
            proposed_by="quinn",
            approved_by="rae",
            approved_at=dt.datetime(2026, 10, 3, tzinfo=dt.UTC),
        )
    )
    before = in_session(lambda db: export_panel(db, "quinn", as_of=dt.date(2026, 10, 2)))
    after = in_session(lambda db: export_panel(db, "quinn", as_of=dt.date(2026, 10, 3)))
    assert (before.settings_version, before.prompt_version) == (7, 3)
    assert (after.settings_version, after.prompt_version) == (8, 4)


def test_export_refusals(loaded):
    clock.set_now(EXPORT_DAY)
    with pytest.raises(PermissionRefused) as pm:
        in_session(lambda db: export_panel(db, "pat", as_of=dt.date(2026, 10, 6)))
    assert str(pm.value) == "pm may not export"
    with pytest.raises(UnknownFormat) as fmt:
        in_session(lambda db: export_panel(db, "quinn", as_of=dt.date(2026, 10, 6), fmt="xlsx"))
    assert str(fmt.value) == "exports are csv or parquet, not 'xlsx'"
    with pytest.raises(UnknownDeal):
        in_session(lambda db: export_panel(db, "quinn", as_of=dt.date(2026, 10, 6), deal="X"))
    clock.set_now(EXPORT_DAY)
    for day in (EXPORT_DAY.date(), dt.date(2026, 10, 8)):
        with pytest.raises(DayNotOver) as early:
            in_session(lambda db, day=day: export_panel(db, "quinn", as_of=day))
        assert str(early.value) == "an export is as of a day that has ended"
    with pytest.raises(NoSettings) as unset:
        in_session(lambda db: export_panel(db, "quinn", as_of=dt.date(2026, 9, 14)))
    assert str(unset.value) == "no settings approved by 2026-09-14T23:59:59.999999+00:00"
    assert _count(PanelExport) == 0
    refusals = in_session(
        lambda db: db.scalar(
            select(func.count()).select_from(AuditEntry).where(AuditEntry.outcome == "refused")
        )
    )
    assert refusals == 6
