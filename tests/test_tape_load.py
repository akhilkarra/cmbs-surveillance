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

from cmbs.absee.schedule_al import MalformedTape, parse_assets
from cmbs.access.roles import Role
from cmbs.access.service import Actor
from cmbs.audit.tables import AuditEntry
from cmbs.db import engine
from cmbs.deals.service import ConflictingDeal, get_or_create
from cmbs.deals.tables import Deal
from cmbs.domain import Period
from cmbs.filings.service import ConflictingFiling, register_filing, sha256_of, store_file
from cmbs.filings.tables import Filing, FilingFile
from cmbs.tape.service import (
    LOAD_ACTION,
    QUARANTINE_ACTION,
    TAPE_ACTION,
    LoadSummary,
    Quarantined,
    TapeRefused,
    amendment_chain,
    check_form,
    load_tape,
    plan,
    quarantined_loans,
    reporting_period_ends,
    stored_assets,
    tape_period,
)
from cmbs.tape.tables import LoanPeriod, LoanTape, QuarantinedLoan, TapeAsset
from tests.conftest import needs_db
from tests.fixtures.tape.database import module_database_fixture, tx_fixture  # noqa: F401
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
    assets = parse_assets(fixture_bytes())
    assert [a.asset_number for a in assets] == ["1", "1A", "2", "2-001", "3", "4", "5", "6", "7"]
    work = plan(assets)
    assert [(a.asset_number, a.position) for a, _ in work.loaded] == [
        ("1", 1),
        ("2", 3),
        ("3", 5),
        ("4", 6),
        ("5", 7),
        ("6", 8),
        ("7", 9),
    ]
    assert work.quarantined == []
    rows = {row.loan: row for _, row in work.loaded}
    assert (rows["1"].ending_balance, rows["1"].paid_through_date, rows["1"].dscr) == (
        Decimal("50000000.00"),
        dt.date(2026, 9, 1),
        Decimal("1.45"),
    )
    assert (rows["5"].noi, rows["5"].financials_end) == (None, None)


def test_plan_quarantines_the_fixtures_negative_balance():
    content = fixture_bytes().replace(
        b"<reportPeriodEndActualBalanceAmount>12000000.00<",
        b"<reportPeriodEndActualBalanceAmount>-12000000.00<",
    )
    work = plan(parse_assets(content))
    assert [a.asset_number for a, _ in work.loaded] == ["1", "2", "3", "4", "6", "7"]
    assert [(a.asset_number, a.position, reason) for a, reason in work.quarantined] == [
        ("5", 7, "ending balance is negative")
    ]


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
        "notes": [],
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
    assert summary(loans_loaded=1).text().endswith("9 assets, 1 loan loaded, 1 loan quarantined")
    assert (
        summary(notes=["loan 1: x"])
        .text()
        .endswith("1 loan quarantined, 1 loan with property notes")
    )
    assert (
        summary(notes=["loan 1: x", "loan 2: y"]).text().endswith(", 2 loans with property notes")
    )


# ---------------------------------------------------------------------------- database
# Database tests use a two-asset tape: they check writing, locking and refusals. The values of
# the full fixture and the real tapes are checked through plan() above, without a database.

SMALL = tape(
    asset(
        "1",
        prop={
            "propertyName": "Harbor Office Tower",
            "propertyTypeCode": "OF",
            "mostRecentDebtServiceCoverageNetOperatingIncomePercentage": "1.45",
        },
        reportPeriodEndActualBalanceAmount="50000000.00",
        paidThroughDate="09-01-2026",
    ),
    asset("1A", prop={"propertyName": "NA"}),
)
NEGATIVE = tape(asset("5", reportPeriodEndActualBalanceAmount="-12000000.00"), asset("6"))


def load(db: Session, content: bytes | None = None, **changes):
    arguments = {
        "deal": DEAL,
        "content": SMALL if content is None else content,
        "file_name": "ex102.xml",
        "accession": ACCESSION,
        "filing_date": FILED,
        "cik": CIK,
    } | changes
    return load_tape(db, **arguments)


def amend(db: Session, accession: str, filed: dt.date, amends: str, content: bytes | None = None):
    return load(
        db,
        content,
        accession=accession,
        filing_date=filed,
        form="ABS-EE/A",
        amends=amends,
    )


def count(db: Session, table) -> int:
    return db.scalar(select(func.count()).select_from(table)) or 0


