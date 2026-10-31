import { afterEach, describe, expect, it, vi } from "vitest";
import { bookPath, getJson, loanPath, USER_HEADER } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

function answer(status: number, body: unknown) {
  const fetch = vi.fn(async () => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("paths", () => {
  it("name the book's month only when one is asked for", () => {
    expect(bookPath(null)).toBe("/dashboard/book");
    expect(bookPath("2026-09")).toBe("/dashboard/book?month=2026-09");
    expect(bookPath("a&b")).toBe("/dashboard/book?month=a%26b");
  });

  it("name the loan's deal, month and the day it is read as of", () => {
    expect(loanPath("Fixture Trust 2026-F1", "2", null, null)).toBe(
      "/dashboard/loan?deal=Fixture+Trust+2026-F1&loan=2",
    );
    expect(loanPath("A&B", "2", "2026-08", null)).toBe(
      "/dashboard/loan?deal=A%26B&loan=2&month=2026-08",
    );
    expect(loanPath("A", "2", null, "2026-09-01")).toBe(
      "/dashboard/loan?deal=A&loan=2&as_of=2026-09-01",
    );
    expect(loanPath("A", "2", "2026-08", "2026-09-01")).toBe(
      "/dashboard/loan?deal=A&loan=2&month=2026-08&as_of=2026-09-01",
    );
  });
});

describe("getJson", () => {
  it("asks as the user and returns the body", async () => {
    const fetch = answer(200, { rows: [] });
    expect(USER_HEADER).toBe("X-CMBS-User");
    await expect(getJson("/dashboard/book", "pat")).resolves.toEqual({ rows: [] });
    expect(fetch).toHaveBeenCalledWith("/dashboard/book", { headers: { "X-CMBS-User": "pat" } });
  });

  it("turns a refusal into an error with the server's words", async () => {
    answer(422, { detail: "not a month like 2026-09: '2026-13'" });
    await expect(getJson("/dashboard/book?month=2026-13", "pat")).rejects.toThrow(
      "not a month like 2026-09: '2026-13'",
    );
  });

  it("names the HTTP status when the server gives no words", async () => {
    answer(500, { detail: [{ msg: "boom" }] });
    await expect(getJson("/x", "pat")).rejects.toThrow(/^the server answered HTTP 500$/);
    answer(404, {});
    await expect(getJson("/x", "pat")).rejects.toThrow(/^the server answered HTTP 404$/);
  });
});
