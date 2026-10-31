"""Steps for features/surfaces/loan_history_chart.feature, run in Chromium through Playwright.

**What the page reads.** The Background loads its months the way the dashboard scenarios do
(features/steps/dashboard.py): through the refresh service, replaying the synthetic EDGAR in
tests/fixtures/refresh/, with each month's tape changed so that the loan reports exactly what the
table says (a blank DSCR is a tape that does not report one). One backfill run loads them the
morning after the last is filed, because alerts need settings in force and the fixture world's
are approved on 2026-09-15; each month is still public only from its own filing date, which the
step checks against the table. The page is then read on 2026-10-28 at noon, so September's tape
(filed 2026-09-29) is the latest and October's (filed 2026-10-29) is not yet published.

**How the chart is read.** The chart draws on a canvas, so the steps read it the way a person
does: the pointer sweeps across the chart, and the legend over it names the month under the
pointer, its values and the filing they came from. The table under the chart, the chart's text
alternative, gives the same months in words, with each payment status change.
"""

import datetime as dt
import xml.etree.ElementTree as ET

from behave import given, then  # pyright: ignore[reportAttributeAccessIssue]
from sqlalchemy import select

from cmbs import clock
from cmbs.absee.schedule_al import NAMESPACE
from cmbs.deals.service import by_name, get_or_create
from cmbs.domain import Period, format_money, money
from cmbs.edgar.replay import Replay
from cmbs.filings.tables import Filing
from cmbs.panel import service as panel
from cmbs.refresh import service as refresh
from cmbs.refresh.sources import FirstRecorded, Sources
from cmbs.testing.bdd import in_session
from cmbs.testing.world import FIXTURE_CIKS
from features.steps.dashboard import (
    EDGAR,
    OPERATOR,
    READ_AT,
    Archive,
    exhibit_url,
    with_tags,
)

# The NOTICE file of TradingView Lightweight Charts and the link its licence asks for.
NOTICE = "TradingView Lightweight Charts™ Copyright (c) 2025 TradingView, Inc."
TRADINGVIEW = "https://www.tradingview.com/"
BALANCE_TAG = "reportPeriodEndActualBalanceAmount"
STATUS_TAG = "paymentStatusLoanCode"
DSCR_TAG = "mostRecentDebtServiceCoverageNetOperatingIncomePercentage"
# The backfill runs the morning after the last month is filed.
REFRESH_TIME = dt.time(9, 0, tzinfo=dt.UTC)


# ---------------------------------------------------------------------------- Background


def without_property_tag(content: bytes, loan: str, tag: str) -> bytes:
    """The tape with the loan's property no longer reporting `tag`."""
    root = ET.fromstring(content)
    for asset in root.findall(NAMESPACE + "assets"):
        if (asset.findtext(NAMESPACE + "assetNumber") or "").strip() != loan:
            continue
        prop = asset.find(NAMESPACE + "property")
        assert prop is not None, f"loan {loan} has no property"
        for element in prop.findall(NAMESPACE + tag):
            prop.remove(element)
    ET.register_namespace("", NAMESPACE.strip("{}"))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def month_tape(content: bytes, loan: str, row) -> bytes:
    """The month's tape with the loan reporting the table row's balance, DSCR and status."""
    tags = {
        BALANCE_TAG: str(money(row["ending balance"])),
        STATUS_TAG: row["payment status"].strip(),
    }
    dscr = row["DSCR"].strip()
    if dscr:
        tags[f"property/{DSCR_TAG}"] = dscr
    else:
        content = without_property_tag(content, loan, DSCR_TAG)
    return with_tags(content, loan, tags)


def stored_rows(deal: str, loan: str, day: dt.date) -> dict[Period, tuple]:
    """The loan's months in the panel on `day`: (filing date, balance, DSCR, status) each."""

    def read(db):
        deal_row = panel.deal_named(db, deal)
        return {
            Period.of(row.period): (
                row.filing_date,
                row.ending_balance,
                row.dscr,
                row.payment_status,
            )
            for row in panel.as_of(db, day, deal_id=deal_row.id, loan=loan)
        }

    return in_session(read)


