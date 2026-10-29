"""Steps for features/operations: batch refresh and backfill, and run history.

EDGAR is replayed: discovery answers for the two fixture deals come from tests/fixtures/refresh/
edgar (synthetic, see its README) and for real deals from tests/fixtures/edgar; filing files
come from tests/fixtures/refresh/archive. A step may replace one file ("its 10-D fails to
parse"). The clock is pinned to the day each step names; the run-history story reads time
passing as one minute per exhibit fetched, so its run lasts from 09:00 to 09:04.

Two run-history scenarios lean on the first one without saying so: "run 43" is the run of the
first scenario, so the step that refreshes the book makes the next run number 43, and a value
traced to "run 43" is loaded by that same refresh when nothing was loaded before.
"""

import datetime as dt
from contextlib import suppress
from pathlib import Path

import parse
from behave import given, register_type, then, when  # pyright: ignore[reportAttributeAccessIssue]
from sqlalchemy import func, select

from cmbs import clock
from cmbs.access.roles import Action
from cmbs.access.service import authorize
from cmbs.book.service import PositionLine, book_as_of, load_positions
from cmbs.deals.service import by_name, get_or_create
from cmbs.distribution.tables import DistributionReport
from cmbs.domain import Period, money
from cmbs.edgar.archives import filing_base_url
from cmbs.edgar.replay import Replay
from cmbs.refresh import service as refresh
from cmbs.refresh.claims import Claims, record_claims
from cmbs.refresh.sources import ArchiveReplay, FirstRecorded, Sources
from cmbs.refresh.tables import RunMonth
from cmbs.runs import service as runs
from cmbs.runs.tables import Run
from cmbs.tape.tables import LoanPeriod, LoanTape
from cmbs.testing.bdd import attempt, changed_tables, data_snapshot, in_session
from cmbs.testing.world import FIXTURE_CIK, FIXTURE_DEAL, set_next_run_number

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
EDGAR = (FIXTURES / "refresh" / "edgar", FIXTURES / "edgar")
ARCHIVE = FIXTURES / "refresh" / "archive"
SECOND_DEAL = "Fixture Trust 2026-F2"
SECOND_CIK = 9_900_002
BOOK_DATE = dt.date(2026, 9, 30)
# Classes and faces from features/README.md; the private deal as in the coverage report.
BOOK = {
    FIXTURE_DEAL: (("E", "20,000,000.00"), ("F", "5,000,000.00")),
    SECOND_DEAL: (("D", "40,000,000.00"),),
    "HYT 2024-RGCY": (("E", "30,000,000.00"),),
}
OPERATOR = "olly"
# "Today" for steps that name no day: the October 2026 filings (filed on the 29th) are not out.
TODAY = dt.datetime(2026, 10, 28, 9, 0, tzinfo=dt.UTC)
# The run the run-history scenarios call "run 43".
STORY_RUN = 43
NOT_A_REPORT = b"<html><body><p>This page is not a distribution report.</p></body></html>"


@parse.with_pattern(r"[A-Z][a-z]+ \d{4}")
def _month(string: str) -> Period:
    return Period.parse(string)


@parse.with_pattern(r"\d{4}-\d{2}-\d{2}")
def _day(string: str) -> dt.date:
    return dt.date.fromisoformat(string)


@parse.with_pattern(r"\d{2}:\d{2}")
def _time(string: str) -> dt.time:
    return dt.time.fromisoformat(string)


@parse.with_pattern(r'[^"]+')
def _name(string: str) -> str:
    return string


register_type(OpsMonth=_month, OpsDay=_day, OpsTime=_time, OpsName=_name)


# ---------------------------------------------------------------------------- sources


class Archive:
    """The recorded archive, with files a scenario replaces, and a clock that may tick."""

    def __init__(self, minutes_per_exhibit: int = 0) -> None:
        self.recorded = FirstRecorded(ArchiveReplay(ARCHIVE))
        self.replaced: dict[str, bytes] = {}
        self.minutes = minutes_per_exhibit

    def get_bytes(self, url: str) -> bytes:
        content = self.replaced.get(url) or self.recorded.get_bytes(url)
        if self.minutes and not url.endswith("/index.json"):
            clock.set_now(clock.now() + dt.timedelta(minutes=self.minutes))
        return content


