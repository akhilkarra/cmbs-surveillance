import { act, render, renderHook, screen, within } from "@testing-library/react";
import type { AutoscaleInfo } from "lightweight-charts";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { HistoryMonth } from "./api";
import {
  DARK_MODE,
  HistoryChart,
  NOTICE,
  scaled,
  THEME,
  TRADINGVIEW,
  useThemeChanges,
} from "./HistoryChart";
import { axisMoney, axisRatio, RATIO_SPAN } from "./historyData";
import styles from "./styles.css?raw";
import { charts, colorScheme, type FakeChart, type FakeSeries, LineSeries } from "./test-chart";
import { historyMonth, loanHistory } from "./test-data";

const TOKENS = {
  "--paper": "#f6f7f4",
  "--muted": "#55605a",
  "--rule": "#c9d1cb",
  "--link": "#1d4f8a",
  "--medium": "#9a5b00",
};

beforeEach(() => {
  // A browser reads a custom property with the space after its colon ("--paper: #f6f7f4" is
  // " #f6f7f4"); jsdom trims it, so the page's tokens are served as a browser serves them.
  const read = CSSStyleDeclaration.prototype.getPropertyValue;
  vi.spyOn(CSSStyleDeclaration.prototype, "getPropertyValue").mockImplementation(function (
    this: CSSStyleDeclaration,
    name: string,
  ) {
    return name in TOKENS ? ` ${TOKENS[name as keyof typeof TOKENS]}` : read.call(this, name);
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

function drawn(): FakeChart {
  expect(charts).toHaveLength(1);
  return charts[0] as FakeChart;
}

function hover(time: unknown) {
  const chart = drawn();
  act(() => chart.hover?.({ time }));
}

function legend() {
  const shown = screen.getByTestId("history-legend");
  return {
    month: shown.getAttribute("data-month"),
    hovered: shown.getAttribute("data-hovered"),
    text: shown.textContent,
  };
}

describe("HistoryChart", () => {
  it("draws on the page's colours, without scrolling, zooming or a logo over the plot", () => {
    render(<HistoryChart history={loanHistory()} asOf="2026-10-28" />);
    const chart = drawn();
    expect(chart.element).toBe(screen.getByTestId("history-chart"));
    expect(chart.options).toEqual({
      autoSize: true,
      layout: {
        background: { type: "solid", color: "#f6f7f4" },
        textColor: "#55605a",
        fontFamily: getComputedStyle(chart.element).fontFamily,
        attributionLogo: false,
        panes: { separatorColor: "#c9d1cb" },
      },
      grid: { vertLines: { visible: false }, horzLines: { color: "#c9d1cb" } },
      rightPriceScale: { borderColor: "#c9d1cb" },
      timeScale: {
        borderColor: "#c9d1cb",
        fixLeftEdge: true,
        fixRightEdge: true,
        lockVisibleTimeRangeOnResize: true,
      },
      handleScroll: false,
      handleScale: false,
    });
    expect(chart.fitted).toBe(true);
  });

  it("draws the balance and the DSCR in panes of their own, a line per run of months", () => {
    render(<HistoryChart history={loanHistory()} asOf="2026-10-28" />);
    const series = drawn().series;
    expect(series).toHaveLength(4);
    const [balance, before, after] = series;
    const line = {
      color: "#1d4f8a",
      lineWidth: 2,
      pointMarkersVisible: true,
      pointMarkersRadius: 4,
    };
    const named = { lastValueVisible: true, priceLineVisible: true };
    expect(balance).toEqual(
      expect.objectContaining({
        definition: LineSeries,
        pane: 0,
        options: {
          ...line,
          ...named,
          lineVisible: true,
          title: "ending balance",
          priceFormat: { type: "custom", formatter: axisMoney },
          autoscaleInfoProvider: expect.any(Function),
        },
        // The top of the balance pane is the status lane's.
        scale: { scaleMargins: { top: 0.32, bottom: 0.08 } },
        data: [
          { time: "2026-07-01", value: 30400000 },
          { time: "2026-08-01", value: 30200000 },
          { time: "2026-09-01", value: 30000000 },
        ],
        markers: [],
      }),
    );
    // August reports no DSCR: July and September are lines of their own, not joined across it.
    const dscr = { type: "custom", formatter: axisRatio };
    const clear = { scaleMargins: { top: 0.18, bottom: 0.18 } };
    expect(before).toEqual(
      expect.objectContaining({
        definition: LineSeries,
        pane: 1,
        options: {
          ...line,
          lineVisible: false,
          title: "",
          lastValueVisible: false,
          priceLineVisible: false,
          priceFormat: dscr,
          autoscaleInfoProvider: expect.any(Function),
        },
        scale: clear,
        data: [{ time: "2026-07-01", value: 1.12 }, { time: "2026-08-01" }, { time: "2026-09-01" }],
      }),
    );
    expect(after).toEqual(
      expect.objectContaining({
        definition: LineSeries,
        pane: 1,
        options: {
          ...line,
          ...named,
          lineVisible: false,
          title: "DSCR",
          priceFormat: dscr,
          autoscaleInfoProvider: expect.any(Function),
        },
        scale: clear,
        data: [{ time: "2026-07-01" }, { time: "2026-08-01" }, { time: "2026-09-01", value: 0.96 }],
      }),
    );
  });

  it("widens each measure's axis when its value never changes", () => {
    render(<HistoryChart history={loanHistory()} asOf="2026-10-28" />);
    const [balance, dscr] = drawn().series as [FakeSeries, FakeSeries];
    type Provider = (values: () => AutoscaleInfo | null) => AutoscaleInfo | null;
    const flat = (value: number) => () => ({
      priceRange: { minValue: value, maxValue: value },
      margins: { above: 2, below: 2 },
    });
    expect((balance.options.autoscaleInfoProvider as Provider)(flat(37500000))).toEqual({
      priceRange: { minValue: 37125000, maxValue: 37875000 },
      margins: { above: 2, below: 2 },
    });
    expect((dscr.options.autoscaleInfoProvider as Provider)(flat(0.03))).toEqual({
      priceRange: { minValue: 0, maxValue: 0.1 },
      margins: { above: 2, below: 2 },
    });
    // A series with nothing to show leaves the axis to the others.
    expect(scaled(null, RATIO_SPAN)).toBeNull();
  });

  it("draws two months running as a line, and a month alone as its point", () => {
    const history = loanHistory();
    history.months[1] = { ...(history.months[1] as HistoryMonth), dscr: "1.05" };
    history.months[2] = { ...(history.months[2] as HistoryMonth), ending_balance: null };
    render(<HistoryChart history={history} asOf="2026-10-28" />);
    const [balanceRun, dscrRun] = drawn().series;
    // July and August's balances are a line; the DSCR, reported every month, is one line.
    expect([balanceRun?.pane, balanceRun?.options.lineVisible]).toEqual([0, true]);
    expect([dscrRun?.pane, dscrRun?.options.lineVisible]).toEqual([1, true]);
    render(<HistoryChart history={loanHistory()} asOf="2026-10-28" />);
    const [, alone] = (charts[1] as FakeChart).series;
    expect(alone?.options).toMatchObject({ lineVisible: false, pointMarkersRadius: 4 });
  });

  it("marks each payment status change over its own month, balance reported or not", () => {
    const history = loanHistory();
    history.months[1] = { ...(history.months[1] as HistoryMonth), ending_balance: null };
    render(<HistoryChart history={history} asOf="2026-10-28" />);
    const series = drawn().series;
    const lane = series.at(-1) as FakeSeries;
    expect(lane).toEqual(
      expect.objectContaining({
        definition: LineSeries,
        pane: 0,
        options: {
          priceScaleId: "status",
          lineVisible: false,
          pointMarkersVisible: false,
          crosshairMarkerVisible: false,
          lastValueVisible: false,
          priceLineVisible: false,
        },
        scale: { scaleMargins: { top: 0.2, bottom: 0.76 } },
        // A point at every month, so each marker is drawn over its own month.
        data: [
          { time: "2026-07-01", value: 0 },
          { time: "2026-08-01", value: 0 },
          { time: "2026-09-01", value: 0 },
        ],
      }),
    );
    const marker = { position: "aboveBar", shape: "arrowDown", color: "#9a5b00" };
    expect(lane.markers).toEqual([
      { ...marker, time: "2026-08-01", text: "grace or late" },
      { ...marker, time: "2026-09-01", text: "30 days" },
    ]);
    expect(series.slice(0, -1).map((line) => line.markers)).toEqual([[], [], [], []]);
  });

  it("names the latest month until a month is hovered, then that month and its filing", () => {
    render(<HistoryChart history={loanHistory()} asOf="2026-10-28" />);
    expect(legend()).toEqual({
      month: "2026-09",
      hovered: "false",
      text:
        "September 2026: ending balance $30,000,000.00, DSCR 0.96x, payment status 30 days (1), " +
        "changed to 30 days; from ABS-EE 0009900001-26-000419, filed 2026-09-29",
    });
    expect(screen.getByTestId("legend-filing").textContent).toBe(
      "from ABS-EE 0009900001-26-000419, filed 2026-09-29",
    );
    hover("2026-08-01");
    expect(legend()).toEqual({
      month: "2026-08",
      hovered: "true",
      text:
        "August 2026: ending balance $30,200,000.00, DSCR not reported, payment status in grace " +
        "period (A), changed to grace or late; from ABS-EE 0009900001-26-000417, filed 2026-08-29",
    });
    hover({ year: 2026, month: 7, day: 1 });
    expect(legend()).toEqual({
      month: "2026-07",
      hovered: "true",
      text:
        "July 2026: ending balance $30,400,000.00, DSCR 1.12x, payment status current (0); " +
        "from ABS-EE 0009900001-26-000415, filed 2026-07-29",
    });
    hover(undefined);
    expect(legend().month).toBe("2026-09");
    expect(legend().hovered).toBe("false");
  });

  it("lists each month in a table: values, status, the change and the filing", () => {
    render(<HistoryChart history={loanHistory()} asOf="2026-09-30" />);
    const table = screen.getByTestId("history-table");
    expect(table.querySelector("caption")?.textContent).toBe(
      "Each month as published by 2026-09-30",
    );
    expect([...table.querySelectorAll("thead th")].map((cell) => cell.textContent)).toEqual([
      "Month",
      "Ending balance",
      "DSCR",
      "Payment status",
      "Status changed to",
      "Filing",
    ]);
    const rows = [...table.querySelectorAll<HTMLElement>("tbody tr")];
    expect(rows.map((row) => row.getAttribute("data-month"))).toEqual([
      "2026-07",
      "2026-08",
      "2026-09",
    ]);
    expect(rows.map((row) => [...row.children].map((cell) => cell.textContent))).toEqual([
      [
        "July 2026",
        "$30,400,000.00",
        "1.12x",
        "current (0)",
        "",
        "ABS-EE 0009900001-26-000415, filed 2026-07-29",
      ],
      [
        "August 2026",
        "$30,200,000.00",
        "not reported",
        "in grace period (A)",
        "grace or late",
        "ABS-EE 0009900001-26-000417, filed 2026-08-29",
      ],
      [
        "September 2026",
        "$30,000,000.00",
        "0.96x",
        "30 days (1)",
        "30 days",
        "ABS-EE 0009900001-26-000419, filed 2026-09-29",
      ],
    ]);
    const august = rows[1] as HTMLElement;
    expect(within(august).getByTestId("dscr").getAttribute("data-reported")).toBe("false");
    expect(within(august).getByTestId("balance").getAttribute("data-reported")).toBe("true");
    const link = within(august).getByTestId("filing");
    expect(link.getAttribute("href")).toBe(
      "https://www.sec.gov/Archives/edgar/data/9900001/000990000126000417/ex102.xml",
    );
    expect([link.getAttribute("target"), link.getAttribute("rel")]).toEqual([
      "_blank",
      "noreferrer",
    ]);
    expect(
      within(rows[0] as HTMLElement)
        .getByTestId("dscr")
        .getAttribute("data-reported"),
    ).toBe("true");
  });

  it("says when the tape leaves the loan out of a month", () => {
    const history = loanHistory();
    history.months[1] = historyMonth("2026-08", "August 2026", {
      on_tape: false,
      ending_balance: null,
      dscr: null,
      payment_status: null,
    });
    render(<HistoryChart history={history} asOf="2026-10-28" />);
    const august = screen
      .getByTestId("history-table")
      .querySelector<HTMLElement>('[data-month="2026-08"]') as HTMLElement;
    expect(within(august).getByTestId("balance").textContent).toBe("not reported");
    expect(within(august).getByTestId("balance").getAttribute("data-reported")).toBe("false");
    expect(within(august).getByTestId("status").textContent).toBe("loan not on the tape");
    expect(within(august).getByTestId("filing").textContent).toBe("ABS-EE 0009900001-26-000417");
  });

  it("says when no tape was filed for a month, in the table and the legend", () => {
    const history = loanHistory();
    history.months[1] = historyMonth("2026-08", "August 2026", {
      on_tape: false,
      ending_balance: null,
      dscr: null,
      payment_status: null,
      source: null,
    });
    history.status_changes = [];
    render(<HistoryChart history={history} asOf="2026-10-28" />);
    const august = screen
      .getByTestId("history-table")
      .querySelector<HTMLElement>('[data-month="2026-08"]') as HTMLElement;
    expect([...august.children].map((cell) => cell.textContent)).toEqual([
      "August 2026",
      "not reported",
      "not reported",
      "no tape filed",
      "",
      "no tape filed",
    ]);
    expect(within(august).queryByTestId("filing")).toBeNull();
    hover("2026-08-01");
    expect(legend().text).toBe(
      "August 2026: ending balance not reported, DSCR not reported, payment status no tape " +
        "filed; no tape filed",
    );
  });

  it("says what a gap in a line means", () => {
    render(<HistoryChart history={loanHistory()} asOf="2026-10-28" />);
    const gap =
      "A gap in a line is a month that does not report the value; the table says whether the " +
      "loan was not on that month's tape or no tape was filed.";
    expect(screen.getByTestId("gap-note").textContent).toBe(gap);
    expect(screen.getByTestId("gap-note").parentElement?.textContent).toBe(
      `${gap} Chart: ${NOTICE} ${TRADINGVIEW}`,
    );
  });

  it("counts the theme's changes, and stops watching when the page closes", async () => {
    const disconnect = vi.spyOn(MutationObserver.prototype, "disconnect");
    const { result, unmount } = renderHook(() => useThemeChanges());
    expect(THEME).toEqual(["data-theme"]);
    expect(result.current).toBe(0);
    act(() => colorScheme.change());
    expect(result.current).toBe(1);
    await act(async () => document.documentElement.setAttribute("data-theme", "dark"));
    expect(result.current).toBe(2);
    expect(disconnect).not.toHaveBeenCalled();
    unmount();
    expect(disconnect).toHaveBeenCalledTimes(1);
    document.documentElement.removeAttribute("data-theme");
  });

  it("keeps the table in a box of its own that scrolls sideways, never the page", () => {
    render(<HistoryChart history={loanHistory()} asOf="2026-10-28" />);
    expect(screen.getByTestId("history-table").parentElement?.className).toBe("table-scroll");
    expect(styles).toMatch(/\.table-scroll \{\n {2}max-width: 100%;\n {2}overflow-x: auto;\n\}/);
  });

  it("credits TradingView with its notice and a link, as its licence requires", () => {
    render(<HistoryChart history={loanHistory()} asOf="2026-10-28" />);
    const credit = screen.getByTestId("chart-attribution");
    expect(NOTICE).toBe("TradingView Lightweight Charts™ Copyright (c) 2025 TradingView, Inc.");
    expect(credit.textContent).toBe(`Chart: ${NOTICE} https://www.tradingview.com/`);
    expect(within(credit).getByRole("link").getAttribute("href")).toBe(TRADINGVIEW);
    expect(TRADINGVIEW).toBe("https://www.tradingview.com/");
    // The notice and link under the chart are the credit; no logo covers the plot.
    expect(drawn().options).toMatchObject({ layout: { attributionLogo: false } });
  });

  it("draws the chart again when the system's colour scheme or the page's theme changes", async () => {
    const { unmount } = render(<HistoryChart history={loanHistory()} asOf="2026-10-28" />);
    const first = drawn();
    expect(colorScheme.queries).toEqual([DARK_MODE]);
    expect(DARK_MODE).toBe("(prefers-color-scheme: dark)");
    act(() => colorScheme.change());
    expect(first.removed).toBe(true);
    expect(charts).toHaveLength(2);
    expect(screen.getByTestId("history-chart")).toBe((charts[1] as FakeChart).element);
    await act(async () => document.documentElement.setAttribute("data-theme", "dark"));
    expect(charts).toHaveLength(3);
    await act(async () => document.documentElement.setAttribute("lang", "en"));
    expect(charts).toHaveLength(3);
    unmount();
    expect(colorScheme.listeners.size).toBe(0);
    await act(async () => document.documentElement.setAttribute("data-theme", "light"));
    expect(charts).toHaveLength(3);
    document.documentElement.removeAttribute("data-theme");
    document.documentElement.removeAttribute("lang");
  });

  it("removes the chart when the page closes, and redraws it for another history", () => {
    const { rerender, unmount } = render(
      <HistoryChart history={loanHistory()} asOf="2026-10-28" />,
    );
    const first = drawn();
    rerender(<HistoryChart history={loanHistory()} asOf="2026-10-28" />);
    expect(first.removed).toBe(true);
    expect(charts).toHaveLength(2);
    const second = charts[1] as FakeChart;
    expect(second.removed).toBe(false);
    unmount();
    expect(second.removed).toBe(true);
  });

  it("keeps the chart while the pointer moves", () => {
    const history = loanHistory();
    const { rerender } = render(<HistoryChart history={history} asOf="2026-10-28" />);
    hover("2026-08-01");
    rerender(<HistoryChart history={history} asOf="2026-10-28" />);
    expect(charts).toHaveLength(1);
    expect(drawn().removed).toBe(false);
  });

  it("says when no tape reporting the loan had been filed, and draws nothing", () => {
    render(<HistoryChart history={{ months: [], status_changes: [] }} asOf="2026-07-28" />);
    expect(screen.getByTestId("history-empty").textContent).toBe(
      "No tape reporting this loan had been filed by 2026-07-28.",
    );
    expect(screen.queryByTestId("history")).toBeNull();
    expect(charts).toHaveLength(0);
  });
});
