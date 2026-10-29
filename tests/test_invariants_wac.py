"""The weighted-average coupon: exact, bounded by the loans' rates, never averaging in a gap."""

from decimal import Decimal
from fractions import Fraction

import pytest

from cmbs.invariants.wac import (
    NO_BALANCE,
    NO_RATE,
    RatedLoan,
    Wac,
    exact_decimal,
    format_rate,
    wac,
)

D = Decimal


def test_the_scenario_pool_is_exactly_five_point_zero_two_five_percent():
    found = wac(
        [
            RatedLoan("1", D("50000000.00"), D("0.0450")),
            RatedLoan("2", D("30000000.00"), D("0.0525")),
            RatedLoan("7", D("20000000.00"), D("0.0600")),
        ]
    )
    assert found == Wac(
        Fraction(5025, 100000),
        lowest=D("0.0450"),
        highest=D("0.0600"),
        averaged=("1", "2", "7"),
        balance=D("100000000.00"),
    )
    assert found.available


def test_a_wac_without_a_finite_decimal_is_kept_exact():
    found = wac([RatedLoan("1", D("1.00"), D("0.01")), RatedLoan("2", D("2.00"), D("0.02"))])
    assert found.rate == Fraction(5, 300)
    assert format_rate(Fraction(5, 300)) == "1.666667% (rounded)"


def test_loans_left_out_are_listed_by_why():
    found = wac(
        [
            RatedLoan("1", D("100.00"), D("0.05")),
            RatedLoan("2", D("100.00"), None),
            RatedLoan("3", None, D("0.09")),
            RatedLoan("4", D("0.00"), D("0.00")),
            RatedLoan("5", D("300.00"), D("0.00")),
        ]
    )
    assert found == Wac(
        Fraction(5, 400),
        lowest=D("0.00"),
        highest=D("0.05"),
        averaged=("1", "5"),
        balance=D("400.00"),
        rate_not_reported=("2",),
        balance_not_reported=("3",),
        zero_rate=("5",),
    )


def test_rates_the_loader_could_not_use_are_listed_not_averaged():
    found = wac(
        [
            RatedLoan("1", D("100.00"), D("0.05")),
            RatedLoan("2", D("100.00"), None, "interest rate is not a number: 'n/a'"),
            RatedLoan("3", D("100.00"), D("4.5"), "interest rate '4.5' is outside 0 to 0.25"),
            RatedLoan("4", D("100.00"), D("-0.01")),
            RatedLoan("5", D("100.00"), D("0.25")),
            RatedLoan("6", D("100.00"), D("0.2500001")),
        ]
    )
    assert found == Wac(
        Fraction(3, 20),
        lowest=D("0.05"),
        highest=D("0.25"),
        averaged=("1", "5"),
        balance=D("200.00"),
        rate_not_readable=("2",),
        rate_out_of_range=("3", "4", "6"),
    )
    only_bad = wac([RatedLoan("1", D("1.00"), D("4.5")), RatedLoan("2", D("1.00"), None, "x")])
    assert only_bad == Wac(None, NO_RATE, rate_not_readable=("2",), rate_out_of_range=("1",))


def test_no_outstanding_balance_means_no_wac():
    found = wac([RatedLoan("1", D("0.00"), D("0.05")), RatedLoan("2", None, D("0.04"))])
    assert found == Wac(None, NO_BALANCE, balance_not_reported=("2",))
    assert not found.available
    assert wac([]) == Wac(None, NO_BALANCE)


def test_balances_without_rates_mean_no_wac_and_say_so():
    found = wac([RatedLoan("1", D("10.00"), None), RatedLoan("2", D("0.00"), None)])
    assert found == Wac(None, NO_RATE, rate_not_reported=("1",))


def test_a_negative_balance_has_no_weight():
    """The loan_period contract quarantines one, but the rule is that only positive count."""
    found = wac([RatedLoan("1", D("-5.00"), D("0.09")), RatedLoan("2", D("5.00"), D("0.01"))])
    assert (found.rate, found.averaged) == (Fraction(1, 100), ("2",))


@pytest.mark.parametrize(
    ("rate", "text"),
    [
        (D("0.045"), "4.50%"),
        (D("0.0450"), "4.50%"),
        (Fraction(5025, 100000), "5.025%"),
        (D("0.06"), "6.00%"),
        (D("0.03768"), "3.768%"),
        (D(".07747000"), "7.747%"),
        (D("0"), "0.00%"),
        (D("1"), "100.00%"),
        (Fraction(2, 3), "66.666667% (rounded)"),
        (Fraction(1, 7), "14.285714% (rounded)"),
    ],
)
def test_rates_print_as_percents(rate, text):
    assert format_rate(rate) == text


def test_exact_decimals():
    assert exact_decimal(Fraction(201, 4000)) == D("0.05025")
    assert str(exact_decimal(Fraction(1, 8))) == "0.125"
    assert str(exact_decimal(Fraction(3))) == "3"
    assert exact_decimal(Fraction(1, 3)) is None
    assert exact_decimal(Fraction(1, 6)) is None
    assert exact_decimal(Fraction(1, 7)) is None
