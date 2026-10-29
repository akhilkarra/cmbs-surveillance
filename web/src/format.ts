// Display formatting only. Values arrive computed from the API, money and ratios as decimal
// strings; the UI never recomputes them and never turns money into a float.

const AMOUNT = /^(?<sign>-?)(?<dollars>\d+)\.(?<cents>\d{2})$/;

/** "30000000.00" -> "$30,000,000.00" */
export function formatMoney(amount: string): string {
  const match = AMOUNT.exec(amount);
  if (match === null) {
    throw new RangeError(`not an amount in dollars and cents: ${amount}`);
  }
  const { sign, dollars, cents } = match.groups as { sign: string; dollars: string; cents: string };
  return `${sign}$${dollars.replace(/\B(?=(\d{3})+$)/g, ",")}.${cents}`;
}

/** "0.96" -> "0.96x", every digit as reported. */
export function formatRatio(ratio: string): string {
  return `${ratio}x`;
}

/** A value the filing does not report is "not reported", never zero. */
export const NOT_REPORTED = "not reported";

export type ValueKind = "money" | "date" | "ratio" | "text";

/** A panel value as the page shows it, by the kind the API names. */
export function formatValue(kind: ValueKind, value: string | null): string {
  if (value === null) {
    return NOT_REPORTED;
  }
  switch (kind) {
    case "money":
      return formatMoney(value);
    case "ratio":
      return formatRatio(value);
    default:
      return value;
  }
}

/** 6 -> "6 open alerts" */
export function openAlertsLabel(count: number): string {
  return `${count} open ${count === 1 ? "alert" : "alerts"}`;
}
