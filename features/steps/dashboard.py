"""Steps for features/surfaces/dashboard.feature, run in Chromium through Playwright.

**What the page reads.** The scenarios load the book and both fixture deals the way production
does, through refreshes that replay the synthetic EDGAR in tests/fixtures/refresh/:
- the book (positions dated 2026-09-30) holds Class E and F of "Fixture Trust 2026-F1" and
  Class D of "Fixture Trust 2026-F2", as features/README.md describes;
- on 2026-09-20 a refresh of each deal loads August 2026 (a deal's first refresh loads its
  newest month), with the August files changed so that September differs from August exactly
  as the Background's alert table says (`AUGUST_TAPE`, `SEPTEMBER_TAPE`, and loan 2's August
  comment without its last sentence);
- on 2026-10-28 at 09:00 run 43 refreshes the book: it loads September 2026 of both deals and
  builds their alerts;
- at 10:00 the same day a quant extracts loan 2's September comment, replaying the synthetic
  model recording in tests/fixtures/model/ (or, when a scenario says the workout strategy is
  waiting for review, the same recording with that one answer unsettled);
- the dashboard is read at noon.
Loan 2's tapes also carry a beginning balance and zero principal, so the balance roll-forward
(`cmbs.crosscheck`, built in parallel) has what it needs to pass.

**What serves the page.** Each behave process starts one HTTP server on a free local port (a
thread): `/dashboard/*` goes to the FastAPI app in process, so it reads the scenario's own
database and pinned clock; everything else is the built web bundle in web/dist. The bundle is
built in the web container (`docker compose run --rm web npm run build`, after `npm ci` when its
node_modules volume is empty) when it is missing or older than its sources; npm runs only there.
The page is opened at that server, not at CMBS_WEB_URL, because only an in-process API can read
each scenario's database. Set CMBS_DASHBOARD_SHOTS=<dir> to keep a screenshot of every page a
scenario opens.
"""

import datetime as dt
import fcntl
import mimetypes
import os
import re
import subprocess
import threading
import traceback
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import parse
from behave import given, register_type, then, when  # pyright: ignore[reportAttributeAccessIssue]
from fastapi.testclient import TestClient
from sqlalchemy import select

from cmbs import clock
from cmbs.absee.schedule_al import NAMESPACE
from cmbs.alerts import service as alerts
from cmbs.api.app import create_app
from cmbs.api.deps import USER_HEADER
from cmbs.book.service import PositionLine, exposure_by_deal, load_positions
from cmbs.deals.service import by_name, get_or_create
from cmbs.domain import Period, format_money, money
from cmbs.edgar.archives import filing_base_url
from cmbs.edgar.replay import Replay
from cmbs.extraction.sentences import split
from cmbs.extraction.service import comment_view, extract_comments
from cmbs.filings.tables import Filing
from cmbs.model.client import ModelClient
from cmbs.model.recordings import DbRecordings
from cmbs.refresh import service as refresh
from cmbs.refresh.sources import ArchiveReplay, FirstRecorded, Sources
from cmbs.testing.bdd import in_session
from cmbs.testing.world import FIXTURE_CIKS, set_next_run_number
from tests.fixtures.model.synthetic import LOAN_2_SEPTEMBER, loan_2_september_comment, recording
from tests.fixtures.model.synthetic import load as load_recordings

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
DIST = WEB / "dist"
FIXTURES = ROOT / "tests" / "fixtures"
EDGAR = (FIXTURES / "refresh" / "edgar", FIXTURES / "edgar")
ARCHIVE = FIXTURES / "refresh" / "archive"

