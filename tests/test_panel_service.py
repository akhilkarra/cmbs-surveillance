"""Point-in-time reads: a value is visible only from its filing date, amendments add versions."""

import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest
from pandera.errors import SchemaErrors
from sqlalchemy.orm import Session

from cmbs.domain import Period
from cmbs.panel import service as panel
from cmbs.panel.contract import ModelInput, Rejection, model_input, rejections
from cmbs.panel.service import NothingPublished, UnknownDeal, loan_order
from cmbs.tape.service import load_tape
from tests.conftest import needs_db
from tests.fixtures.tape.tapes import fixture_bytes, real_bytes

FIXTURE = "Fixture Trust 2026-F1"
BNK13 = "BANK 2018-BNK13"
SEPTEMBER = Period(2026, 9)
AUGUST = Period(2026, 8)


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


def load_fixture(db: Session, accession: str, filed: dt.date, content: bytes | None = None, **kw):
    return load_tape(
        db,
        deal=FIXTURE,
        content=fixture_bytes() if content is None else content,
        file_name="ex102.xml",
        accession=accession,
        filing_date=filed,
        cik=9_900_001,
        **kw,
    )


def load_bnk13(db: Session) -> None:
    for name, accession, filed in [
        ("bank-2018-bnk13_2026-08_ex102.xml", "0001888524-26-015516", dt.date(2026, 8, 27)),
        ("bank-2018-bnk13_2026-09_ex102.xml", "0001888524-26-017705", dt.date(2026, 9, 29)),
    ]:
        load_tape(
            db,
            deal=BNK13,
            content=real_bytes(name),
            file_name=name,
            accession=accession,
            filing_date=filed,
            cik=1744707,
        )


def with_amendment(db: Session) -> None:
    load_fixture(db, "0009900001-26-000101", dt.date(2026, 9, 29))
    load_fixture(
        db,
        "0009900001-26-000102",
        dt.date(2026, 10, 5),
        fixture_bytes().replace(b">1.45<", b">1.25<"),
        form="ABS-EE/A",
        amends="0009900001-26-000101",
    )


@needs_db
def test_a_tape_is_invisible_before_its_filing_date(db):
    load_bnk13(db)
    deal = panel.deal_named(db, BNK13)
    rows = panel.as_of(db, dt.date(2026, 9, 28), deal_id=deal.id)
    assert panel.periods(rows) == [AUGUST]
    assert [row.loan for row in rows] == ["11"]
    assert panel.as_of(db, dt.date(2026, 8, 26), deal_id=deal.id) == []


@needs_db
def test_a_tape_is_visible_from_its_filing_date(db):
    load_bnk13(db)
    deal = panel.deal_named(db, BNK13)
    rows = panel.as_of(db, dt.date(2026, 9, 29), deal_id=deal.id)
    assert panel.periods(rows) == [AUGUST, SEPTEMBER]
    september = [row.loan for row in rows if row.period == SEPTEMBER.first_day]
    assert len(september) == 42
    assert september[:3] == ["1", "2", "3"] and september[-1] == "56"


@needs_db
def test_as_of_filters_by_period_and_loan(db):
    load_bnk13(db)
    deal = panel.deal_named(db, BNK13)
    day = dt.date(2026, 10, 1)
    rows = panel.as_of(db, day, deal_id=deal.id, period=AUGUST)
    assert [(row.loan, row.period) for row in rows] == [("11", AUGUST.first_day)]
    rows = panel.as_of(db, day, deal_id=deal.id, loan="11")
    assert [row.period for row in rows] == [AUGUST.first_day, SEPTEMBER.first_day]
    assert rows[1].workout_strategy == "2" and rows[0].workout_strategy == "13"


@needs_db
def test_as_of_without_a_deal_covers_every_deal_in_order(db):
    load_bnk13(db)
    load_fixture(db, "0009900001-26-000101", dt.date(2026, 9, 29))
    rows = panel.as_of(db, dt.date(2026, 9, 29), period=SEPTEMBER)
    deals = [row.deal_id for row in rows]
    assert len(rows) == 42 + 7
    assert deals == sorted(deals)


@needs_db
def test_before_an_amendment_the_original_is_shown(db):
    with_amendment(db)
    [row] = panel.as_of(db, dt.date(2026, 10, 4), loan="1")
    assert (row.dscr, row.filing_date) == (Decimal("1.45"), dt.date(2026, 9, 29))


