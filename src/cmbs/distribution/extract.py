"""Find a table's columns by their header text and read one record per loan.

A header may be split over several rows and cells: the label of a grid position is the text
of every header cell above it, top to bottom, so "Special Servicing" over "Comments" reads
"special servicing comments". Positions whose header text differs only in a footnote marker
("Status (2)", "Strategy (3)" under one spanning cell) stay separate columns. The header is the
smallest block of rows in which every required column is found, extended by the header-like
rows just above it and below it.

Rows are matched to loans by the prospectus loan id column. A free-text column (a comment) may
spread over several cells and run past the last header column. It may also continue on the
rows below the loan: a row with nothing in the loan id column and nothing inside any other
column the table reads is more of the text above (Computershare prints each comment line as
its own row spanning the table). All of that text is joined in order.
"""

import itertools
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from cmbs.distribution.grid import Cell, Row, Title, clean, header_label, title_key
from cmbs.distribution.values import loan_id

LOAN = "loan"
MAX_HEADER_ROWS = 4


@dataclass(frozen=True)
class Column:
    """A column to find: `pattern` is searched in the header label, `exclude` vetoes a match."""

    name: str
    pattern: re.Pattern[str]
    required: bool = False
    exclude: re.Pattern[str] | None = None
    free_text: bool = False

    def matches(self, label: str) -> bool:
        return bool(self.pattern.search(label)) and not (
            self.exclude is not None and self.exclude.search(label)
        )


@dataclass(frozen=True)
class TableSpec:
    """A table of the report: its title as printed, and its columns.

    The first column is always the prospectus loan id, named LOAN.
    """

    name: str
    columns: tuple[Column, ...]

    def __post_init__(self) -> None:
        if not self.columns or self.columns[0].name != LOAN or not self.columns[0].required:
            raise ValueError(f"{self.name}: the first column must be the required {LOAN!r}")
        if sum(column.free_text for column in self.columns) > 1:
            raise ValueError(f"{self.name}: at most one free-text column")

    @property
    def free_text(self) -> str | None:
        return next((column.name for column in self.columns if column.free_text), None)

    @property
    def key(self) -> str:
        return title_key(self.name)


@dataclass(frozen=True)
class HeaderColumn:
    label: str
    start: int
    end: int
    last: bool = False


Layout = dict[str, HeaderColumn]


@dataclass(frozen=True)
class Record:
    """One loan's values from one table, as printed (whitespace normalised)."""

    loan: str
    values: dict[str, str]


def header_columns(rows: Sequence[Row]) -> list[HeaderColumn]:
    """Runs of grid positions that share one header text, left to right.

    Positions are grouped on the text as printed, footnote markers included, and labelled
    without them, so two columns told apart only by their markers stay two columns.
    """
    width = max((row.width for row in rows), default=0)
    labels: list[str] = []
    for position in range(width):
        parts: list[str] = []
        seen: set[int] = set()
        for row in rows:
            for cell in row.cells:
                if cell.start <= position < cell.end and cell.text and cell.ident not in seen:
                    seen.add(cell.ident)
                    parts.append(cell.text)
        labels.append(clean(" ".join(parts)).lower())
    columns: list[HeaderColumn] = []
    position = 0
    for printed, run in itertools.groupby(labels):
        size = len(list(run))
        if label := header_label(printed):
            columns.append(HeaderColumn(label, position, position + size))
        position += size
    if columns:
        columns[-1] = HeaderColumn(columns[-1].label, columns[-1].start, columns[-1].end, True)
    return columns


def match_header(spec: TableSpec, rows: Sequence[Row]) -> Layout | None:
    """Each column of `spec` mapped to a distinct header column; None if a required one is
    missing. Columns are matched in the order the spec lists them."""
    available = header_columns(rows)
    layout: Layout = {}
    for column in spec.columns:
        found = next(
            (
                candidate
                for candidate in available
                if candidate not in layout.values() and column.matches(candidate.label)
            ),
            None,
        )
        if found is not None:
            layout[column.name] = found
        elif column.required:
            return None
    return layout


def _cells_under(row: Row, column: HeaderColumn, *, open_ended: bool) -> list[Cell]:
    end = None if open_ended else column.end
    return [
        cell
        for cell in row.filled()
        if cell.end > column.start and (end is None or cell.start < end)
    ]


def _loan_of(row: Row, layout: Layout) -> str | None:
    cells = _cells_under(row, layout[LOAN], open_ended=False)
    return loan_id(" ".join(cell.text for cell in cells))


def _continues_header(row: Row, layout: Layout, width: int) -> bool:
    filled = row.filled()
    if not filled or _loan_of(row, layout) is not None:
        return False
    return not (len(filled) == 1 and filled[0].span * 2 >= width)


