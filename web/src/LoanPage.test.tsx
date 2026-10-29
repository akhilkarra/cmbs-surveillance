import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FinancialsBlock, LoanContent, LoanPage } from "./LoanPage";
import { commentValue, F1, loanPage } from "./test-data";

afterEach(() => {
  vi.unstubAllGlobals();
});

function row(section: string, field: string): HTMLElement {
  const found = screen.getByTestId(section).querySelector<HTMLElement>(`[data-field="${field}"]`);
  if (found === null) throw new Error(`no ${field} row in ${section}`);
  return found;
}

function checks(element: HTMLElement): [string | null, string | null][] {
  return within(element)
    .queryAllByTestId("check")
    .map((check) => [check.textContent, check.getAttribute("data-outcome")]);
}

describe("LoanContent", () => {
  it("heads the page with the loan, its property and the day the panel is read", () => {
    render(<LoanContent data={loanPage()} />);
    expect(screen.getByTestId("loan-title").textContent).toBe(`Loan 2 of ${F1}`);
    expect(screen.getByTestId("as-of").textContent).toBe("2026-10-28");
    expect(screen.getByTestId("as-of").parentElement?.textContent).toBe(
      "Lakeside Hotel Portfolio. September 2026, as the filings stood on 2026-10-28.",
    );
    const back = screen.getByRole("link", { name: "Open alerts on the book, September 2026" });
    expect(back.getAttribute("href")).toBe("#/book/2026-09");
  });

  it("names a property the tape does not", () => {
    render(<LoanContent data={loanPage({ property_name: null })} />);
    expect(screen.getByText(/^Property not named\. September 2026/)).toBeTruthy();
  });

  it("shows each tape value formatted, with its checks and the run that loaded it", () => {
    render(<LoanContent data={loanPage()} />);
    const tape = screen.getByRole("link", { name: "ABS-EE 0009900001-26-000419 on EDGAR" });
    expect(tape.getAttribute("href")).toBe(
      "https://www.sec.gov/Archives/edgar/data/9900001/000990000126000419/f1-absee.htm",
    );
    expect(tape.parentElement?.textContent).toBe(
      "ABS-EE 0009900001-26-000419 on EDGAR, filed 2026-09-29.",
    );
    const balance = row("tape-values", "ending_balance");
    expect(within(balance).getByRole("rowheader").textContent).toBe("ending balance");
    const value = within(balance).getByTestId("value");
    expect([value.textContent, value.getAttribute("data-reported")]).toEqual([
      "$30,000,000.00",
      "true",
    ]);
    expect(checks(balance)).toEqual([
      ["contract passed", "passed"],
      ["roll-forward not checked", "not checked"],
    ]);
    expect(within(balance).getByTestId("loaded").textContent).toBe("run 43");
    const paid = within(row("tape-values", "paid_through_date")).getByTestId("value");
    expect([paid.textContent, paid.getAttribute("data-reported")]).toEqual([
      "not reported",
      "false",
    ]);
  });

  it("sets each extracted value beside the sentence behind it", () => {
    render(<LoanContent data={loanPage()} />);
    expect(screen.getByTestId("comment-month").textContent).toBe("September 2026");
    const filing = screen.getByTestId("comment-filing");
    expect(filing.textContent).toBe("10-D 0009900001-26-000418 on EDGAR");
    expect(filing.getAttribute("href")).toBe(
      "https://www.sec.gov/Archives/edgar/data/9900001/000990000126000418/f1-10d.htm",
    );
    expect(filing.getAttribute("target")).toBe("_blank");
    expect(filing.getAttribute("rel")).toBe("noreferrer");
    expect(screen.getByTestId("comment-loaded").textContent).toBe("extracted by quinn");
    expect(filing.parentElement?.textContent).toBe(
      "10-D 0009900001-26-000418 on EDGAR, filed 2026-09-29. Read by jev-1.14.0 with prompt " +
        "version 3, settings version 7; extracted by quinn.",
    );
    const request = row("comment-values", "borrower_request");
    expect(request.getAttribute("data-status")).toBe("auto-accepted");
    expect(within(request).getByTestId("value").textContent).toBe("maturity extension");
    expect(within(request).getByTestId("sentence-number").textContent).toBe("sentence 3");
    expect(within(request).getByTestId("evidence").textContent).toBe(
      "The borrower has requested a 12-month maturity extension.",
    );
    expect(checks(request)).toEqual([
      ["auto-accepted", "passed"],
      ["evidence checked", "passed"],
    ]);
    expect(within(request).queryByTestId("status")).toBeNull();
  });

  it("marks a value awaiting review instead of showing one", () => {
    render(<LoanContent data={loanPage()} />);
    const workout = row("comment-values", "workout_strategy");
    expect(workout.getAttribute("data-status")).toBe("needs review");
    expect(within(workout).queryByTestId("value")).toBeNull();
    expect(within(workout).getByTestId("status").textContent).toBe("needs review");
    expect(within(workout).getByText("unsettled answer").className).toBe("reason");
    expect(within(workout).queryByTestId("evidence")).toBeNull();
    expect(checks(workout)).toEqual([]);
  });

  it("numbers the comment's sentences as filed, repeats included", () => {
    render(<LoanContent data={loanPage()} />);
    const sentences = screen.getAllByRole("listitem").filter((item) => item.closest(".sentences"));
    expect(sentences.map((item) => item.textContent)).toEqual([
      "The loan transferred to Special Servicing effective 3/6/2025.",
      "As of 12/31/2024, DSCR NOI was 0.71x.",
      "The borrower has requested a 12-month maturity extension.",
      "The borrower has requested a 12-month maturity extension.",
    ]);
  });

  it("says when no comment was extracted", () => {
    render(
      <LoanContent
        data={loanPage({
          comment: null,
          comment_note: "no servicer comment extracted for September 2026",
        })}
      />,
    );
    expect(screen.getByText("no servicer comment extracted for September 2026").className).toBe(
      "empty",
    );
    expect(screen.queryByTestId("comment-values")).toBeNull();
  });

  it("lists check flags and the month's alerts, or says there are none", () => {
    const { unmount } = render(<LoanContent data={loanPage()} />);
    expect(screen.getByText("No check flagged this loan for September 2026.")).toBeTruthy();
    expect(screen.getByText("No alerts on this loan for September 2026.")).toBeTruthy();
    unmount();
    const complaints = vi.spyOn(console, "error");
    render(
      <LoanContent
        data={loanPage({
          flags: [
            { check: "reconciliation", message: "paid-through date differs" },
            { check: "text vs tape", message: "transfer date differs" },
          ],
          alerts: [
            {
              alert_id: 4,
              severity: "medium",
              message: "workout strategy changed: to be determined to full payoff",
              state_text: "new",
            },
          ],
        })}
      />,
    );
    expect(
      within(screen.getByTestId("flags"))
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual(["reconciliation paid-through date differs", "text vs tape transfer date differs"]);
    const [alert] = within(screen.getByTestId("loan-alerts")).getAllByRole("listitem");
    expect(alert?.textContent).toBe(
      "medium workout strategy changed: to be determined to full payoff new",
    );
    expect(within(alert as HTMLElement).getByText("medium").className).toBe(
      "severity severity-medium",
    );
    // Each flag is its own list item to React: no duplicate keys.
    expect(complaints).not.toHaveBeenCalled();
    complaints.mockRestore();
  });

  it("shows a value with no sentence without evidence", () => {
    const value = commentValue({ sentence: null, evidence: null, checks: [] });
    const page = loanPage();
    render(
      <LoanContent
        data={{ ...page, comment: page.comment && { ...page.comment, values: [value] } }}
      />,
    );
    const request = row("comment-values", "borrower_request");
    expect(within(request).queryByTestId("sentence-number")).toBeNull();
  });
});

describe("FinancialsBlock", () => {
  it("shows the end date and the age at period end", () => {
    render(<FinancialsBlock financials={loanPage().financials} />);
    expect(screen.getByTestId("financials-end").textContent).toBe("2024-12-31");
    expect(screen.getByTestId("financials-age").textContent).toBe("619 days old at period end");
    expect(screen.getByTestId("financials").textContent).toBe(
      "Most recent financialsThrough 2024-12-31, 619 days old at period end",
    );
  });

  it("shows why there is no age", () => {
    render(
      <FinancialsBlock
        financials={{
          start: null,
          end: null,
          days_at_period_end: null,
          days_at_filing: null,
          status: "no updated financials",
        }}
      />,
    );
    expect(screen.getByTestId("financials-end").textContent).toBe("not reported");
    expect(screen.getByTestId("financials-age").textContent).toBe("no updated financials");
  });
});

describe("LoanPage", () => {
  it("reads the loan as the user", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify(loanPage())));
    vi.stubGlobal("fetch", fetch);
    render(<LoanPage user="pat" deal={F1} loan="2" month={null} />);
    const page = screen.getByTestId("loan");
    expect(screen.getByText("Reading loan 2").className).toBe("status");
    await waitFor(() => expect(page.getAttribute("data-state")).toBe("ready"));
    expect(fetch).toHaveBeenCalledWith("/dashboard/loan?deal=Fixture+Trust+2026-F1&loan=2", {
      headers: { "X-CMBS-User": "pat" },
    });
  });
});
