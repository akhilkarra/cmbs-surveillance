import { render, screen, waitFor } from "@testing-library/react";
import type { ICellRendererParams } from "ag-grid-community";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  BookContent,
  BookPage,
  COLUMNS,
  DEFAULT_COLUMN,
  GRID_THEME_PARAMS,
  LoanCell,
  SeverityCell,
} from "./BookPage";
import type { GridRow } from "./bookGrid";
import styles from "./styles.css?raw";
import { bookPage, F1, F2 } from "./test-data";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the grid's set-up", () => {
  it("takes every colour from a token the page defines, and the page's font", () => {
    const { fontFamily, fontSize, wrapperBorderRadius, rowBorder, ...colours } = GRID_THEME_PARAMS;
    expect([fontFamily, fontSize, wrapperBorderRadius]).toEqual(["inherit", 14, 0]);
    const used = [...Object.values(colours), rowBorder.color];
    expect(used).toHaveLength(8);
    for (const colour of used) {
      const token = /^var\((--[a-z-]+)\)$/.exec(colour)?.[1];
      expect(token, colour).toBeDefined();
      expect(styles).toContain(`${token}: #`);
    }
  });

  it("right-aligns money, wraps what changed, and never reorders", () => {
    const byId = Object.fromEntries(COLUMNS.map((column) => [column.colId, column]));
    expect(COLUMNS.map((column) => column.colId)).toEqual([
      "severity",
      "deal",
      "loan",
      "alert",
      "exposure",
      "balance",
      "state",
    ]);
    expect([byId.exposure?.type, byId.balance?.type]).toEqual(["rightAligned", "rightAligned"]);
    expect([byId.alert?.wrapText, byId.alert?.autoHeight]).toEqual([true, true]);
    expect(DEFAULT_COLUMN).toEqual({ sortable: false, suppressMovable: true });
  });
});

function cell<V>(value: V, data?: Partial<GridRow>) {
  return { value, data } as unknown as ICellRendererParams<GridRow, V>;
}

/** The grid's rows in row-index order, each cell's text by column id. */
function gridRows(container: HTMLElement): Record<string, string>[] {
  return [...container.querySelectorAll(".ag-row")]
    .sort((a, b) => Number(a.getAttribute("row-index")) - Number(b.getAttribute("row-index")))
    .map((row) =>
      Object.fromEntries(
        [...row.querySelectorAll("[col-id]")].map((c) => [
          c.getAttribute("col-id"),
          c.textContent?.trim(),
        ]),
      ),
    );
}

describe("grid cells", () => {
  it("mark severity with its own class", () => {
    render(<SeverityCell {...cell<GridRow["severity"]>("high")} />);
    const mark = screen.getByText("high");
    expect(mark.className).toBe("severity severity-high");
  });

  it("link a loan to its page", () => {
    render(<LoanCell {...cell("7", { loanHref: "#/loan/F1/7/2026-09" })} />);
    expect(screen.getByRole("link", { name: "7" }).getAttribute("href")).toBe(
      "#/loan/F1/7/2026-09",
    );
  });
});

describe("BookContent", () => {
  it("heads the page with the month, its neighbours and the count", () => {
    render(<BookContent data={bookPage()} />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Open alerts on the book");
    expect(screen.getByTestId("book-month").textContent).toBe("September 2026");
    expect(screen.getByTestId("book-count").textContent).toBe("2 open alerts");
    const earlier = screen.getByRole("link", { name: "Previous month, August 2026" });
    expect([earlier.getAttribute("href"), earlier.textContent]).toEqual([
      "#/book/2026-08",
      "August 2026",
    ]);
    const later = screen.getByRole("link", { name: "Next month, October 2026" });
    expect([later.getAttribute("href"), later.textContent]).toEqual([
      "#/book/2026-10",
      "October 2026",
    ]);
  });

  it("shows the alerts in the order the API sent them", async () => {
    // AG Grid reports a feature used without its module on the console.
    const complaints = [vi.spyOn(console, "error"), vi.spyOn(console, "warn")];
    const { container } = render(<BookContent data={bookPage()} />);
    await waitFor(() => expect(gridRows(container)).toHaveLength(2));
    for (const complaint of complaints) {
      expect(complaint).not.toHaveBeenCalled();
      complaint.mockRestore();
    }
    expect(gridRows(container)).toEqual([
      {
        severity: "high",
        deal: F1,
        loan: "7",
        alert: "transferred to special servicing on 2026-09-02",
        exposure: "$25,000,000.00",
        balance: "$9,000,000.00",
        state: "new",
      },
      {
        severity: "medium",
        deal: F2,
        loan: "3",
        alert: "payment status worsened: current to 60 days",
        exposure: "$40,000,000.00",
        balance: "not reported",
        state: "acknowledged by pat on 2026-10-28",
      },
    ]);
    const headers = [...container.querySelectorAll(".ag-header-cell-text")].map(
      (header) => header.textContent,
    );
    expect(headers).toEqual([
      "Severity",
      "Deal",
      "Loan",
      "What changed",
      "Book exposure",
      "Loan balance",
      "State",
    ]);
    expect(container.querySelector(".ag-header-cell-sortable")).toBeNull();
  });

  it("says why the book is empty instead of drawing a grid", () => {
    const { container } = render(
      <BookContent
        data={bookPage({ rows: [], note: "no open alerts on the book for October 2026" })}
      />,
    );
    expect(screen.getByText("no open alerts on the book for October 2026").className).toBe("empty");
    expect(screen.getByTestId("book-count").textContent).toBe("0 open alerts");
    expect(container.querySelector(".ag-root")).toBeNull();
  });
});

describe("BookPage", () => {
  it("reads the month's book as the user and shows it when ready", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify(bookPage())));
    vi.stubGlobal("fetch", fetch);
    render(<BookPage user="pat" month="2026-09" />);
    const page = screen.getByTestId("book");
    expect(page.getAttribute("data-state")).toBe("loading");
    expect(screen.getByText("Reading the book's alerts").className).toBe("status");
    await waitFor(() => expect(page.getAttribute("data-state")).toBe("ready"));
    expect(screen.getByTestId("book-month").textContent).toBe("September 2026");
    expect(screen.queryByText("Reading the book's alerts")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(fetch).toHaveBeenCalledWith("/dashboard/book?month=2026-09", {
      headers: { "X-CMBS-User": "pat" },
    });
  });

  it("shows a refusal in the server's words", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () => new Response(JSON.stringify({ detail: "unknown user: nemo" }), { status: 401 }),
      ),
    );
    render(<BookPage user="nemo" month={null} />);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("unknown user: nemo");
    expect(alert.className).toBe("status failed");
    expect(screen.getByTestId("book").getAttribute("data-state")).toBe("error");
    expect(screen.queryByText("Reading the book's alerts")).toBeNull();
  });
});
