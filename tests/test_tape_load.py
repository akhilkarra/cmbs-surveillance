"""Loading a tape through raw, bronze and silver: the pure planning and the database load."""

import datetime as dt
import threading
import time
from dataclasses import replace
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cmbs.absee.schedule_al import parse_assets
from cmbs.access.roles import Role
from cmbs.access.service import Actor
from cmbs.audit.tables import AuditEntry
from cmbs.db import engine
from cmbs.deals.service import get_or_create
from cmbs.domain import Period
from cmbs.filings.service import ConflictingFiling, register_filing, sha256_of
from cmbs.filings.tables import Filing, FilingFile
from cmbs.tape.service import (
    LOAD_ACTION,
    QUARANTINE_ACTION,
    LoadSummary,
    Quarantined,
    TapeRefused,
    check_form,
    load_tape,
    plan,
    quarantined_loans,
    reporting_period_ends,
    stored_assets,
    tape_period,
)
from cmbs.tape.tables import LoanPeriod, QuarantinedLoan, TapeAsset
from tests.conftest import needs_db
from tests.fixtures.tape.tapes import asset, fixture_bytes, tape

DEAL = "Fixture Trust 2026-F1"
CIK = 9_900_001
ACCESSION = "0009900001-26-000101"
AMENDMENT = "0009900001-26-000102"
FILED = dt.date(2026, 9, 29)
SEPTEMBER = Period(2026, 9)


# ---------------------------------------------------------------------------- pure checks


@pytest.mark.parametrize(
    ("form", "amends", "message"),
    [
        ("ABS-EE", "x", "an ABS-EE amends nothing; file the correction as ABS-EE/A"),
        ("ABS-EE/A", None, "an ABS-EE/A must name the filing it amends"),
        ("10-D", None, "a loan tape is filed as ABS-EE or ABS-EE/A, not 10-D"),
    ],
)
def test_check_form_refuses(form, amends, message):
    with pytest.raises(TapeRefused) as refusal:
        check_form(form, amends)
    assert str(refusal.value) == message


@pytest.mark.parametrize(("form", "amends"), [("ABS-EE", None), ("ABS-EE/A", ACCESSION)])
def test_check_form_accepts(form, amends):
    check_form(form, amends)


def test_the_period_is_the_month_every_asset_reports():
    assets = parse_assets(fixture_bytes())
    assert tape_period(assets, FILED) == SEPTEMBER
    assert tape_period(assets, dt.date(2026, 9, 11)) == SEPTEMBER


def test_unreadable_or_missing_period_ends_are_left_to_the_contract():
    assets = parse_assets(
        tape(
            asset("1"),
            asset("2", reportingPeriodEndDate="bad"),
            asset("3", reportingPeriodEndDate=""),
        )
    )
    assert reporting_period_ends(assets) == [dt.date(2026, 9, 11)]


@pytest.mark.parametrize(
    ("content", "filed", "message"),
    [
        (
            tape(asset("1", reportingPeriodEndDate="")),
            FILED,
            "the tape reports no reporting period end",
        ),
        (
            tape(asset("1", reportingPeriodEndDate="08-11-2026"), asset("2")),
            FILED,
            "the tape reports more than one period: August 2026, September 2026",
        ),
        (
            tape(asset("1")),
            dt.date(2026, 9, 10),
            "a tape filed on 2026-09-10 cannot report a period ending 2026-09-11",
        ),
    ],
)
def test_tape_period_refuses(content, filed, message):
    with pytest.raises(TapeRefused) as refusal:
        tape_period(parse_assets(content), filed)
    assert str(refusal.value) == message


def test_plan_loads_loans_only_and_quarantines_with_reasons_in_file_order():
    content = tape(
        asset("1", reportPeriodEndActualBalanceAmount="5.00"),
        asset("1A", prop={"propertyName": "NA"}),
        asset("2", reportPeriodEndActualBalanceAmount="-1.00"),
        asset("2-001"),
        asset("3", reportPeriodEndActualBalanceAmount="abc"),
        asset(
            "4",
            reportPeriodEndActualBalanceAmount="-2.00",
            reportPeriodBeginningScheduleLoanBalanceAmount="-3.00",
        ),
    )
    work = plan(parse_assets(content))
    assert [(a.asset_number, row.ending_balance) for a, row in work.loaded] == [
        ("1", Decimal("5.00"))
    ]
    assert [(a.asset_number, reason) for a, reason in work.quarantined] == [
        ("2", "ending balance is negative"),
        ("3", "ending balance is not a number: 'abc'"),
        ("4", "beginning balance is negative; ending balance is negative"),
    ]


