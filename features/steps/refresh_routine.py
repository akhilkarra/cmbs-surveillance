"""Steps for features/surfaces/refresh_routine.feature.

The routine is `cmbs refresh deal`: the refresh service run with a model to extract comments,
and its report (`cmbs.refresh.report`). EDGAR and the model are replayed from the synthetic
recordings (`tests/test_routine_world.py`). Months loaded in Given steps are loaded by a backfill
on 2026-10-01; the refresh itself runs that day too, before the October 2026 filings (filed on
the 29th) exist.

A scenario that names no earlier month ("the September 2026 tape has ...") starts with the deal
loaded through August 2026, so the refresh has September to load.
"""

import re

import parse
from behave import given, register_type, then, when  # pyright: ignore[reportAttributeAccessIssue]
from sqlalchemy import func, select

from cmbs import clock
from cmbs.deals.service import by_name
from cmbs.distribution.tables import DistributionReport
from cmbs.domain import Period
from cmbs.edgar.service import filings_of
from cmbs.extraction.tables import Extraction, ReviewItem
from cmbs.refresh import service as refresh
from cmbs.refresh.report import routine_report
from cmbs.runs import service as runs
from cmbs.tape.tables import LoanPeriod, LoanTape
from cmbs.testing.bdd import attempt, in_session
from tests.test_routine_world import (
    LOAD_DAY,
    Archive,
    accession,
    backfill,
    exhibit_url,
    sources,
)

AUGUST = Period(2026, 8)
LINK = re.compile(r"(ABS-EE|10-D) (\S+) (https://[^\s;]+)")


@parse.with_pattern(r"[A-Z][a-z]+ \d{4}")
def _month(string: str) -> Period:
    return Period.parse(string)


register_type(RoutineMonth=_month)


def _archive(context) -> Archive:
    if getattr(context, "archive", None) is None:
        context.archive = Archive()
    return context.archive


def _loaded_through(context, deal: str, month: Period) -> None:
    context.deal = deal
    backfill(in_session, AUGUST, month, archive=_archive(context), deal=deal)
    deal_id = _deal_id(deal)
    loaded = in_session(lambda db: refresh.loaded_months(db, deal_id))
    assert max(loaded) == month, loaded


def _deal_id(name: str) -> int:
    deal = in_session(lambda db: by_name(db, name))
    assert deal is not None, f"no deal named {name!r}"
    return deal.id


def _replace_tape(context, month: Period, change) -> None:
    archive = _archive(context)
    url = exhibit_url(context.deal, month, "ABS-EE")
    tape = archive.recorded.get_bytes(url).decode()
    changed = change(tape)
    assert changed != tape, "the tape was not changed"
    archive.replaced[url] = changed.encode()


def _asset(tape: str, loan: str) -> tuple[int, int]:
    start = tape.index(f"<assetNumber>{loan}</assetNumber>")
    return start, tape.index("</assets>", start)


# ---------------------------------------------------------------------------- Given


@given('"{deal}" is loaded through {month:RoutineMonth}')
def step_loaded_through(context, deal, month):
    _loaded_through(context, deal, month)


@given("its {month:RoutineMonth} 10-D and ABS-EE have been filed")
def step_filed(context, month):
    deal_id = _deal_id(context.deal)
    filed = in_session(lambda db: filings_of(db, deal_id, LOAD_DAY.date()))
    found = {(f.form, f.accession) for f in filed if f.period == month.first_day}
    expected = {(form, accession(context.deal, month, form)) for form in ("10-D", "ABS-EE")}
    assert expected <= found, found


@given("no later filing exists")
def step_no_later_filing(context):
    deal_id = _deal_id(context.deal)
    loaded = max(in_session(lambda db: refresh.loaded_months(db, deal_id)))
    later = in_session(
        lambda db: [
            f
            for f in filings_of(db, deal_id, LOAD_DAY.date())
            if f.period is not None and f.period > loaded.first_day
        ]
    )
    assert later == [], later


@given(
    "the {month:RoutineMonth} tape has {quarantined:d} quarantined loan and "
    "{breaks:d} balance roll-forward break"
)
def step_tape_has_problems(context, month, quarantined, breaks):
    """Loan 5 reports a negative balance, which the loan_period contract quarantines; loan 7
    reports a beginning balance equal to August's ending balance and scheduled principal of
    $25,000.00, but the same ending balance, so the month does not roll forward by $25,000.00."""
    assert (quarantined, breaks) == (1, 1)
    _loaded_through(context, "Fixture Trust 2026-F1", month.shift(-1))

    def change(tape: str) -> str:
        tape = tape.replace(
            "<reportPeriodEndActualBalanceAmount>12000000.00<",
            "<reportPeriodEndActualBalanceAmount>-12000000.00<",
        )
        start, end = _asset(tape, "7")
        amounts = (
            "<reportPeriodBeginningScheduleLoanBalanceAmount>9000000.00"
            "</reportPeriodBeginningScheduleLoanBalanceAmount>\n"
            "    <scheduledPrincipalAmount>25000.00</scheduledPrincipalAmount>\n"
            "    <unscheduledPrincipalCollectedAmount>0.00</unscheduledPrincipalCollectedAmount>\n"
            "    <otherPrincipalAdjustmentAmount>0.00</otherPrincipalAdjustmentAmount>\n"
            "    <realizedLossToTrustAmount>0.00</realizedLossToTrustAmount>\n    "
        )
        at = tape.index("<reportPeriodEndActualBalanceAmount>", start, end)
        return tape[:at] + amounts + tape[at:]

    _replace_tape(context, month, change)