F1 = "Fixture Trust 2026-F1"
F2 = "Fixture Trust 2026-F2"
AUGUST = Period(2026, 8)
SEPTEMBER = Period(2026, 9)
BOOK_DATE = dt.date(2026, 9, 30)
BOOK = (
    PositionLine(F1, "E", money("20,000,000.00")),
    PositionLine(F1, "F", money("5,000,000.00")),
    PositionLine(F2, "D", money("40,000,000.00")),
)
OPERATOR = "olly"  # runs the book's refreshes and loads its positions
QUANT = "quinn"  # extracts comments
CLOSER = "alex"
AUGUST_REFRESH = dt.datetime(2026, 9, 20, 9, 0, tzinfo=dt.UTC)
SEPTEMBER_REFRESH = dt.datetime(2026, 10, 28, 9, 0, tzinfo=dt.UTC)
EXTRACTED_AT = dt.datetime(2026, 10, 28, 10, 0, tzinfo=dt.UTC)
CLOSED_AT = dt.datetime(2026, 10, 28, 11, 0, tzinfo=dt.UTC)
READ_AT = dt.datetime(2026, 10, 28, 12, 0, tzinfo=dt.UTC)
STORY_RUN = 43

# Loan 2's balance rolls forward in both months: $30,000,000.00 at the start, no principal paid
# or adjusted and no loss, $30,000,000.00 at the end.
ROLLFORWARD = {
    "reportPeriodBeginningScheduleLoanBalanceAmount": "30000000.00",
    "scheduledPrincipalAmount": "0.00",
    "unscheduledPrincipalCollectedAmount": "0.00",
    "otherPrincipalAdjustmentAmount": "0.00",
    "realizedLossToTrustAmount": "0.00",
}
DSCR_TAG = "mostRecentDebtServiceCoverageNetOperatingIncomePercentage"
# (deal, loan) -> tags each month's tape reports; a tag under `property` sits on the property.
AUGUST_TAPE: dict[tuple[str, str], dict[str, str]] = {
    (F1, "2"): {**ROLLFORWARD, "workoutStrategyCode": "13"},  # to be determined
    (F1, "4"): {"paymentStatusLoanCode": "0"},  # current
    (F1, "5"): {"paidThroughDate": "07-01-2026"},
    (F1, "7"): {f"property/{DSCR_TAG}": "1.12"},
    (F2, "3"): {"paymentStatusLoanCode": "0"},  # current
}
SEPTEMBER_TAPE: dict[tuple[str, str], dict[str, str]] = {
    (F1, "2"): {**ROLLFORWARD, "workoutStrategyCode": "11"},  # full payoff
    (F1, "4"): {"paymentStatusLoanCode": "1"},  # 30 days
    (F1, "5"): {"paidThroughDate": "07-01-2026"},  # did not advance
    (F1, "7"): {
        "mostRecentSpecialServicerTransferDate": "09-02-2026",
        f"property/{DSCR_TAG}": "0.96",
    },
    (F2, "3"): {"paymentStatusLoanCode": "2"},  # 60 days
}
# The sentence loan 2's September comment adds to August's.
ADDED_SENTENCE = "The special servicer is evaluating the request."
# Loan 2's workout strategy with an answer inside the abstention band (0.30 to 0.70).
UNSETTLED_WORKOUT = ("modification", 0.55, 0.92, 4)
CLOSE_NOTE = "Transfer confirmed with the special servicer; loan is on the watch list."


@parse.with_pattern(r'[^"]+')
def _name(string: str) -> str:
    return string


@parse.with_pattern(r"-?\$[\d,]+\.\d{2}")
def _money(string: str):
    return money(string)


@parse.with_pattern(r"[A-Z][a-z]+ \d{4}")
def _month(string: str) -> Period:
    return Period.parse(string)


@parse.with_pattern(r"\d{4}-\d{2}-\d{2}")
def _day(string: str) -> dt.date:
    return dt.date.fromisoformat(string)


@parse.with_pattern(r"high|medium|low")
def _severity(string: str) -> str:
    return string


register_type(
    DashName=_name, DashMoney=_money, DashMonth=_month, DashDay=_day, DashSeverity=_severity
)


# ---------------------------------------------------------------------------- the world


class Archive:
    """The recorded EDGAR archive, with files a scenario replaces."""

    def __init__(self) -> None:
        self.recorded = FirstRecorded(ArchiveReplay(ARCHIVE))
        self.replaced: dict[str, bytes] = {}

    def get_bytes(self, url: str) -> bytes:
        return self.replaced.get(url) or self.recorded.get_bytes(url)


