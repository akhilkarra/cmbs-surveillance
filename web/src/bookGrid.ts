// The book view's grid: one row per open alert, in the order the API sends (severity, then
// exposure, then the loan's balance). Columns cannot be sorted, so no other order is shown.

import type { BookRow, Severity } from "./api";
import { formatMoney, formatValue } from "./format";
import { loanHref } from "./route";

export interface GridRow {
  id: string;
  severity: Severity;
  deal: string;
  loan: string;
  loanHref: string;
  alert: string;
  exposure: string;
  balance: string;
  state: string;
}

/** A whole loan held through several deals lists each deal and note; it links to the first. */
export function gridRow(row: BookRow, month: string): GridRow {
  const [first] = row.loans;
  return {
    id: String(row.alert_id),
    severity: row.severity,
    deal: row.loans.map((loan) => loan.deal).join(", "),
    loan: row.loans.map((loan) => loan.loan).join(", "),
    loanHref: first ? loanHref(first.deal, first.loan, month) : "",
    alert: row.message,
    exposure: formatMoney(row.exposure),
    balance: formatValue("money", row.balance),
    state: row.state_text,
  };
}
