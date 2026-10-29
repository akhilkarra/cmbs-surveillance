// Sample API answers for component tests, shaped as src/cmbs/dashboard/routes.py sends them.
import type { BookPageData, CommentValue, LoanPageData } from "./api";

export const F1 = "Fixture Trust 2026-F1";
export const F2 = "Fixture Trust 2026-F2";

export function bookPage(changes: Partial<BookPageData> = {}): BookPageData {
  return {
    month: { key: "2026-09", label: "September 2026" },
    previous: { key: "2026-08", label: "August 2026" },
    next: { key: "2026-10", label: "October 2026" },
    as_of: "2026-10-28",
    rows: [
      {
        alert_id: 1,
        severity: "high",
        title: "transferred to special servicing",
        message: "transferred to special servicing on 2026-09-02",
        loans: [{ deal: F1, loan: "7" }],
        exposure: "25000000.00",
        balance: "9000000.00",
        classes: ["E", "F"],
        state: "new",
        state_text: "new",
        note: null,
      },
      {
        alert_id: 7,
        severity: "medium",
        title: "payment status worsened",
        message: "payment status worsened: current to 60 days",
        loans: [{ deal: F2, loan: "3" }],
        exposure: "40000000.00",
        balance: null,
        classes: ["D"],
        state: "acknowledged",
        state_text: "acknowledged by pat on 2026-10-28",
        note: null,
      },
    ],
    note: null,
    ...changes,
  };
}

export function commentValue(changes: Partial<CommentValue> = {}): CommentValue {
  return {
    field: "borrower_request",
    label: "borrower request",
    status: "auto-accepted",
    value: "maturity extension",
    sentence: 3,
    evidence: "The borrower has requested a 12-month maturity extension.",
    checks: [
      { label: "auto-accepted", outcome: "passed" },
      { label: "evidence checked", outcome: "passed" },
    ],
    review_reason: null,
    model_value: "maturity extension",
    decided_by: null,
    decided_at: null,
    overridden_by: null,
    overridden_at: null,
    override_reason: null,
    declined_reason: null,
    decision: null,
    ...changes,
  };
}

export function loanPage(changes: Partial<LoanPageData> = {}): LoanPageData {
  return {
    deal: F1,
    loan: "2",
    month: { key: "2026-09", label: "September 2026" },
    as_of: "2026-10-28",
    property_name: "Lakeside Hotel Portfolio",
    property_type: "LO",
    tape: {
      source: {
        form: "ABS-EE",
        accession: "0009900001-26-000419",
        filing_date: "2026-09-29",
        url: "https://www.sec.gov/Archives/edgar/data/9900001/000990000126000419/f1-absee.htm",
      },
      loaded: { run: 43, by: "olly", at: "2026-10-28T09:00:00Z", text: "run 43" },
      values: [
        {
          field: "ending_balance",
          label: "ending balance",
          kind: "money",
          value: "30000000.00",
          checks: [
            { label: "contract passed", outcome: "passed" },
            { label: "roll-forward not checked", outcome: "not checked" },
          ],
        },
        {
          field: "paid_through_date",
          label: "paid-through date",
          kind: "date",
          value: null,
          checks: [{ label: "contract passed", outcome: "passed" }],
        },
      ],
    },
    financials: {
      start: "2024-01-01",
      end: "2024-12-31",
      days_at_period_end: 619,
      days_at_filing: 637,
      status: "619 days old at period end, 637 days old at filing",
    },
    comment: {
      source: {
        form: "10-D",
        accession: "0009900001-26-000418",
        filing_date: "2026-09-29",
        url: "https://www.sec.gov/Archives/edgar/data/9900001/000990000126000418/f1-10d.htm",
      },
      loaded: { run: null, by: "quinn", at: "2026-10-28T10:00:00Z", text: "extracted by quinn" },
      sentences: [
        "The loan transferred to Special Servicing effective 3/6/2025.",
        "As of 12/31/2024, DSCR NOI was 0.71x.",
        "The borrower has requested a 12-month maturity extension.",
        "The borrower has requested a 12-month maturity extension.",
      ],
      model: "jev-1.14.0",
      prompt_version: 3,
      settings_version: 7,
      values: [
        commentValue(),
        commentValue({
          field: "workout_strategy",
          label: "workout strategy",
          status: "needs review",
          value: null,
          sentence: null,
          evidence: null,
          checks: [],
          review_reason: "unsettled answer",
        }),
      ],
    },
    comment_note: null,
    flags: [],
    alerts: [],
    ...changes,
  };
}
