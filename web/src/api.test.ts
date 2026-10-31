import { afterEach, describe, expect, it, vi } from "vitest";
import { bookRead, loanRead } from "./api";
import { asUser } from "./http";
import { bookPage, loanPage } from "./test-data";

afterEach(() => {
  vi.unstubAllGlobals();
});

function answer(body: unknown) {
  const fetch = vi.fn(async () => new Response(JSON.stringify(body)));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("bookRead", () => {
  it("names the book's month only when one is asked for", () => {
    expect(bookRead(null).key).toBe("/dashboard/book");
    expect(bookRead("2026-09").key).toBe("/dashboard/book?month=2026-09");
    expect(bookRead("a&b").key).toBe("/dashboard/book?month=a%26b");
  });

  it("reads the book through the generated client, as the user", async () => {
    const fetch = answer(bookPage());
    await expect(bookRead("2026-09").send(asUser("pat"))).resolves.toEqual(bookPage());
    expect(fetch).toHaveBeenCalledWith("/dashboard/book?month=2026-09", {
      headers: { "X-CMBS-User": "pat" },
      method: "GET",
    });
    await bookRead(null).send(asUser("alex"));
    expect(fetch).toHaveBeenLastCalledWith("/dashboard/book", {
      headers: { "X-CMBS-User": "alex" },
      method: "GET",
    });
  });
});

describe("loanRead", () => {
  it("names the loan's deal and month", () => {
    expect(loanRead("Fixture Trust 2026-F1", "2", null).key).toBe(
      "/dashboard/loan?deal=Fixture+Trust+2026-F1&loan=2",
    );
    expect(loanRead("A&B", "2", "2026-08").key).toBe(
      "/dashboard/loan?deal=A%26B&loan=2&month=2026-08",
    );
  });

  it("reads the loan through the generated client, as the user", async () => {
    const fetch = answer(loanPage());
    await expect(loanRead("A&B", "2", "2026-08").send(asUser("pat"))).resolves.toEqual(loanPage());
    expect(fetch).toHaveBeenCalledWith("/dashboard/loan?deal=A%26B&loan=2&month=2026-08", {
      headers: { "X-CMBS-User": "pat" },
      method: "GET",
    });
    await loanRead("A&B", "2", null).send(asUser("pat"));
    expect(fetch).toHaveBeenLastCalledWith("/dashboard/loan?deal=A%26B&loan=2", {
      headers: { "X-CMBS-User": "pat" },
      method: "GET",
    });
  });
});
