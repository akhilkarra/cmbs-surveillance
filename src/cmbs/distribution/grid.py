"""Flatten a distribution report's HTML into section titles and table rows, in document order.

Administrators render each report page as HTML tables, often nested in layout tables, with
headers broken across cells and rows and long text split over several cells. Nothing here
relies on fixed positions: every row is laid out on a grid that honours `colspan` and
`rowspan`, so a column is a range of grid positions that later steps find by its header text.

A section title is any cell or text block whose words equal a known title, so a title found
in the table of contents opens an empty section and does no harm.
"""

import itertools
import re
import unicodedata
from collections.abc import Collection, Iterator
from dataclasses import dataclass

import lxml.etree
import lxml.html
from lxml.html import HtmlElement

from cmbs.errors import Refused

MAX_SPAN = 64
# A title shares its row with at most a page number or a date; a header row with a cell that
# happens to read like a title has more cells than this.
MAX_TITLE_ROW_CELLS = 3
_WHITESPACE = re.compile(r"\s+")
# Zero-width spaces and joiners, word joiner, byte-order mark, soft hyphen.
_INVISIBLE: dict[int, None] = dict.fromkeys((0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0xAD))
_NOT_WORD = re.compile(r"[^a-z0-9&]+")
_CONTINUED = re.compile(r"\s+(continued|cont d|cont)$")
# Footnote markers in headers: "(1)", or superscript digits as in "Status\u00b9".
_FOOTNOTE = re.compile(r"\(\s*\d{1,2}\s*\)|[\u00b9\u00b2\u00b3\u2070-\u2079]+")
# Elements whose boundaries separate words even when the markup has no space between them.
_SEPARATING = ("br", "p", "div", "li", "td", "th", "tr", "table", "h1", "h2", "h3", "h4", "h5")
_TEXT_BLOCKS = frozenset({"p", "div", "span", "font", "b", "strong", "h1", "h2", "h3", "h4", "h5"})


class UnreadableReport(Refused):
    """The file is not an HTML distribution report this module can read."""


def clean(text: str) -> str:
    """Collapse whitespace, including non-breaking and zero-width characters.

    NFC, not NFKC: compatibility folding would turn a superscript footnote mark into a plain
    digit ("Status\u00b9" into "Status1"), which no header pattern could then tell apart.
    """
    text = unicodedata.normalize("NFC", text).translate(_INVISIBLE)
    return _WHITESPACE.sub(" ", text).strip()


def title_key(text: str) -> str:
    """Words only, so 'Mortgage Loan Detail (Part 2)' and 'Mortgage Loan Detail - Part 2' meet.

    A trailing '(continued)' is dropped: a table carried onto the next page keeps its section.
    """
    words = _NOT_WORD.sub(" ", clean(text).lower()).strip()
    return _CONTINUED.sub("", words).strip()


def header_label(text: str) -> str:
    """Lower-case header text without footnote markers: 'Current P & I Advances (1)' and
    'Current P & I Advances\u00b9' both read 'current p&i advances'."""
    text = _FOOTNOTE.sub(" ", clean(text).lower())
    return clean(re.sub(r"\s*&\s*", "&", text))


@dataclass(frozen=True)
class Cell:
    """One table cell placed on its table's grid. `ident` is shared by the rows a rowspan covers."""

    ident: int
    text: str
    start: int
    span: int = 1

    @property
    def end(self) -> int:
        return self.start + self.span


@dataclass(frozen=True)
class Row:
    """A table row, its cells ordered by grid position. `table` numbers the source table."""

    table: int
    cells: tuple[Cell, ...]

    @property
    def width(self) -> int:
        return max((cell.end for cell in self.cells), default=0)

    def filled(self) -> tuple[Cell, ...]:
        return tuple(cell for cell in self.cells if cell.text)


@dataclass(frozen=True)
class Title:
    """A section title; `key` is its `title_key`."""

    key: str
    text: str


def _span(value: str | None) -> int:
    """A colspan or rowspan attribute, read leniently: missing, garbage or zero means 1."""
    try:
        number = int(str(value).strip())
    except ValueError:
        return 1
    return min(max(number, 1), MAX_SPAN)


class _TableGrid:
    """Places the rows of one table on a grid, carrying rowspan cells down."""

    def __init__(self, number: int, idents: Iterator[int]) -> None:
        self.number = number
        self._idents = idents
        self._carried: dict[int, tuple[Cell, int]] = {}

    def place(self, cells: list[tuple[str, int, int]]) -> Row:
        placed: list[Cell] = []
        occupied: set[int] = set()
        carried: dict[int, tuple[Cell, int]] = {}
        for start, (cell, rows_left) in self._carried.items():
            placed.append(cell)
            occupied.update(range(cell.start, cell.end))
            if rows_left > 1:
                carried[start] = (cell, rows_left - 1)
        column = 0
        for text, colspan, rowspan in cells:
            while column in occupied:
                column += 1
            cell = Cell(next(self._idents), text, column, colspan)
            placed.append(cell)
            if rowspan > 1:
                carried[column] = (cell, rowspan - 1)
            column += colspan
        self._carried = carried
        return Row(self.number, tuple(sorted(placed, key=lambda cell: cell.start)))


def read_document(content: bytes) -> HtmlElement:
    """Parse the report, dropping scripts, styles and comments, with word breaks made explicit."""
    try:
        root = lxml.html.fromstring(content)
    except (lxml.etree.ParserError, ValueError) as error:
        raise UnreadableReport("the distribution report is empty or not HTML") from error
    for junk in root.xpath("//script | //style | //comment()"):
        junk.drop_tree()
    for element in root.iter(*_SEPARATING):
        element.tail = " " + (element.tail or "")
        if element.tag != "br":
            element.text = " " + (element.text or "")
    return root


def document_text(root: HtmlElement) -> str:
    return clean(" ".join(root.itertext()))


def _cells_of(row: HtmlElement) -> list[HtmlElement]:
    return list(row.iterchildren("td", "th"))  # the HTML parser lower-cases tag names


def _text_of(element: HtmlElement) -> str:
    return clean(" ".join(element.itertext()))


def flatten(root: HtmlElement, titles: Collection[str]) -> list[Title | Row]:
    """Every title and table row, in document order. `titles` are title keys to recognise.

    A row whose cells hold nested tables is layout, not data: its nested rows are read instead.
    Title text outside any table (a heading in a paragraph) is recognised too.
    """
    events: list[Title | Row] = []
    grids: dict[HtmlElement | None, _TableGrid] = {}
    idents = itertools.count()
    for element in root.iter(lxml.etree.Element):  # elements only: comments are already gone
        tag = element.tag
        if tag == "tr":
            cells = _cells_of(element)
            if any(cell.find(".//table") is not None for cell in cells):
                continue
            texts = [_text_of(cell) for cell in cells]
            title = next((text for text in texts if title_key(text) in titles), None)
            if title is not None and sum(1 for text in texts if text) <= MAX_TITLE_ROW_CELLS:
                events.append(Title(title_key(title), title))
                continue
            table = next(element.iterancestors("table"), None)
            if table not in grids:
                grids[table] = _TableGrid(len(grids), idents)
            spans = [
                (text, _span(cell.get("colspan")), _span(cell.get("rowspan")))
                for text, cell in zip(texts, cells, strict=True)
            ]
            events.append(grids[table].place(spans))
        elif (
            tag in _TEXT_BLOCKS
            and next(element.iterancestors("table"), None) is None
            and element.find(".//table") is None
        ):
            text = _text_of(element)
            if title_key(text) in titles:
                events.append(Title(title_key(text), text))
    return events
