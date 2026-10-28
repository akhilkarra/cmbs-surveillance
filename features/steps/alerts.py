"""Steps for monthly alerts, the alert lifecycle and the alert half of the book feature.

Each fixture deal's months are made from tests/fixtures/schedule_al_minimal.xml (see
tests/fixtures/alerts/README.md): a month made that way shows no change from the month before,
and Given steps change one loan's tags in one month before anything is loaded. A month's 10-D
is made from tests/fixtures/alerts/fixture_10d_template.htm with each loan's special-servicing
comment. Every month is filed on the 29th; months are loaded at the first step that builds or
reads alerts, and alerts are built on the day after the month was filed, with the clock pinned
there, as a refresh run that morning would.

The book: unless a scenario loads its own positions, exposure is read from the positions file
the features README describes (dated 2026-09-30: Class E $20,000,000.00 and Class F
$5,000,000.00 of "Fixture Trust 2026-F1", Class D $40,000,000.00 of "Fixture Trust 2026-F2").
Steps that only load positions or link whole loans live in book.py; they share state with
these steps through `context.positions_loaded` and `context.linked`.
"""

import datetime as dt
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal
from html import escape
from pathlib import Path

import parse
from behave import given, register_type, then, when  # pyright: ignore[reportAttributeAccessIssue]

from cmbs import clock
from cmbs.absee.schedule_al import NAMESPACE
from cmbs.alerts import service as alerts
from cmbs.alerts.codes import WORKOUT_STRATEGY
from cmbs.alerts.service import AlertView
from cmbs.alerts.tables import AlertBuild
from cmbs.book.service import PositionLine, book_as_of, load_positions
from cmbs.deals.service import by_name
from cmbs.distribution.service import load_report
from cmbs.domain import Period, money
from cmbs.panel.service import deal_named
from cmbs.settings.service import in_force
from cmbs.tape.service import load_tape
from cmbs.testing.bdd import attempt, in_session
from cmbs.testing.world import FIXTURE_CIK

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_TAPE = ROOT / "tests" / "fixtures" / "schedule_al_minimal.xml"
REPORT_TEMPLATE = ROOT / "tests" / "fixtures" / "alerts" / "fixture_10d_template.htm"
TAPE_PERIOD = Period(2026, 9)  # the month the fixture tape reports
URI = NAMESPACE.strip("{}")
FILED_ON_DAY = 29
# Synthetic issuing-entity CIKs, far above any EDGAR has assigned (cmbs.testing.world).
CIKS = {
    "Fixture Trust 2026-F1": FIXTURE_CIK,
    "Fixture Trust 2026-F2": 9_900_002,
    "Fixture Trust 2026-F3": 9_900_003,
}
LOADER = "quinn"  # a quant loads filings
READER = "pat"  # a PM reads alerts
BOOK_DATE = dt.date(2026, 9, 30)
DEFAULT_BOOK = (
    PositionLine("Fixture Trust 2026-F1", "E", money("20,000,000.00")),
    PositionLine("Fixture Trust 2026-F1", "F", money("5,000,000.00")),
    PositionLine("Fixture Trust 2026-F2", "D", money("40,000,000.00")),
)
# Loan 2's special-servicing comment in every month unless a scenario changes it: the comment
# of the fixture deal's September 2026 10-D (tests/fixtures/distribution/).
LOAN_2_COMMENT = (
    "The loan transferred to Special Servicing effective 3/6/2025 due to imminent monetary "
    "default. As of 12/31/2024, DSCR NOI was 0.71x and occupancy was 61.4%. The borrower has "
    "requested a 12-month maturity extension. The special servicer is evaluating the request."
)
# Payment statuses the scenarios name, as Schedule AL codes.
STATUS_CODES = {
    "current": "0",
    "30 days delinquent": "1",
    "60 days delinquent": "2",
    "90+ days delinquent": "3",
}
WORKOUT_CODES = {code.name: number for number, code in WORKOUT_STRATEGY.items()}