def _sources(context) -> Sources:
    if getattr(context, "sources", None) is None:
        context.archive = Archive()
        context.sources = Sources(
            edgar=FirstRecorded(*(Replay(directory) for directory in EDGAR)),
            archive=context.archive,
        )
    return context.sources


def _cik(deal: str) -> int:
    return {FIXTURE_DEAL: FIXTURE_CIK, SECOND_DEAL: SECOND_CIK}[deal]


def _report_url(deal: str, period: Period) -> str:
    """A fixture deal's EX-99.1 for a month, named as tests/fixtures/refresh/build.py names it."""
    filed = dt.date(period.year, period.month, 17) + dt.timedelta(days=12)
    accession = f"{_cik(deal):010d}-{filed.year % 100:02d}-{400 + period.month * 2:06d}"
    slug = deal.lower().replace(" ", "-")
    return (
        filing_base_url(_cik(deal), accession) + f"{slug}_ex991-{period.year}{period.month:02d}.htm"
    )


def _fail_report(context, deal: str, period: Period) -> None:
    sources = _sources(context)
    url = _report_url(deal, period)
    sources.archive.get_bytes(url)  # the file exists: the scenario replaces a real one
    context.archive.replaced[url] = NOT_A_REPORT


def _at(day: dt.date, time: dt.time = dt.time(9, 0)) -> dt.datetime:
    return dt.datetime.combine(day, time, dt.UTC)


def _deal_id(name: str) -> int | None:
    def read(db):
        deal = by_name(db, name)
        return None if deal is None else deal.id

    return in_session(read)


def _known_deal_id(name: str) -> int:
    deal_id = _deal_id(name)
    assert deal_id is not None, f"no deal named {name!r}"
    return deal_id


def _load_book() -> None:
    lines = [
        PositionLine(deal, tranche, money(face))
        for deal, positions in BOOK.items()
        for tranche, face in positions
    ]
    in_session(lambda db: load_positions(db, user=OPERATOR, as_of=BOOK_DATE, lines=lines))


def _run(context, action) -> None:
    context.report = None
    context.run_report = attempt(context, action)
    if context.run_report is not None:
        context.report = context.run_report.text()


def _months(context) -> list[dict]:
    return [month for deal in context.run_report.run.summary["deals"] for month in deal["months"]]


def _deal_result(context, deal: str) -> dict:
    found = [d for d in context.run_report.run.summary["deals"] if d["deal"] == deal]
    assert len(found) == 1, f"{deal} in the run summary: {found}"
    return found[0]


def _set_next_run(number: int) -> None:
    in_session(lambda db: set_next_run_number(db, number))


def _register(deal: str) -> int:
    """A refresh or backfill of one deal needs the deal registered (with its CIK, as filing
    discovery would register it); the scenarios name only known deals."""
    return in_session(lambda db: get_or_create(db, deal, cik=_cik(deal)).id)


# ---------------------------------------------------------------------------- batch refresh


@given('the book holds "{a:OpsName}", "{b:OpsName}" and "{c:OpsName}"')
def step_book_holds(context, a, b, c):
    assert sorted([a, b, c]) == sorted(BOOK), f"the fixture book holds {sorted(BOOK)}"
    _load_book()
    context.book = [a, b, c]


@when('"{user:OpsName}" refreshes the book')
def step_refreshes_book(context, user):
    clock.set_now(TODAY)
    _run(context, lambda: refresh.refresh_book(user, sources=_sources(context)))
    assert context.refusal is None, f"the refresh was refused: {context.refusal}"


@then('"{a:OpsName}" and "{b:OpsName}" are refreshed')
def step_are_refreshed(context, a, b):
    for deal in (a, b):
        result = _deal_result(context, deal)
        assert result["outcome"] == "refreshed", result
        assert result["months"], f"{deal}: nothing was loaded"
        assert all(m["outcome"] == "loaded" for m in result["months"]), result


