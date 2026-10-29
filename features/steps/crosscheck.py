"""Steps for features/surveillance/text_vs_tape_checks.feature.

Each month of "Fixture Trust 2026-F1" is made from tests/fixtures/schedule_al_minimal.xml (its
September 2026 tape) with the reporting period end and paid-through dates moved to the month,
and a 10-D made from tests/fixtures/alerts/fixture_10d_template.htm holding the comments the
scenario states. Given steps change one loan's tags or comment before anything is loaded; the
first When step loads every month (tape and 10-D filed on the 29th), extracts the comments the
morning after, and runs the checks that afternoon, with the clock pinned.

Model answers come from `ScriptedProvider`, which never touches the network: for each comment,
the figure a scenario states is answered with probability 0.95, supported at 0.95 by the
sentence that prints it, so the decision rules auto-accept it; every other field is not stated.
"""

import datetime as dt
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html import escape
from pathlib import Path

import parse
from behave import given, register_type, then, when  # pyright: ignore[reportAttributeAccessIssue]

from cmbs import clock
from cmbs.absee.schedule_al import NAMESPACE
from cmbs.checks.service import flags_for
from cmbs.crosscheck import rules
from cmbs.crosscheck.service import check_text_vs_tape
from cmbs.deals.service import by_name
from cmbs.distribution.service import load_report
from cmbs.domain import Period
from cmbs.extraction.questions import sentence_name, support_name, value_name
from cmbs.extraction.sentences import split
from cmbs.extraction.service import extract_comments
from cmbs.model.client import ModelClient
from cmbs.model.recordings import DbRecordings
from cmbs.model.scripted import ScriptedProvider, choice_answer, respond
from cmbs.model.types import Answer, Choice, ModelRequest, NoulAnswer
from cmbs.panel import service as panel
from cmbs.tape.service import load_tape
from cmbs.testing.bdd import changed_tables, data_snapshot, in_session
from cmbs.testing.world import FIXTURE_CIK, FIXTURE_DEAL

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_TAPE = ROOT / "tests" / "fixtures" / "schedule_al_minimal.xml"
REPORT_TEMPLATE = ROOT / "tests" / "fixtures" / "alerts" / "fixture_10d_template.htm"
TAPE_PERIOD = Period(2026, 9)
SEPTEMBER = Period(2026, 9)
URI = NAMESPACE.strip("{}")
LOADER = "quinn"  # a quant loads filings, extracts and runs the checks
SETTLED = 0.95
TRANSFER_TAG = "mostRecentSpecialServicerTransferDate"


@parse.with_pattern(r"[A-Z][a-z]+ \d{4}")
def _month(string: str) -> Period:
    return Period.parse(string)


@parse.with_pattern(r"\d{4}-\d{2}-\d{2}")
def _day(string: str) -> dt.date:
    return dt.date.fromisoformat(string)


@parse.with_pattern(r"[^\"]+")
def _loan(string: str) -> str:
    return string


@parse.with_pattern(r"[A-Z][a-z]+")
def _month_name(string: str) -> str:
    return string


register_type(XcMonth=_month, XcMonthName=_month_name, XcDay=_day, XcLoan=_loan)


@dataclass
class Month:
    period: Period
    tape: bytes
    comments: dict[str, str] = field(default_factory=dict)
    # Per loan, the figures the model picks out of its comment: field name -> printed text.
    figures: dict[str, dict[str, str]] = field(default_factory=dict)

    @property
    def filed(self) -> dt.date:
        return dt.date(self.period.year, self.period.month, 29)

    def accession(self, form: int) -> str:
        return f"{FIXTURE_CIK:010d}-{self.period.year % 100:02d}-{self.period.month:04d}{form:02d}"


def _us(day: dt.date) -> str:
    """2025-03-06 -> '3/6/2025', as comments print dates."""
    return f"{day.month}/{day.day}/{day.year}"


def _tape_date(day: dt.date) -> str:
    return day.strftime("%m-%d-%Y")


