"""Flattening report HTML into titles and grid rows (cmbs.distribution.grid)."""

import pytest

from cmbs.distribution.grid import (
    MAX_SPAN,
    Cell,
    Row,
    Title,
    UnreadableReport,
    _span,
    clean,
    document_text,
    flatten,
    header_label,
    read_document,
    title_key,
)

TITLES = frozenset({title_key("Delinquency Loan Detail"), title_key("Supplemental Notes")})


def events(html: str) -> list[Title | Row]:
    return flatten(read_document(html.encode()), TITLES)


def texts(row: Title | Row) -> list[tuple[str, int, int]]:
    assert isinstance(row, Row)
    return [(cell.text, cell.start, cell.span) for cell in row.cells]


def test_clean_collapses_every_kind_of_space():
    zero_width, byte_order_mark = chr(0x200B), chr(0xFEFF)
    assert clean(f"  Pros\xa0 ID{zero_width}\n\t(1) {byte_order_mark}") == "Pros ID (1)"
    assert clean("soft\xadhyphen") == "softhyphen"
    # Superscript footnote marks survive cleaning, so a header can drop them (header_label).
    assert clean("Status\u00b9") == "Status\u00b9"
    assert clean("") == ""


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("Specially Serviced Loan Detail – Part 2", "specially serviced loan detail part 2"),
        ("Specially Serviced Loan Detail - Part 2", "specially serviced loan detail part 2"),
        ("Mortgage Loan Detail (Part 2)", "mortgage loan detail part 2"),
        ("MORTGAGE LOAN DETAIL - PART 2 (continued)", "mortgage loan detail part 2"),
        ("Delinquency Loan Detail (Cont'd)", "delinquency loan detail"),
        ("Delinquency Loan Detail cont.", "delinquency loan detail"),
        (
            "Bond/Collateral Reconciliation - Cash Flows",
            "bond collateral reconciliation cash flows",
        ),
        ("P & I Advances", "p & i advances"),
        ("Discount", "discount"),
        ("", ""),
    ],
)
def test_title_key_keeps_only_the_words(text, key):
    assert title_key(text) == key


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("Current P & I Advances (1)", "current p&i advances"),
        ("Loan Status (2)", "loan status"),
        ("Pros\xa0ID", "pros id"),
        ("P&I", "p&i"),
        ("Year (2026)", "year (2026)"),
        ("Mortgage Loan Status\u00b9", "mortgage loan status"),
        ("Strategy Code\u00b2", "strategy code"),
        ("Type\u2074\u2075", "type"),
    ],
)
def test_header_label(text, label):
    assert header_label(text) == label


@pytest.mark.parametrize(
    ("value", "span"),
    [(None, 1), ("3", 3), (" 2 ", 2), ("abc", 1), ("0", 1), ("-2", 1), ("1000", MAX_SPAN)],
)
def test_spans_are_read_leniently(value, span):
    assert _span(value) == span


@pytest.mark.parametrize("content", [b"", b"   \n "])
def test_an_empty_file_is_refused(content):
    with pytest.raises(UnreadableReport) as refused:
        read_document(content)
    assert str(refused.value) == "the distribution report is empty or not HTML"


def test_scripts_styles_and_comments_are_dropped_and_breaks_separate_words():
    root = read_document(
        b"<html><head><style>td{}</style><script>var x;</script></head><body>"
        b"<!-- hidden note --><p>Distribution<br>Date</p><div>09/17/26</div>tail</body></html>"
    )
    assert document_text(root) == "Distribution Date 09/17/26 tail"


def test_cells_are_placed_on_a_grid_with_colspan_and_rowspan():
    found = events(
        "<table>"
        "<tr><td rowspan='2'>Pros ID</td><td colspan='2'>P &amp; I Advances</td><td>X</td></tr>"
        "<tr><td>Current</td><td>Outstanding</td><td>Y</td></tr>"
        "<tr><td>2</td><td>1.00</td><td>2.00</td><td>Z</td></tr>"
        "</table>"
    )
    assert [texts(row) for row in found] == [
        [("Pros ID", 0, 1), ("P & I Advances", 1, 2), ("X", 3, 1)],
        [("Pros ID", 0, 1), ("Current", 1, 1), ("Outstanding", 2, 1), ("Y", 3, 1)],
        [("2", 0, 1), ("1.00", 1, 1), ("2.00", 2, 1), ("Z", 3, 1)],
    ]
    first, second, third = found
    assert isinstance(first, Row) and isinstance(second, Row) and isinstance(third, Row)
    assert first.cells[0].ident == second.cells[0].ident  # the rowspan cell is one cell
    assert third.cells[0].ident != first.cells[0].ident
    assert first.width == 4


