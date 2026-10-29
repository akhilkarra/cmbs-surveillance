import {
  CellStyleModule,
  ClientSideRowModelModule,
  type ColDef,
  type ICellRendererParams,
  ModuleRegistry,
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

ModuleRegistry.registerModules([ClientSideRowModelModule, CellStyleModule, RowAutoHeightModule]);

const THEME = themeQuartz.withParams({
  fontFamily: "inherit",
  fontSize: 14,
  backgroundColor: "var(--paper)",
  foregroundColor: "var(--ink)",
  headerBackgroundColor: "var(--panel)",
  headerTextColor: "var(--ink)",
  borderColor: "var(--rule)",
  rowHoverColor: "var(--hover)",
  accentColor: "var(--link)",
  wrapperBorderRadius: 0,
  rowBorder: { color: "var(--rule)" },
});

export function SeverityCell(props: ICellRendererParams<GridRow, GridRow["severity"]>) {
  return <span className={`severity severity-${props.value}`}>{props.value}</span>;
}

export function LoanCell(props: ICellRendererParams<GridRow, string>) {
  return <a href={props.data?.loanHref}>{props.value}</a>;
}

const COLUMNS: ColDef<GridRow>[] = [
  {
    colId: "severity",
    field: "severity",
    headerName: "Severity",
    width: 110,
    cellRenderer: SeverityCell,
  },
  { colId: "deal", field: "deal", headerName: "Deal", flex: 1.2, minWidth: 190 },
  { colId: "loan", field: "loan", headerName: "Loan", width: 90, cellRenderer: LoanCell },
  {
    colId: "alert",
    field: "alert",
    headerName: "What changed",
    flex: 2,
    minWidth: 280,
    wrapText: true,
    autoHeight: true,
  },
  {
    colId: "exposure",
    field: "exposure",
    headerName: "Book exposure",
    width: 160,
    type: "rightAligned",
  },
  {
    colId: "balance",
    field: "balance",
    headerName: "Loan balance",
    width: 160,
    type: "rightAligned",
  },
  { colId: "state", field: "state", headerName: "State", flex: 1, minWidth: 150 },
];

const DEFAULT_COLUMN: ColDef<GridRow> = { sortable: false, suppressMovable: true };

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
