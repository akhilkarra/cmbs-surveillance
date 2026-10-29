"""Point-in-time reads: a value is visible only from its filing date, amendments add versions.

Every database test runs in a rolled-back session on one database for the module (`tx`). The
real August and September 2026 BANK 2018-BNK13 tapes and the BANK5 2026-5YR23 new issue are
loaded and read in one test; the rest use one- or two-asset tapes.
"""

import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest
from pandera.errors import SchemaErrors
from sqlalchemy.orm import Session

from cmbs.absee.schedule_al import parse_assets
from cmbs.analysis.staleness import TapeLoan, measure
from cmbs.domain import Period
from cmbs.panel import service as panel
from cmbs.panel.contract import ModelInput, Rejection, model_input, rejections
from cmbs.panel.service import NothingPublished, Superseded, UnknownDeal, loan_order
from cmbs.tape.service import LoadSummary, load_tape, plan
from tests.conftest import needs_db
from tests.fixtures.tape.database import module_database_fixture, tx_fixture  # noqa: F401
from tests.fixtures.tape.tapes import REAL_FILES, asset, fixture_bytes, real_bytes, tape

FIXTURE = "Fixture Trust 2026-F1"
BNK13 = "BANK 2018-BNK13"
NEW_ISSUE = "BANK5 2026-5YR23"
SEPTEMBER = Period(2026, 9)
AUGUST = Period(2026, 8)
REAL = [
    (BNK13, "bank-2018-bnk13_2026-08_ex102.xml.gz", dt.date(2026, 8, 27)),
    (BNK13, "bank-2018-bnk13_2026-09_ex102.xml.gz", dt.date(2026, 9, 29)),
    (NEW_ISSUE, "bank5-2026-5yr23_2026-09_ex102.xml.gz", dt.date(2026, 10, 1)),
]


@pytest.mark.parametrize(
    ("loans", "ordered"),
    [
        (["10", "2", "1"], ["1", "2", "10"]),
        (["2-001", "2", "1A", "1"], ["1", "1A", "2", "2-001"]),
        (["2-010", "2-002"], ["2-002", "2-010"]),
        (["B", "A"], ["A", "B"]),
    ],
)
def test_loan_order_compares_numbers_as_numbers(loans, ordered):
    assert sorted(loans, key=loan_order) == ordered


# ---------------------------------------------------------------------------- the real tapes


def load_real(db: Session) -> list[LoadSummary]:
    summaries = []
    for deal, file, filed in REAL:
        facts = REAL_FILES[file]
        summaries.append(
            load_tape(
                db,
                deal=deal,
                content=real_bytes(file),
                file_name="exh_102.xml",
                accession=facts.accession,
                filing_date=filed,
                cik=facts.cik,
            )
        )
    return summaries