@then('"{deal:OpsName}" is skipped with "{reason}"')
def step_is_skipped(context, deal, reason):
    result = _deal_result(context, deal)
    assert (result["outcome"], result["reason"]) == ("skipped", reason), result
    assert result["months"] == [], result


@then("the run summary lists all three deals with their outcome")
def step_summary_lists_deals(context):
    deals = context.run_report.run.summary["deals"]
    assert sorted(d["deal"] for d in deals) == sorted(context.book), deals
    lines = context.report.splitlines()
    for deal in deals:
        assert any(line.startswith(f"{deal['deal']}: {deal['outcome']}") for line in lines), (
            context.report
        )


@given('"{deal:OpsName}" has no months loaded')
def step_no_months_loaded(context, deal):
    context.deal = deal
    deal_id = _register(deal)
    assert not in_session(lambda db: refresh.loaded_months(db, deal_id))


@when('"{user:OpsName}" backfills "{deal:OpsName}" from {start:OpsMonth} to {end:OpsMonth}')
def step_backfills(context, user, deal, start, end):
    clock.set_now(TODAY)
    context.deal, context.backfilled = deal, (start, end)
    _run(context, lambda: refresh.backfill(user, deal, start, end, sources=_sources(context)))
    assert context.refusal is None, f"the backfill was refused: {context.refusal}"


@then("{count:d} months are loaded, oldest first")
def step_months_loaded_in_order(context, count):
    start, end = context.backfilled
    expected = [start.shift(n) for n in range(start.months_until(end) + 1)]
    months = _months(context)
    assert [m["period"] for m in months] == [p.label for p in expected], months
    assert all(m["outcome"] == "loaded" for m in months), months
    assert len(expected) == count
    deal_id = _known_deal_id(context.deal)
    assert in_session(lambda db: refresh.loaded_months(db, deal_id)) == set(expected)
    # Loaded oldest first: each month's run record is later than the one before.
    recorded = in_session(
        lambda db: [
            Period.of(row.period)
            for row in db.scalars(
                select(RunMonth)
                .where(RunMonth.run_id == context.run_report.run.number)
                .order_by(RunMonth.id)
            )
        ]
    )
    assert recorded == expected, recorded


@then("each month's alerts are built against the month before it")
def step_alerts_per_month(context):
    months = _months(context)
    missing = [m["period"] for m in months if m["alerts"] is None]
    assert not missing, (
        f"alerts were not built for {', '.join(missing)}: cmbs.alerts.service is not installed"
    )


@given('the {period:OpsMonth} ABS-EE for "{deal:OpsName}" loads but its 10-D fails to parse')
def step_report_fails(context, period, deal):
    context.deal, context.period = deal, period
    _register(deal)
    _fail_report(context, deal, period)
    # The month is the newest with both filings two days after they were filed.
    context.today = _at(dt.date(period.year, period.month, 29) + dt.timedelta(days=2))


@when("the run finishes")
def step_run_finishes(context):
    clock.set_now(context.today)
    _run(context, lambda: refresh.refresh_deal("quinn", context.deal, sources=_sources(context)))
    assert context.refusal is None, f"the refresh was refused: {context.refusal}"


@then('{period:OpsMonth} for "{deal:OpsName}" is marked "{status}"')
def step_month_marked(context, period, deal, status):
    deal_id = _known_deal_id(deal)
    found = in_session(lambda db: refresh.month_status(db, deal_id=deal_id, period=period))
    assert found.status == status, found


@then('no {period:OpsMonth} loan rows for "{deal:OpsName}" are stored')
def step_no_rows_stored(context, period, deal):
    deal_id = _known_deal_id(deal)

    def count(db):
        return [
            db.scalar(
                select(func.count())
                .select_from(table)
                .where(table.deal_id == deal_id, table.period == period.first_day)
            )
            for table in (LoanPeriod, LoanTape, DistributionReport)
        ]

    assert in_session(count) == [0, 0, 0], "the month's tape or report was stored"


@then('the run summary says "{text}" for that month')
def step_summary_says_for_month(context, text):
    months = [m for m in _months(context) if m["period"] == context.period.label]
    assert len(months) == 1, _months(context)
    assert months[0]["outcome"] == "not loaded", months[0]
    assert months[0]["reason"] == text, months[0]
    assert f"{context.period.label}: not loaded, {text}" in context.report, context.report


