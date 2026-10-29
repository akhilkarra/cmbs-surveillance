"""Text-versus-tape rules: pure comparisons of what a comment and a tape say."""

import datetime as dt
from decimal import Decimal

import pytest

from cmbs.crosscheck import rules
from cmbs.crosscheck.rules import (
    NEWER_FINANCIALS,
    NEWER_THAN_TAPE,
    NOT_IN_TAPE,
    TAPE_HAS_NONE,
    TRANSFER_DATE,
    TRANSFER_MISSING,
    Finding,
    Status,
    TapeSide,
    TextSide,
    compare,
    compare_financials,
    compare_transfer,
    periods_apart,
    periods_phrase,
    text_financials_date,
    transfer_message,
)

D = dt.date


def text(**values) -> TextSide:
    return TextSide(
        accession="0009900001-26-000902", filed=D(2026, 9, 29), as_of=D(2026, 9, 17), **values
    )


def tape(**values) -> TapeSide:
    return TapeSide(
        accession="0009900001-26-000901", filed=D(2026, 9, 29), as_of=D(2026, 9, 11), **values
    )


TEXT_DATES = {
    "accession": "0009900001-26-000902",
    "filed": "2026-09-29",
    "as_of": "2026-09-17",
}
TAPE_DATES = {
    "accession": "0009900001-26-000901",
    "filed": "2026-09-29",
    "as_of": "2026-09-11",
}


@pytest.mark.parametrize(
    ("first", "second", "apart"),
    [
        (D(2025, 3, 6), D(2025, 3, 6), 0),
        (D(2025, 3, 1), D(2025, 3, 31), 0),
        (D(2025, 1, 20), D(2025, 3, 6), 2),
        (D(2025, 3, 6), D(2025, 1, 20), 2),
        (D(2024, 11, 15), D(2025, 3, 6), 4),
        (D(2025, 1, 31), D(2025, 2, 1), 1),
    ],
)
def test_periods_apart_counts_calendar_months_either_way(first, second, apart):
    assert periods_apart(first, second) == apart


@pytest.mark.parametrize(
    ("count", "phrase"),
    [
        (0, "zero periods"),
        (1, "one period"),
        (2, "two periods"),
        (9, "nine periods"),
        (10, "10 periods"),
        (12, "12 periods"),
    ],
)
def test_periods_phrase(count, phrase):
    assert periods_phrase(count) == phrase


def test_transfer_message_names_the_tolerance():
    assert transfer_message(2) == "transfer dates differ by more than two periods"
    assert transfer_message(1) == "transfer dates differ by more than one period"


def test_matching_transfer_dates_agree():
    finding = compare_transfer(
        text(transfer_date=D(2025, 3, 6), transfer_sentence=1),
        tape(transfer_date=D(2025, 3, 6)),
        tolerance=2,
    )
    assert finding == Finding(
        TRANSFER_DATE,
        Status.AGREE,
        "transfer dates agree within two periods",
        {
            "text": {
                "source": "10-D comment",
                "transfer_date": "2025-03-06",
                "sentence": 1,
                **TEXT_DATES,
            },
            "tape": {"source": "loan tape", "transfer_date": "2025-03-06", **TAPE_DATES},
            "tolerance_periods": 2,
            "periods_apart": 0,
        },
    )


def test_transfer_dates_exactly_the_tolerance_apart_agree():
    finding = compare_transfer(
        text(transfer_date=D(2025, 1, 20), transfer_sentence=1),
        tape(transfer_date=D(2025, 3, 6)),
        tolerance=2,
    )
    assert finding is not None
    assert (finding.status, finding.sides["periods_apart"]) == (Status.AGREE, 2)


def test_transfer_dates_beyond_the_tolerance_are_flagged_with_both_dates():
    finding = compare_transfer(
        text(transfer_date=D(2024, 11, 15), transfer_sentence=2),
        tape(transfer_date=D(2025, 3, 6)),
        tolerance=2,
    )
    assert finding == Finding(
        TRANSFER_DATE,
        Status.FLAG,
        "transfer dates differ by more than two periods",
        {
            "text": {
                "source": "10-D comment",
                "transfer_date": "2024-11-15",
                "sentence": 2,
                **TEXT_DATES,
            },
            "tape": {"source": "loan tape", "transfer_date": "2025-03-06", **TAPE_DATES},
            "tolerance_periods": 2,
            "periods_apart": 4,
        },
    )


