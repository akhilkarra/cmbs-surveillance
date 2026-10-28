"""Point-in-time reads: a value is visible only from its filing date, amendments add versions.

The read-only tests share one database per module, with the real August and September 2026
BANK 2018-BNK13 tapes (and the BANK5 2026-5YR23 new issue) loaded once. Tests that write use
the per-test `db` fixture and one- or two-asset tapes.
"""

import datetime as dt
import os
from collections.abc import Iterator
from dataclasses import replace
from decimal import Decimal

import pytest
from pandera.errors import SchemaErrors
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from cmbs.absee.schedule_al import parse_assets
from cmbs.analysis.staleness import TapeLoan, measure
from cmbs.db.registry import load_all_tables
from cmbs.domain import Period
from cmbs.panel import service as panel
from cmbs.panel.contract import ModelInput, Rejection, model_input, rejections
from cmbs.panel.service import NothingPublished, Superseded, UnknownDeal, loan_order
from cmbs.tape.service import LoadSummary, load_tape, plan
from tests.conftest import needs_db
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


class Loaded:
    def __init__(self, db: Session, summaries: dict[str, LoadSummary]) -> None:
        self.db = db
        self.summaries = summaries

    def deal_id(self, name: str) -> int:
        return panel.deal_named(self.db, name).id


@pytest.fixture(scope="module")
def real(db_template) -> Iterator[Loaded]:
    """One database for the module with the three real tapes loaded, read but never written."""
    from cmbs.testing import databases

    load_all_tables()
    name = f"cmbs_pytest_{os.getpid()}_panel_real"
    url = databases.clone(db_template, name)
    bind = create_engine(url)
    summaries = {}
    with Session(bind, expire_on_commit=False) as db:
        for deal, file, filed in REAL:
            facts = REAL_FILES[file]
            summaries[file] = load_tape(
                db,
                deal=deal,
                content=real_bytes(file),
                file_name="exh_102.xml",
                accession=facts.accession,
                filing_date=filed,
                cik=facts.cik,
            )
        db.commit()
        yield Loaded(db, summaries)
    bind.dispose()
    databases.drop(name)


@needs_db
def test_the_real_tapes_load_end_to_end(real):
    loads = [
        (s.assets, s.loans_loaded, len(s.quarantined), s.notes) for s in real.summaries.values()
    ]
    assert loads == [(87, 62, 0, []), (67, 42, 0, []), (33, 33, 0, [])]
    september = real.summaries["bank-2018-bnk13_2026-09_ex102.xml.gz"]
    assert september.file_sha256 == REAL_FILES["bank-2018-bnk13_2026-09_ex102.xml.gz"].sha256


@needs_db
def test_a_tape_is_invisible_before_its_filing_date(real):
    rows = panel.as_of(real.db, dt.date(2026, 9, 28), deal_id=real.deal_id(BNK13))
    assert panel.periods(rows) == [AUGUST]
    assert len(rows) == 62
    assert {row.accession for row in rows} == {"0001888524-26-015516"}
    assert panel.as_of(real.db, dt.date(2026, 8, 26), deal_id=real.deal_id(BNK13)) == []


@needs_db
def test_a_tape_is_visible_from_its_filing_date(real):
    rows = panel.as_of(real.db, dt.date(2026, 9, 29), deal_id=real.deal_id(BNK13))
    assert panel.periods(rows) == [AUGUST, SEPTEMBER]
    september = [row.loan for row in rows if row.period == SEPTEMBER.first_day]
    assert len(september) == 42
    assert september[:3] == ["1", "2", "3"] and september[-1] == "56"


@needs_db
def test_as_of_filters_by_period_and_loan(real):
    day = dt.date(2026, 10, 1)
    rows = panel.as_of(real.db, day, deal_id=real.deal_id(BNK13), period=AUGUST)
    assert {row.period for row in rows} == {AUGUST.first_day} and len(rows) == 62
    rows = panel.as_of(real.db, day, deal_id=real.deal_id(BNK13), loan="11")
    assert [row.period for row in rows] == [AUGUST.first_day, SEPTEMBER.first_day]
    assert rows[1].workout_strategy == "2" and rows[0].workout_strategy == "13"


@needs_db
def test_as_of_without_a_deal_covers_every_deal_in_order(real):
    rows = panel.as_of(real.db, dt.date(2026, 10, 1), period=SEPTEMBER)
    deals = [row.deal_id for row in rows]
    assert len(rows) == 42 + 33
    assert deals == sorted(deals)


@needs_db
def test_ditson_building_in_september(real):
    day = dt.date(2026, 9, 29)
    [ditson] = panel.as_of(real.db, day, deal_id=real.deal_id(BNK13), period=SEPTEMBER, loan="11")
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


