// What the loan history chart (HistoryChart.tsx) draws, from what the API sends (LoanHistory).
// Every figure shown as text is the API string, formatted; a number is made only to place a point
// on the chart, and a value not reported is no point at all, so the line has a gap there.

import type { PriceRange } from "lightweight-charts";
import type { HistoryMonth, StatusChange } from "./api";
import { formatValue, NOT_REPORTED } from "./format";

/** A point on a line: a month with its value, or a month without one (a gap). */
export interface Point {
  time: string;
  value?: number;
}

/** One point per month at the month's first day; a month that does not report the value has
 *  no value, which the chart leaves as a gap rather than a zero. */
export function linePoints(
  months: HistoryMonth[],
  value: (month: HistoryMonth) => string | null,
): Point[] {
  return months.map((month) => {
    const reported = value(month);
    return reported === null ? { time: month.day } : { time: month.day, value: Number(reported) };
  });
}

/** The line's segments, one per run of consecutive months that report the value: a line drawn
 *  through a month without a point joins its neighbours across the gap, so each run is drawn as
 *  a line of its own. Every segment keeps every month (valued only in its run), so each line
 *  spans the whole time axis; a value no month reports is one segment without values. */
export function lineSegments(
  months: HistoryMonth[],
  value: (month: HistoryMonth) => string | null,
): Point[][] {
  const points = linePoints(months, value);
  const runs: number[][] = [];
  points.forEach((point, index) => {
    if (point.value === undefined) return;
    const run = runs.at(-1);
    if (run?.at(-1) === index - 1) run.push(index);
    else runs.push([index]);
  });
  if (runs.length === 0) return [points];
  return runs.map((run) =>
    points.map((point, index) => (run.includes(index) ? point : { time: point.time })),
  );
}

export const balanceOf = (month: HistoryMonth) => month.ending_balance;
export const dscrOf = (month: HistoryMonth) => month.dscr;

/** A payment status change as the chart marks it: an arrow over the month, named by the
 *  delinquency bucket the loan moved into. */
export interface Marker {
  time: string;
  position: "aboveBar";
  shape: "arrowDown";
  color: string;
  text: string;
}

export function statusMarkers(changes: StatusChange[], color: string): Marker[] {
  return changes.map((change) => ({
    time: change.day,
    position: "aboveBar",
    shape: "arrowDown",
    color,
    text: change.bucket,
  }));
}

/** The status lane: a point at every month, at one level, that the chart does not draw but
 *  hangs the status change markers on. The library draws a marker at a point of its series, so
 *  a lane with every month puts each marker over its own month, even one with no balance. */
export function statusLane(months: HistoryMonth[]): Point[] {
  return months.map((month) => ({ time: month.day, value: 0 }));
}

/** The chart's time for a month ("2026-08-01"), as the crosshair reports it: the string the
 *  data was given in, or a business day object; anything else is no month. */
export function timeKey(time: unknown): string | null {
  if (typeof time === "string") {
    return time;
  }
  if (typeof time === "object" && time !== null && "year" in time) {
    const { year, month, day } = time as { year: number; month: number; day: number };
    return [year, month, day]
      .map((part, index) => String(part).padStart(index ? 2 : 4, "0"))
      .join("-");
  }
  return null;
}

export function monthAt(months: HistoryMonth[], time: string | null): HistoryMonth | undefined {
  return months.find((month) => month.day === time);
}

export function changeIn(changes: StatusChange[], month: HistoryMonth): StatusChange | undefined {
  return changes.find((change) => change.month.key === month.month.key);
}

export const NOT_ON_TAPE = "loan not on the tape";
export const NO_TAPE = "no tape filed";

/** "in grace period (A)"; "not reported"; or that the month's tape leaves the loan out, or that
 *  no tape was filed for the month. */
export function statusText(month: HistoryMonth): string {
  if (month.source === null) {
    return NO_TAPE;
  }
  if (!month.on_tape) {
    return NOT_ON_TAPE;
  }
  const status = month.payment_status;
  return status === null ? NOT_REPORTED : `${status.name} (${status.code})`;
}

/** "from ABS-EE 0009900001-26-000417, filed 2026-08-29", or that no tape was filed. */
export function filingText(month: HistoryMonth): string {
  const { source } = month;
  return source === null
    ? NO_TAPE
    : `from ${source.form} ${source.accession}, filed ${source.filing_date}`;
}

export function balanceText(month: HistoryMonth): string {
  return formatValue("money", month.ending_balance);
}

export function dscrText(month: HistoryMonth): string {
  return formatValue("ratio", month.dscr);
}

const COMPACT = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 2,
});

/** The balance axis's tick labels ("$30.2M"); the balances themselves are shown in full. */
export function axisMoney(price: number): string {
  return COMPACT.format(price);
}

/** The DSCR axis's tick labels ("0.96x"). */
export function axisRatio(price: number): string {
  return `${price.toFixed(2)}x`;
}

/** The least an axis spans, given the middle of its values: 2% of the balance (and at least
 *  $1,000, for a loan paid down to nothing), 0.10x of DSCR. */
export const MONEY_SPAN = (middle: number) => Math.max(middle * 0.02, 1000);
export const RATIO_SPAN = () => 0.1;

/** An axis range widened about its middle to at least the measure's span, so a value that never
 *  changes (an interest-only balance, say) gets ticks with distinct labels rather than one label
 *  repeated. A range of values none of which is negative is never widened below zero. */
export function widened(
  range: PriceRange | null,
  span: (middle: number) => number,
): PriceRange | null {
  if (range === null) return null;
  const middle = (range.minValue + range.maxValue) / 2;
  const width = Math.max(range.maxValue - range.minValue, span(middle));
  const floor = range.minValue >= 0 ? 0 : Number.NEGATIVE_INFINITY;
  const low = Math.max(middle - width / 2, floor);
  return { minValue: low, maxValue: low + width };
}
