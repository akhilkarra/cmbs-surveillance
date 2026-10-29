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
    });
    expect(parseRoute("#/loan/Fixture%20Trust%202026-F1/2-001/2026-08")).toEqual({
      page: "loan",
      deal: "Fixture Trust 2026-F1",
      loan: "2-001",
      month: "2026-08",
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