@given("EDGAR answers HTTP 403 when the {month:RoutineMonth} ABS-EE is requested")
def step_edgar_forbids(context, month):
    _loaded_through(context, "Fixture Trust 2026-F1", month.shift(-1))
    context.period = month
    _archive(context).forbidden.add(exhibit_url(context.deal, month, "ABS-EE"))


# ---------------------------------------------------------------------------- When


@when('"{user}" refreshes "{deal}"')
def step_refreshes(context, user, deal):
    context.deal = deal
    clock.set_now(LOAD_DAY)
    report = attempt(
        context,
        lambda: refresh.refresh_deal(user, deal, sources=sources(_archive(context))),
    )
    assert report is not None, f"the refresh was refused: {context.refusal}"
    context.run_report = report
    context.report = routine_report(report.run)


# ---------------------------------------------------------------------------- Then


def _month_counts(context, month: Period) -> dict[str, tuple[int, list[tuple[str, str, str]]]]:
    """The report's counts for a month, by what they count in the singular ("loan loaded"):
    (number, [(form, accession, url)])."""
    lines = context.report.splitlines()
    head = lines.index(f"  {month.label}: loaded")
    counts = {}
    for line in lines[head + 1 :]:
        if not line.startswith("    "):
            break
        found = re.match(r"    (\d+) ([a-z]+?)s? ([a-z -]+?): (.*)$", line)
        if found:
            label = f"{found.group(2)} {found.group(3)}"
            counts[label] = (int(found.group(1)), LINK.findall(found.group(4)))
    return counts


@then("{month:RoutineMonth} is loaded")
def step_month_loaded(context, month):
    deal_id = _deal_id(context.deal)
    status = in_session(lambda db: refresh.month_status(db, deal_id=deal_id, period=month))
    assert status.status == "loaded", status
    assert f"  {month.label}: loaded" in context.report.splitlines(), context.report


@then("the run gets a number in the run history")
def step_run_numbered(context):
    number = context.run_report.run.number
    listed = in_session(lambda db: [view.number for view in runs.history(db, "olly")])
    assert number in listed, listed
    assert f"run {number} (refresh deal, started by quinn): succeeded" in context.report


@then(
    "the report gives the number of loans loaded, comments extracted, items sent to review "
    "and alerts raised"
)
def step_report_counts(context):
    month = Period(2026, 9)
    deal_id = _deal_id(context.deal)
    run = context.run_report.run.number

    def truth(db):
        loans = db.scalar(
            select(func.count())
            .select_from(LoanPeriod)
            .where(LoanPeriod.deal_id == deal_id, LoanPeriod.period == month.first_day)
        )
        extracted = db.scalars(
            select(Extraction.id).where(
                Extraction.run_id == run, Extraction.period == month.first_day
            )
        ).all()
        review = db.scalar(
            select(func.count())
            .select_from(ReviewItem)
            .where(ReviewItem.deal_id == deal_id, ReviewItem.created_at >= LOAD_DAY)
        )
        return loans, len(extracted), review

    loans, comments, review = in_session(truth)
    months = context.run_report.run.summary["deals"][0]["months"]
    alerts = months[0]["alerts"]
    counts = _month_counts(context, month)
    found = {label: number for label, (number, _) in counts.items()}
    expected = {
        "loan loaded": loans,
        "comment extracted": comments,
        "item sent to review": review,
        "alert raised": alerts,
    }
    assert {key: found.get(key) for key in expected} == expected, context.report
    assert (loans, comments) == (7, 1), (loans, comments)
    context.counts = counts


@then("each number links to the filings it came from")
def step_numbers_link(context):
    month = Period(2026, 9)
    deal = context.deal

    def link(form: str) -> tuple[str, str, str]:
        number = accession(deal, month, form)
        base = exhibit_url(deal, month, form).rsplit("/", 1)[0] + "/"
        return form, number, base

    tape, report = link("ABS-EE"), link("10-D")
    expected = {
        "loan loaded": [tape],
        "comment extracted": [report],
        "item sent to review": [report],
        "alert raised": [tape, report],
    }
    found = {label: links for label, (_, links) in context.counts.items() if label in expected}
    assert found == expected, context.report


@then('the report opens with "{text}"')
def step_report_opens(context, text):
    assert context.report.startswith(text), context.report


@then('the refresh is marked "{status}"')
def step_marked(context, status):
    number = context.run_report.run.number
    stored = in_session(lambda db: runs.read_run(db, "olly", number))
    assert stored.status == status, stored.status
    assert f"run {number} (refresh deal, started by quinn): {status}" in context.report


@then('the refresh stops with "{message}"')
def step_stops_with(context, message):
    run = context.run_report.run
    assert run.status == "failed", run.status
    assert run.stopped is not None and run.stopped["error"] == message, run.stopped
    head = f"stopped at {context.deal}, {context.period.label}: {message}"
    assert context.report.splitlines()[0].startswith(head), context.report


@then("nothing is loaded for {month:RoutineMonth}")
def step_nothing_loaded(context, month):
    deal_id = _deal_id(context.deal)

    def count(db):
        return [
            db.scalar(
                select(func.count())
                .select_from(table)
                .where(table.deal_id == deal_id, table.period == month.first_day)
            )
            for table in (LoanPeriod, LoanTape, DistributionReport, Extraction)
        ]

    assert in_session(count) == [0, 0, 0, 0], "something of the month was stored"
    status = in_session(lambda db: refresh.month_status(db, deal_id=deal_id, period=month))
    assert status.status == "not loaded", status
