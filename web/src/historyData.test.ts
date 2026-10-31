import { describe, expect, it } from "vitest";
import {
  axisMoney,
  axisRatio,
  balanceOf,
  balanceText,
  changeIn,
  dscrOf,
  dscrText,
  filingText,
  linePoints,
  lineSegments,
  monthAt,
  NO_TAPE,
  NOT_ON_TAPE,
  statusLane,
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

describe("lineSegments", () => {
  const month = (key: string, dscr: string | null) =>
    historyMonth(key, key, { day: `${key}-01`, dscr });
  const months5 = [
    month("2026-05", "1.30"),
    month("2026-06", "1.25"),
    month("2026-07", null),
    month("2026-08", "1.10"),
    month("2026-09", null),
  ];
  const times = ["2026-05-01", "2026-06-01", "2026-07-01", "2026-08-01", "2026-09-01"];

  it("draws each run of reported months as a line of its own over every month", () => {
    expect(lineSegments(months5, dscrOf)).toEqual([
      [
        { time: times[0], value: 1.3 },
        { time: times[1], value: 1.25 },
        { time: times[2] },
        { time: times[3] },
        { time: times[4] },
      ],
      [
        { time: times[0] },
        { time: times[1] },
        { time: times[2] },
        { time: times[3], value: 1.1 },
        { time: times[4] },
      ],
    ]);
  });

  it("draws a value every month reports as one line", () => {
    expect(lineSegments(months, balanceOf)).toEqual([linePoints(months, balanceOf)]);
  });

  it("keeps the months of a value no month reports, as one line without values", () => {
    const none = [month("2026-07", null), month("2026-08", null)];
    expect(lineSegments(none, dscrOf)).toEqual([[{ time: "2026-07-01" }, { time: "2026-08-01" }]]);
    expect(lineSegments([], dscrOf)).toEqual([[]]);
  });

  it("starts a new line after every gap, however short the run", () => {
    const alternate = [
      month("2026-05", "1.30"),
      month("2026-06", null),
      month("2026-07", "1.20"),
      month("2026-08", null),
      month("2026-09", "1.10"),
    ];
    const segments = lineSegments(alternate, dscrOf);
    expect(segments.map((points) => points.filter((p) => p.value !== undefined))).toEqual([
      [{ time: times[0], value: 1.3 }],
      [{ time: times[2], value: 1.2 }],
      [{ time: times[4], value: 1.1 }],
    ]);
  });
});

describe("statusLane", () => {
  it("has a point at every month, reported or not", () => {
    const gap = historyMonth("2026-10", "October 2026", { on_tape: false, source: null });
    expect(statusLane([...months, gap])).toEqual([
      { time: "2026-07-01", value: 0 },
      { time: "2026-08-01", value: 0 },
      { time: "2026-09-01", value: 0 },
      { time: "2026-10-01", value: 0 },
    ]);
  });
});

describe("filingText", () => {
  it("names the filing a month came from, or says no tape was filed", () => {
    expect(filingText(august)).toBe("from ABS-EE 0009900001-26-000417, filed 2026-08-29");
    expect(filingText(historyMonth("2026-08", "August 2026", { source: null }))).toBe(
      "no tape filed",
    );
    expect(NO_TAPE).toBe("no tape filed");
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
    expect(
      statusText(
        historyMonth("2026-07", "July 2026", {
          on_tape: false,
          payment_status: null,
          source: null,
        }),
      ),
    ).toBe(NO_TAPE);
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