@needs_db
def test_defeased_loans_carry_their_status(real):
    rows = panel.as_of(real.db, dt.date(2026, 9, 29), deal_id=real.deal_id(BNK13), period=SEPTEMBER)
    assert {row.loan for row in rows if row.defeased_status == "F"} == {"36", "49"}


@needs_db
def test_a_nested_portfolio_is_one_loan_row(real):
    [loan] = panel.as_of(real.db, dt.date(2026, 10, 1), deal_id=real.deal_id(NEW_ISSUE), loan="1")
    assert (loan.property_count, loan.property_type, loan.property_name) == (
        90,
        "IN",
        "Portfolio (90 properties)",
    )
    assert (loan.noi, loan.dscr, loan.occupancy, loan.financials_end) == (None, None, None, None)


@needs_db
def test_tape_as_of_gives_the_latest_period_visible(real):
    tape_ = panel.tape_as_of(real.db, BNK13, dt.date(2026, 9, 20))
    assert (tape_.deal, tape_.as_of, tape_.period) == (BNK13, dt.date(2026, 9, 20), AUGUST)
    assert tape_.accession == "0001888524-26-015516"
    assert len(tape_.rows) == 62 and tape_.rows[0].loan == "1" and tape_.rows[-1].loan == "62"
    assert tape_.superseded == []
    assert panel.tape_as_of(real.db, BNK13, dt.date(2026, 10, 1), AUGUST).period == AUGUST


@needs_db
def test_tape_as_of_refuses_unknown_deals_and_unpublished_periods(real):
    with pytest.raises(UnknownDeal, match="^no deal named 'Nope'$"):
        panel.tape_as_of(real.db, "Nope", dt.date(2026, 10, 1))
    with pytest.raises(NothingPublished) as refusal:
        panel.tape_as_of(real.db, BNK13, dt.date(2026, 8, 1))
    assert str(refusal.value) == "no loan tape of BANK 2018-BNK13 had been filed by 2026-08-01"
    with pytest.raises(NothingPublished) as refusal:
        panel.tape_as_of(real.db, BNK13, dt.date(2026, 9, 28), SEPTEMBER)
    assert str(refusal.value) == (
        "no September 2026 loan tape of BANK 2018-BNK13 had been filed by 2026-09-28"
    )


@needs_db
def test_staleness_of_the_real_bnk13_tape_matches_the_research(real):
    _, report = panel.staleness(real.db, BNK13, dt.date(2026, 9, 29))
    summary = report.summary
    assert (summary.active_loans, summary.loans_with_financials) == (34, 34)
    assert (summary.median_days, summary.p90_days, summary.median_days_vs_filing) == (73, 164, 91)
    assert report.loan("36").status == "excluded: defeased"


@needs_db
def test_staleness_of_a_new_issue(real):
    _, report = panel.staleness(real.db, NEW_ISSUE, dt.date(2026, 10, 1))
    assert (report.summary.active_loans, report.summary.loans_with_financials) == (33, 0)
    assert report.summary.note == "only at-securitization financials so far"


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
def test_before_an_amendment_the_original_is_shown(db):
    load(db, ORIGINAL, dt.date(2026, 9, 29))
    amendment(db, "0009900001-26-000102", dt.date(2026, 10, 5), ORIGINAL)
    assert dscr_as_of(db, dt.date(2026, 10, 4)) == (Decimal("1.45"), ORIGINAL)
    assert dscr_as_of(db, dt.date(2026, 10, 5)) == (Decimal("1.25"), "0009900001-26-000102")
    deal = panel.deal_named(db, FIXTURE)
    stored = panel.versions(db, deal_id=deal.id, loan="1", period=SEPTEMBER)
    assert [(v.dscr, v.filing_date) for v in stored] == [
        (Decimal("1.45"), dt.date(2026, 9, 29)),
        (Decimal("1.25"), dt.date(2026, 10, 5)),
    ]


@needs_db
def test_an_amendment_filed_the_same_day_wins_whatever_its_accession(db):
    """The reviewer's case: a different filer agent's lower accession, the same day."""
    load(db, ORIGINAL, dt.date(2026, 9, 29))
    amendment(db, "0000950123-26-000001", dt.date(2026, 9, 29), ORIGINAL)
    assert dscr_as_of(db, dt.date(2026, 9, 29)) == (Decimal("1.25"), "0000950123-26-000001")
    deal = panel.deal_named(db, FIXTURE)
    stored = panel.versions(db, deal_id=deal.id, loan="1", period=SEPTEMBER)
    assert [v.accession for v in stored] == [ORIGINAL, "0000950123-26-000001"]