@needs_db
def test_loads_raw_bronze_and_silver(tx):
    result = load(tx)
    tx.commit()
    assert result == LoadSummary(
        deal=DEAL,
        accession=ACCESSION,
        period=SEPTEMBER,
        filing_date=FILED,
        file_sha256=sha256_of(SMALL),
        assets=2,
        loans_loaded=1,
        quarantined=[],
        already_loaded=False,
        notes=[],
    )
    filing = tx.get(Filing, ACCESSION)
    assert filing is not None
    assert (filing.cik, filing.form, filing.period, filing.amends) == (
        CIK,
        "ABS-EE",
        dt.date(2026, 9, 1),
        None,
    )
    loaded = tx.get(LoanTape, ACCESSION)
    assert loaded is not None
    assert (loaded.file_name, loaded.period, loaded.filing_date, loaded.amendment_depth) == (
        "ex102.xml",
        dt.date(2026, 9, 1),
        FILED,
        0,
    )
    assert [(a.asset_number, a.position) for a in stored_assets(tx, ACCESSION)] == [
        ("1", 1),
        ("1A", 2),
    ]
    [one] = tx.scalars(select(LoanPeriod)).all()
    assert (one.loan, one.period, one.filing_date, one.accession, one.file_name) == (
        "1",
        dt.date(2026, 9, 1),
        FILED,
        ACCESSION,
        "ex102.xml",
    )
    assert (one.asset_position, one.file_sha256, one.ending_balance, one.dscr) == (
        1,
        sha256_of(SMALL),
        Decimal("50000000.00"),
        Decimal("1.45"),
    )
    assert (one.property_count, one.noi, one.financials_end) == (1, None, None)


@needs_db
def test_loading_again_changes_nothing_and_reports_the_same_counts(tx):
    first = load(tx)
    tx.commit()
    again = load(tx)
    assert again == replace(first, already_loaded=True)
    assert count(tx, TapeAsset) == 2
    assert count(tx, LoanPeriod) == 1


@needs_db
def test_a_loan_failing_the_contract_is_quarantined_not_loaded(tx):
    result = load(tx, NEGATIVE)
    assert result.loans_loaded == 1
    assert result.quarantined == [Quarantined("5", 1, "ending balance is negative")]
    [stored] = quarantined_loans(tx, ACCESSION)
    assert (stored.loan, stored.period, stored.filing_date, stored.reason) == (
        "5",
        dt.date(2026, 9, 1),
        FILED,
        "ending balance is negative",
    )
    assert set(tx.scalars(select(LoanPeriod.loan))) == {"6"}
    assert count(tx, TapeAsset) == 2  # bronze keeps the loan as reported


@needs_db
def test_a_portfolio_note_reaches_the_summary(tx):
    content = tape(
        asset("1").replace(
            "</assets>",
            "<property><mostRecentFinancialsEndDate>06-30-2026</mostRecentFinancialsEndDate>"
            "</property><property></property></assets>",
        )
    )
    result = load(tx, content)
    note = (
        "1 of 2 properties report no most recent financials, "
        "so the loan's NOI, NCF and DSCR are not reported"
    )
    assert result.notes == [f"loan 1: {note}"]
    assert tx.scalars(select(LoanPeriod.property_note)).one() == note


@needs_db
def test_the_database_refuses_a_negative_balance_the_contract_missed(tx):
    load(tx)
    tx.flush()
    with pytest.raises(IntegrityError, match="ck_loan_period_ending_balance_not_negative"):
        tx.execute(text("UPDATE silver.loan_period SET ending_balance = -1 WHERE loan = '1'"))


@needs_db
@pytest.mark.parametrize(
    ("table", "column", "value", "constraint"),
    [
        ("silver.loan_period", "beginning_balance", "-1", "beginning_balance_not_negative"),
        ("silver.loan_period", "financials_start", "'2026-07-01'", "financials_in_order"),
        ("silver.loan_period", "period", "'2026-09-02'", "loan_period_period_is_first"),
        ("silver.loan_period", "file_sha256", "'abc'", "sha256_length"),
        ("silver.loan_period", "property_count", "-1", "property_count_not_negative"),
        (
            "silver.loan_period",
            "financials_end_newest",
            "'2026-01-01'",
            "newest_financials_not_older",
        ),
        ("bronze.loan_tape", "amendment_depth", "-1", "depth_not_negative"),
        ("bronze.loan_tape", "period", "'2026-09-02'", "loan_tape_period_is_first"),
    ],
)
def test_the_database_repeats_the_contract(tx, table, column, value, constraint):
    load(tx)
    tx.execute(
        text(
            "UPDATE silver.loan_period SET financials_start = '2026-01-01', "
            "financials_end = '2026-06-30', financials_end_newest = '2026-06-30'"
        )
    )
    tx.flush()
    with pytest.raises(IntegrityError, match=constraint):
        tx.execute(text(f"UPDATE {table} SET {column} = {value}"))


