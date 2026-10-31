// The loan page's history: the loan's ending balance and DSCR month by month as published on the
// page's day, each in a pane of its own over one time axis (two measures, never two scales on one
// plot), with an arrow on each month the payment status changed. Hovering a month names its
// values and the filing they came from; the table under the chart says the same in text.

import {
  ColorType,
  createChart,
  createSeriesMarkers,
  LineSeries,
  type MouseEventParams,
} from "lightweight-charts";
import { useCallback, useState } from "react";
import type { HistoryMonth, LoanHistory, StatusChange } from "./api";
import {
  axisMoney,
  axisRatio,
  balanceOf,
  balanceText,
  changeIn,
  dscrOf,
  dscrText,
  linePoints,
  monthAt,
  statusMarkers,
  statusText,
  timeKey,
} from "./historyData";

/** The attribution TradingView Lightweight Charts' licence asks for: its NOTICE file and a link
 *  to tradingview.com (the chart's own logo links there too). */
export const NOTICE = "TradingView Lightweight Charts™ Copyright (c) 2025 TradingView, Inc.";
export const TRADINGVIEW = "https://www.tradingview.com/";

/** A colour token of the page (styles.css), so the chart follows light and dark mode. */
function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function drawChart(
  element: HTMLDivElement,
  history: LoanHistory,
  onHover: (time: string | null) => void,
): () => void {
  const rule = token("--rule");
  const chart = createChart(element, {
    autoSize: true,
    layout: {
      background: { type: ColorType.Solid, color: token("--paper") },
      textColor: token("--muted"),
      fontFamily: getComputedStyle(element).fontFamily,
      attributionLogo: true,
      panes: { separatorColor: rule },
    },
    grid: { vertLines: { visible: false }, horzLines: { color: rule } },
    rightPriceScale: { borderColor: rule },
    timeScale: { borderColor: rule, fixLeftEdge: true, fixRightEdge: true },
    // The page scrolls under the pointer; the chart always shows every month.
    handleScroll: false,
    handleScale: false,
  });
  const line = { color: token("--link"), lineWidth: 2, pointMarkersVisible: true } as const;
  const balance = chart.addSeries(
    LineSeries,
    { ...line, title: "ending balance", priceFormat: { type: "custom", formatter: axisMoney } },
    0,
  );
  balance.setData(linePoints(history.months, balanceOf));
  const dscr = chart.addSeries(
    LineSeries,
    { ...line, title: "DSCR", priceFormat: { type: "custom", formatter: axisRatio } },
    1,
  );
  dscr.setData(linePoints(history.months, dscrOf));
  createSeriesMarkers(balance, statusMarkers(history.status_changes, token("--medium")));
  chart.subscribeCrosshairMove((param: MouseEventParams) => onHover(timeKey(param.time)));
  chart.timeScale().fitContent();
  return () => chart.remove();
}

/** The hovered month's values and filing, or the latest month's when none is hovered. */
export function Legend(props: { month: HistoryMonth; change?: StatusChange; hovered: boolean }) {
  const { month, change } = props;
  return (
    <p
      className="legend"
      data-testid="history-legend"
      data-month={month.month.key}
      data-hovered={props.hovered}
    >
      <strong data-testid="legend-month">{month.month.label}</strong>: ending balance{" "}
      <span data-testid="legend-balance">{balanceText(month)}</span>, DSCR{" "}
      <span data-testid="legend-dscr">{dscrText(month)}</span>, payment status{" "}
      <span data-testid="legend-status">{statusText(month)}</span>
      {change && <span data-testid="legend-change">, changed to {change.bucket}</span>}; from{" "}
      <span data-testid="legend-filing">
        {month.source.form} {month.source.accession}, filed {month.source.filing_date}
      </span>
    </p>
  );
}

function HistoryRow({ month, change }: { month: HistoryMonth; change?: StatusChange }) {
  return (
    <tr data-month={month.month.key}>
      <th scope="row">{month.month.label}</th>
      <td className="figure" data-testid="balance" data-reported={month.ending_balance !== null}>
        {balanceText(month)}
      </td>
      <td className="figure" data-testid="dscr" data-reported={month.dscr !== null}>
        {dscrText(month)}
      </td>
      <td data-testid="status">{statusText(month)}</td>
      <td data-testid="change">{change?.bucket}</td>
      <td>
        <a href={month.source.url} target="_blank" rel="noreferrer" data-testid="filing">
          {month.source.form} {month.source.accession}
        </a>
        , filed {month.source.filing_date}
      </td>
    </tr>
  );
}

export function HistoryChart({ history, asOf }: { history: LoanHistory; asOf: string }) {
  const [hovered, setHovered] = useState<string | null>(null);
  // React 19 calls a ref's cleanup when the element goes or the history changes.
  const chartRef = useCallback(
    (element: HTMLDivElement) => drawChart(element, history, setHovered),
    [history],
  );
  const { months } = history;
  const last = months.at(-1);
  if (last === undefined) {
    return (
      <p className="empty" data-testid="history-empty">
        No tape reporting this loan had been filed by {asOf}.
      </p>
    );
  }
  const pointed = monthAt(months, hovered);
  const shown = pointed ?? last;
  return (
    <figure className="history" data-testid="history">
      <Legend
        month={shown}
        change={changeIn(history.status_changes, shown)}
        hovered={pointed !== undefined}
      />
      <div className="history-chart" data-testid="history-chart" ref={chartRef} />
      <figcaption className="attribution" data-testid="chart-attribution">
        Chart: {NOTICE} <a href={TRADINGVIEW}>{TRADINGVIEW}</a>
      </figcaption>
      <table className="values history-table" data-testid="history-table">
        <caption>Each month as published by {asOf}</caption>
        <thead>
          <tr>
            <th scope="col">Month</th>
            <th scope="col">Ending balance</th>
            <th scope="col">DSCR</th>
            <th scope="col">Payment status</th>
            <th scope="col">Status changed to</th>
            <th scope="col">Filing</th>
          </tr>
        </thead>
        <tbody>
          {months.map((month) => (
            <HistoryRow
              key={month.month.key}
              month={month}
              change={changeIn(history.status_changes, month)}
            />
          ))}
        </tbody>
      </table>
    </figure>
  );
}
