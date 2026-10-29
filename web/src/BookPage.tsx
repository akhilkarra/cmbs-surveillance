import {
  CellStyleModule,
  ClientSideRowModelModule,
  type ColDef,
  type ICellRendererParams,
  RowAutoHeightModule,
  themeQuartz,
} from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { type BookPageData, bookPath } from "./api";
import { type GridRow, gridRow } from "./bookGrid";
import { openAlertsLabel } from "./format";
import { Loadable } from "./Loadable";
import { bookHref } from "./route";
import { useApi } from "./useApi";

/** The grid takes its type and colours from the page's tokens, so it follows dark mode. */
export const GRID_THEME_PARAMS = {
  fontFamily: "inherit",
  fontSize: 14,
  // Narrower than the theme's 16px, so the columns need less width before the grid scrolls.
  cellHorizontalPadding: 12,
  backgroundColor: "var(--paper)",
  foregroundColor: "var(--ink)",
  headerBackgroundColor: "var(--panel)",
  headerTextColor: "var(--ink)",
  borderColor: "var(--rule)",
  rowHoverColor: "var(--hover)",
  accentColor: "var(--link)",
  wrapperBorderRadius: 0,
  rowBorder: { color: "var(--rule)" },
};

const THEME = themeQuartz.withParams(GRID_THEME_PARAMS);

export function SeverityCell(props: ICellRendererParams<GridRow, GridRow["severity"]>) {
  return <span className={`severity severity-${props.value}`}>{props.value}</span>;
}

/** A loan links to its page; an alert on a deal ("19 loans") has no one loan to open. */
export function LoanCell(props: ICellRendererParams<GridRow, string>) {
  const href = props.data?.loanHref;
  return href ? <a href={href}>{props.value}</a> : <span>{props.value}</span>;
}

// Text that can run long wraps, and its row grows to fit it (autoHeight), so no line spills onto
// the next row. Below the columns' minimum widths together (a window about 1,060px wide) the
// grid scrolls sideways rather than squeezing or hiding a column.
const WRAPS = { wrapText: true, autoHeight: true } as const;

export const COLUMNS: ColDef<GridRow>[] = [
  {
    colId: "severity",
    field: "severity",
    headerName: "Severity",
    width: 104,
    cellRenderer: SeverityCell,
  },
  { colId: "deal", field: "deal", headerName: "Deal", flex: 1.2, minWidth: 130, ...WRAPS },
  { colId: "loan", field: "loan", headerName: "Loan", width: 92, cellRenderer: LoanCell },
  {
    colId: "alert",
    field: "alert",
    headerName: "What changed",
    flex: 2,
    minWidth: 240,
    ...WRAPS,
  },
  {
    colId: "exposure",
    field: "exposure",
    headerName: "Book exposure",
    width: 140,
    type: "rightAligned",
  },
  {
    colId: "balance",
    field: "balance",
    headerName: "Loan balance",
    width: 140,
    type: "rightAligned",
  },
  { colId: "state", field: "state", headerName: "State", flex: 1, minWidth: 130, ...WRAPS },
];

// Rows and columns keep the API's order: no sorting, no moving.
export const DEFAULT_COLUMN: ColDef<GridRow> = { sortable: false, suppressMovable: true };

export function BookContent({ data }: { data: BookPageData }) {
  const rows = data.rows.map((row) => gridRow(row, data.month.key));
  return (
    <>
      <header className="page-head">
        <h1>Open alerts on the book</h1>
        <nav className="months" aria-label="Month">
          <a
            href={bookHref(data.previous.key)}
            aria-label={`Previous month, ${data.previous.label}`}
          >
            {data.previous.label}
          </a>
          <strong data-testid="book-month" aria-current="page">
            {data.month.label}
          </strong>
          <a href={bookHref(data.next.key)} aria-label={`Next month, ${data.next.label}`}>
            {data.next.label}
          </a>
        </nav>
        <p className="lede">
          <span data-testid="book-count">{openAlertsLabel(data.rows.length)}</span>, most severe
          first, then by the book's exposure and the loan's balance.
        </p>
      </header>
      {data.note === null ? (
        <div className="grid">
          <AgGridReact<GridRow>
            modules={[ClientSideRowModelModule, CellStyleModule, RowAutoHeightModule]}
            theme={THEME}
            rowData={rows}
            columnDefs={COLUMNS}
            defaultColDef={DEFAULT_COLUMN}
            getRowId={(params) => params.data.id}
            domLayout="autoHeight"
            suppressCellFocus
          />
        </div>
      ) : (
        <p className="empty">{data.note}</p>
      )}
    </>
  );
}

export function BookPage({ user, month }: { user: string; month: string | null }) {
  const resource = useApi<BookPageData>(bookPath(month), user);
  return (
    <Loadable testId="book" resource={resource} loading="Reading the book's alerts">
      {(data) => <BookContent data={data} />}
    </Loadable>
  );
}