def test_plan_of_the_fixture_loads_seven_loans():
    work = plan(parse_assets(fixture_bytes()))
    assert [a.asset_number for a, _ in work.loaded] == ["1", "2", "3", "4", "5", "6", "7"]
    assert work.quarantined == []


def summary(**changes) -> LoadSummary:
    values = {
        "deal": DEAL,
        "accession": ACCESSION,
        "period": SEPTEMBER,
        "filing_date": FILED,
        "file_sha256": "0" * 64,
        "assets": 9,
        "loans_loaded": 6,
        "quarantined": [Quarantined("5", 7, "ending balance is negative")],
        "already_loaded": False,
    }
    return LoadSummary(**values | changes)


def test_summary_text():
    assert summary().text() == (
        "Fixture Trust 2026-F1 September 2026 (0009900001-26-000101, filed 2026-09-29): "
        "loaded; 9 assets, 6 loans loaded, 1 loan quarantined"
    )
    assert (
        summary(quarantined=[], already_loaded=True)
        .text()
        .endswith("already loaded, nothing changed; 9 assets, 6 loans loaded, 0 loans quarantined")
    )


# ---------------------------------------------------------------------------- database


def load(db: Session, content: bytes | None = None, **changes):
    arguments = {
        "deal": DEAL,
        "content": fixture_bytes() if content is None else content,
        "file_name": "ex102.xml",
        "accession": ACCESSION,
        "filing_date": FILED,
        "cik": CIK,
    } | changes
    return load_tape(db, **arguments)


def count(db: Session, table) -> int:
    return db.scalar(select(func.count()).select_from(table)) or 0


@needs_db
def test_loads_raw_bronze_and_silver(db):
    result = load(db)
    db.commit()
    content = fixture_bytes()
    assert result == LoadSummary(
        deal=DEAL,
        accession=ACCESSION,
        period=SEPTEMBER,
        filing_date=FILED,
        file_sha256=sha256_of(content),
        assets=9,
        loans_loaded=7,
        quarantined=[],
        already_loaded=False,
    )
    filing = db.get(Filing, ACCESSION)
    assert filing is not None
    assert (filing.cik, filing.form, filing.period, filing.amends) == (
        CIK,
        "ABS-EE",
        dt.date(2026, 9, 1),
        None,
    )
    assert [a.asset_number for a in stored_assets(db, ACCESSION)] == [
        "1",
        "1A",
        "2",
        "2-001",
        "3",
        "4",
        "5",
        "6",
        "7",
    ]
    rows = {row.loan: row for row in db.scalars(select(LoanPeriod))}
    assert sorted(rows) == ["1", "2", "3", "4", "5", "6", "7"]
    one = rows["1"]
    assert (one.period, one.filing_date, one.accession, one.file_name, one.asset_position) == (
        dt.date(2026, 9, 1),
        FILED,
        ACCESSION,
        "ex102.xml",
        1,
    )
    assert one.file_sha256 == sha256_of(content)
    assert one.ending_balance == Decimal("50000000.00")
    assert rows["7"].asset_position == 9
    assert rows["5"].noi is None and rows["5"].financials_end is None


@needs_db
def test_loading_again_changes_nothing_and_reports_the_same_counts(db):
    first = load(db)
    db.commit()
    again = load(db)
    assert again == replace(first, already_loaded=True)
    assert count(db, TapeAsset) == 9
    assert count(db, LoanPeriod) == 7


@needs_db
def test_a_loan_failing_the_contract_is_quarantined_not_loaded(db):
    content = fixture_bytes().replace(
        b"<reportPeriodEndActualBalanceAmount>12000000.00<",
        b"<reportPeriodEndActualBalanceAmount>-12000000.00<",
    )
    result = load(db, content)
    assert result.loans_loaded == 6
    assert result.quarantined == [Quarantined("5", 7, "ending balance is negative")]
    [stored] = quarantined_loans(db, ACCESSION)
    assert (stored.loan, stored.period, stored.filing_date, stored.reason) == (
        "5",
        dt.date(2026, 9, 1),
        FILED,
        "ending balance is negative",
    )
    assert "5" not in set(db.scalars(select(LoanPeriod.loan)))
    assert count(db, TapeAsset) == 9  # bronze keeps the loan as reported


@needs_db
def test_the_database_refuses_a_negative_balance_the_contract_missed(db):
    load(db)
    db.flush()
    with pytest.raises(IntegrityError, match="ck_loan_period_ending_balance_not_negative"):
        db.execute(text("UPDATE silver.loan_period SET ending_balance = -1 WHERE loan = '1'"))


