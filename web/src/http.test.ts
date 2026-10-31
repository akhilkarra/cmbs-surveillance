import { afterEach, describe, expect, it, vi } from "vitest";
import { asUser, http, USER_HEADER } from "./http";

afterEach(() => {
  vi.unstubAllGlobals();
});

function answer(status: number, body: unknown) {
  const fetch = vi.fn(async () => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("asUser", () => {
  it("names the user in the header the API reads", () => {
    expect(USER_HEADER).toBe("X-CMBS-User");
    expect(asUser("pat")).toEqual({ headers: { "X-CMBS-User": "pat" } });
  });
});

describe("http", () => {
  it("sends the request as given and returns the body", async () => {
    const fetch = answer(200, { rows: [] });
    await expect(http("/dashboard/book", { ...asUser("pat"), method: "GET" })).resolves.toEqual({
      rows: [],
    });
    expect(fetch).toHaveBeenCalledWith("/dashboard/book", {
      headers: { "X-CMBS-User": "pat" },
      method: "GET",
    });
  });

  it("sends no options when none are given", async () => {
    const fetch = answer(200, {});
    await http("/health");
    expect(fetch).toHaveBeenCalledWith("/health", {});
  });

  it("turns a refusal into an error with the server's words", async () => {
    answer(422, { detail: "not a month like 2026-09: '2026-13'" });
    await expect(http("/dashboard/book?month=2026-13")).rejects.toThrow(
      "not a month like 2026-09: '2026-13'",
    );
  });

  it("names the HTTP status when the server gives no words", async () => {
    answer(500, { detail: [{ msg: "boom" }] });
    await expect(http("/x")).rejects.toThrow(/^the server answered HTTP 500$/);
    answer(404, {});
    await expect(http("/x")).rejects.toThrow(/^the server answered HTTP 404$/);
    answer(400, null);
    await expect(http("/x")).rejects.toThrow(/^the server answered HTTP 400$/);
  });

  it("names the HTTP status when the refusal is not JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("<html>Bad Gateway</html>", { status: 502 })),
    );
    await expect(http("/x")).rejects.toThrow(/^the server answered HTTP 502$/);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("Internal Server Error", { status: 500 })),
    );
    await expect(http("/x")).rejects.toThrow(/^the server answered HTTP 500$/);
  });

  it("says plainly when the server cannot be reached", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    await expect(http("/x")).rejects.toThrow(/^the server could not be reached$/);
  });
});