@given('run {number:d} is refreshing "{deal:OpsName}"')
def step_run_is_refreshing(context, number, deal):
    """A live run: it holds the deal's claim until the scenario ends."""
    context.deal = deal
    _set_next_run(number)
    in_session(lambda db: get_or_create(db, deal, cik=_cik(deal)))
    deal_id = _known_deal_id(deal)
    claims = Claims.open()
    assert claims.try_take(deal_id), f"{deal} is already claimed"

    def start(db):
        actor = authorize(db, OPERATOR, Action.RUN_REFRESH)
        run = runs.start_run(db, kind=refresh.REFRESH_DEAL, started_by=actor.user)
        record_claims(db, run, [deal_id])
        return run.id

    assert in_session(start) == number

    def let_go():
        with suppress(Exception):  # the scenario's database may be gone already
            claims.close()

    context.add_cleanup(let_go)
    context.holder = number


@when('"{user:OpsName}" starts a refresh of "{deal:OpsName}"')
def step_starts_refresh(context, user, deal):
    clock.set_now(TODAY)
    _run(context, lambda: refresh.refresh_deal(user, deal, sources=_sources(context)))


@then('the new run is refused with "{message}"')
def step_new_run_refused(context, message):
    assert context.refusal is not None, "the run was not refused"
    assert str(context.refusal) == message, f"refused with {str(context.refusal)!r}"


@then("nothing is loaded twice")
def step_nothing_loaded_twice(context):
    assert context.snapshot is not None
    changed = changed_tables(context.snapshot, data_snapshot())
    assert not changed, f"stored data changed in {changed}"
    numbers = in_session(lambda db: [(r.id, r.status) for r in db.scalars(select(Run))])
    assert numbers == [(context.holder, "running")], f"runs: {numbers}"


# ---------------------------------------------------------------------------- run history


def _story_refresh(context, day: dt.date, start: dt.time) -> None:
    """The refresh of the run-history story: the book, as run 43, one minute per exhibit."""
    _ensure_book()
    _set_next_run(STORY_RUN)
    sources = _sources(context)
    context.archive.minutes = 1
    clock.set_now(_at(day, start))
    try:
        _run(context, lambda: refresh.refresh_book(OPERATOR, sources=sources))
    finally:
        context.archive.minutes = 0
    assert context.refusal is None, f"the refresh was refused: {context.refusal}"


def _ensure_book() -> None:
    if in_session(lambda db: book_as_of(db, BOOK_DATE)) is None:
        _load_book()


@when(
    '"{user:OpsName}" refreshes the book on {day:OpsDay} at {start:OpsTime} '
    "and it ends at {end:OpsTime}"
)
def step_refreshes_book_on(context, user, day, start, end):
    assert user == OPERATOR, f"the run-history story is {OPERATOR}'s refresh"
    _story_refresh(context, day, start)
    context.expected_end = _at(day, end)


@then(
    'the run history shows run {number:d}: "{user:OpsName}", {day:OpsDay} {start:OpsTime} to '
    "{end:OpsTime}, {refreshed:d} deals refreshed, {skipped:d} skipped, {rows:d} loan rows "
    "loaded, {alerts:d} alerts raised"
)
def step_history_shows(context, number, user, day, start, end, refreshed, skipped, rows, alerts):
    listed = in_session(lambda db: runs.history(db, OPERATOR, start=day, end=day))
    found = [view for view in listed if view.number == number]
    assert len(found) == 1, f"runs on {day}: {[v.headline() for v in listed]}"
    view = found[0]
    assert view.started_by == user, view
    assert (view.started_at, view.ended_at) == (_at(day, start), _at(day, end)), view.period()
    summary = view.summary
    assert summary["deals_refreshed"] == refreshed, summary
    assert summary["deals_skipped"] == skipped, summary
    assert summary["loan_rows"] == rows, summary
    assert summary["alerts_raised"] == alerts, (
        f"{view.headline()} (alerts are built by cmbs.alerts, which is not installed)"
    )
    expected = (
        f"run {number}: {user}, {day} {start:%H:%M} to {end:%H:%M}, {refreshed} deals refreshed, "
        f"{skipped} skipped, {rows} loan rows loaded, {alerts} alerts raised"
    )
    assert view.headline() == expected, view.headline()


