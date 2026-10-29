import { describe, expect, it } from "vitest";
import { gridRow } from "./bookGrid";
import { bookPage, F1, F2 } from "./test-data";

describe("gridRow", () => {
  const [high, medium] = bookPage().rows;

  it("formats one alert for the grid, linking its loan's month", () => {
    expect(high && gridRow(high, "2026-09")).toEqual({
      id: "1",
      severity: "high",
      deal: F1,
      loan: "7",
      loanHref: "#/loan/Fixture%20Trust%202026-F1/7/2026-09",
      alert: "transferred to special servicing on 2026-09-02",
      exposure: "$25,000,000.00",
      balance: "$9,000,000.00",
      state: "new",
    });
  });

  it("says a balance the tape does not report is not reported", () => {
    expect(medium && gridRow(medium, "2026-09").balance).toBe("not reported");
    expect(medium && gridRow(medium, "2026-09").state).toBe("acknowledged by pat on 2026-10-28");
  });

  it("lists every deal of a whole loan and links to the first", () => {
    const whole = {
      ...(high as NonNullable<typeof high>),
      loans: [
        { deal: F1, loan: "7" },
        { deal: F2, loan: "3" },
      ],
    };
    const row = gridRow(whole, "2026-09");
    expect([row.deal, row.loan, row.loanHref]).toEqual([
      `${F1}, ${F2}`,
      "7, 3",
      "#/loan/Fixture%20Trust%202026-F1/7/2026-09",
    ]);
  });

  it("links nowhere for an alert that covers no loan", () => {
    const none = { ...(high as NonNullable<typeof high>), loans: [] };
    expect(gridRow(none, "2026-09")).toMatchObject({ deal: "", loan: "", loanHref: "" });
  });
});