@needs_db
@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        ("beginning_balance", "-1", "beginning_balance_not_negative"),
        ("financials_start", "'2026-07-01'", "financials_in_order"),
        ("period", "'2026-09-02'", "period_is_first_of_month"),
        ("file_sha256", "'abc'", "sha256_length"),
    ],
)
def test_the_database_repeats_the_contract(db, column, value, constraint):
    load(db)
    db.flush()
    with pytest.raises(IntegrityError, match=constraint):
        db.execute(text(f"UPDATE silver.loan_period SET {column} = {value} WHERE loan = '1'"))


@needs_db
def test_a_deal_without_a_cik_is_refused_before_anything_is_stored(db):
    with pytest.raises(TapeRefused) as refusal:
        load(db, cik=None)
    assert (
        str(refusal.value)
        == "Fixture Trust 2026-F1 has no CIK, so its filings cannot be registered"
    )
    db.rollback()
    assert count(db, Filing) == 0


@needs_db
def test_the_deals_known_cik_is_used(db):
    get_or_create(db, DEAL, cik=CIK)
    assert load(db, cik=None).loans_loaded == 7
    assert db.get(Filing, ACCESSION).cik == CIK  # pyright: ignore[reportOptionalMemberAccess]


@needs_db
def test_an_empty_tape_is_refused(db):
    with pytest.raises(TapeRefused, match="^the tape lists no assets$"):
        load(db, tape())


@needs_db
def test_a_second_file_for_a_loaded_accession_is_refused(db):
    load(db)
    with pytest.raises(TapeRefused) as refusal:
        load(db, file_name="other.xml")
    assert str(refusal.value) == "0009900001-26-000101 is already loaded from ex102.xml"
    assert db.get(FilingFile, (ACCESSION, "other.xml")) is None  # refused before storing


@needs_db
def test_different_bytes_under_the_same_file_are_refused(db):
    load(db)
    with pytest.raises(ConflictingFiling):
        load(db, fixture_bytes() + b"\n")


@needs_db
def test_a_filing_registered_for_another_period_is_refused(db):
    deal = get_or_create(db, DEAL, cik=CIK)
    register_filing(
        db,
        accession=ACCESSION,
        cik=CIK,
        form="ABS-EE",
        filing_date=FILED,
        deal_id=deal.id,
        period=dt.date(2026, 8, 1),
    )
    with pytest.raises(ConflictingFiling):
        load(db)


@needs_db
def test_an_amendment_adds_a_version_and_keeps_the_original(db):
    load(db)
    corrected = fixture_bytes().replace(b">1.45<", b">1.25<")
    result = load(
        db,
        corrected,
        accession=AMENDMENT,
        filing_date=dt.date(2026, 10, 5),
        form="ABS-EE/A",
        amends=ACCESSION,
    )
    assert result.loans_loaded == 7
    versions = db.execute(
        select(LoanPeriod.accession, LoanPeriod.filing_date, LoanPeriod.dscr)
        .where(LoanPeriod.loan == "1")
        .order_by(LoanPeriod.filing_date)
    ).all()
    assert [tuple(v) for v in versions] == [
        (ACCESSION, FILED, Decimal("1.45")),
        (AMENDMENT, dt.date(2026, 10, 5), Decimal("1.25")),
    ]
    filing = db.get(Filing, AMENDMENT)
    assert filing is not None and (filing.form, filing.amends) == ("ABS-EE/A", ACCESSION)


@needs_db
@pytest.mark.parametrize(
    ("amends", "filed", "message"),
    [
        (
            "0009900001-26-000999",
            dt.date(2026, 10, 5),
            "0009900001-26-000999 is not a registered September 2026 loan tape of this deal",
        ),
        (
            ACCESSION,
            dt.date(2026, 9, 28),
            "an amendment filed on 2026-09-28 cannot correct 0009900001-26-000101, "
            "filed later on 2026-09-29",
        ),
    ],
)
def test_an_amendment_must_follow_a_loaded_tape(db, amends, filed, message):
    load(db)
    with pytest.raises(TapeRefused) as refusal:
        load(db, accession=AMENDMENT, filing_date=filed, form="ABS-EE/A", amends=amends)
    assert str(refusal.value) == message


@needs_db
def test_an_amendment_must_be_of_the_same_deal_period_and_kind(db):
    load(db)
    other = get_or_create(db, "Fixture Trust 2026-F2", cik=CIK + 1)
    register_filing(
        db,
        accession="0009900001-26-000103",
        cik=CIK + 1,
        form="ABS-EE",
        filing_date=FILED,
        deal_id=other.id,
        period=dt.date(2026, 9, 1),
    )
    deal = get_or_create(db, DEAL)
    register_filing(
        db,
        accession="0009900001-26-000104",
        cik=CIK,
        form="10-D",
        filing_date=FILED,
        deal_id=deal.id,
        period=dt.date(2026, 9, 1),
    )
    register_filing(
        db,
        accession="0009900001-26-000105",
        cik=CIK,
        form="ABS-EE",
        filing_date=FILED,
        deal_id=deal.id,
        period=dt.date(2026, 8, 1),
    )
    for amends in ("0009900001-26-000103", "0009900001-26-000104", "0009900001-26-000105"):
        with pytest.raises(TapeRefused, match="is not a registered September 2026 loan tape"):
            load(
                db,
                accession=AMENDMENT,
                filing_date=dt.date(2026, 10, 5),
                form="ABS-EE/A",
                amends=amends,
            )


