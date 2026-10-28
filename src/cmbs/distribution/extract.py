"""Find a table's columns by their header text and read one record per loan.

A header may be split over several rows and cells: the label of a grid position is the text
of every header cell above it, top to bottom, so "Special Servicing" over "Comments" reads
"special servicing comments". The header is the smallest block of rows in which every required
column is found, extended by the rows that follow it until the first loan.

Rows are matched to loans by the prospectus loan id column. A free-text column (a comment) may
spread over several cells, run past the last header column, or continue on following rows
whose only filled cells lie inside it; all of that text is joined in order.
"""

import itertools
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from cmbs.distribution.grid import Cell, Row, Title, header_label, title_key
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
    """Runs of grid positions that share one header label, left to right."""
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
        labels.append(header_label(" ".join(parts)))
    columns: list[HeaderColumn] = []
    position = 0
    for label, run in itertools.groupby(labels):
        size = len(list(run))
        if label:
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
    """The layout and the index of the first row after the header, or None."""
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
        self.free_text = {column.name for column in spec.columns if column.free_text}
        self.drafts: dict[str, _Draft] = {}
        self.layout: Layout | None = None
        self.width = 0

    def _open_ended(self, name: str, column: HeaderColumn) -> bool:
        return name in self.free_text and column.last

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
        """Add a row's text to the loan above when all its filled cells sit in free text."""
        targets: list[tuple[str, Cell]] = []
        for cell in row.filled():
            name = next(
                (
                    name
                    for name in self.free_text
                    if name in layout
                    and cell.start >= layout[name].start
                    and (self._open_ended(name, layout[name]) or cell.end <= layout[name].end)
                ),
                None,
            )
            if name is None:
                return False
            targets.append((name, cell))
        for name, cell in targets:
            draft.take(name, [cell])
        return True

    def records(self, notes: list[str]) -> list[Record]:
        result = []
        for draft in self.drafts.values():
            values: dict[str, str] = {}
            for name, parts in draft.values.items():
                if name in self.free_text:
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
