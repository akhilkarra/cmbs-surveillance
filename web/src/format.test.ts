import { describe, expect, it } from "vitest";
import { financialsAgeLabel, formatDscr, formatMoney } from "./format";

describe("formatMoney", () => {
  it("shows dollars with separators and cents", () => {
    expect(formatMoney(30_000_000)).toBe("$30,000,000.00");
    expect(formatMoney(0)).toBe("$0.00");
    expect(formatMoney(1234.5)).toBe("$1,234.50");
  });
});

describe("formatDscr", () => {
  it("shows two decimals and an x", () => {
    expect(formatDscr(0.96)).toBe("0.96x");
    expect(formatDscr(1)).toBe("1.00x");
    expect(formatDscr(1.125)).toBe("1.13x");
  });
});

describe("financialsAgeLabel", () => {
  it("matches the dashboard scenario", () => {
    expect(financialsAgeLabel(619)).toBe("619 days old at period end");
  });

  it("is singular for one day", () => {
    expect(financialsAgeLabel(1)).toBe("1 day old at period end");
    expect(financialsAgeLabel(0)).toBe("0 days old at period end");
  });

  it("rejects ages that are not whole, non-negative days", () => {
    expect(() => financialsAgeLabel(-1)).toThrow(/whole number of days >= 0, got -1/);
    expect(() => financialsAgeLabel(1.5)).toThrow(RangeError);
    expect(() => financialsAgeLabel(1.5)).toThrow(/got 1.5/);
  });
});
