"""Steps for features/surfaces/loan_history_chart.feature, run in Chromium through Playwright.

**What the page reads.** The Background loads its months the way the dashboard scenarios do
(features/steps/dashboard.py): through the refresh service, replaying the synthetic EDGAR in
tests/fixtures/refresh/, each month published on the day the table says and its tape changed so
that the loan reports exactly what the table says (a blank DSCR is a tape that does not report
one; features/steps/published.py). One backfill run loads them the morning after the last is
filed, because alerts need settings in force and the fixture world's are approved on 2026-09-15;
each month is still public only from its own filing date, which the step checks against the
table. The page is then read on 2026-10-28 at noon, so September's tape (filed 2026-09-29) is
the latest and October's (filed 2026-10-29) is not yet published. The same Background step
serves the loan history in `ask` (features/steps/history_ask.py), whose table adds occupancy
and financials end.

**How the chart is read.** The chart draws on a canvas, so the steps read it the way a person
does: the pointer sweeps across the chart, and the legend over it names the month under the
pointer, its values and the filing they came from. The table under the chart, the chart's text
alternative, gives the same months in words, with each payment status change.
"""

import datetime as dt
from decimal import Decimal

from behave import given, then  # pyright: ignore[reportAttributeAccessIssue]
from sqlalchemy import select

from cmbs import clock
from cmbs.deals.service import by_name
from cmbs.domain import Period, format_money, money
from cmbs.filings.tables import Filing
from cmbs.panel import service as panel
from cmbs.testing.bdd import in_session
from features.steps.dashboard import READ_AT
from features.steps.published import Publication, set_tags, tape_date

# The NOTICE file of TradingView Lightweight Charts and the link its licence asks for.
NOTICE = "TradingView Lightweight Charts™ Copyright (c) 2025 TradingView, Inc."
TRADINGVIEW = "https://www.tradingview.com/"
BALANCE_TAG = "reportPeriodEndActualBalanceAmount"
STATUS_TAG = "paymentStatusLoanCode"
DSCR_TAG = "mostRecentDebtServiceCoverageNetOperatingIncomePercentage"
# The backfill runs the morning after the last month is filed.
REFRESH_TIME = dt.time(9, 0, tzinfo=dt.UTC)


# ---------------------------------------------------------------------------- Background

OCCUPANCY_TAG = "mostRecentPhysicalOccupancyPercentage"
FINANCIALS_END_TAG = "mostRecentFinancialsEndDate"


def month_tags(row) -> dict[str, str | None]:
    """The tags the loan reports in the table row's month: balance, status and DSCR, and the
    occupancy and financials end when the table has those columns. A blank cell is a tag the
    tape does not report."""

    def cell(name: str) -> str | None:
        text = row[name].strip()
        return text or None

    tags: dict[str, str | None] = {
        BALANCE_TAG: str(money(row["ending balance"])),
        STATUS_TAG: cell("payment status"),
        f"property/{DSCR_TAG}": cell("DSCR"),
    }
    if "occupancy" in row.headings:
        tags[f"property/{OCCUPANCY_TAG}"] = cell("occupancy")
    if "financials end" in row.headings:
        end = cell("financials end")
        tags[f"property/{FINANCIALS_END_TAG}"] = (
            None if end is None else tape_date(dt.date.fromisoformat(end))
        )
    return tags


def stored_rows(deal: str, loan: str, day: dt.date) -> dict[Period, dict[str, object]]:
    """The loan's months in the panel on `day`, each with its filing date and values."""

    def read(db):
        deal_row = panel.deal_named(db, deal)
        return {
            Period.of(row.period): {
                "filed": row.filing_date,
                "ending balance": row.ending_balance,
                "DSCR": row.dscr,
                "payment status": row.payment_status,
                "occupancy": row.occupancy,
                "financials end": row.financials_end,
            }
            for row in panel.as_of(db, day, deal_id=deal_row.id, loan=loan)
        }

    return in_session(read)


# How each table column reads as the panel stores it; a blank cell is not reported.
COLUMN_READS = {
    "filed": dt.date.fromisoformat,
    "ending balance": money,
    "DSCR": money,
    "payment status": str,
    "occupancy": Decimal,
    "financials end": dt.date.fromisoformat,
}