@parse.with_pattern(r"[A-Z][a-z]+ \d{4}")
def _month(string: str) -> Period:
    return Period.parse(string)


@parse.with_pattern(r"\d{4}-\d{2}-\d{2}")
def _day(string: str) -> dt.date:
    return dt.date.fromisoformat(string)


@parse.with_pattern(r'[^"]+')
def _name(string: str) -> str:
    return string


@parse.with_pattern(r"high|medium|low")
def _severity(string: str) -> str:
    return string


@parse.with_pattern(r"-?\$[\d,]+\.\d{2}")
def _money(string: str) -> Decimal:
    return money(string)


@parse.with_pattern(r"\d+\.\d+")
def _ratio(string: str) -> Decimal:
    return Decimal(string)


@parse.with_pattern("|".join(STATUS_CODES).replace("+", r"\+"))
def _status(string: str) -> str:
    return STATUS_CODES[string]


register_type(
    AlertMonth=_month,
    AlertDay=_day,
    AlertName=_name,
    Severity=_severity,
    AlertMoney=_money,
    AlertRatio=_ratio,
    PaymentStatus=_status,
)


# ---------------------------------------------------------------------------- fixture months


@dataclass
class FixtureMonth:
    """One month of one fixture deal: its tape and 10-D, until and after they are loaded."""

    deal: str
    period: Period
    tape: bytes
    comments: dict[str, str]
    loaded: bool = False

    @property
    def cik(self) -> int:
        return CIKS[self.deal]

    @property
    def filing_date(self) -> dt.date:
        return dt.date(self.period.year, self.period.month, FILED_ON_DAY)

    def accession(self, form: int) -> str:
        """1 for the tape, 2 for the 10-D: '0009900001-26-000901'."""
        return f"{self.cik:010d}-{self.period.year % 100:02d}-{self.period.month:04d}{form:02d}"

    @property
    def tape_accession(self) -> str:
        return self.accession(1)

    @property
    def report_accession(self) -> str:
        return self.accession(2)


def _assets(root: ET.Element) -> list[ET.Element]:
    return root.findall(NAMESPACE + "assets")


def _date_text(day: dt.date) -> str:
    return day.strftime("%m-%d-%Y")


def _shift_tag(asset: ET.Element, tag: str, months: int) -> None:
    """Move a reported date by whole months, keeping its day."""
    element = asset.find(NAMESPACE + tag)
    text = (element.text or "").strip() if element is not None else ""
    if element is None or not text:
        return
    reported = dt.datetime.strptime(text, "%m-%d-%Y").date()
    moved = Period.of(reported).shift(months)
    element.text = _date_text(dt.date(moved.year, moved.month, reported.day))


def _bytes(root: ET.Element) -> bytes:
    ET.register_namespace("", URI)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def moved_tape(content: bytes, months: int) -> bytes:
    """The tape with its reporting period end and paid-through dates moved by `months`."""
    root = ET.fromstring(content)
    for asset in _assets(root):
        _shift_tag(asset, "reportingPeriodEndDate", months)
        _shift_tag(asset, "paidThroughDate", months)
    return _bytes(root)


def _asset(root: ET.Element, loan: str) -> ET.Element:
    for asset in _assets(root):
        number = asset.find(NAMESPACE + "assetNumber")
        if number is not None and (number.text or "").strip() == loan:
            return asset
    raise AssertionError(f"the fixture tape has no loan {loan!r}")


def with_tag(content: bytes, loan: str, tag: str, value: str | None, *, on_property=False) -> bytes:
    """The tape with one tag of one loan set, or removed when `value` is None."""
    root = ET.fromstring(content)
    parent = _asset(root, loan)
    if on_property:
        found = parent.find(NAMESPACE + "property")
        parent = found if found is not None else ET.SubElement(parent, NAMESPACE + "property")
    element = parent.find(NAMESPACE + tag)
    if value is None:
        if element is not None:
            parent.remove(element)
        return _bytes(root)
    if element is None:
        element = ET.SubElement(parent, NAMESPACE + tag)
    element.text = value
    return _bytes(root)


