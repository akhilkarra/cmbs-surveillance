import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App, ChooseUser, Page } from "./App";
import { bookPage, F1, loanPage } from "./test-data";

function serve() {
  const fetch = vi.fn(
    async (path: string) =>
      new Response(JSON.stringify(path.startsWith("/dashboard/loan") ? loanPage() : bookPage())),
  );
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

function go(url: string) {
  window.history.replaceState(null, "", url);
}

beforeEach(() => {
  go("/");
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Page", () => {
  it("opens the book for a month", async () => {
    const fetch = serve();
    render(<Page hash="#/book/2026-09" user="pat" />);
    await screen.findByTestId("book-month");
    expect(fetch).toHaveBeenCalledWith("/dashboard/book?month=2026-09", expect.anything());
  });

  it("opens a loan", async () => {
    const fetch = serve();
    render(<Page hash="#/loan/Fixture%20Trust%202026-F1/2/2026-09" user="pat" />);
    await screen.findByTestId("loan-title");
    expect(fetch).toHaveBeenCalledWith(
      "/dashboard/loan?deal=Fixture+Trust+2026-F1&loan=2&month=2026-09",
      expect.anything(),
    );
  });

  it("says when nothing lives at an address", () => {
    render(<Page hash="#/runs" user="pat" />);
    expect(screen.getByTestId("missing").textContent).toBe(
      "Page not foundNothing lives at #/runs. Open the book's alerts.",
    );
    expect(screen.getByRole("link", { name: "Open the book's alerts" }).getAttribute("href")).toBe(
      "#/book",
    );
  });

  it("says a malformed address is not found instead of blanking the app", () => {
    render(<Page hash="#/loan/%E0%A4%A/2" user="pat" />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Page not found");
  });
});

describe("App's error boundary", () => {
  it("shows why a page failed to draw, and recovers on another page", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify(
              bookPage({ rows: [{ ...bookPage().rows[0], exposure: "lots" } as never] }),
            ),
          ),
      ),
    );
    const quiet = vi.spyOn(console, "error").mockImplementation(() => {});
    go("/?user=pat#/book/2026-09");
    render(<App />);
    const failed = await screen.findByTestId("page-error");
    expect(within(failed).getByRole("heading").textContent).toBe("This page could not be shown");
    expect(within(failed).getByRole("alert").textContent).toBe(
      "not an amount in dollars and cents: lots",
    );
    expect(within(failed).getByRole("link").getAttribute("href")).toBe("#/book");
    expect(screen.getByText("Reading as pat")).toBeTruthy();
    quiet.mockRestore();
    act(() => {
      window.location.hash = "#/runs";
    });
    await waitFor(() => expect(screen.getByTestId("missing")).toBeTruthy());
    expect(screen.queryByTestId("page-error")).toBeNull();
  });
});

describe("ChooseUser", () => {
  it("passes on a trimmed name and ignores a blank one", () => {
    const chosen = vi.fn();
    render(<ChooseUser onChoose={chosen} />);
    const field = screen.getByLabelText("User name");
    fireEvent.change(field, { target: { value: "   " } });
    // The form never leaves the page: submitting it is cancelled.
    expect(fireEvent.submit(field.closest("form") as HTMLFormElement)).toBe(false);
    expect(chosen).not.toHaveBeenCalled();
    fireEvent.change(field, { target: { value: " pat " } });
    fireEvent.click(screen.getByRole("button", { name: "Open the dashboard" }));
    expect(chosen).toHaveBeenCalledExactlyOnceWith("pat");
  });
});

describe("App", () => {
  it("asks who is reading, then keeps the name in the address", async () => {
    serve();
    go("/#/book/2026-09");
    render(<App />);
    expect(screen.queryByText(/Reading as/)).toBeNull();
    fireEvent.change(screen.getByLabelText("User name"), { target: { value: "pat" } });
    fireEvent.submit(screen.getByTestId("choose-user").querySelector("form") as HTMLFormElement);
    expect(window.location.search).toBe("?user=pat");
    expect(window.location.hash).toBe("#/book/2026-09");
    expect(screen.getByText("Reading as pat")).toBeTruthy();
    await screen.findByTestId("book-month");
  });

  it("follows the hash from page to page", async () => {
    serve();
    go("/?user=pat#/book/2026-09");
    render(<App />);
    await screen.findByTestId("book-month");
    expect(screen.getByRole("link", { name: "CMBS surveillance" }).getAttribute("href")).toBe(
      "#/book",
    );
    act(() => {
      window.location.hash = `#/loan/${encodeURIComponent(F1)}/2`;
    });
    await waitFor(() =>
      expect(screen.getByTestId("loan-title").textContent).toBe(`Loan 2 of ${F1}`),
    );
  });

  it("stops following the hash once closed", () => {
    serve();
    go("/?user=pat#/runs");
    const removed = vi.spyOn(window, "removeEventListener");
    const { unmount } = render(<App />);
    unmount();
    expect(removed).toHaveBeenCalledWith("hashchange", expect.any(Function));
    removed.mockRestore();
  });
});