def test_a_rowspan_lasts_exactly_its_rows():
    found = events(
        "<table>"
        "<tr><td rowspan='3'>A</td><td>1</td></tr>"
        "<tr><td>2</td></tr><tr><td>3</td></tr><tr><td>4</td><td>5</td></tr>"
        "</table>"
    )
    assert [texts(row) for row in found] == [
        [("A", 0, 1), ("1", 1, 1)],
        [("A", 0, 1), ("2", 1, 1)],
        [("A", 0, 1), ("3", 1, 1)],
        [("4", 0, 1), ("5", 1, 1)],
    ]


def test_a_rowspan_in_a_middle_column_pushes_later_cells_right():
    found = events(
        "<table><tr><td>a</td><td rowspan='2' colspan='2'>B</td><td>c</td></tr>"
        "<tr><td>d</td><td>e</td></tr></table>"
    )
    assert texts(found[1]) == [("d", 0, 1), ("B", 1, 2), ("e", 3, 1)]


def test_layout_rows_are_skipped_and_each_table_is_numbered():
    found = events(
        "<table><tr><td>"
        "<table><tr><td rowspan='2'>A</td><td>1</td></tr></table>"
        "<table><tr><td>B</td></tr></table>"
        "</td></tr></table>"
    )
    rows = [row for row in found if isinstance(row, Row)]
    assert [(row.table, texts(row)) for row in rows] == [
        (0, [("A", 0, 1), ("1", 1, 1)]),
        (1, [("B", 0, 1)]),  # the rowspan does not leak into the next table
    ]


def test_titles_are_found_in_cells_and_in_text_outside_tables():
    found = events(
        "<h2>Delinquency&nbsp;Loan Detail</h2>"
        "<p>Not a title</p>"
        "<table>"
        "<tr><td>Supplemental Notes</td><td>28</td></tr>"
        "<tr><td>Delinquency Loan Detail (continued)</td></tr>"
        "<tr><td>Delinquency Loan Detail</td><td>a</td><td>b</td><td>c</td></tr>"
        "</table>"
    )
    assert found[:3] == [
        Title("delinquency loan detail", "Delinquency Loan Detail"),
        Title("supplemental notes", "Supplemental Notes"),
        Title("delinquency loan detail", "Delinquency Loan Detail (continued)"),
    ]
    # A row with more filled cells than a title row has is data, even if one cell reads like one.
    assert texts(found[3]) == [
        ("Delinquency Loan Detail", 0, 1),
        ("a", 1, 1),
        ("b", 2, 1),
        ("c", 3, 1),
    ]
    assert len(found) == 4


def test_a_title_inside_a_layout_block_with_a_table_is_not_read_from_the_block():
    found = events("<div>Supplemental Notes<table><tr><td>x</td></tr></table></div>")
    assert [type(event) for event in found] == [Row]


def test_filled_cells_and_width():
    row = Row(0, (Cell(1, "", 0), Cell(2, "x", 1, 3)))
    assert row.filled() == (Cell(2, "x", 1, 3),)
    assert row.width == 4
    assert Row(0, ()).width == 0
    assert Cell(3, "y", 2, 2).end == 4


def test_a_title_may_share_its_row_with_two_other_cells():
    found = events(
        "<table><tr><td>Supplemental Notes</td><td>Page 28</td><td>of 28</td></tr></table>"
    )
    assert found == [Title("supplemental notes", "Supplemental Notes")]


def test_upper_case_markup_is_read():
    found = events("<TABLE><TR><TD>x</TD><TH COLSPAN='2'>y</TH></TR></TABLE>")
    assert texts(found[0]) == [("x", 0, 1), ("y", 1, 2)]