def report(month: FixtureMonth) -> bytes:
    rows = "\n".join(
        f"    <tr><td>{loan}</td><td>30000000{loan}</td><td>{escape(text)}</td></tr>"
        for loan, text in sorted(month.comments.items())
    )
    distribution = dt.date(month.period.year, month.period.month, 17)
    determination = dt.date(month.period.year, month.period.month, 11)
    return (
        REPORT_TEMPLATE.read_text()
        .format(
            deal=escape(month.deal),
            distribution_date=distribution.strftime("%m/%d/%y"),
            determination_date=determination.strftime("%m/%d/%y"),
            rows=rows,
        )
        .encode()
    )


def _months(context) -> dict[tuple[str, Period], FixtureMonth]:
    if getattr(context, "months", None) is None:
        context.months = {}
    return context.months


def month(context, deal: str, period: Period) -> FixtureMonth:
    """The deal's month, made if new: from the deal's latest earlier month, so changes a
    scenario made to it carry on, or else from the fixture tape."""
    months = _months(context)
    if (deal, period) not in months:
        earlier = [m for (d, p), m in months.items() if d == deal and p < period]
        if earlier:
            last = max(earlier, key=lambda m: m.period)
            tape = moved_tape(last.tape, last.period.months_until(period))
            comments = dict(last.comments)
        else:
            base = FIXTURE_TAPE.read_bytes()
            tape = moved_tape(base, TAPE_PERIOD.months_until(period))
            comments = {"2": LOAN_2_COMMENT}
        months[(deal, period)] = FixtureMonth(deal, period, tape, comments)
    return months[(deal, period)]


def change(context, deal: str, period: Period, loan: str, tag: str, value: str | None, **kw):
    target = month(context, deal, period)
    assert not target.loaded, f"{deal} {period} was loaded before this change"
    target.tape = with_tag(target.tape, loan, tag, value, **kw)
    context.changed = True


def load_months(context) -> None:
    """Load every month made so far that is not loaded yet, oldest first."""
    for target in sorted(_months(context).values(), key=lambda m: (m.period, m.deal)):
        if target.loaded:
            continue
        in_session(
            lambda db, m=target: load_tape(
                db,
                deal=m.deal,
                content=m.tape,
                file_name=f"{m.deal.lower().replace(' ', '-')}_{m.period.first_day:%Y-%m}.xml",
                accession=m.tape_accession,
                filing_date=m.filing_date,
                cik=m.cik,
            )
        )
        in_session(
            lambda db, m=target: load_report(
                db,
                user=LOADER,
                deal=m.deal,
                accession=m.report_accession,
                cik=m.cik,
                filing_date=m.filing_date,
                file_name=f"{m.deal.lower().replace(' ', '-')}_{m.period.first_day:%Y-%m}.htm",
                content=report(m),
                period=m.period,
            )
        )
        target.loaded = True


def build_day(period: Period) -> dt.datetime:
    """The morning after the month was filed."""
    filed = dt.date(period.year, period.month, FILED_ON_DAY)
    return dt.datetime.combine(filed + dt.timedelta(days=1), dt.time(12), dt.UTC)


def build(context, deals: list[str], period: Period, on: dt.datetime | None = None) -> None:
    """Load what is pending, then build the month for each deal: on the day after its filing,
    unless the scenario's build runs later (`on`)."""
    load_months(context)
    clock.set_now(on or build_day(period))
    context.period = period
    context.summaries = {
        deal: in_session(
            lambda db, deal=deal: alerts.build_alerts(
                db, deal_id=deal_named(db, deal).id, period=period
            )
        )
        for deal in deals
    }


def deals_in(context, period: Period) -> list[str]:
    return sorted({deal for (deal, p) in _months(context) if p == period})


