"""Positions files: parsing and checks without a database, loading and the book with one."""

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select

from cmbs import clock
from cmbs.access.service import PermissionRefused
from cmbs.audit.tables import AuditEntry
from cmbs.book.service import (
    InvalidPositions,
    PositionLine,
    book_as_of,
    check_lines,
    exposure_by_deal,
    exposures,
    load_positions,
    parse_positions,
)
from cmbs.book.tables import Position, PositionsFile
from cmbs.deals.service import by_name
from tests.conftest import needs_db

F1, F2 = "Fixture Trust 2026-F1", "Fixture Trust 2026-F2"
BOOK = [
    PositionLine(F1, "E", Decimal("20000000.00")),
    PositionLine(F1, "F", Decimal("5000000.00")),
    PositionLine(F2, "D", Decimal("40000000.00")),
]


# Parsing


def test_a_positions_file_is_read_with_money_as_decimal():
    text = (
        "deal,class,face amount\n"
        f'{F1},E,"20,000,000.00"\n'
        f"  Fixture   Trust 2026-F1 , F ,$5000000\n"
        "\n"
        f"{F2},D,40000000.005\n"
    )
    assert parse_positions(text) == [
        PositionLine(F1, "E", Decimal("20000000.00")),
        PositionLine(F1, "F", Decimal("5000000.00")),
        PositionLine(F2, "D", Decimal("40000000.01")),
    ]


def test_headers_are_matched_without_case_or_underscores_in_any_order():
    text = "Face_Amount, CLASS ,Deal\n1000,A,X 2026-1\n"
    assert parse_positions(text) == [PositionLine("X 2026-1", "A", Decimal("1000.00"))]


def test_an_empty_file_is_refused():
    with pytest.raises(InvalidPositions, match="^the positions file is empty$"):
        parse_positions("")


def test_a_missing_column_is_named():
    with pytest.raises(
        InvalidPositions, match="^the positions file has no class, face amount column$"
    ):
        parse_positions("deal,amount\n")


def test_a_short_line_is_refused_with_its_number():
    with pytest.raises(InvalidPositions, match="^line 3 has 2 fields, not 3$"):
        parse_positions("deal,class,face amount\nX,A,1\nY,B\n")


def test_a_face_that_is_not_money_is_refused_with_its_number():
    with pytest.raises(InvalidPositions, match="^line 2: 'n/a' is not a face amount$"):
        parse_positions("deal,class,face amount\nX,A, n/a \n")


def test_a_header_only_file_is_an_empty_book():
    assert parse_positions("deal,class,face amount\n") == []


# Checks


def test_check_accepts_a_valid_book():
    check_lines(BOOK)


@pytest.mark.parametrize(
    ("line", "message"),
    [
        (PositionLine("", "E", Decimal("1")), "position 1 names no deal"),
        (PositionLine(F1, "", Decimal("1")), f"position 1 in {F1} names no class"),
        (
            PositionLine(F1, "E", Decimal("0")),
            f"Class E of {F1} has face $0.00; a position must hold more than zero",
        ),
        (
            PositionLine(F1, "E", Decimal("-5")),
            f"Class E of {F1} has face -$5.00; a position must hold more than zero",
        ),
    ],
)
def test_check_refuses_incomplete_or_non_positive_lines(line, message):
    with pytest.raises(InvalidPositions) as refused:
        check_lines([line])
    assert str(refused.value) == message


def test_check_refuses_a_class_listed_twice():
    with pytest.raises(InvalidPositions, match=f"^Class E of {F1} is listed twice$"):
        check_lines([*BOOK, PositionLine(F1, "E", Decimal("1"))])


def test_check_allows_the_same_class_letter_in_two_deals():
    check_lines([PositionLine(F1, "D", Decimal("1")), PositionLine(F2, "D", Decimal("1"))])


def test_exposure_is_total_face_per_deal():
    assert exposures(BOOK) == {F1: Decimal("25000000.00"), F2: Decimal("40000000.00")}
    assert exposures([]) == {}


# Loading and reading the book


