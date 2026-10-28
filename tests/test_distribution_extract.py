"""Finding columns by header text and reading records per loan (cmbs.distribution.extract)."""

import re

import pytest

from cmbs.distribution.extract import (
    LOAN,
    Column,
    HeaderColumn,
    Record,
    TableSpec,
    find_header,
    header_columns,
    match_header,
    read_table,
    sections,
)
from cmbs.distribution.grid import Row, flatten, read_document, title_key

ID = Column(LOAN, re.compile(r"pros id"), required=True)
NOTE = Column("note", re.compile(r"comment"), required=True, free_text=True)
STATUS = Column("status", re.compile(r"status"), exclude=re.compile(r"resolution"))
SPEC = TableSpec("Loan Notes", (ID, STATUS, NOTE))
TITLES = frozenset({SPEC.key, title_key("Other Table")})


def report(html: str) -> list[tuple[str, list[Row]]]:
    return sections(flatten(read_document(html.encode()), TITLES))


def rows(html: str) -> list[Row]:
    return [
        row
        for _, found in report(f"<table><tr><td>Loan Notes</td></tr>{html}</table>")
        for row in found
    ]


def read(html: str) -> tuple[list[Record] | None, list[str]]:
    notes: list[str] = []
    return read_table(SPEC, report(html), notes), notes


def test_a_spec_must_start_with_the_required_loan_column():
    with pytest.raises(ValueError) as error:
        TableSpec("Bad", (STATUS,))
    assert str(error.value) == "Bad: the first column must be the required 'loan'"
    with pytest.raises(ValueError):
        TableSpec("Bad", (Column(LOAN, re.compile("x")),))
    with pytest.raises(ValueError):
        TableSpec("Bad", ())


def test_a_column_matches_its_pattern_unless_excluded():
    assert STATUS.matches("loan status")
    assert not STATUS.matches("resolution status")
    assert not STATUS.matches("paid through")
    assert ID.matches("pros id")


def test_header_labels_join_split_cells_top_to_bottom():
    header = rows(
        "<tr><td rowspan='2'>Pros<br>ID</td><td colspan='2'>Special Servicing</td>"
        "<td></td></tr>"
        "<tr><td colspan='2'>Comments (1)</td><td>Status</td></tr>"
    )
    assert header_columns(header) == [
        HeaderColumn("pros id", 0, 1),
        HeaderColumn("special servicing comments", 1, 3),
        HeaderColumn("status", 3, 4, last=True),
    ]


def test_header_columns_skip_unlabelled_positions_and_mark_the_last():
    header = rows("<tr><td>Pros ID</td><td></td><td>Comment</td><td></td></tr>")
    assert header_columns(header) == [
        HeaderColumn("pros id", 0, 1),
        HeaderColumn("comment", 2, 3, last=True),
    ]
    assert header_columns([]) == []


def test_match_header_needs_every_required_column_once():
    assert match_header(SPEC, rows("<tr><td>Pros ID</td><td>Status</td></tr>")) is None
    layout = match_header(SPEC, rows("<tr><td>Pros ID</td><td>Comment</td></tr>"))
    assert layout == {
        LOAN: HeaderColumn("pros id", 0, 1),
        "note": HeaderColumn("comment", 1, 2, True),
    }
    # One header column serves one field: "pros id comment" cannot be both.
    assert match_header(SPEC, rows("<tr><td>Pros ID Comment</td></tr>")) is None


def test_the_header_stops_at_a_blank_row_above_it():
    table = rows(
        "<tr><td>Distribution Date: 09/17/26</td><td>comment line</td></tr>"
        "<tr><td>&nbsp;</td><td>&nbsp;</td></tr>"
        "<tr><td>Pros</td><td>Special Servicing</td></tr>"
        "<tr><td>ID</td><td>Comments</td></tr>"
        "<tr><td>11</td><td>Text</td></tr>"
    )
    found = find_header(SPEC, table)
    assert found == (
        {
            LOAN: HeaderColumn("pros id", 0, 1),
            "note": HeaderColumn("special servicing comments", 1, 2, True),
        },
        4,
    )