def exhibit_url(deal: str, period: Period, form: str) -> str:
    """A fixture deal's EX-102 (ABS-EE) or EX-99.1 (10-D), as tests/fixtures/refresh/build.py
    names them."""
    cik = FIXTURE_CIKS[deal]
    filed = dt.date(period.year, period.month, 17) + dt.timedelta(days=12)
    number = 400 + period.month * 2 + (1 if form == "ABS-EE" else 0)
    accession = f"{cik:010d}-{filed.year % 100:02d}-{number:06d}"
    slug = deal.lower().replace(" ", "-")
    stamp = f"{period.year}{period.month:02d}"
    name = f"{slug}_ex102-{stamp}.xml" if form == "ABS-EE" else f"{slug}_ex991-{stamp}.htm"
    return filing_base_url(cik, accession) + name


def with_tags(content: bytes, loan: str, tags: dict[str, str]) -> bytes:
    """The tape with the loan's tags set; 'property/<tag>' sets a tag of its property."""
    root = ET.fromstring(content)
    found = [
        asset
        for asset in root.findall(NAMESPACE + "assets")
        if (asset.findtext(NAMESPACE + "assetNumber") or "").strip() == loan
    ]
    assert len(found) == 1, f"{len(found)} assets numbered {loan!r}"
    for path, value in tags.items():
        parent = found[0]
        if path.startswith("property/"):
            path = path.removeprefix("property/")
            prop = parent.find(NAMESPACE + "property")
            assert prop is not None, f"loan {loan} has no property"
            parent = prop
        element = parent.find(NAMESPACE + path)
        if element is None:
            element = ET.SubElement(parent, NAMESPACE + path)
        element.text = value
    ET.register_namespace("", NAMESPACE.strip("{}"))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def without_sentence(report: bytes, sentence: str) -> bytes:
    """The 10-D with one comment sentence (a row of its own in the comment table) taken out."""
    text = report.decode()
    row = rf'<tr><td></td><td></td><td></td><td colspan="2">{re.escape(sentence)}</td></tr>\n'
    changed = re.sub(row, "", text)
    assert changed != text, f"the 10-D has no row {sentence!r}"
    return changed.encode()


def _archive(context) -> Archive:
    if getattr(context, "archive", None) is None:
        archive = Archive()
        for period, changes in ((AUGUST, AUGUST_TAPE), (SEPTEMBER, SEPTEMBER_TAPE)):
            for (deal, loan), tags in changes.items():
                url = exhibit_url(deal, period, "ABS-EE")
                content = archive.replaced.get(url) or archive.recorded.get_bytes(url)
                archive.replaced[url] = with_tags(content, loan, tags)
        august_report = exhibit_url(F1, AUGUST, "10-D")
        archive.replaced[august_report] = without_sentence(
            archive.recorded.get_bytes(august_report), ADDED_SENTENCE
        )
        context.archive = archive
    return context.archive


def _sources(context) -> Sources:
    return Sources(
        edgar=FirstRecorded(*(Replay(directory) for directory in EDGAR)), archive=_archive(context)
    )


def _loaded_periods(report) -> list[tuple[str, str]]:
    return [(d["deal"], m["period"]) for d in report.run.summary["deals"] for m in d["months"]]


def ensure_world(context) -> None:
    """The book, August refreshed on 2026-09-20 and September by run 43 on 2026-10-28."""
    if getattr(context, "world_built", False):
        return
    ensure_book(context)
    for deal in (F1, F2):
        in_session(lambda db, deal=deal: get_or_create(db, deal, cik=FIXTURE_CIKS[deal]))
        clock.set_now(AUGUST_REFRESH)
        earlier = refresh.refresh_deal(OPERATOR, deal, sources=_sources(context))
        assert _loaded_periods(earlier) == [(deal, AUGUST.label)], earlier.text()
    in_session(lambda db: set_next_run_number(db, STORY_RUN))
    clock.set_now(SEPTEMBER_REFRESH)
    story = refresh.refresh_book(OPERATOR, sources=_sources(context))
    assert story.run.number == STORY_RUN, story.text()
    assert sorted(_loaded_periods(story)) == [(F1, SEPTEMBER.label), (F2, SEPTEMBER.label)], (
        story.text()
    )
    context.world_built = True
    clock.set_now(READ_AT)


