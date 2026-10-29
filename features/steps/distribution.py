"""Steps for features/data/distribution_reports.feature.

The real reports are replayed from tests/fixtures/distribution/<accession>/<file name>, the
layout `cmbs edgar download --with-distribution-report` writes. Until they are saved there,
scenarios 1 and 2 fail and say which file they are waiting on. Scenario 3 uses a synthetic
report for the fixture deal; scenarios 4 and 5 reconcile that report with the fixture tape.
"""

import datetime as dt
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import parse
from behave import given, register_type, then, when  # pyright: ignore[reportAttributeAccessIssue]
from sqlalchemy import func, select

from cmbs import clock
from cmbs.absee.schedule_al import NAMESPACE
from cmbs.deals.service import by_name
from cmbs.distribution.layouts import SPECIAL_SERVICING_COMMENTS
from cmbs.distribution.parse import parse_report
from cmbs.distribution.reconcile import flags as reconciliation_flags
from cmbs.distribution.reconcile import reconcile
from cmbs.distribution.service import (
    appraisal_reductions,
    comments_for_loan,
    delinquencies,
    load_report,
    summary_of,
)
from cmbs.distribution.tables import ReportComment
from cmbs.domain import Period, money
from cmbs.panel.service import as_of
from cmbs.tape.service import load_tape
from cmbs.testing.bdd import changed_tables, data_snapshot, in_session
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


# ---------------------------------------------------------------------------- reconciliation
# Scenarios 4 and 5: the fixture report and the fixture tape for September 2026, the tape's
# payment statuses set as each scenario states them. Both are loaded in the Given steps, so the
# When step only reconciles and "neither value is changed" compares exactly that step.

FIXTURE_TAPE = FIXTURES.parent / "schedule_al_minimal.xml"
FIXTURE_TAPE_ACCESSION = "0009900001-26-000010"
# How the scenarios name Schedule AL payment status codes.
TAPE_STATUSES = {
    "current": "0",
    "30 days delinquent": "1",
    "60 days delinquent": "2",
    "90 days or more delinquent": "3",
}
_STATEMENT = re.compile(r'loan "([^"]+)" as (.+?)(?= and loan "|$)')


@parse.with_pattern(r"[A-Z][a-z]+ \d{4}")
def _month(string: str) -> Period:
    return Period.parse(string)


@parse.with_pattern(r'[^"]+')
def _quoted(string: str) -> str:
    return string


register_type(ReconMonth=_month, ReconName=_quoted)


def _listed_delinquent(context, period: Period, deal: str, loans: set[str]) -> None:
    assert deal == FIXTURE_DEAL, f"only {FIXTURE_DEAL} has a synthetic report"
    listed = {item.loan for item in parse_report(FIXTURE_REPORT.path.read_bytes()).delinquencies}
    assert loans <= listed, f"the fixture report lists {sorted(listed)} as delinquent"
    _use(context, FIXTURE_REPORT, period)
    step_parse(context)


@given(
    'the {period:ReconMonth} 10-D for "{deal:ReconName}" lists loans "{first:ReconName}" and '
    '"{second:ReconName}" as delinquent'
)
def step_lists_two_delinquent(context, period, deal, first, second):
    _listed_delinquent(context, period, deal, {first, second})


@given(
    'the {period:ReconMonth} 10-D for "{deal:ReconName}" lists loan "{loan:ReconName}" '
    "as delinquent"
)
def step_lists_one_delinquent(context, period, deal, loan):
    _listed_delinquent(context, period, deal, {loan})


def _with_status(content: bytes, loan: str, code: str) -> bytes:
    root = ET.fromstring(content)
    for asset in root.findall(NAMESPACE + "assets"):
        number = asset.find(NAMESPACE + "assetNumber")
        if number is None or (number.text or "").strip() != loan:
            continue
        status = asset.find(NAMESPACE + "paymentStatusLoanCode")
        if status is None:
            status = ET.SubElement(asset, NAMESPACE + "paymentStatusLoanCode")
        status.text = code
        ET.register_namespace("", NAMESPACE.strip("{}"))
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)
    raise AssertionError(f"the fixture tape has no loan {loan!r}")


@given("the {period:ReconMonth} ABS-EE shows {statements}")
def step_tape_shows(context, period, statements):
    assert period == context.period, f"the report is for {context.period}, not {period}"
    content = FIXTURE_TAPE.read_bytes()
    found = _STATEMENT.findall(statements)
    assert found, f"no 'loan \"...\" as ...' in {statements!r}"
    for loan, status in found:
        assert status in TAPE_STATUSES, f"no payment status code for {status!r}"
        content = _with_status(content, loan, TAPE_STATUSES[status])
    in_session(
        lambda db: load_tape(
            db,
            deal=FIXTURE_DEAL,
            content=content,
            file_name=FIXTURE_TAPE.name,
            accession=FIXTURE_TAPE_ACCESSION,
            filing_date=FIXTURE_REPORT.filing_date,
            cik=FIXTURE_REPORT.cik,
        )
    )
    context.tape_statuses = {loan: TAPE_STATUSES[status] for loan, status in found}


def _fixture_deal_id(db, name: str) -> int:
    deal = by_name(db, name)
    assert deal is not None, f"no deal named {name!r}"
    return deal.id


@when("the report is reconciled with the tape")
def step_reconcile(context):
    clock.set_now(dt.date(2026, 10, 1))
    context.reconciliation = in_session(
        lambda db: reconcile(db, deal_id=_fixture_deal_id(db, context.deal), period=context.period)
    )


@then("the reconciliation passes")
def step_reconciliation_passes(context):
    result = context.reconciliation
    assert result.passed, result.text()
    assert result.tape_accession == FIXTURE_TAPE_ACCESSION, result
    assert result.report_accession == FIXTURE_REPORT.accession, result
    flagged = in_session(
        lambda db: reconciliation_flags(
            db, deal_id=_fixture_deal_id(db, context.deal), period=context.period
        )
    )
    assert not flagged, [flag.message for flag in flagged]


@then("neither the report's value nor the tape's value is changed")
def step_neither_changed(context):
    assert context.snapshot is not None, "no When step ran before this check"
    changed = changed_tables(context.snapshot, data_snapshot())
    assert changed == ["silver.check_flag"], f"stored data changed in {changed}"

    def read(db):
        deal_id = _fixture_deal_id(db, context.deal)
        reported = {
            item.loan: item.status
            for item in delinquencies(db, deal=context.deal, period=context.period)
        }
        tape = {
            row.loan: row.payment_status
            for row in as_of(db, dt.date.max, deal_id=deal_id, period=context.period)
        }
        return reported, tape

    reported, tape = in_session(read)
    for loan, code in context.tape_statuses.items():
        assert tape[loan] == code, f"loan {loan} on the tape: {tape[loan]!r}"
        assert loan in reported, f"loan {loan} is no longer listed by the report"
