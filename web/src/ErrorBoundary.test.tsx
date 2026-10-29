import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ErrorBoundary } from "./ErrorBoundary";

function Fails({ message }: { message: string }): never {
  throw new Error(message);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ErrorBoundary", () => {
  it("shows its children while nothing fails", () => {
    render(
      <ErrorBoundary resetKey="#/book">
        <p>fine</p>
      </ErrorBoundary>,
    );
    expect(screen.getByText("fine")).toBeTruthy();
    expect(screen.queryByTestId("page-error")).toBeNull();
  });

  it("keeps the error until the page changes, then tries again", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const { rerender } = render(
      <ErrorBoundary resetKey="#/book">
        <Fails message="the grid broke" />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("alert").textContent).toBe("the grid broke");
    rerender(
      <ErrorBoundary resetKey="#/book">
        <p>fine</p>
      </ErrorBoundary>,
    );
    expect(screen.getByRole("alert").textContent).toBe("the grid broke");
    rerender(
      <ErrorBoundary resetKey="#/book/2026-08">
        <p>fine</p>
      </ErrorBoundary>,
    );
    expect(screen.getByText("fine")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
