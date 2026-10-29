import { describe, expect, it } from "vitest";
import { formatMoney, formatRatio, formatValue, NOT_REPORTED, openAlertsLabel } from "./format";

describe("formatMoney", () => {
  it("shows dollars with separators and the cents as sent", () => {
    expect(formatMoney("30000000.00")).toBe("$30,000,000.00");
    expect(formatMoney("0.00")).toBe("$0.00");
    expect(formatMoney("1234.50")).toBe("$1,234.50");
    expect(formatMoney("123.45")).toBe("$123.45");
    expect(formatMoney("1000.00")).toBe("$1,000.00");
    expect(formatMoney("100000.00")).toBe("$100,000.00");
  });

  it("keeps every digit of amounts a float would round", () => {
    expect(formatMoney("90071992547409931.07")).toBe("$90,071,992,547,409,931.07");
  });

  it("puts the sign before the dollar sign", () => {
    expect(formatMoney("-12000000.00")).toBe("-$12,000,000.00");
  });

  it("refuses anything that is not dollars and cents", () => {
    for (const text of ["30000000", "1.5", "$5.00", "1,000.00", " 1.00", "1.00 ", "", "-"]) {
      expect(() => formatMoney(text)).toThrow(`not an amount in dollars and cents: ${text}`);
    }
    expect(() => formatMoney("1.00")).not.toThrow();
  });
});

describe("formatRatio", () => {
  it("adds an x and keeps every digit reported", () => {
    expect(formatRatio("0.96")).toBe("0.96x");
    expect(formatRatio("1.125")).toBe("1.125x");
  });
});

describe("formatValue", () => {
  it("formats by the kind the API names", () => {
    expect(formatValue("money", "30000000.00")).toBe("$30,000,000.00");
    expect(formatValue("ratio", "0.96")).toBe("0.96x");
    expect(formatValue("date", "2026-09-02")).toBe("2026-09-02");
    expect(formatValue("text", "full payoff")).toBe("full payoff");
  });

  it("says a missing value is not reported, whatever its kind", () => {
    expect(NOT_REPORTED).toBe("not reported");
    for (const kind of ["money", "date", "ratio", "text"] as const) {
      expect(formatValue(kind, null)).toBe("not reported");
    }
  });
});

describe("openAlertsLabel", () => {
  it("counts alerts", () => {
    expect(openAlertsLabel(6)).toBe("6 open alerts");
    expect(openAlertsLabel(1)).toBe("1 open alert");
    expect(openAlertsLabel(0)).toBe("0 open alerts");
  });
});