def test_header_rows_above_the_smallest_block_are_added_to_it():
    # Mortgage Loan Detail (Part 2), BANK 2018-BNK13, September 2026: the required columns are
    # all in the bottom two rows, but "Appraisal" over "Reduction Date" starts a row higher,
    # in a cell that also spans the two NOI date columns.
    spec = TableSpec(
        "Loan Notes",
        (
            ID,
            Column("ara", re.compile("appraisal reduction"), True, re.compile("date")),
            Column("ara_date", re.compile(r"appraisal reduction date")),
        ),
    )
    table = rows(
        "<tr><td></td><td></td><td></td><td></td><td></td><td></td></tr>"
        "<tr><td></td><td colspan='3'>Most Recent Most Recent Appraisal</td><td></td>"
        "<td>Cumulative</td></tr>"
        "<tr><td></td><td>NOI Start</td><td>NOI End</td><td>Reduction</td><td>Appraisal</td>"
        "<td>Servicer</td></tr>"
        "<tr><td>Pros ID</td><td>Date</td><td>Date</td><td>Date</td><td>Reduction Amount</td>"
        "<td>Advances</td></tr>"
        "<tr><td>11</td><td>01/01/26</td><td>03/31/26</td><td>07/27/26</td>"
        "<td>20,092,857.80</td><td>214,557.19</td></tr>"
    )
    layout, start = find_header(spec, table) or ({}, -1)
    assert start == 4
    assert layout["ara"] == HeaderColumn("appraisal reduction amount", 4, 5)
    assert layout["ara_date"] == HeaderColumn(
        "most recent most recent appraisal reduction date", 3, 4
    )


def test_footnote_markers_keep_columns_under_one_spanning_cell_apart():
    # Delinquency Loan Detail, CGCMT 2016-C1, September 2026: "Status Strategy Special Serv"
    # is one cell over three columns, told apart only by "(2)", "(3)" and "Transfer Date".
    header = rows(
        "<tr><td>OMCR</td><td colspan='3'>Status Strategy Special Serv</td></tr>"
        "<tr><td></td><td>(2)</td><td>(3)</td><td>Transfer Date</td></tr>"
    )
    assert header_columns(header) == [
        HeaderColumn("omcr", 0, 1),
        HeaderColumn("status strategy special serv", 1, 2),
        HeaderColumn("status strategy special serv", 2, 3),
        HeaderColumn("status strategy special serv transfer date", 3, 4, last=True),
    ]


def test_a_spec_has_at_most_one_free_text_column():
    with pytest.raises(ValueError) as error:
        TableSpec("Bad", (ID, NOTE, Column("more", re.compile("x"), free_text=True)))
    assert str(error.value) == "Bad: at most one free-text column"
    assert SPEC.free_text == "note"
    assert TableSpec("Plain", (ID, STATUS)).free_text is None


def test_comment_lines_printed_as_rows_across_the_table_join_the_loan_above():
    # Specially Serviced Loan Detail - Part 2, BANK 2018-BNK13, September 2026: the loan row
    # leaves the comment column empty; each comment line is a row of its own that starts after
    # the loan id column and spans the table, and the last line may span only a few columns.
    found, notes = read(
        "<table><tr><td>Loan Notes</td></tr>"
        "<tr><td>Pros ID</td><td>Loan ID</td><td>Status</td><td></td>"
        "<td>Special Servicing Comments</td></tr>"
        "<tr><td>11</td><td>1852535</td><td>2</td><td></td><td></td></tr>"
        "<tr><td></td><td colspan='4'>The loan transferred to Special Servicing effective "
        "6/4/2026 due to monetary default.</td></tr>"
        "<tr><td></td><td colspan='2'>and initiate discussions with t he Borrower.</td>"
        "<td></td><td></td></tr>"
        "<tr><td colspan='5'>&nbsp;</td></tr>"
        "<tr><td>57</td><td>470109040</td><td>0</td><td></td><td></td></tr>"
        "<tr><td></td><td>taxes ($180k).</td><td></td><td></td><td></td></tr>"
        "<tr><td colspan='2'>1 Property Type Codes</td><td></td><td>2 Resolution</td></tr>"
        "</table>"
    )
    assert found == [
        Record(
            "11",
            {
                "status": "2",
                "note": "The loan transferred to Special Servicing effective 6/4/2026 due to "
                "monetary default. and initiate discussions with t he Borrower.",
            },
        ),
        Record("57", {"status": "0", "note": "taxes ($180k)."}),
    ]
    assert notes == []


def test_a_line_inside_another_column_is_not_comment_text():
    found, _ = read(
        "<table><tr><td>Loan Notes</td></tr>"
        "<tr><td>Pros ID</td><td>Status</td><td>Comment</td></tr>"
        "<tr><td>11</td><td>3</td><td>Text.</td></tr>"
        "<tr><td></td><td>9</td><td></td></tr>"
        "<tr><td></td><td></td><td>Not attached.</td></tr>"
        "</table>"
    )
    assert found == [Record("11", {"status": "3", "note": "Text."})]