def test_one_period_past_a_smaller_tolerance_is_flagged():
    finding = compare_transfer(
        text(transfer_date=D(2025, 1, 20)), tape(transfer_date=D(2025, 3, 6)), tolerance=1
    )
    assert finding is not None
    assert (finding.status, finding.message) == (
        Status.FLAG,
        "transfer dates differ by more than one period",
    )


def test_a_transfer_the_tape_does_not_report_is_flagged():
    finding = compare_transfer(
        text(transfer_date=D(2026, 8, 15), transfer_sentence=1), tape(), tolerance=2
    )
    assert finding == Finding(
        TRANSFER_MISSING,
        Status.FLAG,
        NOT_IN_TAPE,
        {
            "text": {
                "source": "10-D comment",
                "transfer_date": "2026-08-15",
                "sentence": 1,
                **TEXT_DATES,
            },
            "tape": {"source": "loan tape", "transfer_date": "not reported", **TAPE_DATES},
            "tolerance_periods": 2,
        },
    )


def test_no_transfer_in_the_comment_compares_nothing():
    assert compare_transfer(text(), tape(transfer_date=D(2025, 3, 6)), tolerance=2) is None


def test_text_financials_date_is_the_latest_dated_figure():
    assert text_financials_date(text()) is None
    assert text_financials_date(text(dscr_as_of=D(2026, 6, 30))) is None  # no figure
    assert text_financials_date(text(dscr=Decimal("0.64"))) is None  # no date
    assert text_financials_date(text(dscr=Decimal("0.64"), dscr_as_of=D(2026, 6, 30))) == D(
        2026, 6, 30
    )
    both = text(
        dscr=Decimal("0.64"),
        dscr_as_of=D(2026, 3, 31),
        occupancy=Decimal("61.4"),
        occupancy_as_of=D(2026, 6, 30),
    )
    assert text_financials_date(both) == D(2026, 6, 30)
    only_occupancy = text(occupancy=Decimal("61.4"), occupancy_as_of=D(2026, 5, 31))
    assert text_financials_date(only_occupancy) == D(2026, 5, 31)


def test_newer_text_financials_are_noted_with_both_sides():
    finding = compare_financials(
        text(dscr=Decimal("0.64"), dscr_as_of=D(2026, 6, 30)),
        tape(dscr=Decimal("0.71"), financials_end=D(2024, 12, 31)),
    )
    assert finding == Finding(
        NEWER_FINANCIALS,
        Status.NOTE,
        NEWER_THAN_TAPE,
        {
            "text": {
                "source": "10-D comment",
                "dscr": "0.64",
                "dscr_as_of": "2026-06-30",
                "occupancy": "not reported",
                "occupancy_as_of": "not reported",
                **TEXT_DATES,
            },
            "tape": {
                "source": "loan tape",
                "dscr": "0.71",
                "occupancy": "not reported",
                "financials_end": "2024-12-31",
                **TAPE_DATES,
            },
        },
    )


def test_text_financials_as_old_as_the_tape_agree():
    finding = compare_financials(
        text(dscr=Decimal("0.71"), dscr_as_of=D(2024, 12, 31)),
        tape(dscr=Decimal("0.71"), financials_end=D(2024, 12, 31)),
    )
    assert finding is not None
    assert (finding.status, finding.message) == (
        Status.AGREE,
        "tape financials are as recent as text",
    )


def test_text_financials_the_tape_lacks_are_noted():
    finding = compare_financials(text(dscr=Decimal("0.64"), dscr_as_of=D(2026, 6, 30)), tape())
    assert finding is not None
    assert (finding.check, finding.status, finding.message) == (
        NEWER_FINANCIALS,
        Status.NOTE,
        TAPE_HAS_NONE,
    )


def test_undated_text_financials_compare_nothing():
    assert compare_financials(text(dscr=Decimal("0.64")), tape()) is None


def test_compare_gives_transfer_then_financials():
    found = compare(
        text(
            transfer_date=D(2024, 11, 15),
            dscr=Decimal("0.64"),
            dscr_as_of=D(2026, 6, 30),
        ),
        tape(transfer_date=D(2025, 3, 6), financials_end=D(2024, 12, 31)),
        tolerance=2,
    )
    assert [(f.check, f.status) for f in found] == [
        (TRANSFER_DATE, Status.FLAG),
        (NEWER_FINANCIALS, Status.NOTE),
    ]
    assert compare(text(), tape(), tolerance=2) == []


def test_note_checks_are_only_the_financials_note():
    assert frozenset({NEWER_FINANCIALS}) == rules.NOTE_CHECKS
    assert frozenset({TRANSFER_DATE, TRANSFER_MISSING}) == rules.TRANSFER_CHECKS