def ensure_default_book(context) -> None:
    """The features README's book, unless the scenario loaded positions of its own."""
    if getattr(context, "positions_loaded", False):
        return
    in_session(
        lambda db: load_positions(db, user=LOADER, as_of=BOOK_DATE, lines=list(DEFAULT_BOOK))
    )
    context.positions_loaded = True


def read(alert_id: int, reader: str = READER) -> AlertView:
    return in_session(lambda db: alerts.read_alert(db, reader=reader, alert_id=alert_id))


def raised(context) -> list[AlertView]:
    return [view for summary in context.summaries.values() for view in summary.alerts]


def matching(views: list[AlertView], severity: str, text: str, loan: str) -> list[AlertView]:
    """Alerts of the severity whose message or title is `text`, covering the loan."""
    return [
        view
        for view in views
        if view.severity == severity
        and text in (view.message, view.title)
        and loan in {covered.loan for covered in view.loans}
    ]


def find_one(views: list[AlertView], severity: str, text: str, loan: str) -> AlertView:
    found = matching(views, severity, text, loan)
    assert len(found) == 1, (
        f"{len(found)} {severity} alerts {text!r} for loan {loan}; raised: "
        f"{[(v.severity, v.message, [c.loan for c in v.loans]) for v in views]}"
    )
    return found[0]


# ---------------------------------------------------------------------------- monthly alerts


@given('"{deal:AlertName}" has been ingested for {first:AlertMonth} and {second:AlertMonth}')
def step_ingested(context, deal, first, second):
    for period in (first, second):
        month(context, deal, period)
    context.deal = deal
    context.changed = False


@given('loan "{loan:AlertName}" had no special-servicer transfer in {period:AlertMonth}')
def step_no_transfer(context, loan, period):
    change(context, context.deal, period, loan, "mostRecentSpecialServicerTransferDate", None)


@given('loan "{loan:AlertName}" shows a transfer date of {day:AlertDay} in {period:AlertMonth}')
def step_transfer_date(context, loan, day, period):
    change(
        context,
        context.deal,
        period,
        loan,
        "mostRecentSpecialServicerTransferDate",
        _date_text(day),
    )


@given(
    'loan "{loan:AlertName}" was {first:PaymentStatus} in {p1:AlertMonth} and '
    "{second:PaymentStatus} in {p2:AlertMonth}"
)
def step_payment_status(context, loan, first, p1, second, p2):
    change(context, context.deal, p1, loan, "paymentStatusLoanCode", first)
    change(context, context.deal, p2, loan, "paymentStatusLoanCode", second)


@given(
    'loan "{loan:AlertName}" was paid through {day:AlertDay} in both {p1:AlertMonth} '
    "and {p2:AlertMonth}"
)
def step_paid_through(context, loan, day, p1, p2):
    for period in (p1, p2):
        change(context, context.deal, period, loan, "paidThroughDate", _date_text(day))


@given("the DSCR alert threshold is {threshold:AlertRatio}x in settings version {version:d}")
def step_threshold(context, threshold, version):
    settings = in_session(lambda db: in_force(db, build_day(TAPE_PERIOD)))
    assert settings.version == version, f"settings version {settings.version} is in force"
    assert settings.values.dscr_alert_threshold == threshold, settings.values


@given(
    'loan "{loan:AlertName}"\'s DSCR was {first:AlertRatio}x in {p1:AlertMonth} and '
    "{second:AlertRatio}x in {p2:AlertMonth}"
)
def step_dscr(context, loan, first, p1, second, p2):
    tag = "mostRecentDebtServiceCoverageNetOperatingIncomePercentage"
    change(context, context.deal, p1, loan, tag, str(first), on_property=True)
    change(context, context.deal, p2, loan, tag, str(second), on_property=True)


@given(
    'loan "{loan:AlertName}"\'s workout strategy was "{first:AlertName}" in {p1:AlertMonth} '
    'and "{second:AlertName}" in {p2:AlertMonth}'
)
def step_workout(context, loan, first, p1, second, p2):
    change(context, context.deal, p1, loan, "workoutStrategyCode", WORKOUT_CODES[first])
    change(context, context.deal, p2, loan, "workoutStrategyCode", WORKOUT_CODES[second])