@needs_db
def test_an_amendment_of_an_amendment_is_accepted(db):
    load(db)
    load(
        db, accession=AMENDMENT, filing_date=dt.date(2026, 10, 5), form="ABS-EE/A", amends=ACCESSION
    )
    third = load(
        db,
        accession="0009900001-26-000106",
        filing_date=dt.date(2026, 10, 6),
        form="ABS-EE/A",
        amends=AMENDMENT,
    )
    assert third.loans_loaded == 7


@needs_db
def test_every_loan_row_written_is_audited_with_the_actor_and_run(seeded):
    db = seeded
    content = fixture_bytes().replace(
        b"<reportPeriodEndActualBalanceAmount>12000000.00<",
        b"<reportPeriodEndActualBalanceAmount>-12000000.00<",
    )
    load(db, content, actor=Actor("olly", Role.OPERATOR), run_id=42)
    entries = db.scalars(select(AuditEntry).order_by(AuditEntry.id)).all()
    assert [(e.action, e.subject_key) for e in entries] == [
        (LOAD_ACTION, f"{DEAL}/1"),
        (LOAD_ACTION, f"{DEAL}/2"),
        (LOAD_ACTION, f"{DEAL}/3"),
        (LOAD_ACTION, f"{DEAL}/4"),
        (QUARANTINE_ACTION, f"{DEAL}/5"),
        (LOAD_ACTION, f"{DEAL}/6"),
        (LOAD_ACTION, f"{DEAL}/7"),
    ]
    first = entries[0]
    assert (first.user_name, first.role, first.run_id, first.subject, first.reason) == (
        "olly",
        "operator",
        42,
        "loan",
        None,
    )
    assert first.detail == {"accession": ACCESSION, "period": "2026-09-01"}
    assert entries[4].reason == "ending balance is negative"
    load(db, content, actor=Actor("olly", Role.OPERATOR), run_id=43)
    assert count(db, AuditEntry) == 7  # a repeated load writes nothing, so audits nothing


@needs_db
def test_without_an_actor_nothing_is_audited(db):
    load(db)
    assert count(db, AuditEntry) == 0


@needs_db
def test_a_tape_whose_every_loan_fails_stores_assets_and_quarantines_them(db):
    content = tape(
        asset("1", reportPeriodEndActualBalanceAmount="-1.00"),
        asset("1A", prop={"propertyName": "NA"}),
    )
    result = load(db, content)
    assert (result.assets, result.loans_loaded) == (2, 0)
    assert result.quarantined == [Quarantined("1", 1, "ending balance is negative")]
    assert count(db, LoanPeriod) == 0


@needs_db
def test_a_tape_of_companion_notes_only_loads_no_loans(db):
    result = load(db, tape(asset("1A", prop={"propertyName": "NA"})))
    assert (result.assets, result.loans_loaded, result.quarantined) == (1, 0, [])
    assert count(db, QuarantinedLoan) == 0


def test_a_run_needs_an_actor():
    with pytest.raises(ValueError, match="a run's writes are audited, so a run id needs an actor"):
        load_tape(
            None,  # pyright: ignore[reportArgumentType]
            deal=DEAL,
            content=b"",
            file_name="ex102.xml",
            accession=ACCESSION,
            filing_date=FILED,
            run_id=1,
        )


@needs_db
def test_two_loads_of_the_same_filing_at_once_both_succeed_and_store_it_once(db_url):
    outcome: dict[str, object] = {}

    def second() -> None:
        try:
            with engine.session() as other:
                outcome["summary"] = load(other)
        except Exception as error:  # the assertion below reports it
            outcome["error"] = error

    with Session(engine.engine()) as holder:
        first = load(holder)
        thread = threading.Thread(target=second)
        thread.start()
        time.sleep(0.3)  # let the second load reach the deal row the first one holds
        assert thread.is_alive(), "the second load did not wait for the first"
        holder.commit()
        thread.join(timeout=10)
    assert "error" not in outcome, f"second load failed: {outcome.get('error')!r}"
    assert outcome["summary"] == replace(first, already_loaded=True)
    with engine.session() as check:
        assert count(check, TapeAsset) == 9
        assert count(check, LoanPeriod) == 7
        assert count(check, QuarantinedLoan) == 0