# ---------------------------------------------------------------------------- refusals


def deals(db: Session) -> list[str]:
    return list(db.scalars(select(Deal.name)))


@needs_db
def test_a_deal_without_a_cik_is_refused_before_anything_is_written(tx):
    with pytest.raises(TapeRefused) as refusal:
        load(tx, cik=None)
    assert (
        str(refusal.value)
        == "Fixture Trust 2026-F1 has no CIK, so its filings cannot be registered"
    )
    assert deals(tx) == []  # not even in the session: nothing was written before the check
    assert count(tx, Filing) == 0


@needs_db
def test_a_refused_amendment_for_a_new_deal_leaves_no_deal(tx):
    with pytest.raises(TapeRefused) as refusal:
        amend(tx, AMENDMENT, dt.date(2026, 10, 5), ACCESSION)
    assert str(refusal.value) == (
        "0009900001-26-000101 is not a registered September 2026 loan tape of this deal"
    )
    assert deals(tx) == []


@needs_db
def test_the_deals_known_cik_is_used(tx):
    get_or_create(tx, DEAL, cik=CIK)
    assert load(tx, cik=None).loans_loaded == 1
    assert tx.get(Filing, ACCESSION).cik == CIK  # pyright: ignore[reportOptionalMemberAccess]


@needs_db
def test_a_different_cik_for_a_known_deal_is_refused(tx):
    get_or_create(tx, DEAL, cik=CIK)
    with pytest.raises(
        ConflictingDeal, match="^Fixture Trust 2026-F1 already has CIK 9900001, not 1$"
    ):
        load(tx, cik=1)
    assert count(tx, Filing) == 0


@needs_db
def test_an_empty_tape_is_refused(tx):
    with pytest.raises(TapeRefused, match="^the tape lists no assets$"):
        load(tx, tape())


@needs_db
def test_a_second_file_for_a_loaded_accession_is_refused(tx):
    load(tx)
    with pytest.raises(TapeRefused) as refusal:
        load(tx, file_name="other.xml")
    assert str(refusal.value) == "0009900001-26-000101 is already loaded from ex102.xml"
    assert tx.get(FilingFile, (ACCESSION, "other.xml")) is None  # refused before storing


@needs_db
def test_different_bytes_under_the_same_file_are_refused(tx):
    load(tx)
    with pytest.raises(ConflictingFiling) as refusal:
        load(tx, SMALL + b"\n")
    assert (
        str(refusal.value) == "0009900001-26-000101/ex102.xml is already stored with other content"
    )


@needs_db
def test_a_file_stored_with_other_bytes_before_any_load_is_refused(tx):
    deal = get_or_create(tx, DEAL, cik=CIK)
    register_filing(
        tx, accession=ACCESSION, cik=CIK, form="ABS-EE", filing_date=FILED, deal_id=deal.id
    )
    store_file(tx, accession=ACCESSION, name="ex102.xml", content=b"<other/>")
    with pytest.raises(ConflictingFiling, match="already stored with other content"):
        load(tx)
    assert count(tx, LoanTape) == 0


@needs_db
@pytest.mark.parametrize(
    ("registered", "message"),
    [
        (
            {"period": dt.date(2026, 8, 1)},
            "0009900001-26-000101 already has period datetime.date(2026, 8, 1), "
            "not datetime.date(2026, 9, 1)",
        ),
        ({"form": "10-D"}, "0009900001-26-000101 is already registered with different facts"),
        (
            {"filing_date": dt.date(2026, 9, 30)},
            "0009900001-26-000101 is already registered with different facts",
        ),
    ],
)
def test_a_filing_registered_with_other_facts_is_refused_before_writing(tx, registered, message):
    deal = get_or_create(tx, DEAL, cik=CIK)
    facts = {"form": "ABS-EE", "filing_date": FILED, "deal_id": deal.id} | registered
    register_filing(tx, accession=ACCESSION, cik=CIK, **facts)
    with pytest.raises(ConflictingFiling) as refusal:
        load(tx)
    assert str(refusal.value) == message
    assert count(tx, LoanTape) == 0


