// What the loan history chart (HistoryChart.tsx) draws, from what the API sends (LoanHistory).
// Every figure shown as text is the API string, formatted; a number is made only to place a point
// on the chart, and a value not reported is no point at all, so the line has a gap there.

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

/** "in grace period (A)"; "not reported"; or that the month's tape leaves the loan out. */
export function statusText(month: HistoryMonth): string {
  if (!month.on_tape) {
    return NOT_ON_TAPE;
  }
  const status = month.payment_status;
  return status === null ? NOT_REPORTED : `${status.name} (${status.code})`;
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