def test_rows_below_the_header_extend_it_until_the_first_loan():
    table = rows(
        "<tr><td>Pros ID</td><td>Loan</td><td>Comment</td></tr>"
        "<tr><td></td><td>Status</td><td></td></tr>"
        "<tr><td>11</td><td>3</td><td>Text</td></tr>"
    )
    layout, start = find_header(SPEC, table) or ({}, -1)
    assert start == 2
    assert layout["status"] == HeaderColumn("loan status", 1, 2)


def test_a_full_width_line_under_the_header_is_not_part_of_it():
    table = rows(
        "<tr><td>Pros ID</td><td>Status</td><td>Comment</td></tr>"
        "<tr><td colspan='3'>No loans this period</td></tr>"
    )
    layout, start = find_header(SPEC, table) or ({}, -1)
    assert start == 1
    assert layout["note"].label == "comment"


def test_no_header_means_none():
    assert find_header(SPEC, rows("<tr><td>11</td><td>x</td></tr>")) is None
    assert find_header(SPEC, []) is None


def test_comments_spread_over_cells_and_rows_are_joined_in_order():
    found, notes = read(
        "<table><tr><td>Loan Notes</td></tr>"
        "<tr><td>Pros ID</td><td>Status</td><td colspan='2'>Comment</td></tr>"
        "<tr><td>011</td><td>3</td><td>First part.</td><td>Second part.</td>"
        "<td>Past the header.</td></tr>"
        "<tr><td></td><td></td><td colspan='2'>Next row.</td></tr>"
        "<tr><td>12</td><td>1</td><td colspan='2'>Other loan.</td></tr>"
        "</table>"
    )
    assert found == [
        Record(
            "11", {"status": "3", "note": "First part. Second part. Past the header. Next row."}
        ),
        Record("12", {"status": "1", "note": "Other loan."}),
    ]
    assert notes == []


def test_a_row_outside_the_comment_ends_the_comment():
    found, _ = read(
        "<table><tr><td>Loan Notes</td></tr>"
        "<tr><td>Pros ID</td><td>Status</td><td>Comment</td></tr>"
        "<tr><td>11</td><td>3</td><td>Kept.</td></tr>"
        "<tr><td>Totals</td><td></td><td></td></tr>"
        "<tr><td></td><td></td><td>Orphan text.</td></tr>"
        "<tr><td colspan='3'>(1) A footnote across the table.</td></tr>"
        "</table>"
    )
    assert found == [Record("11", {"status": "3", "note": "Kept."})]


def test_text_in_a_middle_free_text_column_does_not_run_past_it():
    spec = TableSpec("Loan Notes", (ID, NOTE, STATUS))
    notes: list[str] = []
    found = read_table(
        spec,
        report(
            "<table><tr><td>Loan Notes</td></tr>"
            "<tr><td>Pros ID</td><td>Comment</td><td>Status</td></tr>"
            "<tr><td>11</td><td>Text.</td><td>3</td></tr>"
            "<tr><td></td><td></td><td>Not a comment.</td></tr>"
            "</table>"
        ),
        notes,
    )
    assert found == [Record("11", {"note": "Text.", "status": "3"})]


def test_a_rowspan_loan_id_and_repeated_rows_merge_into_one_record():
    found, notes = read(
        "<table><tr><td>Loan Notes</td></tr>"
        "<tr><td>Pros ID</td><td>Status</td><td>Comment</td></tr>"
        "<tr><td rowspan='2'>11</td><td>3</td><td rowspan='2'>Once.</td></tr>"
        "<tr><td>3</td></tr>"
        "<tr><td>11</td><td>2</td><td>Again.</td></tr>"
        "</table>"
    )
    assert found == [Record("11", {"status": "3", "note": "Once. Again."})]
    assert notes == ["Loan Notes: loan 11 shows 2 values of status; kept the first, '3'"]


def test_a_table_carried_onto_the_next_page_reuses_the_header_of_the_same_width():
    found, _ = read(
        "<table><tr><td>Loan Notes</td></tr>"
        "<tr><td>Pros ID</td><td>Status</td><td>Comment</td></tr>"
        "<tr><td>11</td><td>3</td><td>A.</td></tr></table>"
        "<table><tr><td>12</td><td>1</td><td>B.</td></tr></table>"
        "<table><tr><td>(1)</td><td>Legend</td></tr><tr><td>1</td><td>30 days</td></tr></table>"
    )
    assert [record.loan for record in found or []] == ["11", "12"]