@needs_db
def test_the_real_tapes_point_in_time(tx):
    """One test loads the three real tapes and reads them every way the panel can, so the
    cost of loading them is paid once and is part of this test's own time.
    """
    db = tx
    loads = [(s.assets, s.loans_loaded, len(s.quarantined), s.notes) for s in load_real(db)]
    assert loads == [(87, 62, 0, []), (67, 42, 0, []), (33, 33, 0, [])]
    bnk13 = panel.deal_named(db, BNK13).id
    new_issue = panel.deal_named(db, NEW_ISSUE).id

    # A tape is invisible before its filing date and visible from it.
    rows = panel.as_of(db, dt.date(2026, 9, 28), deal_id=bnk13)
    assert panel.periods(rows) == [AUGUST]
    assert len(rows) == 62
    assert {row.accession for row in rows} == {"0001888524-26-015516"}
    assert panel.as_of(db, dt.date(2026, 8, 26), deal_id=bnk13) == []
    rows = panel.as_of(db, dt.date(2026, 9, 29), deal_id=bnk13)
    assert panel.periods(rows) == [AUGUST, SEPTEMBER]
    september = [row.loan for row in rows if row.period == SEPTEMBER.first_day]
    assert len(september) == 42
    assert september[:3] == ["1", "2", "3"] and september[-1] == "56"

    # Filters by period and loan, and every deal in order without one.
    day = dt.date(2026, 10, 1)
    rows = panel.as_of(db, day, deal_id=bnk13, period=AUGUST)
    assert {row.period for row in rows} == {AUGUST.first_day} and len(rows) == 62
    rows = panel.as_of(db, day, deal_id=bnk13, loan="11")
    assert [row.period for row in rows] == [AUGUST.first_day, SEPTEMBER.first_day]
    assert rows[1].workout_strategy == "2" and rows[0].workout_strategy == "13"
    rows = panel.as_of(db, day, period=SEPTEMBER)
    assert len(rows) == 42 + 33
    assert [row.deal_id for row in rows] == sorted(row.deal_id for row in rows)

    # The Ditson Building in September, as the research notes give it.
    [ditson] = panel.as_of(db, dt.date(2026, 9, 29), deal_id=bnk13, period=SEPTEMBER, loan="11")
    assert (
        ditson.ending_balance,
        ditson.paid_through_date,
        ditson.payment_status,
        ditson.special_servicer_transfer_date,
        ditson.financials_end,
        ditson.dscr,
        ditson.occupancy,
        ditson.property_count,
    ) == (
        Decimal("37500000.00"),
        dt.date(2026, 3, 1),
        "3",
        dt.date(2026, 6, 4),
        dt.date(2026, 3, 31),
        Decimal("0.0348"),
        Decimal("0.28"),
        1,
    )
    sept = panel.as_of(db, dt.date(2026, 9, 29), deal_id=bnk13, period=SEPTEMBER)
    assert {row.loan for row in sept if row.defeased_status == "F"} == {"36", "49"}

    # A nested portfolio of the new issue is one loan row.
    [portfolio] = panel.as_of(db, day, deal_id=new_issue, loan="1")
    assert (portfolio.property_count, portfolio.property_type, portfolio.property_name) == (
        90,
        "IN",
        "Portfolio (90 properties)",
    )
    assert (portfolio.noi, portfolio.dscr, portfolio.occupancy, portfolio.financials_end) == (
        None,
        None,
        None,
        None,
    )

    # The latest period on a day, and refusals for what had not been filed.
    current = panel.tape_as_of(db, BNK13, dt.date(2026, 9, 20))
    assert (current.deal, current.as_of, current.period) == (BNK13, dt.date(2026, 9, 20), AUGUST)
    assert current.accession == "0001888524-26-015516"
    assert len(current.rows) == 62 and current.rows[0].loan == "1"
    assert current.rows[-1].loan == "62" and current.superseded == []
    assert panel.tape_as_of(db, BNK13, dt.date(2026, 10, 1), AUGUST).period == AUGUST
    with pytest.raises(UnknownDeal, match="^no deal named 'Nope'$"):
        panel.tape_as_of(db, "Nope", dt.date(2026, 10, 1))
    with pytest.raises(NothingPublished) as refusal:
        panel.tape_as_of(db, BNK13, dt.date(2026, 8, 1))
    assert str(refusal.value) == "no loan tape of BANK 2018-BNK13 had been filed by 2026-08-01"
    with pytest.raises(NothingPublished) as refusal:
        panel.tape_as_of(db, BNK13, dt.date(2026, 9, 28), SEPTEMBER)
    assert str(refusal.value) == (
        "no September 2026 loan tape of BANK 2018-BNK13 had been filed by 2026-09-28"
    )

    # Staleness matches the research notes; the new issue has only securitization figures.
    _, report = panel.staleness(db, BNK13, dt.date(2026, 9, 29))
    summary = report.summary
    assert (summary.active_loans, summary.loans_with_financials) == (34, 34)
    assert (summary.median_days, summary.p90_days, summary.median_days_vs_filing) == (73, 164, 91)
    assert report.loan("36").status == "excluded: defeased"
    _, report = panel.staleness(db, NEW_ISSUE, dt.date(2026, 10, 1))
    assert (report.summary.active_loans, report.summary.loans_with_financials) == (33, 0)
    assert report.summary.note == "only at-securitization financials so far"

    # A model input built before the filing date uses data published after it.
    built = model_input(ditson, "dscr", dt.date(2026, 9, 20))
    assert built == ModelInput(
        deal_id=bnk13,
        loan="11",
        period=SEPTEMBER.first_day,
        as_of=dt.date(2026, 9, 20),
        field="dscr",
        value=Decimal("0.0348"),
        accession="0001888524-26-017705",
        published=dt.date(2026, 9, 29),
    )
    assert [r.reason for r in rejections([built])] == ["uses data published after 2026-09-20"]


def test_staleness_of_the_fixture_without_a_database():
    """The fixture's figures through plan() and measure(), as the panel would show them."""
    rows = [row for _, row in plan(parse_assets(fixture_bytes())).loaded]
    report = measure(
        [
            TapeLoan(
                loan=row.loan,
                ending_balance=row.ending_balance,
                property_name=row.property_name,
                property_type=row.property_type,
                defeased_status=row.defeased_status,
                reporting_period_end=row.reporting_period_end or dt.date.min,
                financials_end=row.financials_end,
                filing_date=dt.date(2026, 9, 29),
            )
            for row in rows
        ]
    )
    assert report.loan("1").days_vs_filing == 91
    assert (report.summary.median_days, report.summary.p90_days) == (164, 619)


