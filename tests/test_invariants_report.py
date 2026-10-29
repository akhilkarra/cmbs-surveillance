"""How the pool's WAC reads when it is not available, or averages in a reported zero rate."""

from decimal import Decimal
from fractions import Fraction

from cmbs.invariants.report import wac_json, wac_text
from cmbs.invariants.wac import NO_BALANCE, NO_RATE, Wac


def test_a_wac_not_available_says_why():
    assert wac_text(Wac(None, NO_BALANCE)) == (
        "weighted-average coupon: not available (no loan has an outstanding balance)"
    )
    found = Wac(None, NO_RATE, rate_not_reported=("1",))
    assert wac_text(found) == (
        "weighted-average coupon: not available "
        "(no loan with an outstanding balance reports an interest rate)"
    )
    assert wac_json(found) == {
        "available": False,
        "reason": NO_RATE,
        "rate": None,
        "percent": None,
        "lowest": None,
        "highest": None,
        "averaged": [],
        "balance": "0.00",
        "rate_not_reported": ["1"],
        "balance_not_reported": [],
        "zero_rate": [],
    }


def test_a_reported_zero_rate_is_named():
    found = Wac(
        Fraction(1, 40),
        lowest=Decimal("0.00"),
        highest=Decimal("0.05"),
        averaged=("1", "2"),
        balance=Decimal("200.00"),
        zero_rate=("2",),
    )
    assert wac_text(found) == (
        "weighted-average coupon: 2.50%, between 0.00% and 5.00%, over 2 loans and $200.00; "
        "reported rate 0.00: loan 2"
    )
    assert wac_json(found)["rate"] == "1/40"
    assert wac_json(found)["percent"] == "2.50%"