@given(
    'loan "{loan:AlertName}"\'s {period:AlertMonth} comment adds the sentence '
    '"{sentence:AlertName}"'
)
def step_comment_adds(context, loan, period, sentence):
    later = month(context, context.deal, period)
    earlier = month(context, context.deal, period.shift(-1))
    assert sentence in later.comments.get(loan, ""), later.comments
    earlier.comments[loan] = " ".join(earlier.comments[loan].replace(sentence, " ").split())
    context.added_sentence = sentence
    context.changed = True


@given("no loan changed between {first:AlertMonth} and {second:AlertMonth}")
def step_no_change(context, first, second):
    assert not context.changed, "a step changed a loan"
    for period in (first, second):
        assert (context.deal, period) in _months(context), f"{period} was not ingested"


@when("the {period:AlertMonth} alerts are built")
def step_build(context, period):
    build(context, deals_in(context, period), period)


@then('a {severity:Severity} alert "{text:AlertName}" is raised for loan "{loan:AlertName}"')
def step_alert_raised(context, severity, text, loan):
    context.alert = find_one(raised(context), severity, text, loan)


@then("the alert links to the {period:AlertMonth} ABS-EE")
def step_links_to(context, period):
    expected = month(context, context.deal, period).tape_accession
    assert context.alert.accessions == (expected,), context.alert.accessions


@then("the alert records settings version {version:d}")
def step_records_version(context, version):
    assert context.alert.settings_version == version, context.alert


@then("the alert shows only the added sentence")
def step_only_added(context):
    assert context.alert.added == (context.added_sentence,), context.alert.added


@then('the alert list is empty and says "{note}"')
def step_empty_list(context, note):
    listed = in_session(
        lambda db: alerts.month_alerts(db, reader=READER, deal=context.deal, period=context.period)
    )
    assert listed.alerts == (), [view.message for view in listed.alerts]
    assert listed.note == note, listed.note
    summary = context.summaries[context.deal]
    assert (summary.raised, summary.note) == (0, note), summary


# ---------------------------------------------------------------------------- lifecycle


@given(
    'a {severity:Severity} alert "{text:AlertName}" for loan "{loan:AlertName}" of '
    '"{deal:AlertName}", raised for {period:AlertMonth}'
)
def step_background_alert(context, severity, text, loan, deal, period):
    assert text == "transferred to special servicing", f"no fixture month raises {text!r}"
    context.deal = deal
    month(context, deal, period.shift(-1))
    step_transfer_date(context, loan, dt.date(period.year, period.month, 2), period)
    build(context, [deal], period)
    context.alert = find_one(raised(context), severity, text, loan)
    context.alert_id = context.alert.id


def _act(context, action) -> None:
    context.view = attempt(context, lambda: in_session(action))


@when('"{user:AlertName}" acknowledges the alert')
def step_acknowledge(context, user):
    _act(context, lambda db: alerts.acknowledge(db, user=user, alert_id=context.alert_id))


@given('"{user:AlertName}" has acknowledged the alert')
def step_has_acknowledged(context, user):
    step_acknowledge(context, user)
    assert context.refusal is None, context.refusal


@then('the alert\'s state is "{text}"')
def step_state_is(context, text):
    assert context.refusal is None, context.refusal
    context.state_text = read(context.alert_id).state_text
    assert context.state_text == text, context.state_text


@then('"{user:AlertName}" sees the same state')
def step_same_state(context, user):
    assert read(context.alert_id, user).state_text == context.state_text


@when('"{user:AlertName}" assigns it to "{to:AlertName}" with the note "{note}"')
def step_assign(context, user, to, note):
    context.note = note
    _act(
        context,
        lambda db: alerts.assign(db, user=user, alert_id=context.alert_id, to=to, note=note),
    )


@then('the alert shows "{text}" and the note')
def step_shows(context, text):
    assert context.refusal is None, context.refusal
    view = read(context.alert_id)
    assert (view.state_text, view.note) == (text, context.note), view