def _bytes(root: ET.Element) -> bytes:
    ET.register_namespace("", URI)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _moved(content: bytes, months: int) -> bytes:
    """The tape with its reporting period end and paid-through dates moved by `months`."""
    root = ET.fromstring(content)
    for asset in root.findall(NAMESPACE + "assets"):
        for tag in ("reportingPeriodEndDate", "paidThroughDate"):
            element = asset.find(NAMESPACE + tag)
            text = (element.text or "").strip() if element is not None else ""
            if element is None or not text:
                continue
            reported = dt.datetime.strptime(text, "%m-%d-%Y").date()
            moved = Period.of(reported).shift(months)
            element.text = _tape_date(dt.date(moved.year, moved.month, reported.day))
    return _bytes(root)


def _with_tag(content: bytes, loan: str, tag: str, value: str | None, *, on_property=False):
    root = ET.fromstring(content)
    for asset in root.findall(NAMESPACE + "assets"):
        number = asset.find(NAMESPACE + "assetNumber")
        if number is not None and (number.text or "").strip() == loan:
            break
    else:
        raise AssertionError(f"the fixture tape has no loan {loan!r}")
    parent = asset
    if on_property:
        found = asset.find(NAMESPACE + "property")
        parent = found if found is not None else ET.SubElement(asset, NAMESPACE + "property")
    element = parent.find(NAMESPACE + tag)
    if value is None:
        if element is not None:
            parent.remove(element)
    else:
        if element is None:
            element = ET.SubElement(parent, NAMESPACE + tag)
        element.text = value
    return _bytes(root)


def month(context, period: Period) -> Month:
    months = getattr(context, "xc_months", None)
    if months is None:
        months = context.xc_months = {}
    if period not in months:
        tape = _moved(FIXTURE_TAPE.read_bytes(), TAPE_PERIOD.months_until(period))
        months[period] = Month(period, tape)
    context.deal = FIXTURE_DEAL
    return months[period]


def set_tag(context, period: Period, loan: str, tag: str, value: str | None, **kw) -> None:
    target = month(context, period)
    target.tape = _with_tag(target.tape, loan, tag, value, **kw)


def say(context, period: Period, loan: str, comment: str, figures: dict[str, str]) -> None:
    target = month(context, period)
    target.comments[loan] = comment
    target.figures[loan] = figures
    context.loan = loan
    context.period = period


def _report(target: Month) -> bytes:
    rows = "\n".join(
        f"    <tr><td>{loan}</td><td>30000000{loan}</td><td>{escape(text)}</td></tr>"
        for loan, text in sorted(target.comments.items())
    )
    distribution = dt.date(target.period.year, target.period.month, 17)
    determination = dt.date(target.period.year, target.period.month, 11)
    return (
        REPORT_TEMPLATE.read_text()
        .format(
            deal=escape(FIXTURE_DEAL),
            distribution_date=distribution.strftime("%m/%d/%y"),
            determination_date=determination.strftime("%m/%d/%y"),
            rows=rows,
        )
        .encode()
    )


def _answers(request: ModelRequest, figures: dict[str, str], comment: str) -> dict[str, Answer]:
    sentences = split(comment)
    answers: dict[str, Answer] = {}
    for name, printed in figures.items():
        value = request.questions[value_name(name)]
        cited = request.questions[sentence_name(name)]
        assert isinstance(value, Choice) and isinstance(cited, Choice)
        sentence = next(s.text for s in sentences if printed in s.text)
        answers[value_name(name)] = choice_answer(value, printed, SETTLED)
        answers[support_name(name)] = NoulAnswer(noul=SETTLED)
        answers[sentence_name(name)] = choice_answer(cited, sentence, SETTLED)
    return answers


def _provider(target: Month) -> ScriptedProvider:
    by_comment = {
        " ".join(text.split()): (target.figures.get(loan, {}), text)
        for loan, text in target.comments.items()
    }

    def answer(request: ModelRequest):
        figures, comment = by_comment[request.state["comment"]]
        return respond(request, _answers(request, figures, comment))

    return ScriptedProvider(answer)


