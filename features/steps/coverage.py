"""Steps for features/governance/coverage_report.feature.

The book is loaded as a positions file dated 2026-09-30 (features/README.md). Each run of the
report first runs filing discovery for every deal in the book, replayed from
tests/fixtures/edgar, then reads the stored result, as a refresh followed by a report would.
"""

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Any

from behave import given, then, when  # pyright: ignore[reportAttributeAccessIssue]

from cmbs import clock
from cmbs.book.service import PositionLine, book_as_of, load_positions
from cmbs.coverage.service import configure_vendor_source, render, report
from cmbs.domain import Period, money
from cmbs.edgar import submissions
from cmbs.edgar.discovery import discover
from cmbs.edgar.replay import Replay, Source
from cmbs.edgar.service import discover_deal
from cmbs.testing.bdd import in_session

RECORDINGS = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "edgar"
BOOK_DATE = dt.date(2026, 9, 30)
OPERATOR, QUANT, PM = "olly", "quinn", "pat"
VENDOR_SOURCE = "fixture vendor loan tape"


class FiledThrough:
    """A source that hides one entity's filings made after a cutoff date."""

    def __init__(self, upstream: Source, cik: int, cutoff: dt.date) -> None:
        self.upstream, self.cik, self.cutoff = upstream, cik, cutoff

    def get_json(self, url: str) -> Any:
        data = self.upstream.get_json(url)
        if url != submissions.submissions_url(self.cik):
            return data
        recent = data["filings"]["recent"]
        keep = [
            i
            for i, filed in enumerate(recent["filingDate"])
            if dt.date.fromisoformat(filed) <= self.cutoff
        ]
        trimmed = {column: [values[i] for i in keep] for column, values in recent.items()}
        return {**data, "filings": {**data["filings"], "recent": trimmed}}


def _source(context) -> Source:
    if getattr(context, "edgar", None) is None:
        context.edgar = Replay(RECORDINGS)
    return context.edgar


@given("the book holds:")
def step_book_holds(context):
    lines = [
        PositionLine(row["deal"], row["class"], money(row["face amount"])) for row in context.table
    ]
    in_session(lambda db: load_positions(db, user=OPERATOR, as_of=BOOK_DATE, lines=lines))
    _source(context)


def _run_report(context, on: dt.date) -> None:
    source = _source(context)

    def run(db):
        book = book_as_of(db, on)
        assert book is not None, f"no positions file on or before {on}"
        for deal in sorted({held.deal for held in book.positions}):
            discover_deal(db, source, deal, on, user=QUANT)
        return report(db, user=PM, on=on)

    context.coverage = in_session(run)
    context.report = render(context.coverage)


@when("the coverage report runs on {day}")
def step_report_runs_on(context, day):
    _run_report(context, dt.date.fromisoformat(day))


@when("the coverage report runs")
def step_report_runs(context):
    _run_report(context, clock.today())


@then("the deals are classed:")
def step_deals_classed(context):
    expected = {(row["deal"], row["coverage"], row["detail"]) for row in context.table}
    actual = {(row.deal, row.coverage.value, row.detail) for row in context.coverage.deals}
    assert actual == expected, f"classed:\n{sorted(actual)}\n\nreport:\n{context.report}"
    for deal, coverage, detail in expected:
        line = next(text for text in context.report.splitlines() if text.startswith(deal))
        assert coverage in line and detail in line, line


@then("exposure by coverage class is:")
def step_exposure_by_class(context):
    expected = {
        (row["coverage"], money(row["exposure"]), row["share of book"]) for row in context.table
    }
    actual = {
        (row.coverage.value, row.exposure, f"{row.share}%") for row in context.coverage.by_class
    }
    assert actual == expected, f"by class: {sorted(actual)}\n\nreport:\n{context.report}"
    assert sum((row[1] for row in expected), Decimal(0)) == context.coverage.total


@given('"{deal}" has filed every month through {period}')
def step_filed_every_month_through(context, deal, period):
    context.deal = deal
    through = Period.parse(period)
    source = _source(context)
    found = discover(source, deal, dt.date.max)
    reports = {f.period for f in found.of_form("10-D") if f.period is not None}
    first = min(reports)
    missing = [
        first.shift(n).label
        for n in range(first.months_until(through) + 1)
        if first.shift(n) not in reports
    ]
    assert not missing, f"{deal} has no 10-D for {missing}"
    cutoff = max(
        f.filing_date for f in found.filings if f.period is not None and f.period <= through
    )
    assert found.match.cik is not None, f"{deal} has no issuing entity"
    context.edgar = FiledThrough(source, found.match.cik, cutoff)


@given("no {period} filing has arrived by {day}")
def step_no_filing_by(context, period, day):
    found = discover(_source(context), context.deal, dt.date.fromisoformat(day))
    late = [f.accession for f in found.filings if f.period == Period.parse(period)]
    assert not late, f"{period} filings arrived: {late}"


def _row(context, deal):
    rows = [row for row in context.coverage.deals if row.deal == deal]
    assert rows, f"{deal} is not in the report:\n{context.report}"
    return rows[0]


@then('"{deal}" is marked "{mark}" with "{detail}"')
def step_deal_marked_with(context, deal, mark, detail):
    row = _row(context, deal)
    assert mark == "latest filing overdue", f"no such mark: {mark!r}"
    assert row.overdue_expected_by is not None, f"{deal} is not overdue: {row}"
    assert detail == f"expected by about {row.overdue_expected_by.isoformat()}", row
    assert f"{deal}: {mark}, {detail}" in context.report, context.report


@given('a vendor loan tape source is configured for "{deal}"')
def step_vendor_source(context, deal):
    context.deal = deal
    context.vendor_source = VENDOR_SOURCE
    in_session(
        lambda db: configure_vendor_source(db, user=OPERATOR, deal=deal, source=VENDOR_SOURCE)
    )


@then('"{deal}" is classed "{coverage}"')
def step_deal_classed(context, deal, coverage):
    row = _row(context, deal)
    assert row.coverage.value == coverage, f"{deal} is {row.coverage.value}: {row.detail}"


@then("the report names the vendor source")
def step_report_names_vendor(context):
    row = _row(context, context.deal)
    assert row.vendor_sources == (context.vendor_source,), row
    assert f"{context.deal}: {context.vendor_source}" in context.report, context.report
