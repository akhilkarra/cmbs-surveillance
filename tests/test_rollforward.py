"""The balance roll-forward: pure arithmetic on what one tape reports, to the cent."""

import datetime as dt
from decimal import Decimal

import pytest

from cmbs.crosscheck.rollforward import (
    ACTUAL,
    SCHEDULED,
    Balances,
    Continuity,
    Ending,
    LoanRollForward,
    Roll,
    Status,
    continuity,
    continuity_sides,
    ending_of,
    roll,
    roll_sides,
)
from cmbs.domain import Period, money

SEPTEMBER = Period(2026, 9)
AUGUST = Period(2026, 8)


def balances(
    beginning: str | None = "9,050,000.00",
    scheduled: str | None = "40,000.00",
    unscheduled: str | None = "0.00",
    other: str | None = "0.00",
    loss: str | None = "0.00",
    ending: str | None = "9,000,000.00",
    basis: str = ACTUAL,
) -> Balances:
    def amount(text: str | None) -> Decimal | None:
        return None if text is None else money(text)

    return Balances(
        beginning=amount(beginning),
        scheduled_principal=amount(scheduled),
        unscheduled_principal=amount(unscheduled),
        other_principal_adjustments=amount(other),
        realized_loss=amount(loss),
        ending=Ending(amount(ending), basis),
    )


def test_a_balance_that_rolls_forward_passes():
    assert roll(
        balances(beginning="50,100,000.00", scheduled="100,000.00", ending="50,000,000.00")
    ) == Roll(Status.PASSED, expected_ending=money("50,000,000.00"), gap=money("0.00"))


def test_a_break_carries_the_gap_explained_minus_reported():
    assert roll(balances()) == Roll(
        Status.BREAK, expected_ending=money("9,010,000.00"), gap=money("10,000.00")
    )


def test_a_reported_balance_above_the_roll_gives_a_negative_gap():
    result = roll(balances(ending="9,010,000.01"))
    assert (result.status, result.gap) == (Status.BREAK, money("-0.01"))


def test_every_deduction_counts():
    result = roll(
        balances(
            beginning="1,000.00",
            scheduled="100.00",
            unscheduled="200.00",
            other="30.00",
            loss="4.00",
            ending="666.00",
        )
    )
    assert result == Roll(Status.PASSED, expected_ending=money("666.00"), gap=money("0.00"))
    # One cent off in any component is a break of one cent.
    assert roll(
        balances(
            beginning="1,000.00",
            scheduled="100.00",
            unscheduled="200.00",
            other="30.00",
            loss="4.01",
            ending="666.00",
        )
    ).gap == money("-0.01")


def test_negative_other_adjustments_add_to_the_balance():
    result = roll(
        balances(beginning="1,000.00", scheduled="0.00", other="-50.00", ending="1,050.00")
    )
    assert (result.status, result.expected_ending) == (Status.PASSED, money("1,050.00"))


@pytest.mark.parametrize(
    ("beginning", "ending", "reason"),
    [
        (None, "9,000,000.00", "beginning balance not reported"),
        ("9,050,000.00", None, "ending balance not reported"),
        (None, None, "beginning balance and ending balance not reported"),
    ],
)
def test_without_both_balances_nothing_is_checked(beginning, ending, reason):
    assert roll(balances(beginning=beginning, ending=ending)) == Roll(
        Status.NOT_CHECKED, reason=reason
    )


def test_missing_deductions_are_listed_never_taken_as_zero():
    result = roll(balances(beginning="9,040,000.00", other=None, loss=None))
    assert result == Roll(
        Status.PASSED,
        expected_ending=money("9,000,000.00"),
        gap=money("0.00"),
        not_reported=("other principal adjustments", "realized loss"),
    )


def test_a_gap_with_missing_deductions_is_still_a_break():
    result = roll(balances(unscheduled=None))
    assert result == Roll(
        Status.BREAK,
        expected_ending=money("9,010,000.00"),
        gap=money("10,000.00"),
        not_reported=("unscheduled principal",),
    )


def test_every_deduction_missing_compares_the_balances_alone():
    result = roll(
        balances(
            beginning="5.00", scheduled=None, unscheduled=None, other=None, loss=None, ending="5.00"
        )
    )
    assert result.status is Status.PASSED
    assert result.not_reported == (
        "scheduled principal",
        "unscheduled principal",
        "other principal adjustments",
        "realized loss",
    )


def test_an_unreadable_ending_is_not_checked():
    problem = Ending(None, SCHEDULED, "ending scheduled balance is not a number: 'n/a'")
    result = roll(
        Balances(
            beginning=money("1.00"),
            scheduled_principal=None,
            unscheduled_principal=None,
            other_principal_adjustments=None,
            realized_loss=None,
            ending=problem,
        )
    )
    assert result == Roll(
        Status.NOT_CHECKED, reason="ending scheduled balance is not a number: 'n/a'"
    )