def _load(target: Month) -> None:
    name = f"fixture-trust-2026-f1_{target.period.first_day:%Y-%m}"
    clock.set_now(dt.datetime.combine(target.filed, dt.time(18), dt.UTC))
    in_session(
        lambda db: load_tape(
            db,
            deal=FIXTURE_DEAL,
            content=target.tape,
            file_name=f"{name}.xml",
            accession=target.accession(1),
            filing_date=target.filed,
            cik=FIXTURE_CIK,
        )
    )
    if not target.comments:
        return
    in_session(
        lambda db: load_report(
            db,
            user=LOADER,
            deal=FIXTURE_DEAL,
            accession=target.accession(2),
            cik=FIXTURE_CIK,
            filing_date=target.filed,
            file_name=f"{name}.htm",
            content=_report(target),
            period=target.period,
        )
    )
    clock.set_now(dt.datetime.combine(target.filed + dt.timedelta(days=1), dt.time(9), dt.UTC))
    provider = _provider(target)

    def extract(db):
        client = ModelClient(
            DbRecordings(db), provider=provider, replay_only=False, allow_synthetic=True
        )
        return extract_comments(
            db, user=LOADER, deal=FIXTURE_DEAL, period=target.period, client=client
        )

    in_session(extract)


def _tape_row(context, period: Period, loan: str):
    rows = in_session(
        lambda db: panel.as_of(
            db, clock.today(), deal_id=panel.deal_named(db, FIXTURE_DEAL).id, period=period
        )
    )
    found = [row for row in rows if row.loan == loan]
    assert len(found) == 1, f"loan {loan} rows on the {period} tape: {found}"
    row = found[0]
    return (row.dscr, row.financials_end, row.financials_end_newest, row.accession)


def _flags(loan: str, checks: frozenset[str] | None = None):
    def read(db):
        deal = by_name(db, FIXTURE_DEAL)
        assert deal is not None
        return [
            flag
            for flag in flags_for(db, deal_id=deal.id, loan=loan)
            if checks is None or flag.check in checks
        ]

    return in_session(read)


# Given: what the comment says.


TRANSFER_COMMENT = (
    "The loan transferred to Special Servicing effective {date} due to imminent monetary "
    "default. The special servicer is evaluating the borrower's request."
)


@given('loan "{loan:XcLoan}"\'s comment says it transferred to special servicing on {day:XcDay}')
def step_comment_transfer(context, loan, day):
    _comment_transfer(context, SEPTEMBER, loan, day)


@given(
    'loan "{loan:XcLoan}"\'s {period:XcMonth} comment says it transferred to special '
    "servicing on {day:XcDay}"
)
def step_month_comment_transfer(context, loan, period, day):
    _comment_transfer(context, period, loan, day)


def _comment_transfer(context, period: Period, loan: str, day: dt.date) -> None:
    printed = _us(day)
    say(context, period, loan, TRANSFER_COMMENT.format(date=printed), {"transfer_date": printed})
    context.text_transfer = day


@given('loan "{loan:XcLoan}"\'s {period:XcMonth} comment quotes DSCR {ratio}x as of {day:XcDay}')
def step_comment_dscr(context, loan, period, ratio, day):
    printed = _us(day)
    comment = f"The loan remains with the special servicer. As of {printed}, DSCR NOI was {ratio}x."
    say(context, period, loan, comment, {"dscr": f"{ratio}x", "dscr_as_of": printed})


# Given: what the tape says.


@given('the tape shows a special-servicer transfer date of {day:XcDay} for loan "{loan:XcLoan}"')
def step_tape_transfer(context, day, loan):
    set_tag(context, context.period, loan, TRANSFER_TAG, _tape_date(day))
    context.tape_transfer = day


@given(
    "the {first:XcMonthName} and {second:XcMonth} tapes show no special-servicer transfer for "
    'loan "{loan:XcLoan}"'
)
def step_tapes_no_transfer(context, first, second, loan):
    """'the August and September 2026 tapes': both months in the second one's year."""
    for period in (Period.parse(f"{first} {second.year}"), second):
        set_tag(context, period, loan, TRANSFER_TAG, None)


@given(
    'the {period:XcMonth} tape\'s most recent DSCR for loan "{loan:XcLoan}" is as of {day:XcDay}'
)
def step_tape_dscr_as_of(context, period, loan, day):
    # Loan 2's comment in the fixture 10-D gives the DSCR the tape would carry: 0.71x.
    set_tag(context, period, loan, "mostRecentFinancialsStartDate", "01-01-2024", on_property=True)
    set_tag(context, period, loan, "mostRecentFinancialsEndDate", _tape_date(day), on_property=True)
    set_tag(
        context,
        period,
        loan,
        "mostRecentDebtServiceCoverageNetOperatingIncomePercentage",
        "0.71",
        on_property=True,
    )
    context.tape_financials_end = day