# ---------------------------------------------------------------------------- versions


CIK = 9_900_001
ORIGINAL = "0009900001-26-000101"
ONE_LOAN = tape(
    asset(
        "1",
        prop={"mostRecentDebtServiceCoverageNetOperatingIncomePercentage": "1.45"},
        reportPeriodEndActualBalanceAmount="50000000.00",
    ),
    asset("7", reportPeriodEndActualBalanceAmount="9000000.00"),
)
CORRECTED = ONE_LOAN.replace(b">1.45<", b">1.25<")


def load(db: Session, accession: str, filed: dt.date, content: bytes = ONE_LOAN, **kw):
    return load_tape(
        db,
        deal=FIXTURE,
        content=content,
        file_name="ex102.xml",
        accession=accession,
        filing_date=filed,
        cik=CIK,
        **kw,
    )


def amendment(db: Session, accession: str, filed: dt.date, amends: str, content=CORRECTED):
    return load(db, accession, filed, content, form="ABS-EE/A", amends=amends)


def dscr_as_of(db: Session, day: dt.date) -> tuple[Decimal | None, str]:
    [row] = panel.as_of(db, day, loan="1")
    return row.dscr, row.accession


@needs_db
def test_before_an_amendment_the_original_is_shown(tx):
    load(tx, ORIGINAL, dt.date(2026, 9, 29))
    amendment(tx, "0009900001-26-000102", dt.date(2026, 10, 5), ORIGINAL)
    assert dscr_as_of(tx, dt.date(2026, 10, 4)) == (Decimal("1.45"), ORIGINAL)
    assert dscr_as_of(tx, dt.date(2026, 10, 5)) == (Decimal("1.25"), "0009900001-26-000102")
    deal = panel.deal_named(tx, FIXTURE)
    stored = panel.versions(tx, deal_id=deal.id, loan="1", period=SEPTEMBER)
    assert [(v.dscr, v.filing_date) for v in stored] == [
        (Decimal("1.45"), dt.date(2026, 9, 29)),
        (Decimal("1.25"), dt.date(2026, 10, 5)),
    ]


@needs_db
def test_an_amendment_filed_the_same_day_wins_whatever_its_accession(tx):
    """The reviewer's case: a different filer agent's lower accession, the same day."""
    load(tx, ORIGINAL, dt.date(2026, 9, 29))
    amendment(tx, "0000950123-26-000001", dt.date(2026, 9, 29), ORIGINAL)
    assert dscr_as_of(tx, dt.date(2026, 9, 29)) == (Decimal("1.25"), "0000950123-26-000001")
    deal = panel.deal_named(tx, FIXTURE)
    stored = panel.versions(tx, deal_id=deal.id, loan="1", period=SEPTEMBER)
    assert [v.accession for v in stored] == [ORIGINAL, "0000950123-26-000001"]


@needs_db
def test_a_later_filing_with_a_lower_accession_wins_over_a_deeper_earlier_one(tx):
    """Filing date ranks first: a plain re-filing on 10-05 (lower accession, depth 0) is
    current over an amendment of 10-01 (higher accession, depth 1)."""
    load(tx, ORIGINAL, dt.date(2026, 9, 29))
    amendment(tx, "0009900001-26-000102", dt.date(2026, 10, 1), ORIGINAL)
    refiled = ONE_LOAN.replace(b">1.45<", b">1.05<")
    load(tx, "0000950123-26-000001", dt.date(2026, 10, 5), refiled)
    assert dscr_as_of(tx, dt.date(2026, 10, 2)) == (Decimal("1.25"), "0009900001-26-000102")
    assert dscr_as_of(tx, dt.date(2026, 10, 5)) == (Decimal("1.05"), "0000950123-26-000001")


@needs_db
def test_an_amendment_of_an_amendment_on_one_day_ranks_deepest(tx):
    load(tx, ORIGINAL, dt.date(2026, 9, 29))
    amendment(tx, "0009900001-26-000102", dt.date(2026, 9, 29), ORIGINAL)
    third = ONE_LOAN.replace(b">1.45<", b">1.15<")
    amendment(tx, "0000950123-26-000001", dt.date(2026, 9, 29), "0009900001-26-000102", third)
    assert dscr_as_of(tx, dt.date(2026, 9, 29)) == (Decimal("1.15"), "0000950123-26-000001")


# ---------------------------------------------------------------------------- superseded values