@then('"{user:AlertName}"\'s work list includes the alert')
def step_work_list(context, user):
    listed = in_session(lambda db: alerts.work_list(db, user=user))
    assert context.alert_id in [view.id for view in listed], listed


@given('the alert is assigned to "{to:AlertName}"')
def step_is_assigned(context, to):
    step_assign(context, READER, to, "please look into this")
    assert context.refusal is None, context.refusal


@when('"{user:AlertName}" closes it with the note "{note}"')
def step_close(context, user, note):
    context.note = note
    _act(context, lambda db: alerts.close(db, user=user, alert_id=context.alert_id, note=note))


@then('the alert\'s state is "{text}" with the note')
def step_state_with_note(context, text):
    assert context.refusal is None, context.refusal
    view = read(context.alert_id)
    assert (view.state_text, view.note) == (text, context.note), view


@then("it no longer appears in the open alerts")
def step_not_open(context):
    listed = in_session(lambda db: alerts.open_alerts(db, reader=READER))
    assert context.alert_id not in [view.id for view in listed]


@given("the alert was closed in {period:AlertMonth}")
def step_was_closed(context, period):
    assert context.alert.period == period, context.alert.period
    step_close(context, "alex", "resolved at the sponsor's expense")
    assert context.refusal is None, context.refusal


@given('loan "{loan:AlertName}"\'s workout strategy changes in {period:AlertMonth}')
def step_workout_changes(context, loan, period):
    before = month(context, context.deal, period.shift(-1))
    reported = _asset(ET.fromstring(before.tape), loan).find(NAMESPACE + "workoutStrategyCode")
    assert reported is None, f"loan {loan} already reports workout strategy {reported.text}"
    change(context, context.deal, period, loan, "workoutStrategyCode", WORKOUT_CODES["foreclosure"])


@then('a new {severity:Severity} alert "{text:AlertName}" is raised for loan "{loan:AlertName}"')
def step_new_alert(context, severity, text, loan):
    view = find_one(raised(context), severity, text, loan)
    assert view.id != context.alert_id and view.period == context.period, view
    assert view.state_text == "new", view.state_text


@then("the {period:AlertMonth} alert stays closed")
def step_stays_closed(context, period):
    view = read(context.alert_id)
    assert view.period == period and view.state_text.startswith("closed by "), view


@when('"{user:AlertName}" tries to close the alert')
def step_tries_close(context, user):
    _act(context, lambda db: alerts.close(db, user=user, alert_id=context.alert_id, note="done"))


# ---------------------------------------------------------------------------- the book


@given('the book holds {amount:AlertMoney} of "{deal:AlertName}"')
def step_book_holds(context, amount, deal):
    ensure_default_book(context)
    held = in_session(lambda db: book_as_of(db, BOOK_DATE))
    assert held is not None and held.exposure().get(deal) == amount, held


@given('the book holds nothing in "{deal:AlertName}"')
def step_holds_nothing(context, deal):
    ensure_default_book(context)
    held = in_session(lambda db: book_as_of(db, BOOK_DATE))
    assert held is not None and deal not in held.exposure(), held


@given(
    'a {severity:Severity} alert "{text:AlertName}" is raised for loan "{loan:AlertName}" of '
    '"{deal:AlertName}"'
)
def step_alert_of_deal(context, severity, text, loan, deal):
    step_background_alert(context, severity, text, loan, deal, TAPE_PERIOD)


@when("the alert is read")
def step_alert_read(context):
    context.view = read(context.alert_id)


@then('it shows exposure {amount:AlertMoney} in classes "{classes}"')
def step_shows_exposure(context, amount, classes):
    view = context.view
    assert (view.exposure, ", ".join(view.classes)) == (amount, classes), view


def _with_change(context, deal: str, period: Period) -> None:
    """Make the deal's month and the one before, with loan 7 transferred in the later one."""
    month(context, deal, period.shift(-1))
    change(
        context,
        deal,
        period,
        "7",
        "mostRecentSpecialServicerTransferDate",
        _date_text(dt.date(period.year, period.month, 2)),
    )