@given('loan "{loan:DashName}" of "{deal:DashName}" was reported in these months:')
def step_loan_reported_in_months(context, loan, deal):
    archive = Archive()
    months: list[Period] = []
    for row in context.table:
        period = Period.parse(row["month"])
        url = exhibit_url(deal, period, "ABS-EE")
        archive.replaced[url] = month_tape(archive.recorded.get_bytes(url), loan, row)
        months.append(period)
    in_session(lambda db: get_or_create(db, deal, cik=FIXTURE_CIKS[deal]))
    # Alerts need settings in force, and the fixture world's are approved on 2026-09-15, so the
    # months are backfilled the morning after the last one is filed. Each month still becomes
    # public on its own filing date: that, not the day it was loaded, is what the panel reads.
    last_filed = max(dt.date.fromisoformat(row["filed"]) for row in context.table)
    clock.set_now(dt.datetime.combine(last_filed + dt.timedelta(days=1), REFRESH_TIME))
    sources = Sources(
        edgar=FirstRecorded(*(Replay(directory) for directory in EDGAR)), archive=archive
    )
    report = refresh.backfill(OPERATOR, deal, min(months), max(months), sources=sources)
    loaded = [(m["period"], m["outcome"]) for d in report.run.summary["deals"] for m in d["months"]]
    assert loaded == [(p.label, "loaded") for p in months], report.text()
    clock.set_now(READ_AT)
    stored = stored_rows(deal, loan, clock.today())
    expected = {
        Period.parse(row["month"]): (
            dt.date.fromisoformat(row["filed"]),
            money(row["ending balance"]),
            money(row["DSCR"]) if row["DSCR"].strip() else None,
            row["payment status"].strip(),
        )
        for row in context.table
    }
    assert stored == expected, f"the panel holds {stored}"
    context.history_months = months
    # The loan page reads this world, not the dashboard's (features/steps/dashboard.py).
    context.loan_world = lambda _context: None


# ---------------------------------------------------------------------------- reading the chart


def table_rows(context) -> list[dict[str, str]]:
    """The history table's months in order: each cell's text by its test id, and the month."""
    return context.page.evaluate(
        """() => [...document.querySelectorAll('[data-testid="history-table"] tbody tr')]
            .map(row => Object.fromEntries([
                ['month', row.getAttribute('data-month')],
                ['label', row.querySelector('th').innerText.trim()],
                ['dscr_reported', row.querySelector('[data-testid="dscr"]')
                    .getAttribute('data-reported')],
                ['filing_href', row.querySelector('[data-testid="filing"]').getAttribute('href')],
                ...[...row.querySelectorAll('[data-testid]')]
                    .map(cell => [cell.getAttribute('data-testid'), cell.innerText.trim()]),
            ]))"""
    )


READ_LEGEND = """() => {
    const legend = document.querySelector('[data-testid="history-legend"]');
    const text = (id) => legend.querySelector(`[data-testid="${id}"]`)?.innerText.trim() ?? null;
    return {
        hovered: legend.getAttribute('data-hovered'),
        month: legend.getAttribute('data-month'),
        label: text('legend-month'),
        balance: text('legend-balance'),
        dscr: text('legend-dscr'),
        status: text('legend-status'),
        change: text('legend-change'),
        filing: text('legend-filing'),
    };
}"""


def hovered_months(context) -> dict[str, dict[str, str | None]]:
    """Sweep the pointer across the chart's balance pane and keep what the legend says for each
    month it names while hovered, by month ('2026-08')."""
    if getattr(context, "hovered", None) is None:
        page = context.page
        chart = page.locator('[data-testid="history-chart"]')
        chart.scroll_into_view_if_needed()
        box = chart.bounding_box()
        assert box is not None, "the history chart is not drawn"
        found: dict[str, dict[str, str | None]] = {}
        y = box["y"] + box["height"] * 0.25
        x = box["x"] + 1
        while x < box["x"] + box["width"] - 1:
            page.mouse.move(x, y)
            legend = page.evaluate(READ_LEGEND)
            if legend["hovered"] == "true":
                found.setdefault(legend["month"], legend)
            x += 4
        page.mouse.move(0, 0)
        context.hovered = found
    return context.hovered


def named_period(context, name: str) -> Period:
    """A month the Background loaded, named without its year ('August')."""
    found = [p for p in context.history_months if p.label.split()[0] == name.strip()]
    assert len(found) == 1, f"the Background loaded no single month named {name!r}"
    return found[0]


def key(period: Period) -> str:
    return f"{period.year:04d}-{period.month:02d}"


def abs_ee(context, period: Period) -> Filing:
    def read(db):
        deal = by_name(db, context.deal)
        assert deal is not None, context.deal
        return db.scalars(
            select(Filing).where(
                Filing.deal_id == deal.id,
                Filing.form == "ABS-EE",
                Filing.period == period.first_day,
            )
        ).one()

    return in_session(read)