@needs_db
def test_a_filing_registered_to_another_deal_is_refused_before_creating_this_one(tx):
    other = get_or_create(tx, "Fixture Trust 2026-F2", cik=CIK)
    register_filing(
        tx, accession=ACCESSION, cik=CIK, form="ABS-EE", filing_date=FILED, deal_id=other.id
    )
    with pytest.raises(
        ConflictingFiling, match="^0009900001-26-000101 is already registered to another deal$"
    ):
        load(tx)
    assert deals(tx) == ["Fixture Trust 2026-F2"]


@needs_db
def test_a_filing_registered_with_another_amendment_link_is_refused(tx):
    load(tx)
    deal = get_or_create(tx, DEAL)
    register_filing(
        tx,
        accession="0009900001-26-000109",
        cik=CIK,
        form="ABS-EE",
        filing_date=FILED,
        deal_id=deal.id,
        period=dt.date(2026, 9, 1),
    )
    register_filing(
        tx,
        accession=AMENDMENT,
        cik=CIK,
        form="ABS-EE/A",
        filing_date=dt.date(2026, 10, 5),
        deal_id=deal.id,
        amends="0009900001-26-000109",
    )
    with pytest.raises(
        ConflictingFiling,
        match="already has amends '0009900001-26-000109', not '0009900001-26-000101'",
    ):
        amend(tx, AMENDMENT, dt.date(2026, 10, 5), ACCESSION)


@needs_db
def test_a_tape_loads_on_top_of_the_filing_as_discovery_registers_it(tx):
    """Discovery registers the ABS-EE first, with EDGAR's primary document and the
    distribution date; the tape then loads without contradicting either."""
    deal = get_or_create(tx, "BANK 2018-BNK13", cik=1744707)
    accession = "0001888524-26-017705"
    register_filing(
        tx,
        accession=accession,
        cik=1744707,
        form="ABS-EE",
        filing_date=FILED,
        deal_id=deal.id,
        report_date=dt.date(2026, 9, 17),
        period=dt.date(2026, 9, 1),
        primary_document="wcm18b13_absee-202609.htm",
    )
    summary = load_tape(
        tx,
        deal="BANK 2018-BNK13",
        content=SMALL,
        file_name="exh_102.xml",
        accession=accession,
        filing_date=FILED,
    )
    assert summary.period == SEPTEMBER
    filing = tx.get(Filing, accession)
    assert filing is not None
    assert (filing.period, filing.report_date, filing.primary_document) == (
        dt.date(2026, 9, 1),
        dt.date(2026, 9, 17),
        "wcm18b13_absee-202609.htm",
    )
    assert tx.get(FilingFile, (accession, "exh_102.xml")) is not None


@needs_db
def test_a_loaded_tape_leaves_primary_document_and_report_date_to_discovery(tx):
    load(tx)
    filing = tx.get(Filing, ACCESSION)
    assert filing is not None
    assert (filing.period, filing.report_date, filing.primary_document) == (
        dt.date(2026, 9, 1),
        None,
        None,
    )


# ---------------------------------------------------------------------------- amendments


@needs_db
def test_an_amendment_adds_a_version_and_keeps_the_original(tx):
    load(tx)
    result = amend(
        tx, AMENDMENT, dt.date(2026, 10, 5), ACCESSION, SMALL.replace(b">1.45<", b">1.25<")
    )
    assert result.loans_loaded == 1
    versions = tx.execute(
        select(LoanPeriod.accession, LoanPeriod.filing_date, LoanPeriod.dscr).order_by(
            LoanPeriod.filing_date
        )
    ).all()
    assert [tuple(v) for v in versions] == [
        (ACCESSION, FILED, Decimal("1.45")),
        (AMENDMENT, dt.date(2026, 10, 5), Decimal("1.25")),
    ]
    filing = tx.get(Filing, AMENDMENT)
    assert filing is not None and (filing.form, filing.amends) == ("ABS-EE/A", ACCESSION)
    assert tx.get(LoanTape, AMENDMENT).amendment_depth == 1  # pyright: ignore[reportOptionalMemberAccess]


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
def test_an_amendment_must_follow_a_loaded_tape(tx, amends, filed, message):
    load(tx)
    with pytest.raises(TapeRefused) as refusal:
        amend(tx, AMENDMENT, filed, amends)
    assert str(refusal.value) == message