@when('the {period:AlertMonth} alerts for "{deal:AlertName}" are built')
def step_build_deal(context, period, deal):
    context.deal = deal
    _with_change(context, deal, period)
    build(context, [deal], period)


@then("the alerts exist with exposure {amount:AlertMoney}")
def step_alerts_exist(context, amount):
    views = context.summaries[context.deal].alerts
    assert views, "no alerts were raised"
    assert {view.exposure for view in views} == {amount}, [view.exposure for view in views]


@then("they are left out of the book view")
def step_left_out(context):
    ids = {view.id for view in context.summaries[context.deal].alerts}
    shown = in_session(lambda db: alerts.book_view(db, reader=READER, period=context.period))
    assert not ids & {view.id for view in shown}, shown


def _built(deal: str, period: Period) -> bool:
    def find(db):
        deal_row = by_name(db, deal)
        return deal_row is not None and db.get(AlertBuild, (deal_row.id, period.first_day))

    return bool(in_session(find))


# Loan 4 falls one payment status further behind each month from July 2026, so every month
# from August on has an alert to read.
SLIDE_START = Period(2026, 7)
SLIDE = ("0", "A", "B", "1", "2", "3")


def ensure_built(context, deal: str, period: Period) -> None:
    """The deal's month built against the month before, made and loaded if need be.

    A month before September 2026 is built as a backfill on the morning after the September
    filing: settings version 7, the only one in the fixture world, was approved on 2026-09-15.
    """
    if _built(deal, period):
        return
    for target in (period.shift(-1), period):
        made = month(context, deal, target)
        if not made.loaded:
            step = min(max(SLIDE_START.months_until(target), 0), len(SLIDE) - 1)
            change(context, deal, target, "4", "paymentStatusLoanCode", SLIDE[step])
    build(context, [deal], period, on=build_day(max(period, TAPE_PERIOD)))


def _read_month(context, deal: str, period: Period) -> None:
    context.deal = deal
    ensure_built(context, deal, period)
    context.listed = in_session(
        lambda db: alerts.month_alerts(db, reader=READER, deal=deal, period=period)
    )


@when('the {period:AlertMonth} alerts for "{deal:AlertName}" are read')
def step_read_deal_month(context, period, deal):
    _read_month(context, deal, period)


@when("the {period:AlertMonth} alerts are read")
def step_read_month(context, period):
    _read_month(context, context.deal, period)


@then("they show exposure {amount:AlertMoney}")
def step_they_show(context, amount):
    views = context.listed.alerts
    assert views, f"no alerts: {context.listed.note}"
    assert {view.exposure for view in views} == {amount}, [view.exposure for view in views]


@given(
    "both {period:AlertMonth} tapes show that loan transferred to special servicing on "
    "{day:AlertDay}"
)
def step_both_transferred(context, period, day):
    for deal, loan in context.linked:
        month(context, deal, period.shift(-1))
        change(
            context, deal, period, loan, "mostRecentSpecialServicerTransferDate", _date_text(day)
        )


@then('one high alert "{text:AlertName}" is raised for the linked loan')
def step_one_linked_alert(context, text):
    found = {
        view.id: view
        for view in raised(context)
        if view.severity == "high" and text in (view.message, view.title)
    }
    assert len(found) == 1, [(v.id, v.message, v.deals) for v in found.values()]
    (context.alert,) = found.values()
    expected = {(deal, loan) for deal, loan in context.linked}
    assert {(c.deal, c.loan) for c in context.alert.loans} == expected, context.alert.loans


@then("it lists both deals with combined exposure {amount:AlertMoney}")
def step_combined_exposure(context, amount):
    ensure_default_book(context)
    view = read(context.alert.id)
    assert view.deals == tuple(sorted(deal for deal, _ in context.linked)), view.deals
    assert view.exposure == amount, view.exposure