def words(text: str) -> list[str]:
    """'a, b and c' -> ['a', 'b', 'c']"""
    head, _, last = text.rpartition(" and ")
    return [*head.split(", "), last] if head else [last]


# ---------------------------------------------------------------------------- Then


@then("the history chart shows ending balance {balances} for {months} {year:d}")
def step_chart_shows_balances(context, balances, months, year):
    periods = [Period.parse(f"{name} {year}") for name in words(months)]
    expected = [format_money(money(amount)) for amount in words(balances)]
    assert len(periods) == len(expected), (periods, expected)
    rows = table_rows(context)
    assert [(row["month"], row["balance"]) for row in rows] == list(
        zip([key(p) for p in periods], expected, strict=True)
    ), rows
    hovered = hovered_months(context)
    assert sorted(hovered) == [key(p) for p in periods], f"the chart shows {sorted(hovered)}"
    for period, amount in zip(periods, expected, strict=True):
        legend = hovered[key(period)]
        assert (legend["label"], legend["balance"]) == (period.label, amount), legend


@then(
    "the DSCR line shows {first}x for {first_month} and {second}x for {second_month} "
    "with a gap for {gap_month}"
)
def step_dscr_line(context, first, first_month, second, second_month, gap_month):
    rows = {row["month"]: row for row in table_rows(context)}
    hovered = hovered_months(context)
    for name, ratio in ((first_month, first), (second_month, second)):
        month = key(named_period(context, name))
        assert rows[month]["dscr"] == f"{ratio}x", rows[month]
        assert hovered[month]["dscr"] == f"{ratio}x", hovered[month]
    gap = key(named_period(context, gap_month))
    assert (rows[gap]["dscr"], rows[gap]["dscr_reported"]) == ("not reported", "false"), rows[gap]
    assert hovered[gap]["dscr"] == "not reported", hovered[gap]


@then("each month's point names the filing it came from")
def step_points_name_filings(context):
    rows = {row["month"]: row for row in table_rows(context)}
    hovered = hovered_months(context)
    assert sorted(hovered) == sorted(rows), f"hovered {sorted(hovered)}, listed {sorted(rows)}"
    for period in context.history_months:
        filing = abs_ee(context, period)
        named = f"ABS-EE {filing.accession}, filed {filing.filing_date.isoformat()}"
        assert hovered[key(period)]["filing"] == named, hovered[key(period)]
        row = rows[key(period)]
        assert row["filing"] == f"ABS-EE {filing.accession}", row
        assert row["filing_href"] == (
            f"https://www.sec.gov/Archives/edgar/data/{filing.cik}/"
            f"{filing.accession.replace('-', '')}/{filing.primary_document}"
        ), row


@then("the history chart ends at {period:DashMonth}")
def step_chart_ends_at(context, period):
    rows = table_rows(context)
    shown = [row["month"] for row in rows]
    assert shown and shown[-1] == key(period), f"the table lists {shown}"
    hovered = hovered_months(context)
    assert max(hovered) == key(period), f"the chart shows {sorted(hovered)}"
    later = [key(p) for p in context.history_months if p > period]
    assert later, "the Background loaded no month after it"
    assert not set(later) & (set(shown) | set(hovered)), f"later months shown: {later}"


@then('the chart marks {first:DashMonth} "{first_mark}" and {second:DashMonth} "{second_mark}"')
def step_chart_marks(context, first, first_mark, second, second_mark):
    marked = {row["month"]: row["change"] for row in table_rows(context) if row["change"]}
    assert marked == {key(first): first_mark, key(second): second_mark}, marked
    hovered = hovered_months(context)
    for period, mark in ((first, first_mark), (second, second_mark)):
        assert hovered[key(period)]["change"] == f", changed to {mark}", hovered[key(period)]
    unmarked = [month for month, legend in hovered.items() if month not in marked]
    assert all(hovered[month]["change"] is None for month in unmarked), hovered


@then("the chart shows the TradingView attribution its licence requires")
def step_chart_attribution(context):
    page = context.page
    credit = page.locator('[data-testid="chart-attribution"]')
    assert credit.is_visible(), "the chart's credit is not shown"
    assert NOTICE in credit.inner_text(), credit.inner_text()
    link = credit.locator(f'a[href="{TRADINGVIEW}"]')
    assert link.count() == 1 and link.is_visible(), "the credit does not link to TradingView"
    # The chart's own logo links to tradingview.com too (the attributionLogo option).
    logo = page.locator('[data-testid="history-chart"] a[href^="https://www.tradingview.com"]')
    assert logo.count() == 1 and logo.is_visible(), "the chart shows no TradingView logo link"
