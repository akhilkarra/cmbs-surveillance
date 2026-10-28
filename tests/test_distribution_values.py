"""Reading single report cells (cmbs.distribution.values)."""

import datetime as dt
from decimal import Decimal

import pytest

from cmbs.distribution import values


@pytest.mark.parametrize(
    ("text", "loan"),
    [
        ("11", "11"),
        ("011", "11"),
        ("0011", "11"),
        (" 11.00 ", "11"),
        ("11A", "11A"),
        ("11-a", "11A"),
        ("11 a", "11A"),
        ("11AB", "11AB"),
        ("#11", "11"),
        ("Loan 11", "11"),
        ("11*", "11"),
        ("0", "0"),
        ("9999", "9999"),
        ("", None),
        ("Totals", None),
        ("310951234", None),
        ("10000", None),
        ("1.01", None),
        ("2-001", None),
        ("11ABC", None),
        ("N/A", None),
    ],
)
def test_loan_ids(text, loan):
    assert values.loan_id(text) == loan


@pytest.mark.parametrize(
    ("text", "amount"),
    [
        ("6,500,000.00", Decimal("6500000.00")),
        ("$6,500,000.00", Decimal("6500000.00")),
        ("  125,000 ", Decimal("125000.00")),
        ("(1,250.50)", Decimal("-1250.50")),
        ("-1,250.50", Decimal("-1250.50")),
        ("0.00", Decimal("0.00")),
        ("", None),
        ("-", None),
        ("--", None),
        ("N/A", None),
        ("NAP", None),
        ("none", None),
    ],
)
def test_amounts(text, amount):
    assert values.amount(text) == amount


def test_a_bracketed_zero_is_not_negative_zero():
    assert str(values.amount("(0.00)")) == "0.00"


@pytest.mark.parametrize("text", ["abc", "1,23", "(abc)"])
def test_an_unreadable_amount_says_so(text):
    with pytest.raises(ValueError, match="not an amount of money"):
        values.amount(text)


@pytest.mark.parametrize(
    ("text", "day"),
    [
        ("06/04/2026", dt.date(2026, 6, 4)),
        ("6/4/2026", dt.date(2026, 6, 4)),
        ("6/4/26", dt.date(2026, 6, 4)),
        ("2026-06-04", dt.date(2026, 6, 4)),
        ("", None),
        ("-", None),
        ("N/A", None),
    ],
)
def test_dates(text, day):
    assert values.date(text) == day


@pytest.mark.parametrize("text", ["13/45/2026", "6/4/226", "June 4"])
def test_an_unreadable_date_says_so(text):
    with pytest.raises(ValueError) as error:
        values.date(text)
    assert str(error.value) == f"not a date: {text!r}"


def test_text_is_whitespace_normalised_and_blank_is_not_reported():
    assert values.text("  The loan\n  transferred. ") == "The loan transferred."
    assert values.text(" n/a ") is None
    assert values.text("") is None
    assert values.is_reported("0") is True
    assert values.is_reported(" - ") is False