def test_a_table_without_a_header_before_any_header_is_ignored():
    found, notes = read(
        "<table><tr><td>Loan Notes</td></tr></table>"
        "<table><tr><td>12</td><td>1</td><td>B.</td></tr></table>"
    )
    assert found == []
    assert notes == ["Loan Notes: the columns were not recognised"]


def test_every_section_under_the_title_is_read_and_others_are_not():
    found, _ = read(
        "<table><tr><td>Loan Notes</td><td>3</td></tr><tr><td>Other Table</td><td>4</td></tr>"
        "</table>"
        "<table><tr><td>Loan Notes</td></tr>"
        "<tr><td>Pros ID</td><td>Comment</td></tr><tr><td>1</td><td>A.</td></tr></table>"
        "<table><tr><td>Other Table</td></tr>"
        "<tr><td>Pros ID</td><td>Comment</td></tr><tr><td>2</td><td>Elsewhere.</td></tr></table>"
        "<table><tr><td>Loan Notes (continued)</td></tr>"
        "<tr><td>Pros ID</td><td>Comment</td></tr><tr><td>3</td><td>C.</td></tr></table>"
    )
    assert found == [Record("1", {"note": "A."}), Record("3", {"note": "C."})]


def test_a_title_only_in_the_contents_means_no_table():
    found, notes = read("<table><tr><td>Loan Notes</td><td>3</td></tr></table>")
    assert found is None
    assert notes == []


def test_rows_before_the_first_title_belong_to_no_section():
    found = report(
        "<table><tr><td>stray</td></tr><tr><td>Loan Notes</td></tr><tr><td>x</td></tr>"
        "<tr><td>Other Table</td></tr></table>"
    )
    assert [
        (key, [[cell.text for cell in row.cells] for row in section]) for key, section in found
    ] == [("loan notes", [["x"]]), ("other table", [])]


def test_a_row_that_would_break_a_required_column_is_not_added_to_the_header():
    vetoed = Column("status", re.compile("status"), required=True, exclude=re.compile("veto"))
    strict = TableSpec("Loan Notes", (ID, vetoed, NOTE))
    table = rows(
        "<tr><td>Pros ID</td><td>Status</td><td>Comment</td></tr>"
        "<tr><td></td><td>veto</td><td>more</td></tr>"
        "<tr><td>11</td><td>3</td><td>Text</td></tr>"
    )
    layout, start = find_header(strict, table) or ({}, -1)
    assert start == 1
    assert layout["status"].label == "status"


def test_a_row_above_that_would_break_a_required_column_is_not_added_to_the_header():
    vetoed = Column("status", re.compile("status"), required=True, exclude=re.compile("veto"))
    strict = TableSpec("Loan Notes", (ID, vetoed, NOTE))
    table = rows(
        "<tr><td>above</td><td>veto</td><td>more</td></tr>"
        "<tr><td>Pros ID</td><td>Status</td><td>Comment</td></tr>"
        "<tr><td>11</td><td>3</td><td>Text</td></tr>"
    )
    layout, start = find_header(strict, table) or ({}, -1)
    assert start == 2
    assert layout[LOAN].label == "pros id"


def test_rows_above_are_added_only_while_the_header_has_room():
    table = rows(
        "".join(f"<tr><td>a{n}</td><td>b{n}</td></tr>" for n in range(5))
        + "<tr><td>Pros ID</td><td>Comment</td></tr>"
        + "<tr><td>11</td><td>Text</td></tr>"
    )
    layout, start = find_header(SPEC, table) or ({}, -1)
    assert start == 6
    assert layout[LOAN].label == "a2 a3 a4 pros id"


def test_a_header_has_at_most_four_rows():
    table = rows(
        "<tr><td>Pros ID</td><td>Comment</td></tr>"
        + "".join(f"<tr><td>a{n}</td><td>b{n}</td></tr>" for n in range(5))
        + "<tr><td>11</td><td>Text</td></tr>"
    )
    layout, start = find_header(SPEC, table) or ({}, -1)
    assert start == 4
    assert layout[LOAN].label == "pros id a0 a1 a2"


def test_a_blank_row_does_not_end_a_comment():
    found, _ = read(
        "<table><tr><td>Loan Notes</td></tr>"
        "<tr><td>Pros ID</td><td>Comment</td></tr>"
        "<tr><td>11</td><td>One.</td></tr>"
        "<tr><td></td><td>&nbsp;</td></tr>"
        "<tr><td></td><td>Two.</td></tr>"
        "</table>"
    )
    assert found == [Record("11", {"note": "One. Two."})]
