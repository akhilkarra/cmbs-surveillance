import datetime as dt
from decimal import Decimal

import pytest

from cmbs.domain import Period, format_money, money


@pytest.mark.parametrize(
    ("text", "expected"),
    [("September 2026", Period(2026, 9)), (" august 2025 ", Period(2025, 8))],
)
def test_period_parses_month_names(text, expected):
    assert Period.parse(text) == expected


@pytest.mark.parametrize("text", ["Sept 2026", "2026-09", "September", "Smarch 2026", ""])
def test_period_rejects_other_shapes(text):
    with pytest.raises(ValueError, match="not a period"):
        Period.parse(text)


def test_period_month_out_of_range_is_rejected():
    with pytest.raises(ValueError, match="month must be 1 to 12"):
        Period(2026, 13)


def test_period_label_first_day_and_of():
    september = Period(2026, 9)
    assert september.label == "September 2026" == str(september)
    assert september.first_day == dt.date(2026, 9, 1)
    assert Period.of(dt.date(2026, 9, 29)) == september


def test_period_shift_crosses_years_both_ways():
    assert Period(2026, 12).shift(1) == Period(2027, 1)
    assert Period(2026, 1).shift(-1) == Period(2025, 12)
    assert Period(2025, 8).shift(13) == Period(2026, 9)


def test_months_until_counts_whole_months():
    assert Period(2025, 8).months_until(Period(2026, 9)) == 13
    assert Period(2026, 9).months_until(Period(2026, 9)) == 0
    assert Period(2026, 9).months_until(Period(2025, 1)) == -20


def test_periods_order_chronologically():
    assert Period(2025, 12) < Period(2026, 1) < Period(2026, 2)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$50,000,000.00", Decimal("50000000.00")),
        ("-$12,000,000.00", Decimal("-12000000.00")),
        ("$-12.5", Decimal("-12.50")),
        ("1,234.56", Decimal("1234.56")),
        ("1234567", Decimal("1234567.00")),
        (".5", Decimal("0.50")),
        ("  $ 7 ", Decimal("7.00")),
        ("6,500,000", Decimal("6500000.00")),
        ("0.00", Decimal("0.00")),
        ("1.005", Decimal("1.01")),
        (Decimal("3.333"), Decimal("3.33")),
        (7, Decimal("7.00")),
    ],
)
def test_money_parses_to_the_cent(text, expected):
    assert money(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "abc",
        "$",
        "",
        "-",
        "1,2,3.4.5",
        "1,23",
        "12,,345",
        "1234,567",
        ",123",
        "--12.00",
        "-$-1",
        "12.",
        "1.2.3",
        "$1,000.00x",
    ],
)
def test_money_rejects_non_amounts(text):
    with pytest.raises(ValueError, match="not an amount"):
        money(text)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("50000000"), "$50,000,000.00"),
        (Decimal("-12000000"), "-$12,000,000.00"),
        (Decimal("0.005"), "$0.01"),
    ],
)
def test_format_money(value, expected):
    assert format_money(value) == expected