@needs_db
def test_a_loan_the_amendment_omits_is_left_out_with_a_note(tx):
    load(tx, ORIGINAL, dt.date(2026, 9, 29))
    only_one = tape(asset("1", reportPeriodEndActualBalanceAmount="50000000.00"))
    amendment(tx, "0009900001-26-000102", dt.date(2026, 10, 5), ORIGINAL, only_one)
    deal = panel.deal_named(tx, FIXTURE)
    assert [r.loan for r in panel.as_of(tx, dt.date(2026, 10, 5), deal_id=deal.id)] == ["1"]
    assert panel.superseded(tx, dt.date(2026, 10, 5), deal_id=deal.id) == [
        Superseded(
            deal_id=deal.id,
            loan="7",
            period=SEPTEMBER.first_day,
            accession=ORIGINAL,
            filing_date=dt.date(2026, 9, 29),
            superseded_by="0009900001-26-000102",
            reason="not reported in 0009900001-26-000102",
        )
    ]
    assert panel.superseded(tx, dt.date(2026, 10, 4), deal_id=deal.id) == []
    assert [r.loan for r in panel.tape_as_of(tx, FIXTURE, dt.date(2026, 10, 5)).superseded] == ["7"]


@needs_db
def test_a_loan_the_amendment_quarantines_is_left_out_with_the_reason(tx):
    load(tx, ORIGINAL, dt.date(2026, 9, 29))
    bad = ONE_LOAN.replace(b">9000000.00<", b">-9000000.00<")
    amendment(tx, "0009900001-26-000102", dt.date(2026, 10, 5), ORIGINAL, bad)
    [left_out] = panel.superseded(tx, dt.date(2026, 10, 6))
    assert (left_out.loan, left_out.reason) == (
        "7",
        "quarantined in 0009900001-26-000102: ending balance is negative",
    )
    assert [r.loan for r in panel.as_of(tx, dt.date(2026, 10, 6))] == ["1"]


@needs_db
def test_the_latest_earlier_version_is_the_one_noted(tx):
    load(tx, ORIGINAL, dt.date(2026, 9, 29))
    amendment(tx, "0009900001-26-000102", dt.date(2026, 10, 1), ORIGINAL)
    only_one = tape(asset("1", reportPeriodEndActualBalanceAmount="50000000.00"))
    amendment(tx, "0009900001-26-000103", dt.date(2026, 10, 5), "0009900001-26-000102", only_one)
    [left_out] = panel.superseded(tx, dt.date(2026, 10, 5))
    assert (left_out.loan, left_out.accession) == ("7", "0009900001-26-000102")


@needs_db
def test_a_period_whose_latest_tape_is_all_quarantined_shows_no_rows(tx):
    load(tx, ORIGINAL, dt.date(2026, 9, 29))
    bad = tape(asset("1", reportPeriodEndActualBalanceAmount="-1.00"))
    amendment(tx, "0009900001-26-000102", dt.date(2026, 10, 5), ORIGINAL, bad)
    current = panel.tape_as_of(tx, FIXTURE, dt.date(2026, 10, 5))
    assert (current.accession, current.rows) == ("0009900001-26-000102", [])
    assert [s.loan for s in current.superseded] == ["1", "7"]


# ---------------------------------------------------------------------------- as_of contract


def entry(as_of: dt.date, published: dt.date, loan: str = "1") -> ModelInput:
    return ModelInput(
        deal_id=1,
        loan=loan,
        period=SEPTEMBER.first_day,
        as_of=as_of,
        field="dscr",
        value=Decimal("1.45"),
        accession=ORIGINAL,
        published=published,
    )


def test_a_value_published_on_or_before_the_as_of_date_passes():
    assert rejections([entry(dt.date(2026, 9, 29), dt.date(2026, 9, 29))]) == []
    assert rejections([entry(dt.date(2026, 9, 30), dt.date(2026, 9, 29))]) == []
    assert rejections([]) == []


def test_a_value_published_later_is_rejected_with_the_as_of_date():
    late = entry(dt.date(2026, 9, 20), dt.date(2026, 9, 29), loan="2")
    early = entry(dt.date(2026, 9, 29), dt.date(2026, 9, 29))
    other = entry(dt.date(2026, 9, 28), dt.date(2026, 9, 29), loan="3")
    assert rejections([late, early, other]) == [
        Rejection(late, "uses data published after 2026-09-20"),
        Rejection(other, "uses data published after 2026-09-28"),
    ]


def test_a_row_without_a_publishing_date_is_a_bug_not_look_ahead():
    unknown = replace(entry(dt.date(2026, 9, 20), dt.date(2026, 9, 29)), published=None)
    with pytest.raises(SchemaErrors):
        rejections([unknown])  # pyright: ignore[reportArgumentType]
