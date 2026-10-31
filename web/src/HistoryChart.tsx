// The loan page's history: the loan's ending balance and DSCR month by month as published on the
// page's day, each in a pane of its own over one time axis (two measures, never two scales on one
// plot), with an arrow over each month the payment status changed. Hovering a month names its
// values and the filing they came from; the table under the chart says the same in text.

import {
  ColorType,
  createChart,
  createSeriesMarkers,
  LineSeries,
  type MouseEventParams,
} from "lightweight-charts";
import { useCallback, useEffect, useState } from "react";
import type { HistoryMonth, LoanHistory, StatusChange } from "./api";
import {
  axisMoney,
  axisRatio,
  balanceOf,
  balanceText,
  changeIn,
  dscrOf,
  dscrText,
  filingText,
  lineSegments,
  monthAt,
  type Point,
  statusLane,
  statusMarkers,
  statusText,
  timeKey,
} from "./historyData";

/** The attribution TradingView Lightweight Charts' licence asks for: its NOTICE file and a link
 *  to tradingview.com, given under the chart (so its logo need not cover the plot). */
export const NOTICE = "TradingView Lightweight Charts™ Copyright (c) 2025 TradingView, Inc.";
export const TRADINGVIEW = "https://www.tradingview.com/";

export const DARK_MODE = "(prefers-color-scheme: dark)";

/** A colour token of the page (styles.css), so the chart follows light and dark mode. */
function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Counts the page's theme changes (the system's light or dark mode, or the page's data-theme),
 *  so the chart is drawn again in the new theme's colours. */
export function useThemeChanges(): number {
  const [changes, setChanges] = useState(0);
  // Stryker disable next-line ArrayDeclaration: any constant list subscribes once, as [] does
  useEffect(() => {
    const changed = () => setChanges((count) => count + 1);
    const system = window.matchMedia(DARK_MODE);
    system.addEventListener("change", changed);
    const page = new MutationObserver(changed);
    page.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => {
      system.removeEventListener("change", changed);
      page.disconnect();
    };
  }, []);
  return changes;
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
      attributionLogo: false,
      panes: { separatorColor: rule },
    },
    grid: { vertLines: { visible: false }, horzLines: { color: rule } },
    rightPriceScale: { borderColor: rule },
    timeScale: {
      borderColor: rule,
      fixLeftEdge: true,
      fixRightEdge: true,
      // A narrower window keeps every month in view rather than cutting off the latest.
      lockVisibleTimeRangeOnResize: true,
    },
    // The page scrolls under the pointer; the chart always shows every month.
    handleScroll: false,
    handleScale: false,
  });
  const color = token("--link");
  // A measure is drawn as one line per run of months reporting it, in its own pane; only the
  // last line names the measure and shows its latest value on the axis. A run of one month is
  // its point alone (the library draws a lone point as a level stretching beside it), so every
  // point is drawn large enough to see by itself.
  const draw = (
    segments: Point[][],
    title: string,
    formatter: (price: number) => string,
    pane: number,
    margins: { top: number; bottom: number },
  ) =>
    segments.forEach((points, index) => {
      const last = index === segments.length - 1;
      const series = chart.addSeries(
        LineSeries,
        {
          color,
          lineWidth: 2,
          lineVisible: points.filter((point) => point.value !== undefined).length > 1,
          pointMarkersVisible: true,
          pointMarkersRadius: 4,
          title: last ? title : "",
          lastValueVisible: last,
          priceLineVisible: last,
          priceFormat: { type: "custom", formatter },
        },
        pane,
      );
      series.priceScale().applyOptions({ scaleMargins: margins });
      series.setData(points);
    });
  // The balance keeps the top of its pane clear for the status lane; the DSCR keeps clear of
  // both edges of its pane, so its labels never meet the balance pane's.
  draw(lineSegments(history.months, balanceOf), "ending balance", axisMoney, 0, {
    top: 0.32,
    bottom: 0.08,
  });
  draw(lineSegments(history.months, dscrOf), "DSCR", axisRatio, 1, { top: 0.18, bottom: 0.18 });
  // The status changes hang on a lane with a point at every month, on a scale of its own across
  // the top of the balance pane, so each arrow is over its own month.
  const lane = chart.addSeries(
    LineSeries,
    {
      priceScaleId: "status",
      lineVisible: false,
      pointMarkersVisible: false,
      crosshairMarkerVisible: false,
      lastValueVisible: false,
      priceLineVisible: false,
    },
    0,
  );
  lane.priceScale().applyOptions({ scaleMargins: { top: 0.2, bottom: 0.76 } });
  lane.setData(statusLane(history.months));
  createSeriesMarkers(lane, statusMarkers(history.status_changes, token("--medium")));
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
      {change && <span data-testid="legend-change">, changed to {change.bucket}</span>};{" "}
      <span data-testid="legend-filing">{filingText(month)}</span>
    </p>
  );
}

function HistoryRow({ month, change }: { month: HistoryMonth; change?: StatusChange }) {
  const { source } = month;
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
        {source === null ? (
          statusText(month)
        ) : (
          <>
            <a href={source.url} target="_blank" rel="noreferrer" data-testid="filing">
              {source.form} {source.accession}
            </a>
            , filed {source.filing_date}
          </>
        )}
      </td>
    </tr>
  );
}

export function HistoryChart({ history, asOf }: { history: LoanHistory; asOf: string }) {
  const [hovered, setHovered] = useState<string | null>(null);
  // React 19 calls a ref's cleanup when the element goes or the history changes; a theme
  // change gives the chart a new element (its key), so it is drawn again in the new colours.
  const chartRef = useCallback(
    (element: HTMLDivElement) => drawChart(element, history, setHovered),
    [history],
  );
  const theme = useThemeChanges();
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
      <div className="history-chart" data-testid="history-chart" key={theme} ref={chartRef} />
      <figcaption className="chart-notes">
        <span data-testid="gap-note">
          A gap in a line is a month that does not report the value; the table says whether the loan
          was not on that month's tape or no tape was filed.
        </span>{" "}
        <span className="attribution" data-testid="chart-attribution">
          Chart: {NOTICE} <a href={TRADINGVIEW}>{TRADINGVIEW}</a>
        </span>
      </figcaption>
      <div className="table-scroll">
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
      </div>
    </figure>
  );
}
