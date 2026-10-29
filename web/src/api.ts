// The dashboard API (src/cmbs/dashboard/routes.py). Money and ratios arrive as decimal strings,
// dates as ISO strings; every label and ordering is the server's.

import type { ValueKind } from "./format";

export const USER_HEADER = "X-CMBS-User";

export type Severity = "high" | "medium" | "low";

export interface MonthRef {
  key: string;
  label: string;
}

export interface LoanRef {
  deal: string;
  loan: string;
}

export interface BookRow {
  alert_id: number;
  severity: Severity;
  title: string;
  message: string;
  loans: LoanRef[];
  exposure: string;
  balance: string | null;
  classes: string[];
  state: string;
  state_text: string;
  note: string | null;
}

export interface BookPageData {
  month: MonthRef;
  previous: MonthRef;
  next: MonthRef;
  as_of: string;
  rows: BookRow[];
  note: string | null;
}

/** One thing that verified a value: a check that "passed", "failed" or was "not checked", or a
 *  person's decision ("decided"). */
export interface Check {
  label: string;
  outcome: "passed" | "failed" | "not checked" | "decided";
}

export interface FilingSource {
  form: string;
  accession: string;
  filing_date: string;
  url: string;
}

export interface LoadedBy {
  run: number | null;
  by: string | null;
  at: string | null;
  text: string;
}

export interface TapeValue {
  field: string;
  label: string;
  kind: ValueKind;
  value: string | null;
  checks: Check[];
}

export interface Financials {
  start: string | null;
  end: string | null;
  days_at_period_end: number | null;
  days_at_filing: number | null;
  status: string;
}

export interface CommentValue {
  field: string;
  label: string;
  status: string;
  value: string | null;
  sentence: number | null;
  evidence: string | null;
  checks: Check[];
  review_reason: string | null;
  model_value: string | null;
  decided_by: string | null;
  decided_at: string | null;
  overridden_by: string | null;
  overridden_at: string | null;
  override_reason: string | null;
  declined_reason: string | null;
  /** Who settled the value by hand, when and why; null when nobody did. */
  decision: string | null;
}

export interface CommentSection {
  source: FilingSource;
  loaded: LoadedBy;
  sentences: string[];
  model: string;
  prompt_version: number;
  settings_version: number;
  values: CommentValue[];
}

export interface LoanFlag {
  id: number;
  check: string;
  message: string;
}

export interface LoanAlert {
  alert_id: number;
  severity: Severity;
  message: string;
  state_text: string;
}

export interface LoanPageData {
  deal: string;
  loan: string;
  month: MonthRef;
  as_of: string;
  property_name: string | null;
  property_type: string | null;
  tape: { source: FilingSource; loaded: LoadedBy; values: TapeValue[] };
  financials: Financials;
  comment: CommentSection | null;
  comment_note: string | null;
  flags: LoanFlag[];
  alerts: LoanAlert[];
}

export function bookPath(month: string | null): string {
  return month === null ? "/dashboard/book" : `/dashboard/book?month=${encodeURIComponent(month)}`;
}

export function loanPath(deal: string, loan: string, month: string | null): string {
  const query = new URLSearchParams({ deal, loan });
  if (month !== null) {
    query.set("month", month);
  }
  return `/dashboard/loan?${query.toString()}`;
}

/** Read an API path as a user; a refusal comes back as an Error with the server's message. */
export async function getJson<T>(path: string, user: string): Promise<T> {
  const response = await fetch(path, { headers: { [USER_HEADER]: user } });
  const body: unknown = await response.json();
  if (!response.ok) {
    const detail = (body as { detail?: unknown }).detail;
    throw new Error(
      typeof detail === "string" ? detail : `the server answered HTTP ${response.status}`,
    );
  }
  return body as T;
}
