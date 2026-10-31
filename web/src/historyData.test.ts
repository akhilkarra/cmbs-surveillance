import { describe, expect, it } from "vitest";
import {
  axisMoney,
  axisRatio,
  balanceOf,
  balanceText,
  changeIn,
  dscrOf,
  dscrText,
  linePoints,
  monthAt,
  NOT_ON_TAPE,
  statusMarkers,
  statusText,
  timeKey,
} from "./historyData";
import { historyMonth, loanHistory } from "./test-data";

const { months, status_changes: changes } = loanHistory();
const [july, august, september] = months as [
  (typeof months)[0],
  (typeof months)[0],
  (typeof months)[0],
];

describe("linePoints", () => {
  it("places each month's value at its first day", () => {
    expect(linePoints(months, balanceOf)).toEqual([
      { time: "2026-07-01", value: 30400000 },
      { time: "2026-08-01", value: 30200000 },
      { time: "2026-09-01", value: 30000000 },
    ]);
  });

  it("leaves a month that does not report the value as a gap, never a zero", () => {
    const points = linePoints(months, dscrOf);
    expect(points).toEqual([
      { time: "2026-07-01", value: 1.12 },
      { time: "2026-08-01" },
      { time: "2026-09-01", value: 0.96 },
    ]);
    expect(points[1]).not.toHaveProperty("value");
  });

  it("keeps a reported zero as zero", () => {
    expect(linePoints([historyMonth("2026-07", "July 2026", { dscr: "0.00" })], dscrOf)).toEqual([
      { time: "2026-07-01", value: 0 },
    ]);
  });
});

describe("statusMarkers", () => {
  it("marks each change over its month with the bucket the loan moved into", () => {
    expect(statusMarkers(changes, "#9a5b00")).toEqual([
      {
        time: "2026-08-01",
        position: "aboveBar",
        shape: "arrowDown",
        color: "#9a5b00",
        text: "grace or late",
      },
      {
        time: "2026-09-01",
        position: "aboveBar",
        shape: "arrowDown",
        color: "#9a5b00",
        text: "30 days",
      },
    ]);
    expect(statusMarkers([], "red")).toEqual([]);
  });
});

describe("timeKey", () => {
  it("reads the time the data was given in, or a business day", () => {
    expect(timeKey("2026-08-01")).toBe("2026-08-01");
    expect(timeKey({ year: 2026, month: 8, day: 1 })).toBe("2026-08-01");
    expect(timeKey({ year: 987, month: 11, day: 30 })).toBe("0987-11-30");
  });

  it("is no month for anything else", () => {
    expect(timeKey(undefined)).toBeNull();
    expect(timeKey(null)).toBeNull();
    expect(timeKey(1785542400)).toBeNull();
    expect(timeKey({ month: 8 })).toBeNull();
  });
});

describe("finding a month", () => {
  it("finds the month at a time, or none", () => {
    expect(monthAt(months, "2026-08-01")).toBe(august);
    expect(monthAt(months, "2026-10-01")).toBeUndefined();
    expect(monthAt(months, null)).toBeUndefined();
  });

  it("finds the month's status change, or none", () => {
    expect(changeIn(changes, august)?.bucket).toBe("grace or late");
    expect(changeIn(changes, september)?.bucket).toBe("30 days");
    expect(changeIn(changes, july)).toBeUndefined();
  });
});

describe("a month in words", () => {
  it("names the payment status and its code", () => {
    expect(statusText(august)).toBe("in grace period (A)");
  });

  it("says a status is not reported, or that the tape leaves the loan out", () => {
    expect(statusText(historyMonth("2026-07", "July 2026", { payment_status: null }))).toBe(
      "not reported",
    );
    expect(
      statusText(historyMonth("2026-07", "July 2026", { on_tape: false, payment_status: null })),
    ).toBe(NOT_ON_TAPE);
    expect(NOT_ON_TAPE).toBe("loan not on the tape");
  });

  it("formats the balance and DSCR, or says they are not reported", () => {
    expect(balanceText(august)).toBe("$30,200,000.00");
    expect(dscrText(july)).toBe("1.12x");
    expect(dscrText(august)).toBe("not reported");
  });
});

describe("axis labels", () => {
  it("shorten balances and put an x after ratios", () => {
    expect(axisMoney(30200000)).toBe("$30.2M");
    expect(axisMoney(30250000)).toBe("$30.25M");
    expect(axisMoney(30255000)).toBe("$30.26M");
    expect(axisRatio(0.96)).toBe("0.96x");
    expect(axisRatio(1.125)).toBe("1.13x");
  });
});