def test_ending_prefers_the_scheduled_balance_as_reported():
    assert ending_of("8990000.00", money("9,000,000.00")) == Ending(
        money("8,990,000.00"), SCHEDULED
    )
    # BANK5 writes zero amounts with no leading digit, and eight places.
    assert ending_of(".00000000", None) == Ending(money("0.00"), SCHEDULED)
    assert ending_of(" 90000000.00000000 ", None) == Ending(money("90,000,000.00"), SCHEDULED)


def test_ending_falls_back_to_the_actual_balance():
    assert ending_of(None, money("9,000,000.00")) == Ending(money("9,000,000.00"), ACTUAL)
    assert ending_of("", money("1.00")) == Ending(money("1.00"), ACTUAL)
    assert ending_of(None, None) == Ending(None, ACTUAL)


def test_an_unreadable_scheduled_ending_is_never_guessed():
    assert ending_of("n/a", money("1.00")) == Ending(
        None, SCHEDULED, "ending scheduled balance is not a number: 'n/a'"
    )
    assert ending_of("1.005", money("1.00")) == Ending(
        None, SCHEDULED, "ending scheduled balance has more than two decimal places: '1.005'"
    )


def test_continuity_passes_when_last_ending_is_this_beginning():
    previous = Ending(money("9,050,000.00"), SCHEDULED)
    assert continuity(money("9,050,000.00"), previous, AUGUST) == Continuity(
        Status.PASSED, previous_ending=money("9,050,000.00"), gap=money("0.00")
    )


def test_continuity_break_carries_last_ending_minus_this_beginning():
    previous = Ending(money("9,060,000.00"), ACTUAL)
    assert continuity(money("9,050,000.00"), previous, AUGUST) == Continuity(
        Status.BREAK, previous_ending=money("9,060,000.00"), gap=money("10,000.00")
    )


@pytest.mark.parametrize(
    ("beginning", "previous", "reason"),
    [
        (money("1.00"), None, "no August 2026 tape row for the loan"),
        (None, Ending(money("1.00"), ACTUAL), "beginning balance not reported"),
        (money("1.00"), Ending(None, ACTUAL), "August 2026 ending balance not reported"),
        (
            money("1.00"),
            Ending(None, SCHEDULED, "ending scheduled balance is not a number: 'x'"),
            "August 2026: ending scheduled balance is not a number: 'x'",
        ),
    ],
)
def test_continuity_not_checked(beginning, previous, reason):
    assert continuity(beginning, previous, AUGUST) == Continuity(Status.NOT_CHECKED, reason=reason)


def result(roll_result: Roll, continuity_result: Continuity) -> LoanRollForward:
    return LoanRollForward(
        loan="7",
        period=SEPTEMBER,
        accession="0009900001-26-000901",
        filed=dt.date(2026, 9, 29),
        balances=balances(unscheduled=None),
        roll=roll_result,
        continuity=continuity_result,
        previous_accession="0009900001-26-000801",
    )


@pytest.mark.parametrize(
    ("roll_status", "continuity_status", "overall"),
    [
        (Status.PASSED, Status.PASSED, Status.PASSED),
        (Status.PASSED, Status.NOT_CHECKED, Status.PASSED),
        (Status.PASSED, Status.BREAK, Status.BREAK),
        (Status.BREAK, Status.PASSED, Status.BREAK),
        (Status.NOT_CHECKED, Status.NOT_CHECKED, Status.NOT_CHECKED),
        (Status.NOT_CHECKED, Status.BREAK, Status.BREAK),
    ],
)
def test_a_break_anywhere_is_a_break(roll_status, continuity_status, overall):
    loan = result(Roll(roll_status), Continuity(continuity_status))
    assert loan.status is overall
    assert loan.label == f"roll-forward {overall.value}"


def test_roll_sides_show_components_both_endings_and_the_gap():
    loan = result(roll(balances(unscheduled=None)), Continuity(Status.PASSED))
    assert roll_sides(loan) == {
        "components": {
            "beginning balance": "$9,050,000.00",
            "scheduled principal": "$40,000.00",
            "unscheduled principal": "not reported",
            "other principal adjustments": "$0.00",
            "realized loss": "$0.00",
        },
        "explained ending balance": "$9,010,000.00",
        "reported ending balance": "$9,000,000.00",
        "ending basis": "actual",
        "gap": "$10,000.00",
        "not reported": ["unscheduled principal"],
        "accession": "0009900001-26-000901",
        "filed": "2026-09-29",
    }


def test_continuity_sides_name_last_month():
    loan = result(
        Roll(Status.PASSED),
        Continuity(Status.BREAK, previous_ending=money("9,060,000.00"), gap=Decimal("10000.00")),
    )
    assert continuity_sides(loan) == {
        "beginning balance": "$9,050,000.00",
        "accession": "0009900001-26-000901",
        "August 2026 ending balance": "$9,060,000.00",
        "previous accession": "0009900001-26-000801",
        "gap": "$10,000.00",
    }