# When.


@when("the cross-checks run")
def step_cross_checks_run(context):
    for target in sorted(context.xc_months.values(), key=lambda m: m.period):
        _load(target)
    latest = max(context.xc_months)
    clock.set_now(dt.datetime.combine(latest.shift(1).first_day, dt.time(15), dt.UTC))
    context.tape_before = _tape_row(context, context.period, context.loan)
    context.snapshot = data_snapshot()  # after loading: the checks alone must leave tapes alone
    context.summaries = [
        in_session(
            lambda db, period=period: check_text_vs_tape(
                db, user=LOADER, deal=FIXTURE_DEAL, period=period
            )
        )
        for period, target in sorted(context.xc_months.items())
        if target.comments
    ]


# Then.


def _transfer_finding(context, loan: str) -> rules.Finding:
    findings = [
        finding
        for summary in context.summaries
        for result in summary.text_vs_tape
        if result.loan == loan
        for finding in result.findings
        if finding.check in rules.TRANSFER_CHECKS
    ]
    assert len(findings) == 1, f"transfer findings for loan {loan}: {findings}"
    return findings[0]


@then('no transfer flag is raised for loan "{loan:XcLoan}"')
def step_no_transfer_flag(context, loan):
    assert _transfer_finding(context, loan).status is rules.Status.AGREE
    flags = _flags(loan, rules.TRANSFER_CHECKS)
    assert flags == [], [(flag.check, flag.message) for flag in flags]


def _one_flag(loan: str, check: str):
    flags = _flags(loan, frozenset({check}))
    assert len(flags) == 1, [(flag.check, flag.message) for flag in flags]
    return flags[0]


@then("the flag shows both dates")
def step_flag_shows_both_dates(context):
    flag = _one_flag(context.loan, rules.TRANSFER_DATE)
    sides = flag.sides
    assert sides["text"]["transfer_date"] == context.text_transfer.isoformat(), sides
    assert sides["tape"]["transfer_date"] == context.tape_transfer.isoformat(), sides
    assert sides["periods_apart"] == rules.periods_apart(
        context.text_transfer, context.tape_transfer
    )


@then("the flag shows both sources' values and as-of dates")
def step_flag_shows_values_and_dates(context):
    flag = _one_flag(context.loan, rules.TRANSFER_MISSING)
    target = month(context, context.period)
    assert flag.sides["text"] == {
        "source": "10-D comment",
        "transfer_date": context.text_transfer.isoformat(),
        "sentence": 1,
        "accession": target.accession(2),
        "filed": target.filed.isoformat(),
        "as_of": dt.date(context.period.year, context.period.month, 17).isoformat(),
    }, flag.sides
    assert flag.sides["tape"] == {
        "source": "loan tape",
        "transfer_date": "not reported",
        "accession": target.accession(1),
        "filed": target.filed.isoformat(),
        "as_of": dt.date(context.period.year, context.period.month, 11).isoformat(),
    }, flag.sides


@then('loan "{loan:XcLoan}" gets the note "{note}"')
def step_gets_note(context, loan, note):
    flag = _one_flag(loan, rules.NEWER_FINANCIALS)
    assert flag.message == note, flag.message
    assert flag.check in rules.NOTE_CHECKS
    assert flag.sides["text"]["dscr_as_of"] > flag.sides["tape"]["financials_end"], flag.sides


@then('the tape\'s DSCR for loan "{loan:XcLoan}" is unchanged')
def step_tape_dscr_unchanged(context, loan):
    after = _tape_row(context, context.period, loan)
    assert after == context.tape_before, (after, context.tape_before)
    dscr, financials_end, _, _ = after
    assert (str(dscr), financials_end) == ("0.71", context.tape_financials_end), after
    changed = changed_tables(context.snapshot, data_snapshot())
    assert not [name for name in changed if name.startswith(("raw.", "bronze."))], changed
    assert "silver.loan_period" not in changed, changed
