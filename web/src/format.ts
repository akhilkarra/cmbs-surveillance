// Display formatting only. Values arrive computed from the API; the UI never recomputes them.

const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** 30000000 -> "$30,000,000.00" */
export function formatMoney(amount: number): string {
  return money.format(amount);
}

/** 0.96 -> "0.96x" */
export function formatDscr(dscr: number): string {
  return `${dscr.toFixed(2)}x`;
}

/** 619 -> "619 days old at period end" */
export function financialsAgeLabel(days: number): string {
  if (!Number.isInteger(days) || days < 0) {
    throw new RangeError(`financials age must be a whole number of days >= 0, got ${days}`);
  }
  return `${days} ${days === 1 ? "day" : "days"} old at period end`;
}
