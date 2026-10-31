import { act, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { HistoryMonth } from "./api";
import { HistoryChart, NOTICE, TRADINGVIEW } from "./HistoryChart";
import { axisMoney, axisRatio } from "./historyData";
import { charts, type FakeChart, LineSeries } from "./test-chart";
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
  it("draws on the page's colours, without scrolling or zooming, showing every month", () => {
    render(<HistoryChart history={loanHistory()} asOf="2026-10-28" />);
    const chart = drawn();
    expect(chart.element).toBe(screen.getByTestId("history-chart"));
    expect(chart.options).toEqual({
      autoSize: true,
      layout: {
        background: { type: "solid", color: "#f6f7f4" },
        textColor: "#55605a",
        fontFamily: getComputedStyle(chart.element).fontFamily,
        attributionLogo: true,
        panes: { separatorColor: "#c9d1cb" },
      },
      grid: { vertLines: { visible: false }, horzLines: { color: "#c9d1cb" } },
      rightPriceScale: { borderColor: "#c9d1cb" },
      timeScale: { borderColor: "#c9d1cb", fixLeftEdge: true, fixRightEdge: true },
      handleScroll: false,
      handleScale: false,
    });
    expect(chart.fitted).toBe(true);
  });

  it("draws the balance and the DSCR in panes of their own, a line per run of months", () => {
    render(<HistoryChart history={loanHistory()} asOf="2026-10-28" />);
    const series = drawn().series;
    expect(series).toHaveLength(3);
    const [balance, before, after] = series;
    const line = { color: "#1d4f8a", lineWidth: 2, pointMarkersVisible: true };
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
        },
        data: [
          { time: "2026-07-01", value: 30400000 },
          { time: "2026-08-01", value: 30200000 },
          { time: "2026-09-01", value: 30000000 },
        ],
      }),
    );
    // August reports no DSCR: July and September are lines of their own, not joined across it.
    const dscr = { type: "custom", formatter: axisRatio };
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
        },
        data: [{ time: "2026-07-01", value: 1.12 }, { time: "2026-08-01" }, { time: "2026-09-01" }],
      }),
    );
    expect(after).toEqual(
      expect.objectContaining({
        definition: LineSeries,
        pane: 1,
        options: { ...line, ...named, lineVisible: false, title: "DSCR", priceFormat: dscr },
        data: [{ time: "2026-07-01" }, { time: "2026-08-01" }, { time: "2026-09-01", value: 0.96 }],
      }),
    );
  });

  it("draws a run of two months as a line and a lone month as its point", () => {
    const history = loanHistory();
    history.months[1] = { ...(history.months[1] as HistoryMonth), dscr: "1.05" };
    history.months[2] = { ...(history.months[2] as HistoryMonth), ending_balance: null };
    render(<HistoryChart history={history} asOf="2026-10-28" />);
    expect(drawn().series.map((line) => [line.pane, line.options.lineVisible])).toEqual([
      [0, true],
      [1, true],
    ]);
  });

  it("marks each payment status change on the balance line that has the month", () => {
    const history = loanHistory();
    history.months[1] = { ...(history.months[1] as HistoryMonth), ending_balance: null };
    render(<HistoryChart history={history} asOf="2026-10-28" />);
    const [july, september, ...dscr] = drawn().series;
    const marker = { position: "aboveBar", shape: "arrowDown", color: "#9a5b00" };
    expect(july?.markers).toEqual([]);
    // August reports no balance: its change is marked on the last balance line.
    expect(september?.markers).toEqual([
      { ...marker, time: "2026-09-01", text: "30 days" },
      { ...marker, time: "2026-08-01", text: "grace or late" },
    ]);
    expect(dscr.map((line) => line.markers)).toEqual([[], []]);
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
  });

  it("credits TradingView with its notice and a link, as its licence requires", () => {
    render(<HistoryChart history={loanHistory()} asOf="2026-10-28" />);
    const credit = screen.getByTestId("chart-attribution");
    expect(NOTICE).toBe("TradingView Lightweight Charts™ Copyright (c) 2025 TradingView, Inc.");
    expect(credit.textContent).toBe(`Chart: ${NOTICE} https://www.tradingview.com/`);
    expect(within(credit).getByRole("link").getAttribute("href")).toBe(TRADINGVIEW);
    expect(TRADINGVIEW).toBe("https://www.tradingview.com/");
    expect(drawn().options).toMatchObject({ layout: { attributionLogo: true } });
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