@needs_db
def test_an_amendment_must_be_of_the_same_deal_period_and_kind(tx):
    load(tx)
    other = get_or_create(tx, "Fixture Trust 2026-F2", cik=CIK + 1)
    deal = get_or_create(tx, DEAL)
    for accession, cik, form, deal_id, period in [
        ("0009900001-26-000103", CIK + 1, "ABS-EE", other.id, dt.date(2026, 9, 1)),
        ("0009900001-26-000104", CIK, "10-D", deal.id, dt.date(2026, 9, 1)),
        ("0009900001-26-000105", CIK, "ABS-EE", deal.id, dt.date(2026, 8, 1)),
    ]:
        register_filing(
            tx,
            accession=accession,
            cik=cik,
            form=form,
            filing_date=FILED,
            deal_id=deal_id,
            period=period,
        )
        with pytest.raises(TapeRefused, match="is not a registered September 2026 loan tape"):
            amend(tx, AMENDMENT, dt.date(2026, 10, 5), accession)


@needs_db
def test_an_amendment_of_an_amendment_ranks_deeper(tx):
    load(tx)
    amend(tx, AMENDMENT, dt.date(2026, 10, 5), ACCESSION)
    third = amend(tx, "0009900001-26-000106", dt.date(2026, 10, 6), AMENDMENT)
    assert third.loans_loaded == 1
    assert tx.get(LoanTape, "0009900001-26-000106").amendment_depth == 2  # pyright: ignore[reportOptionalMemberAccess]


@needs_db
def test_amendment_chain_follows_registered_filings_only(tx):
    load(tx)
    amend(tx, AMENDMENT, dt.date(2026, 10, 5), ACCESSION)
    assert [f.accession for f in amendment_chain(tx, AMENDMENT)] == [AMENDMENT, ACCESSION]
    assert amendment_chain(tx, None) == []
    assert amendment_chain(tx, "0000000000-00-000000") == []


@needs_db
def test_two_unlinked_tapes_of_one_period_filed_the_same_day_are_refused(tx):
    load(tx)
    with pytest.raises(TapeRefused) as refusal:
        load(tx, accession="0000950123-26-000001")
    assert str(refusal.value) == (
        "0000950123-26-000001 and 0009900001-26-000101 are both September 2026 tapes of "
        "Fixture Trust 2026-F1 filed on 2026-09-29, and neither amends the other"
    )
    assert count(tx, LoanTape) == 1


@needs_db
def test_an_amendment_filed_the_same_day_is_accepted(tx):
    load(tx)
    result = amend(tx, "0000950123-26-000001", FILED, ACCESSION)
    assert result.loans_loaded == 1


@needs_db
def test_a_same_day_original_registered_after_its_amendment_is_accepted(tx):
    """Discovery may register the amendment before the original is loaded."""
    deal = get_or_create(tx, DEAL, cik=CIK)
    register_filing(
        tx,
        accession=ACCESSION,
        cik=CIK,
        form="ABS-EE",
        filing_date=FILED,
        deal_id=deal.id,
        period=dt.date(2026, 9, 1),
    )
    register_filing(
        tx,
        accession="0000950123-26-000001",
        cik=CIK,
        form="ABS-EE/A",
        filing_date=FILED,
        deal_id=deal.id,
        period=dt.date(2026, 9, 1),
        amends=ACCESSION,
    )
    assert load(tx).loans_loaded == 1


@needs_db
def test_tapes_of_other_periods_or_days_do_not_clash(tx):
    load(tx)
    august = tape(asset("1", reportingPeriodEndDate="08-11-2026"))
    assert load(tx, august, accession="0000950123-26-000002").period == Period(2026, 8)
    later = load(tx, accession="0000950123-26-000003", filing_date=dt.date(2026, 9, 30))
    assert later.loans_loaded == 1


# ---------------------------------------------------------------------------- audit


@needs_db
def test_every_loan_row_written_is_audited_with_the_actor_and_run(tx):
    db = tx
    load(db, NEGATIVE, actor=Actor("olly", Role.OPERATOR), run_id=42)
    entries = db.scalars(select(AuditEntry).order_by(AuditEntry.id)).all()
    assert [(e.action, e.subject_key, e.reason) for e in entries] == [
        (QUARANTINE_ACTION, f"{DEAL}/5", "ending balance is negative"),
        (LOAD_ACTION, f"{DEAL}/6", None),
    ]
    first = entries[1]
    assert (first.user_name, first.role, first.run_id, first.subject, first.outcome) == (
        "olly",
        "operator",
        42,
        "loan",
        "done",
    )
    assert first.detail == {"accession": ACCESSION, "period": "2026-09-01"}
    load(db, NEGATIVE, actor=Actor("olly", Role.OPERATOR), run_id=43)
    assert count(db, AuditEntry) == 2  # a repeated load writes nothing, so audits nothing