@needs_db
def test_loading_stores_the_file_registers_deals_and_audits(seeded):
    clock.set_now(dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC))
    loaded = load_positions(
        seeded, user="quinn", as_of=dt.date(2026, 9, 30), lines=BOOK, source_name="book.csv"
    )
    seeded.commit()
    assert (loaded.as_of, loaded.loaded_by, loaded.source_name) == (
        dt.date(2026, 9, 30),
        "quinn",
        "book.csv",
    )
    assert loaded.loaded_at == dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC)
    stored = seeded.scalars(select(Position).order_by(Position.id)).all()
    assert [(p.tranche, p.face_amount) for p in stored] == [
        ("E", Decimal("20000000.00")),
        ("F", Decimal("5000000.00")),
        ("D", Decimal("40000000.00")),
    ]
    assert by_name(seeded, F1) is not None and by_name(seeded, F2) is not None
    entry = seeded.scalars(select(AuditEntry)).one()
    assert (entry.user_name, entry.role, entry.action, entry.subject, entry.subject_key) == (
        "quinn",
        "quant",
        "load positions",
        "positions_file",
        str(loaded.id),
    )
    assert entry.after == "3 positions in 2 deals as of 2026-09-30, face $65,000,000.00"
    assert entry.detail == {
        "as_of": "2026-09-30",
        "positions": 3,
        "deals": 2,
        "total_face": "65000000.00",
        "source_name": "book.csv",
    }


@needs_db
def test_operators_may_load_positions_but_pms_may_not(seeded):
    load_positions(seeded, user="olly", as_of=dt.date(2026, 9, 30), lines=BOOK)
    with pytest.raises(PermissionRefused, match="^pm may not load positions$"):
        load_positions(seeded, user="pat", as_of=dt.date(2026, 9, 30), lines=BOOK)


@needs_db
def test_an_invalid_file_changes_nothing(seeded):
    with pytest.raises(InvalidPositions):
        load_positions(
            seeded,
            user="quinn",
            as_of=dt.date(2026, 9, 30),
            lines=[*BOOK, PositionLine(F2, "D", Decimal("1"))],
        )
    seeded.rollback()
    assert seeded.scalars(select(PositionsFile)).all() == []
    assert by_name(seeded, F1) is None


@needs_db
def test_the_book_is_the_latest_file_dated_on_or_before_the_day(seeded):
    august = [PositionLine(F1, "E", Decimal("10000000.00"))]
    load_positions(seeded, user="quinn", as_of=dt.date(2026, 9, 30), lines=BOOK)
    load_positions(seeded, user="quinn", as_of=dt.date(2026, 8, 31), lines=august)
    assert book_as_of(seeded, dt.date(2026, 8, 30)) is None
    assert exposure_by_deal(seeded, dt.date(2026, 8, 30)) == {}
    assert exposure_by_deal(seeded, dt.date(2026, 8, 31)) == {F1: Decimal("10000000.00")}
    assert exposure_by_deal(seeded, dt.date(2026, 9, 29)) == {F1: Decimal("10000000.00")}
    assert exposure_by_deal(seeded, dt.date(2026, 9, 30)) == {
        F1: Decimal("25000000.00"),
        F2: Decimal("40000000.00"),
    }
    book = book_as_of(seeded, dt.date(2026, 12, 31))
    assert book is not None
    assert book.as_of == dt.date(2026, 9, 30)
    assert book.classes_of(F1) == ["E", "F"]
    assert [(h.deal, h.tranche) for h in book.positions] == [(F1, "E"), (F1, "F"), (F2, "D")]


@needs_db
def test_of_two_files_with_one_date_the_one_loaded_last_is_the_book(seeded):
    load_positions(seeded, user="quinn", as_of=dt.date(2026, 9, 30), lines=BOOK)
    corrected = [PositionLine(F1, "E", Decimal("21000000.00"))]
    load_positions(seeded, user="olly", as_of=dt.date(2026, 9, 30), lines=corrected)
    assert exposure_by_deal(seeded, dt.date(2026, 9, 30)) == {F1: Decimal("21000000.00")}


@needs_db
def test_an_empty_file_is_an_empty_book(seeded):
    load_positions(seeded, user="quinn", as_of=dt.date(2026, 9, 30), lines=BOOK)
    load_positions(seeded, user="quinn", as_of=dt.date(2026, 10, 31), lines=[])
    book = book_as_of(seeded, dt.date(2026, 10, 31))
    assert book is not None and book.positions == ()
    assert exposure_by_deal(seeded, dt.date(2026, 10, 31)) == {}