def find_header(spec: TableSpec, rows: Sequence[Row]) -> tuple[Layout, int] | None:
    """The layout and the index of the first row after the header, or None.

    The smallest block of rows with every required column is found first, then widened by the
    header-like rows below it (up to the first loan) and above it (up to a blank row), as long
    as every required column is still found and the header stays within MAX_HEADER_ROWS.
    """
    for size in range(1, MAX_HEADER_ROWS + 1):
        for start in range(len(rows) - size + 1):
            layout = match_header(spec, rows[start : start + size])
            if layout is None:
                continue
            end = start + size
            width = max(row.width for row in rows[start:end])
            while end < len(rows) and end - start < MAX_HEADER_ROWS:
                if not _continues_header(rows[end], layout, width):
                    break
                wider = match_header(spec, rows[start : end + 1])
                if wider is None:
                    break
                layout, end = wider, end + 1
            while start > 0 and end - start < MAX_HEADER_ROWS:
                if not _continues_header(rows[start - 1], layout, width):
                    break
                wider = match_header(spec, rows[start - 1 : end])
                if wider is None:
                    break
                layout, start = wider, start - 1
            return layout, end
    return None


@dataclass
class _Draft:
    loan: str
    values: dict[str, list[str]] = field(default_factory=dict)
    seen: set[tuple[str, int]] = field(default_factory=set)

    def take(self, name: str, cells: Iterable[Cell]) -> None:
        for cell in cells:
            if (name, cell.ident) in self.seen:
                continue
            self.seen.add((name, cell.ident))
            self.values.setdefault(name, []).append(cell.text)


class _Reader:
    """Reads the records of one table spec across the sections it appears in."""

    def __init__(self, spec: TableSpec) -> None:
        self.spec = spec
        self.free_text = spec.free_text
        self.drafts: dict[str, _Draft] = {}
        self.layout: Layout | None = None
        self.width = 0

    def _open_ended(self, name: str, column: HeaderColumn) -> bool:
        return name == self.free_text and column.last

    def read_section(self, rows: Sequence[Row]) -> None:
        for _, group in itertools.groupby(rows, key=lambda row: row.table):
            self._read_table(list(group))

    def _read_table(self, rows: list[Row]) -> None:
        found = find_header(self.spec, rows)
        if found is not None:
            self.layout, start = found
            self.width = max(row.width for row in rows[:start])
        elif self.layout is not None and max(row.width for row in rows) == self.width:
            start = 0  # the table carried onto another page without repeating its header
        else:
            return
        layout = self.layout
        current: _Draft | None = None
        for row in rows[start:]:
            if not row.filled():
                continue
            loan = _loan_of(row, layout)
            if loan is not None:
                current = self.drafts.setdefault(loan, _Draft(loan))
                for name, column in layout.items():
                    if name != LOAN:
                        open_ended = self._open_ended(name, column)
                        current.take(name, _cells_under(row, column, open_ended=open_ended))
            elif current is not None and self._continue_text(current, row, layout):
                continue
            else:
                current = None

    def _continue_text(self, draft: _Draft, row: Row, layout: Layout) -> bool:
        """Add a row's text to the loan above when it can only be more of the free text: the
        loan id column is empty and no filled cell sits inside another column read here."""
        name = self.free_text
        if name is None or name not in layout or _cells_under(row, layout[LOAN], open_ended=False):
            return False
        others = [column for key, column in layout.items() if key not in (LOAN, name)]
        filled = row.filled()
        if any(
            column.start <= cell.start and cell.end <= column.end
            for cell in filled
            for column in others
        ):
            return False
        draft.take(name, filled)
        return True

    def records(self, notes: list[str]) -> list[Record]:
        result = []
        for draft in self.drafts.values():
            values: dict[str, str] = {}
            for name, parts in draft.values.items():
                if name == self.free_text:
                    values[name] = " ".join(parts)
                    continue
                values[name] = parts[0]
                if len(set(parts)) > 1:
                    notes.append(
                        f"{self.spec.name}: loan {draft.loan} shows {len(set(parts))} values of "
                        f"{name}; kept the first, {parts[0]!r}"
                    )
            result.append(Record(draft.loan, values))
        return result


def sections(events: Iterable[Title | Row]) -> list[tuple[str, list[Row]]]:
    """The rows under each title, in order; rows before the first title are dropped."""
    result: list[tuple[str, list[Row]]] = []
    for event in events:
        if isinstance(event, Title):
            result.append((event.key, []))
        elif result:
            result[-1][1].append(event)
    return result


def read_table(
    spec: TableSpec, report: Sequence[tuple[str, list[Row]]], notes: list[str]
) -> list[Record] | None:
    """The records of `spec` from every section titled as it is.

    None when no section with that title has any rows (a title only in the table of contents
    does not count).
    """
    found = [rows for key, rows in report if key == spec.key and rows]
    if not found:
        return None
    reader = _Reader(spec)
    for rows in found:
        reader.read_section(rows)
    if reader.layout is None:
        notes.append(f"{spec.name}: the columns were not recognised")
    return reader.records(notes)
