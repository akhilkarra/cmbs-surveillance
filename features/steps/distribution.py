"""Steps for features/data/distribution_reports.feature: scenarios 1 to 3.

The real reports are replayed from tests/fixtures/distribution/<accession>/<file name>, the
layout `cmbs edgar download --with-distribution-report` writes. Until they are saved there,
scenarios 1 and 2 fail and say which file they are waiting on. Scenario 3 uses a synthetic
report for the fixture deal.
"""

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from behave import given, then, when  # pyright: ignore[reportAttributeAccessIssue]
from sqlalchemy import func, select

from cmbs.deals.service import by_name
from cmbs.distribution.layouts import SPECIAL_SERVICING_COMMENTS
from cmbs.distribution.parse import parse_report
from cmbs.distribution.service import (
    appraisal_reductions,
    comments_for_loan,
    load_report,
    summary_of,
)
from cmbs.distribution.tables import ReportComment
from cmbs.domain import Period, money
from cmbs.testing.bdd import in_session
from cmbs.testing.world import FIXTURE_DEAL

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "distribution"
LOADER = "quinn"  # a quant loads reports


@dataclass(frozen=True)
class ReportFiling:
    deal: str
    accession: str
    cik: int
    filing_date: dt.date
    path: Path


# Filing facts from EDGAR's submissions JSON (data/samples/_submissions/).
REAL_REPORTS = {
    ("BANK 2018-BNK13", "0001888524-26-017704"): ReportFiling(
        "BANK 2018-BNK13",
        "0001888524-26-017704",
        1744707,
        dt.date(2026, 9, 29),
        FIXTURES / "0001888524-26-017704" / "wcm18b13_ex991-202609.htm",
    ),
}
CITI_REPORT = ReportFiling(
    "CGCMT 2016-C1",
    "0001628297-26-000742",
    1673255,
    dt.date(2026, 9, 25),
    FIXTURES / "0001628297-26-000742" / "ex99_1.htm",
)
# The fixture deal has no CIK; this one is far above any EDGAR has assigned.
FIXTURE_REPORT = ReportFiling(
    FIXTURE_DEAL,
    "0009900001-26-000009",
    9900001,
    dt.date(2026, 9, 29),
    FIXTURES / "fixture-trust-2026-f1_10d_2026-09.htm",
)


def _use(context, filing: ReportFiling, period: Period | None) -> None:
    assert filing.path.is_file(), (
        f"waiting on the real filing: save {filing.path.name} from accession "
        f"{filing.accession} as {filing.path.relative_to(FIXTURES.parents[2])}"
    )
    context.deal = filing.deal
    context.filing = filing
    context.period = period


@given('the {period} 10-D for "{deal}", accession {accession}')
def step_real_report(context, period, deal, accession):
    filing = REAL_REPORTS.get((deal, accession))
    assert filing is not None, f"no recorded report for {deal} {accession}"
    _use(context, filing, Period.parse(period))


@given('a 10-D for "{deal}" in the Citi administrator\'s format')
def step_citi_report(context, deal):
    assert deal == CITI_REPORT.deal, f"no recorded Citi-format report for {deal}"
    _use(context, CITI_REPORT, None)


@given('the {period} 10-D for "{deal}" lists an appraisal reduction of ${amount} for loan "{loan}"')
def step_fixture_appraisal(context, period, deal, amount, loan):
    assert deal == FIXTURE_DEAL, f"only {FIXTURE_DEAL} has a synthetic report"
    listed = parse_report(FIXTURE_REPORT.path.read_bytes()).appraisal_reductions
    assert any(item.loan == loan and item.amount == money(amount) for item in listed), (
        f"the fixture report does not list ${amount} for loan {loan}: {listed}"
    )
    _use(context, FIXTURE_REPORT, Period.parse(period))


@when("the distribution report is parsed")
def step_parse(context):
    filing: ReportFiling = context.filing
    context.loaded = in_session(
        lambda db: load_report(
            db,
            user=LOADER,
            deal=filing.deal,
            accession=filing.accession,
            cik=filing.cik,
            filing_date=filing.filing_date,
            file_name=filing.path.name,
            content=filing.path.read_bytes(),
            period=context.period,
        )
    )


@then("the {name} loan, asset {loan}, has a special-servicing comment")
def step_has_comment(context, name, loan):
    comments = in_session(lambda db: comments_for_loan(db, deal=context.deal, loan=loan))
    mine = [item for item in comments if item.accession == context.filing.accession]
    assert len(mine) == 1, f"{name} (loan {loan}) comments: {comments}"
    context.comment = mine[0]


@then('the comment contains "{text}"')
def step_comment_contains(context, text):
    assert text in context.comment.comment, context.comment.comment


@then("the comment records which table of the report it was taken from")
def step_comment_source(context):
    assert context.comment.source_table == SPECIAL_SERVICING_COMMENTS.name, context.comment


@then("no special-servicing comments are stored")
def step_no_comments(context):
    def count(db):
        deal = by_name(db, context.deal)
        assert deal is not None
        query = select(func.count()).where(ReportComment.deal_id == deal.id)
        return db.scalar(query.select_from(ReportComment))

    assert in_session(count) == 0


@then('the parse summary says "{text}"')
def step_summary_says(context, text):
    summary = in_session(lambda db: summary_of(db, context.filing.accession))
    assert summary is not None, "the report was not stored"
    assert text in summary, summary


@then('loan "{loan}" has an appraisal reduction of ${amount} for {period}')
def step_has_appraisal(context, loan, amount, period):
    found = in_session(
        lambda db: appraisal_reductions(
            db, deal=context.deal, loan=loan, period=Period.parse(period)
        )
    )
    assert found, f"no appraisal reduction stored for loan {loan}"
    assert {item.amount for item in found} == {money(amount)}, found