@given('run {number:d} fails while loading {period:OpsMonth} for "{deal:OpsName}"')
def step_run_fails(context, number, period, deal):
    """A backfill of the month before and the month itself; the month's 10-D cannot be read."""
    context.deal, context.failed_at = deal, (deal, period)
    _register(deal)
    _set_next_run(number)
    _fail_report(context, deal, period)
    clock.set_now(_at(dt.date(period.year, period.month, 29) + dt.timedelta(days=1)))
    before = period.shift(-1)
    _run(
        context,
        lambda: refresh.backfill("quinn", deal, before, period, sources=_sources(context)),
    )
    assert context.refusal is None, f"the backfill was refused: {context.refusal}"
    assert context.run_report.run.number == number, context.run_report.run
    assert context.run_report.run.status == "failed", context.report
    context.loaded_before = before


@when('"{user:OpsName}" reads run {number:d}')
def step_reads_run(context, user, number):
    context.run_view = attempt(
        context, lambda: in_session(lambda db: runs.read_run(db, user, number))
    )
    assert context.refusal is None, f"reading the run was refused: {context.refusal}"


@then('the run shows "{status}", the deal and month it was on, and the error "{error}"')
def step_run_shows_failure(context, status, error):
    view = context.run_view
    deal, period = context.failed_at
    assert view.status == status, view
    stopped = view.stopped
    assert stopped is not None, view.summary
    assert (stopped["deal"], stopped["period"], stopped["error"]) == (deal, period.label, error), (
        stopped
    )
    assert f"stopped at {deal}, {period.label}: {error}" in view.text(), view.text()


@then("months loaded before the failure stay loaded")
def step_months_before_stay(context):
    deal, _ = context.failed_at
    deal_id = _known_deal_id(deal)
    found = in_session(
        lambda db: refresh.month_status(db, deal_id=deal_id, period=context.loaded_before)
    )
    assert found.status == "loaded", found


@when('"{user:OpsName}" opens loan "{loan:OpsName}"\'s {period:OpsMonth} balance')
def step_opens_balance(context, user, loan, period):
    if not in_session(lambda db: db.scalar(select(func.count()).select_from(Run))):
        _story_refresh(context, dt.date(2026, 10, 27), dt.time(9, 0))
    deal = getattr(context, "deal", None) or FIXTURE_DEAL
    context.trace = attempt(
        context,
        lambda: in_session(lambda db: runs.trace(db, user, deal=deal, loan=loan, period=period)),
    )
    assert context.refusal is None, f"{user} was refused: {context.refusal}"


@then('it shows "{text}"')
def step_it_shows(context, text):
    shown = context.trace.text()
    assert text in shown, shown


@given("run {number:d} was made on {day:OpsDay}")
def step_run_made_on(context, number, day):
    """A refresh of the fixture deal that day, by the operator."""
    _register(FIXTURE_DEAL)
    _set_next_run(number)
    clock.set_now(_at(day))
    _run(context, lambda: refresh.refresh_deal(OPERATOR, FIXTURE_DEAL, sources=_sources(context)))
    assert context.refusal is None, f"the refresh was refused: {context.refusal}"
    assert context.run_report.run.number == number
    context.made = context.run_report.run
    clock.set_now(TODAY)  # a year and more later


@when('"{user:OpsName}" reads the run history for {month:OpsMonth}')
def step_reads_history_for(context, user, month):
    start, end = runs.month_bounds(month)
    context.history = attempt(
        context, lambda: in_session(lambda db: runs.history(db, user, start=start, end=end))
    )
    assert context.refusal is None, f"reading the history was refused: {context.refusal}"


@then("run {number:d} is still listed with its full summary")
def step_still_listed(context, number):
    found = [view for view in context.history if view.number == number]
    assert len(found) == 1, [view.headline() for view in context.history]
    assert found[0] == context.made, (found[0], context.made)
    assert found[0].summary["deals"], found[0].summary
