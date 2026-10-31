import { describe, expect, it } from "vitest";
import { bookHref, loanHref, parseRoute, userFrom } from "./route";

describe("parseRoute", () => {
  it("opens the book for no hash, #/ and #/book", () => {
    for (const hash of ["", "#", "#/", "#/book", "#book"]) {
      expect(parseRoute(hash)).toEqual({ page: "book", month: null });
    }
  });

  it("reads the book's month", () => {
    expect(parseRoute("#/book/2026-09")).toEqual({ page: "book", month: "2026-09" });
  });

  it("reads a loan, with or without a month, decoding names", () => {
    expect(parseRoute("#/loan/Fixture%20Trust%202026-F1/2")).toEqual({
      page: "loan",
      deal: "Fixture Trust 2026-F1",
      loan: "2",
      month: null,
      asOf: null,
    });
    expect(parseRoute("#/loan/Fixture%20Trust%202026-F1/2-001/2026-08")).toEqual({
      page: "loan",
      deal: "Fixture Trust 2026-F1",
      loan: "2-001",
      month: "2026-08",
      asOf: null,
    });
  });

  it("reads the day a loan is read as of", () => {
    expect(parseRoute("#/loan/Deal/2?as_of=2026-09-01")).toEqual({
      page: "loan",
      deal: "Deal",
      loan: "2",
      month: null,
      asOf: "2026-09-01",
    });
    expect(parseRoute("#/loan/Deal/2/2026-08?x=1&as_of=2026-09-01")).toEqual({
      page: "loan",
      deal: "Deal",
      loan: "2",
      month: "2026-08",
      asOf: "2026-09-01",
    });
    expect(parseRoute("#/loan/Deal/2?as_of=")).toMatchObject({ month: null, asOf: null });
    expect(parseRoute("#/loan/Deal/2?")).toMatchObject({ month: null, asOf: null });
    expect(parseRoute("#/book/2026-09?as_of=2026-09-01")).toEqual({
      page: "book",
      month: "2026-09",
    });
  });

  it("says anything else is missing", () => {
    for (const hash of [
      "#/book/2026-09/extra",
      "#/loan",
      "#/loan/Deal",
      "#/loan//2",
      "#/loan/Deal/",
      "#/loan/Deal/2/2026-09/extra",
      "#/runs",
      "#/2026-09",
      // Only a leading "#/" is a route's start.
      "book/#/2026-09",
      // A malformed escape cannot be decoded.
      "#/loan/%E0%A4%A/2",
      "#/book/%",
    ]) {
      expect(parseRoute(hash)).toEqual({ page: "missing", hash });
    }
  });
});

describe("links", () => {
  it("point at a month's book and a loan's month", () => {
    expect(bookHref("2026-09")).toBe("#/book/2026-09");
    expect(loanHref("Fixture Trust 2026-F1", "2/3", "2026-09")).toBe(
      "#/loan/Fixture%20Trust%202026-F1/2%2F3/2026-09",
    );
  });

  it("round-trip through parseRoute", () => {
    expect(parseRoute(loanHref("A/B Trust", "7", "2026-09"))).toEqual({
      page: "loan",
      deal: "A/B Trust",
      loan: "7",
      month: "2026-09",
      asOf: null,
    });
  });
});

describe("userFrom", () => {
  it("reads ?user=, trimmed", () => {
    expect(userFrom("?user=pat")).toBe("pat");
    expect(userFrom("?x=1&user=%20alex%20")).toBe("alex");
  });

  it("is null when no user is named", () => {
    expect(userFrom("")).toBeNull();
    expect(userFrom("?user=")).toBeNull();
    expect(userFrom("?user=%20%20")).toBeNull();
  });
});