def ensure_book(context) -> None:
    if getattr(context, "book_loaded", False):
        return
    in_session(lambda db: load_positions(db, user=OPERATOR, as_of=BOOK_DATE, lines=list(BOOK)))
    context.book_loaded = True


def ensure_extracted(context, *, unsettled_workout: bool = False) -> None:
    """Loan 2's September comment extracted by a quant, replaying a synthetic recording."""
    if getattr(context, "extracted", False):
        assert not unsettled_workout, "the comment was extracted before the scenario changed it"
        return
    ensure_world(context)

    def extract(db):
        if unsettled_workout:
            answers = {**LOAN_2_SEPTEMBER, "workout_strategy": UNSETTLED_WORKOUT}
            DbRecordings(db).put(recording(loan_2_september_comment(), answers))
        else:
            load_recordings(DbRecordings(db))
        client = ModelClient(
            DbRecordings(db), provider=None, replay_only=True, allow_synthetic=True
        )
        return extract_comments(db, user=QUANT, deal=F1, period=SEPTEMBER, loan="2", client=client)

    clock.set_now(EXTRACTED_AT)
    summary = in_session(extract)
    assert [item.loan for item in summary.comments] == ["2"], summary
    context.extracted = True
    clock.set_now(READ_AT)


def september_alerts() -> list[Any]:
    def read(db):
        return [
            view
            for deal in (F1, F2)
            for view in alerts.month_alerts(db, reader="pat", deal=deal, period=SEPTEMBER).alerts
        ]

    return in_session(read)


# ---------------------------------------------------------------------------- the server


