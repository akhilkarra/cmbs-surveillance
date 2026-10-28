"""Typed fields: candidates found in the text, canonical values, typed reads and display."""

import datetime as dt
from decimal import Decimal

import pytest

from cmbs.extraction.fields import (
    FIELDS,
    InvalidValue,
    Kind,
    by_label,
    candidates,
    canonical,
    display,
    spec,
    typed,
)

COMMENT = (
    "Transferred 3/6/2025. As of 12/31/24, DSCR NOI was 0.71x (1.10 X at closing) and "
    "occupancy was 61.4% vs 88 %. Again 3/6/2025; not a date 13/45/2025 or 1/2/3/2025."
)


def test_the_fields_and_their_kinds():
    assert [(f.name, f.label, f.kind) for f in FIELDS] == [
        ("transfer_reason", "transfer reason", Kind.CATEGORY),
        ("transfer_date", "transfer date", Kind.DATE),
        ("dscr", "DSCR", Kind.RATIO),
        ("dscr_as_of", "DSCR as-of date", Kind.DATE),
        ("occupancy", "occupancy", Kind.PERCENT),
        ("occupancy_as_of", "occupancy as-of date", Kind.DATE),
        ("borrower_request", "borrower request", Kind.CATEGORY),
        ("request_status", "request status", Kind.CATEGORY),
        ("workout_strategy", "workout strategy", Kind.CATEGORY),
    ]
    assert spec("request_status").options == ("pending", "granted", "denied")
    assert "maturity extension" in spec("borrower_request").options
    assert "foreclosure" in spec("workout_strategy").options


def test_fields_are_found_by_label():
    assert by_label("borrower request").name == "borrower_request"
    with pytest.raises(InvalidValue) as refused:
        by_label("sponsor")
    assert str(refused.value) == "there is no field named 'sponsor'"


def test_candidates_are_the_printed_figures_of_the_kind_in_order_once():
    assert candidates(spec("transfer_date"), COMMENT) == ("3/6/2025", "12/31/24")
    assert candidates(spec("dscr"), COMMENT) == ("0.71x", "1.10 X")
    assert candidates(spec("occupancy"), COMMENT) == ("61.4%", "88 %")
    assert candidates(spec("borrower_request"), COMMENT) == ()


def test_no_candidates_when_the_kind_is_not_printed():
    assert candidates(spec("dscr"), "The loan is current.") == ()


@pytest.mark.parametrize(
    ("field", "text", "expected"),
    [
        ("transfer_date", "3/6/2025", "2025-03-06"),
        ("transfer_date", "12/31/24", "2024-12-31"),
        ("transfer_date", "2025-03-06", "2025-03-06"),
        ("dscr", "0.71x", "0.71"),
        ("dscr", "1.10 X", "1.10"),
        ("dscr", "1.25", "1.25"),
        ("occupancy", "61.4%", "61.4"),
        ("occupancy", "88 %", "88"),
        ("borrower_request", " maturity  extension ", "maturity extension"),
    ],
)
def test_canonical_values(field, text, expected):
    assert canonical(spec(field), text) == expected


@pytest.mark.parametrize(
    ("field", "text", "message"),
    [
        ("transfer_date", "13/45/2025", "'13/45/2025' is not a transfer date"),
        ("transfer_date", "soon", "'soon' is not a transfer date"),
        ("dscr", "high", "'high' is not a DSCR"),
        ("dscr", "-0.5x", "'-0.5x' is not a DSCR"),
        ("occupancy", "NaN", "'NaN' is not a occupancy"),
        (
            "request_status",
            "approved",
            "'approved' is not a request status; choose one of: pending, granted, denied",
        ),
    ],
)
def test_values_a_field_cannot_hold_are_refused(field, text, message):
    with pytest.raises(InvalidValue) as refused:
        canonical(spec(field), text)
    assert str(refused.value) == message


def test_typed_values():
    assert typed(spec("transfer_date"), "2025-03-06") == dt.date(2025, 3, 6)
    assert typed(spec("dscr"), "0.71") == Decimal("0.71")
    assert typed(spec("occupancy"), "61.4") == Decimal("61.4")
    assert typed(spec("borrower_request"), "forbearance") == "forbearance"


def test_display_values():
    assert display(spec("dscr"), "0.71") == "0.71x"
    assert display(spec("occupancy"), "61.4") == "61.4%"
    assert display(spec("transfer_date"), "2025-03-06") == "2025-03-06"
    assert display(spec("borrower_request"), "forbearance") == "forbearance"