@needs_db
def test_a_later_filing_with_a_lower_accession_wins_over_a_deeper_earlier_one(db):
    """Filing date ranks first: a plain re-filing on 10-05 (lower accession, depth 0) is
    current over an amendment of 10-01 (higher accession, depth 1)."""
    load(db, ORIGINAL, dt.date(2026, 9, 29))
    amendment(db, "0009900001-26-000102", dt.date(2026, 10, 1), ORIGINAL)
    refiled = ONE_LOAN.replace(b">1.45<", b">1.05<")
    load(db, "0000950123-26-000001", dt.date(2026, 10, 5), refiled)
    assert dscr_as_of(db, dt.date(2026, 10, 2)) == (Decimal("1.25"), "0009900001-26-000102")
    assert dscr_as_of(db, dt.date(2026, 10, 5)) == (Decimal("1.05"), "0000950123-26-000001")


@needs_db
def test_an_amendment_of_an_amendment_on_one_day_ranks_deepest(db):
    load(db, ORIGINAL, dt.date(2026, 9, 29))
    amendment(db, "0009900001-26-000102", dt.date(2026, 9, 29), ORIGINAL)
    third = ONE_LOAN.replace(b">1.45<", b">1.15<")
    amendment(db, "0000950123-26-000001", dt.date(2026, 9, 29), "0009900001-26-000102", third)
    assert dscr_as_of(db, dt.date(2026, 9, 29)) == (Decimal("1.15"), "0000950123-26-000001")


# ---------------------------------------------------------------------------- superseded values


@needs_db
def test_a_loan_the_amendment_omits_is_left_out_with_a_note(db):
    load(db, ORIGINAL, dt.date(2026, 9, 29))
    only_one = tape(asset("1", reportPeriodEndActualBalanceAmount="50000000.00"))
    amendment(db, "0009900001-26-000102", dt.date(2026, 10, 5), ORIGINAL, only_one)
    deal = panel.deal_named(db, FIXTURE)
    assert [r.loan for r in panel.as_of(db, dt.date(2026, 10, 5), deal_id=deal.id)] == ["1"]
    assert panel.superseded(db, dt.date(2026, 10, 5), deal_id=deal.id) == [
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
    assert panel.superseded(db, dt.date(2026, 10, 4), deal_id=deal.id) == []
    assert [r.loan for r in panel.tape_as_of(db, FIXTURE, dt.date(2026, 10, 5)).superseded] == ["7"]


@needs_db
def test_a_loan_the_amendment_quarantines_is_left_out_with_the_reason(db):
    load(db, ORIGINAL, dt.date(2026, 9, 29))
    bad = ONE_LOAN.replace(b">9000000.00<", b">-9000000.00<")
    amendment(db, "0009900001-26-000102", dt.date(2026, 10, 5), ORIGINAL, bad)
    [left_out] = panel.superseded(db, dt.date(2026, 10, 6))
    assert (left_out.loan, left_out.reason) == (
        "7",
        "quarantined in 0009900001-26-000102: ending balance is negative",
    )
    assert [r.loan for r in panel.as_of(db, dt.date(2026, 10, 6))] == ["1"]


@needs_db
def test_the_latest_earlier_version_is_the_one_noted(db):
    load(db, ORIGINAL, dt.date(2026, 9, 29))
    amendment(db, "0009900001-26-000102", dt.date(2026, 10, 1), ORIGINAL)
    only_one = tape(asset("1", reportPeriodEndActualBalanceAmount="50000000.00"))
    amendment(db, "0009900001-26-000103", dt.date(2026, 10, 5), "0009900001-26-000102", only_one)
    [left_out] = panel.superseded(db, dt.date(2026, 10, 5))
    assert (left_out.loan, left_out.accession) == ("7", "0009900001-26-000102")


@needs_db
def test_a_period_whose_latest_tape_is_all_quarantined_shows_no_rows(db):
    load(db, ORIGINAL, dt.date(2026, 9, 29))
    bad = tape(asset("1", reportPeriodEndActualBalanceAmount="-1.00"))
    amendment(db, "0009900001-26-000102", dt.date(2026, 10, 5), ORIGINAL, bad)
    current = panel.tape_as_of(db, FIXTURE, dt.date(2026, 10, 5))
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


@needs_db
def test_a_model_input_carries_the_publishing_filing(real):
    deal_id = real.deal_id(BNK13)
    [version] = panel.as_of(
        real.db, dt.date(2026, 9, 29), deal_id=deal_id, period=SEPTEMBER, loan="11"
    )
    built = model_input(version, "dscr", dt.date(2026, 9, 20))
    assert built == ModelInput(
        deal_id=version.deal_id,
        loan="11",
        period=SEPTEMBER.first_day,
        as_of=dt.date(2026, 9, 20),
        field="dscr",
        value=Decimal("0.0348"),
        accession="0001888524-26-017705",
        published=dt.date(2026, 9, 29),
    )
    assert [r.reason for r in rejections([built])] == ["uses data published after 2026-09-20"]