class _Handler(BaseHTTPRequestHandler):
    """`/dashboard/*` and `/health` go to the API in process; anything else is the bundle."""

    server: "_Server"  # pyright: ignore[reportIncompatibleVariableOverride]

    def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
        path = urlsplit(self.path).path
        if path.startswith("/dashboard/") or path == "/health":
            self._api()
        else:
            self._file(path)

    def _api(self) -> None:
        headers = {USER_HEADER: self.headers.get(USER_HEADER, "")}
        try:
            # The clock is pinned per thread (a context variable): read the scenario's.
            with self.server.lock, clock.frozen(self.server.now):
                response = self.server.client.get(self.path, headers=headers)
            status, body = response.status_code, response.content
            kind = response.headers.get("content-type", "application/json")
        except Exception:
            failure = traceback.format_exc()
            self.server.errors.append(failure)
            status, body, kind = 500, failure.encode(), "text/plain"
        self._send(status, body, kind)

    def _file(self, path: str) -> None:
        target = (DIST / (path.lstrip("/") or "index.html")).resolve()
        if not target.is_relative_to(DIST.resolve()) or not target.is_file():
            self._send(404, b"not found", "text/plain")
            return
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), kind)

    def _send(self, status: int, body: bytes, kind: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - http.server's name
        pass


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.client = TestClient(create_app(), raise_server_exceptions=True)
        self.lock = threading.Lock()
        self.errors: list[str] = []
        # "Now" in the scenario that opened the page; requests are answered at that time.
        self.now = clock.now()


_RUNNING: dict[str, _Server] = {}
BUILD = "[ -x node_modules/.bin/vite ] || npm ci; npm run build"


def _bundle_sources() -> list[Path]:
    """What the bundle is built from: the page, the config and src/ apart from its tests."""
    files = [WEB / "index.html", WEB / "package-lock.json", WEB / "vite.config.ts"]
    return files + [
        path
        for path in (WEB / "src").rglob("*")
        if path.is_file() and ".test." not in path.name and not path.name.startswith("test-")
    ]


def _bundle_is_fresh() -> bool:
    index = DIST / "index.html"
    if not index.is_file():
        return False
    built = index.stat().st_mtime
    return all(path.stat().st_mtime <= built for path in _bundle_sources())


def ensure_bundle() -> None:
    """Build web/dist in the web container when it is missing or older than its sources; one
    behave process builds while the others wait."""
    lock_path = ROOT / "output" / ".dashboard-build.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if _bundle_is_fresh():
            return
        built = subprocess.run(
            # A fresh node_modules volume is installed first, from the lock file.
            ["docker", "compose", "run", "--rm", "--no-deps", "web", "sh", "-c", BUILD],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert built.returncode == 0, (
            "building the dashboard failed; build it with "
            "`docker compose run --rm web npm run build`:\n" + built.stdout + built.stderr
        )


def base_url() -> str:
    """The dashboard server of this behave process, started on first use."""
    if "server" not in _RUNNING:
        ensure_bundle()
        server = _Server()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        _RUNNING["server"] = server
    host, port = _RUNNING["server"].server_address[:2]
    return f"http://{host!s}:{port}"


def server_errors() -> str:
    errors = _RUNNING["server"].errors if "server" in _RUNNING else []
    return "\n".join(errors)


def open_page(context, user: str, route: str, ready: str) -> None:
    """Open the dashboard at a route as a user, and wait until the page says it is ready."""
    page = context.page
    base = base_url()
    _RUNNING["server"].now = clock.now()
    problems: list[str] = []
    page.on("console", lambda message: _console(problems, message))
    page.on("pageerror", lambda error: problems.append(str(error)))
    page.goto(f"{base}/?user={quote(user)}#{route}")
    try:
        page.wait_for_selector(f"{ready}[data-state='ready']", timeout=15_000)
    except Exception as error:
        shown = page.inner_text("body")
        raise AssertionError(
            f"{ready} not ready; the page shows:\n{shown}\n{server_errors()}"
        ) from (error)
    context.page_problems = problems


def _console(problems: list[str], message) -> None:
    if message.type in ("error", "warning"):
        problems.append(f"{message.type}: {message.text}")


def assert_no_page_problems(context) -> None:
    """The page logged no error or warning (AG Grid reports a missing module that way)."""
    problems = getattr(context, "page_problems", [])
    assert not problems, "the page logged:\n" + "\n".join(problems)


def screenshot(context, name: str) -> None:
    """With CMBS_DASHBOARD_SHOTS=<dir>, keep a full-page screenshot of what a step opened."""
    directory = os.environ.get("CMBS_DASHBOARD_SHOTS")
    if directory:
        Path(directory).mkdir(parents=True, exist_ok=True)
        context.page.screenshot(path=Path(directory) / f"{name}.png", full_page=True)


# ---------------------------------------------------------------------------- reading the page


def book_rows(context) -> list[dict[str, str]]:
    """The book view's rows in the order shown, each cell's text by column id."""
    return context.page.evaluate(
        """() => [...document.querySelectorAll(
                '[data-testid="book"] .ag-row')]
            .sort((a, b) =>
                Number(a.getAttribute('row-index')) - Number(b.getAttribute('row-index')))
            .map(row => Object.fromEntries([...row.querySelectorAll('[col-id]')]
                .map(cell => [cell.getAttribute('col-id'), cell.innerText.trim()])))"""
    )


def value_row(context, section: str, field: str):
    row = context.page.locator(f'[data-testid="{section}"] [data-field="{field}"]')
    assert row.count() == 1, f"{row.count()} rows for {field} in {section}"
    return row


def texts(locator) -> list[str]:
    return [text.strip() for text in locator.all_inner_texts()]


# ---------------------------------------------------------------------------- Background


@given(
    'the book holds {first:DashMoney} of "{first_deal:DashName}" and {second:DashMoney} of '
    '"{second_deal:DashName}"'
)
def step_book_holds_two(context, first, first_deal, second, second_deal):
    ensure_book(context)
    held = in_session(lambda db: exposure_by_deal(db, BOOK_DATE))
    assert held == {first_deal: first, second_deal: second}, held


@given("the {period:DashMonth} alerts are:")
def step_month_alerts_are(context, period):
    assert period == SEPTEMBER, "the dashboard's story is September 2026"
    ensure_world(context)
    raised = [
        (view.deals[0], view.loans[0].loan, view.severity.value, view.title, view.message)
        for view in september_alerts()
    ]
    unmatched = list(raised)
    for row in context.table:
        wanted = (row["deal"], row["loan"], row["severity"])
        found = [alert for alert in unmatched if alert[:3] == wanted and row["alert"] in alert[3:]]
        assert len(found) == 1, f"{len(found)} alerts like {dict(row.items())}; raised {raised}"
        unmatched.remove(found[0])
    assert not unmatched, f"alerts the table does not list: {unmatched}"


# ---------------------------------------------------------------------------- the book view


@given(
    '"{user:DashName}" closed the {severity:DashSeverity} alert for loan "{loan:DashName}" of '
    '"{deal:DashName}"'
)
def step_closed_alert(context, user, severity, loan, deal):
    ensure_world(context)
    found = [
        view
        for view in september_alerts()
        if view.severity.value == severity
        and (deal, loan) in {(c.deal, c.loan) for c in view.loans}
    ]
    assert len(found) == 1, f"{len(found)} {severity} alerts for loan {loan} of {deal}"
    clock.set_now(CLOSED_AT)
    in_session(lambda db: alerts.close(db, user=user, alert_id=found[0].id, note=CLOSE_NOTE))
    clock.set_now(READ_AT)


@when('"{user:DashName}" opens the book view for {period:DashMonth}')
def step_opens_book(context, user, period):
    ensure_world(context)
    month = f"{period.year:04d}-{period.month:02d}"
    open_page(context, user, f"/book/{month}", '[data-testid="book"]')
    heading = context.page.locator('[data-testid="book-month"]').inner_text()
    assert heading == period.label, heading
    # The grid draws its rows after the page has its data.
    context.page.wait_for_selector(
        '[data-testid="book"] .ag-row, [data-testid="book"] .empty', timeout=15_000
    )
    screenshot(context, "book")
    assert_no_page_problems(context)


@then("the alerts appear in this order:")
def step_alerts_in_order(context):
    shown = [(row["deal"], row["loan"], row["severity"]) for row in book_rows(context)]
    expected = [(row["deal"], row["loan"], row["severity"]) for row in context.table]
    assert shown == expected, f"shown:\n{shown}"


@then("each alert shows its exposure and its state")
def step_each_shows_exposure_and_state(context):
    held = in_session(lambda db: exposure_by_deal(db, BOOK_DATE))
    rows = book_rows(context)
    assert rows, "no alerts shown"
    for row in rows:
        assert row["exposure"] == format_money(held[row["deal"]]), row
        assert row["state"] == "new", row


@then('{count:d} alerts are listed and the first is loan "{loan:DashName}" of "{deal:DashName}"')
def step_count_and_first(context, count, loan, deal):
    rows = book_rows(context)
    assert len(rows) == count, f"{len(rows)} rows: {rows}"
    assert (rows[0]["loan"], rows[0]["deal"]) == (loan, deal), rows[0]
    count_text = context.page.locator('[data-testid="book-count"]').inner_text()
    assert count_text == f"{count} open alerts", count_text


# ---------------------------------------------------------------------------- the loan view


@given('loan "{loan:DashName}"\'s workout strategy is waiting for review')
def step_workout_waiting(context, loan):
    assert loan == "2", "only loan 2's comment is recorded"
    ensure_extracted(context, unsettled_workout=True)
    view = in_session(lambda db: comment_view(db, "pat", deal=F1, loan=loan, period=SEPTEMBER))
    assert view is not None and view.fields["workout_strategy"].status == "needs review", view


@when('"{user:DashName}" opens loan "{loan:DashName}" of "{deal:DashName}"')
def step_opens_loan_of(context, user, loan, deal):
    ensure_extracted(context)
    context.deal = deal
    open_page(context, user, f"/loan/{quote(deal)}/{quote(loan)}", '[data-testid="loan"]')
    heading = context.page.locator('[data-testid="loan-title"]').inner_text()
    assert heading == f"Loan {loan} of {deal}", heading
    as_of = context.page.locator('[data-testid="as-of"]').inner_text()
    assert as_of == clock.today().isoformat(), f"the page reads the panel as of {as_of}"
    screenshot(context, f"loan-{loan}")
    assert_no_page_problems(context)


@then(
    'the borrower request "{value:DashName}" is shown with sentence {number:d} of the '
    "{period:DashMonth} comment"
)
def step_request_with_sentence(context, value, number, period):
    month = context.page.locator('[data-testid="comment-month"]').inner_text()
    assert month == period.label, month
    row = value_row(context, "comment-values", "borrower_request")
    assert row.locator('[data-testid="value"]').inner_text() == value
    assert row.locator('[data-testid="sentence-number"]').inner_text() == f"sentence {number}"
    stored = in_session(
        lambda db: comment_view(db, "pat", deal=context.deal, loan="2", period=period)
    )
    assert stored is not None, "no extraction stored"
    sentence = [item.text for item in split(stored.comment)][number - 1]
    assert row.locator('[data-testid="evidence"]').inner_text() == sentence


@then("a link opens the {period:DashMonth} 10-D on EDGAR")
def step_link_to_10d(context, period):
    def filing(db):
        deal = by_name(db, context.deal)
        assert deal is not None, context.deal
        return db.scalars(
            select(Filing).where(
                Filing.deal_id == deal.id, Filing.form == "10-D", Filing.period == period.first_day
            )
        ).one()

    report = in_session(filing)
    assert report.primary_document, f"{report.accession} names no primary document"
    expected = (
        f"https://www.sec.gov/Archives/edgar/data/{report.cik}/"
        f"{report.accession.replace('-', '')}/{report.primary_document}"
    )
    link = context.page.locator('[data-testid="comment-filing"]')
    assert link.get_attribute("href") == expected, link.get_attribute("href")
    assert link.get_attribute("target") == "_blank"
    assert link.inner_text() == f"10-D {report.accession} on EDGAR", link.inner_text()


def passed_checks(row) -> list[str]:
    """The checks a value row shows as passed, in order."""
    checks = row.locator('[data-testid="check"]')
    return [
        checks.nth(index).inner_text().strip()
        for index in range(checks.count())
        if checks.nth(index).get_attribute("data-outcome") == "passed"
    ]


@then('the ending balance shows "{first}", "{second}" and "{third}"')
def step_balance_shows(context, first, second, third):
    row = value_row(context, "tape-values", "ending_balance")
    shown = texts(row.locator('[data-testid="check"]')) + texts(
        row.locator('[data-testid="loaded"]')
    )
    passed = passed_checks(row) + texts(row.locator('[data-testid="loaded"]'))
    missing = [text for text in (first, second, third) if text not in passed]
    assert not missing, f"the ending balance shows {shown}; not passed: {missing}"


@then('the borrower request shows "{first}" and "{second}"')
def step_request_shows(context, first, second):
    row = value_row(context, "comment-values", "borrower_request")
    shown = passed_checks(row)
    assert shown == [first, second], shown


@then('the workout strategy shows "{text}" instead of a value')
def step_workout_shows(context, text):
    row = value_row(context, "comment-values", "workout_strategy")
    assert row.locator('[data-testid="value"]').count() == 0, "a value is shown"
    assert row.locator('[data-testid="status"]').inner_text() == text
    assert row.locator('[data-testid="check"]').count() == 0, "a value is shown as verified"


@then('the most recent financials show their end date of {day:DashDay} and "{age}"')
def step_financials_show(context, day, age):
    section = context.page.locator('[data-testid="financials"]')
    assert section.locator('[data-testid="financials-end"]').inner_text() == day.isoformat()
    assert section.locator('[data-testid="financials-age"]').inner_text() == age