@needs_db
def test_from_an_amendments_filing_date_the_correction_is_shown(db):
    with_amendment(db)
    [row] = panel.as_of(db, dt.date(2026, 10, 5), loan="1")
    assert (row.dscr, row.accession) == (Decimal("1.25"), "0009900001-26-000102")


@needs_db
def test_every_version_stays_stored_with_its_filing_date(db):
    with_amendment(db)
    deal = panel.deal_named(db, FIXTURE)
    stored = panel.versions(db, deal_id=deal.id, loan="1", period=SEPTEMBER)
    assert [(v.dscr, v.filing_date) for v in stored] == [
        (Decimal("1.45"), dt.date(2026, 9, 29)),
        (Decimal("1.25"), dt.date(2026, 10, 5)),
    ]


@needs_db
def test_two_filings_on_one_day_resolve_to_the_later_accession(db):
    load_fixture(db, "0009900001-26-000101", dt.date(2026, 9, 29))
    load_fixture(
        db,
        "0009900001-26-000102",
        dt.date(2026, 9, 29),
        fixture_bytes().replace(b">1.45<", b">1.25<"),
        form="ABS-EE/A",
        amends="0009900001-26-000101",
    )
    [row] = panel.as_of(db, dt.date(2026, 9, 29), loan="1")
    assert row.dscr == Decimal("1.25")


@needs_db
def test_tape_as_of_gives_the_latest_period_visible(db):
    load_bnk13(db)
    tape = panel.tape_as_of(db, BNK13, dt.date(2026, 9, 20))
    assert (tape.deal, tape.as_of, tape.period) == (BNK13, dt.date(2026, 9, 20), AUGUST)
    assert [row.loan for row in tape.rows] == ["11"]
    tape = panel.tape_as_of(db, BNK13, dt.date(2026, 10, 1), AUGUST)
    assert tape.period == AUGUST


@needs_db
def test_tape_as_of_refuses_unknown_deals_and_unpublished_periods(db):
    load_bnk13(db)
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


@needs_db
def test_staleness_reads_the_panel(db):
    load_fixture(db, "0009900001-26-000101", dt.date(2026, 9, 29))
    tape, report = panel.staleness(db, FIXTURE, dt.date(2026, 9, 29))
    assert tape.period == SEPTEMBER
    assert report.loan("1").days_vs_filing == 91
    assert (report.summary.median_days, report.summary.p90_days) == (164, 619)


@needs_db
def test_staleness_of_the_real_bnk13_tape_matches_the_research(db):
    load_bnk13(db)
    _, report = panel.staleness(db, BNK13, dt.date(2026, 9, 29))
    summary = report.summary
    assert (summary.active_loans, summary.loans_with_financials) == (34, 34)
    assert (summary.median_days, summary.p90_days, summary.median_days_vs_filing) == (73, 164, 91)


@needs_db
def test_staleness_of_a_new_issue(db):
    load_tape(
        db,
        deal="BANK5 2026-5YR23",
        content=real_bytes("bank5-2026-5yr23_2026-09_ex102.xml"),
        file_name="ex102.xml",
        accession="0001888524-26-018152",
        filing_date=dt.date(2026, 10, 1),
        cik=2140978,
    )
    _, report = panel.staleness(db, "BANK5 2026-5YR23", dt.date(2026, 10, 1))
    assert (report.summary.active_loans, report.summary.loans_with_financials) == (33, 0)
    assert report.summary.note == "only at-securitization financials so far"


# ---------------------------------------------------------------------------- as_of contract


def entry(as_of: dt.date, published: dt.date, loan: str = "1") -> ModelInput:
    return ModelInput(
        deal_id=1,
        loan=loan,
        period=SEPTEMBER.first_day,
        as_of=as_of,
        field="dscr",
        value=Decimal("1.45"),
        accession="0009900001-26-000101",
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
def test_a_model_input_carries_the_publishing_filing(db):
    load_fixture(db, "0009900001-26-000101", dt.date(2026, 9, 29))
    [version] = panel.as_of(db, dt.date(2026, 9, 29), loan="1")
    built = model_input(version, "dscr", dt.date(2026, 9, 20))
    assert built == ModelInput(
        deal_id=version.deal_id,
        loan="1",
        period=SEPTEMBER.first_day,
        as_of=dt.date(2026, 9, 20),
        field="dscr",
        value=Decimal("1.45"),
        accession="0009900001-26-000101",
        published=dt.date(2026, 9, 29),
    )
    assert [r.reason for r in rejections([built])] == ["uses data published after 2026-09-20"]