@given('loan "{loan:DashName}" of "{deal:DashName}" was reported in these months:')
def step_loan_reported_in_months(context, loan, deal):
    """Each month is published on the day the table says (features/steps/published.py), with
    the loan reporting exactly the row's values, and all are loaded by one backfill run."""
    publication = Publication(deal)
    months: list[Period] = []
    for row in context.table:
        period = Period.parse(row["month"])
        month = publication.month(period, dt.date.fromisoformat(row["filed"]))
        month.tape.append(set_tags(loan, month_tags(row)))
        months.append(period)
    # Alerts need settings in force, and the fixture world's are approved on 2026-09-15, so the
    # months are backfilled the morning after the last one is filed. Each month still becomes
    # public on its own filing date: that, not the day it was loaded, is what the panel reads.
    last_filed = max(dt.date.fromisoformat(row["filed"]) for row in context.table)
    publication.load(dt.datetime.combine(last_filed + dt.timedelta(days=1), REFRESH_TIME))
    clock.set_now(READ_AT)
    stored = stored_rows(deal, loan, clock.today())
    assert sorted(stored) == months, f"the panel holds {sorted(stored)}"
    for row in context.table:
        held = stored[Period.parse(row["month"])]
        for column in row.headings:
            if column == "month":
                continue
            text = row[column].strip()
            expected = COLUMN_READS[column](text) if text else None
            assert held[column] == expected, f"{row['month']} {column}: the panel holds {held}"
    context.deal = deal
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
    month it names while hovered, by month ('2026-08'), with `x`: the middle of the stretch of
    the chart where the pointer is on that month (where the chart draws the month)."""
    if getattr(context, "hovered", None) is None:
        page = context.page
        chart = page.locator('[data-testid="history-chart"]')
        chart.scroll_into_view_if_needed()
        box = chart.bounding_box()
        assert box is not None, "the history chart is not drawn"
        found: dict[str, dict] = {}
        spans: dict[str, list[float]] = {}
        y = box["y"] + box["height"] * 0.25
        x = box["x"] + 1
        while x < box["x"] + box["width"] - 1:
            page.mouse.move(x, y)
            legend = page.evaluate(READ_LEGEND)
            if legend["hovered"] == "true":
                found.setdefault(legend["month"], legend)
                spans.setdefault(legend["month"], []).append(x)
            x += 4
        page.mouse.move(0, 0)
        for month, xs in spans.items():
            found[month]["x"] = (min(xs) + max(xs)) / 2
        context.hovered = found
    return context.hovered


# Whether the chart's canvases hold a pixel of the status marker colour (the page's --medium
# token) in a narrow column at each month's x: the months the chart draws a marker over.
DRAWN_MARKS = """([columns, width]) => {
    const chart = document.querySelector('[data-testid="history-chart"]');
    const hex = getComputedStyle(document.documentElement).getPropertyValue('--medium').trim();
    const colour = [1, 3, 5].map((at) => parseInt(hex.slice(at, at + 2), 16));
    const canvases = [...chart.querySelectorAll('canvas')];
    const marked = (x) => canvases.some((canvas) => {
        const box = canvas.getBoundingClientRect();
        if (x - width < box.left || x + width > box.right || box.height === 0) return false;
        const scale = canvas.width / box.width;
        const left = Math.round((x - width - box.left) * scale);
        const pixels = canvas.getContext('2d').getImageData(
            left, 0, Math.round(2 * width * scale), canvas.height).data;
        for (let at = 0; at < pixels.length; at += 4) {
            if (pixels[at + 3] > 200 && colour.every((c, i) => Math.abs(pixels[at + i] - c) < 24)) {
                return true;
            }
        }
        return false;
    });
    return columns.filter(([, x]) => marked(x)).map(([month]) => month);
}"""


def drawn_marks(context) -> list[str]:
    """The months ('2026-08') the chart draws a payment status marker over."""
    columns = [[month, legend["x"]] for month, legend in sorted(hovered_months(context).items())]
    return context.page.evaluate(DRAWN_MARKS, [columns, 6])


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
        named = f"from ABS-EE {filing.accession}, filed {filing.filing_date.isoformat()}"
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
    # The markers are drawn over those months on the chart itself, and over no other.
    drawn = drawn_marks(context)
    assert drawn == sorted(marked), f"the chart draws markers over {drawn}"
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
    # The notice and link under the chart are the credit: no logo is drawn over the plot.
    logo = page.locator('[data-testid="history-chart"] a[href^="https://www.tradingview.com"]')
    assert logo.count() == 0, "a TradingView logo covers the chart"