@needs_db
def test_without_an_actor_nothing_is_audited(tx):
    load(tx)
    with pytest.raises(TapeRefused):
        load(tx, cik=None, deal="Fixture Trust 2026-F9")
    assert count(tx, AuditEntry) == 0


REFUSALS = [
    (
        {"cik": None},
        TapeRefused,
        "Fixture Trust 2026-F1 has no CIK, so its filings cannot be registered",
    ),
    ({"content": b"not xml"}, MalformedTape, "the file is not XML: syntax error: line 1, column 0"),
    ({"form": "10-D"}, TapeRefused, "a loan tape is filed as ABS-EE or ABS-EE/A, not 10-D"),
    (
        {"content": SMALL + b" "},
        ConflictingFiling,
        "0009900001-26-000101/ex102.xml is already stored with other content",
    ),
]


@needs_db
def test_a_refusal_with_an_actor_is_recorded_even_after_rollback(seeded):
    """Refusals are committed on their own connection, so these share one database."""
    db = seeded
    for number, (changes, error, message) in enumerate(REFUSALS):
        if number == len(REFUSALS) - 1:  # the last refusal needs the tape already stored
            load(db)
            db.commit()
        with pytest.raises(error) as refusal:
            load(db, actor=Actor("quinn", Role.QUANT), **changes)
        assert str(refusal.value) == message
        db.rollback()
    entries = db.scalars(select(AuditEntry).order_by(AuditEntry.id)).all()
    assert [entry.reason for entry in entries] == [message for _, _, message in REFUSALS]
    for entry in entries:
        assert (entry.user_name, entry.role, entry.action, entry.outcome) == (
            "quinn",
            "quant",
            TAPE_ACTION,
            "refused",
        )
        assert (entry.subject, entry.subject_key) == ("filing", ACCESSION)


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
def test_a_tape_whose_every_loan_fails_stores_assets_and_quarantines_them(tx):
    content = tape(
        asset("1", reportPeriodEndActualBalanceAmount="-1.00"),
        asset("1A", prop={"propertyName": "NA"}),
    )
    result = load(tx, content)
    assert (result.assets, result.loans_loaded) == (2, 0)
    assert result.quarantined == [Quarantined("1", 1, "ending balance is negative")]
    assert count(tx, LoanPeriod) == 0


@needs_db
def test_a_tape_of_companion_notes_only_loads_no_loans(tx):
    result = load(tx, tape(asset("1A", prop={"propertyName": "NA"})))
    assert (result.assets, result.loans_loaded, result.quarantined) == (1, 0, [])
    assert count(tx, QuarantinedLoan) == 0


# ---------------------------------------------------------------------------- concurrency


def run_while_first_holds_its_locks(first, second):
    """Run `first` in a session that holds its locks until `second` (in another thread) has
    reached them, then commit; return both results."""
    outcome: dict[str, object] = {}

    def in_thread() -> None:
        try:
            with engine.session() as other:
                outcome["value"] = second(other)
        except Exception as error:  # the assertion below reports it
            outcome["error"] = error

    with Session(engine.engine()) as holder:
        value = first(holder)
        thread = threading.Thread(target=in_thread)
        thread.start()
        time.sleep(0.3)  # let the second load reach the deal row the first one holds
        assert thread.is_alive(), "the second load did not wait for the first"
        holder.commit()
        thread.join(timeout=10)
    return value, outcome


@needs_db
def test_two_loads_of_the_same_filing_at_once_both_succeed_and_store_it_once(db_url):
    first, outcome = run_while_first_holds_its_locks(load, load)
    assert "error" not in outcome, f"second load failed: {outcome.get('error')!r}"
    assert outcome["value"] == replace(first, already_loaded=True)
    with engine.session() as check:
        assert count(check, TapeAsset) == 2
        assert count(check, LoanPeriod) == 1
        assert count(check, LoanTape) == 1


@needs_db
def test_two_unlinked_same_day_tapes_of_a_new_deal_at_once_are_caught_under_the_lock(db_url):
    _, outcome = run_while_first_holds_its_locks(
        load, lambda other: load(other, accession="0000950123-26-000001")
    )
    assert isinstance(outcome.get("error"), TapeRefused), outcome
    assert "neither amends the other" in str(outcome["error"])
    with engine.session() as check:
        assert count(check, LoanTape) == 1
