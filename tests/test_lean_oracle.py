"""Differential tests: Python and the Lean oracle on the same cases must agree exactly.

Lean proves its definitions right for every input (lean/Invariants); these tests tie Python's
implementations to those definitions (grill log Q4). Hypothesis generates months, pools and
judgments, Python computes its answer, the oracle computes Lean's, and the two must be equal:
statuses, cents, counts, and rates as exact fractions.

Skipped, with the reason, when Lean is not installed; CI's `lean` job installs it and runs them.
"""

import os
from collections.abc import Iterator
from decimal import Decimal
from fractions import Fraction

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from cmbs.crosscheck import rollforward as rf
from cmbs.domain import Period
from cmbs.invariants import oracle, table
from cmbs.invariants.partition import Bucket, PoolLoan, partition
from cmbs.invariants.wac import RatedLoan, wac

EXAMPLES = settings(max_examples=300, deadline=None, database=None)


@pytest.fixture(scope="module")
def lean() -> Iterator[oracle.Oracle]:
    """The running oracle. Skips without Lean, except where CMBS_REQUIRE_LEAN is set (CI's
    `lean` job), where a missing Lean is a failure."""
    executable, why = oracle.build()
    if executable is None:
        if os.environ.get("CMBS_REQUIRE_LEAN"):
            pytest.fail(why)
        pytest.skip(why)
    with oracle.Oracle([str(executable)]) as running:
        yield running


# Amounts as tapes report them: whole cents, sometimes not reported.
cents = st.integers(min_value=-(10**15), max_value=10**15)
money = cents.map(lambda value: Decimal(value).scaleb(-2))
reported = st.none() | money
small = st.integers(min_value=0, max_value=10**6).map(lambda value: Decimal(value).scaleb(-2))


# The balance roll-forward.

LEAN_REASON = {
    rf.REALIZED_LOSS_MONTH: "realized loss",
    "beginning balance not reported": "not reported: beginning",
    f"{rf.ENDING_SCHEDULED} not reported": "not reported: ending",
    f"beginning balance and {rf.ENDING_SCHEDULED} not reported": "not reported: beginning, ending",
}


@st.composite
def months(draw) -> rf.Balances:
    """Any month, and often one whose reported amounts roll exactly or miss by a little."""
    beginning, scheduled, unscheduled, other = (draw(reported) for _ in range(4))
    loss = draw(st.none() | st.just(Decimal("0.00")) | small | money)
    ending = draw(reported)
    if beginning is not None and draw(st.booleans()):
        explained = beginning
        for amount in (scheduled, unscheduled, other):
            explained -= amount or 0
        ending = explained + draw(st.sampled_from([0, 0, Decimal("0.01"), Decimal("-10000")]))
    return rf.Balances(beginning, scheduled, unscheduled, other, loss, rf.Ending(ending))


def python_roll(balances: rf.Balances) -> dict[str, object]:
    roll = rf.roll(balances)
    return {
        "status": roll.status.value,
        "expected": oracle.cents(roll.expected_ending),
        "gap": oracle.cents(roll.gap),
        "reason": None if roll.reason is None else LEAN_REASON[roll.reason],
        "missing": list(roll.not_reported),
    }


@given(months())
@EXAMPLES
def test_rollforward_agrees_with_lean(lean, balances):
    assert python_roll(balances) == lean.ask(oracle.rollforward_case(balances))


def test_rollforward_cases_from_the_scenarios(lean):
    rolls = rf.Balances(
        Decimal("50100000.00"),
        Decimal("100000.00"),
        Decimal("0.00"),
        Decimal("0.00"),
        Decimal("0.00"),
        rf.Ending(Decimal("50000000.00")),
    )
    breaks = rf.Balances(
        Decimal("9050000.00"),
        Decimal("40000.00"),
        Decimal("0.00"),
        Decimal("0.00"),
        Decimal("0.00"),
        rf.Ending(Decimal("9000000.00")),
    )
    assert lean.ask(oracle.rollforward_case(rolls))["status"] == "passed"
    answer = lean.ask(oracle.rollforward_case(breaks))
    assert (answer["status"], answer["gap"]) == ("break", 1_000_000)
    assert python_roll(rolls) == lean.ask(oracle.rollforward_case(rolls))
    assert python_roll(breaks) == answer


# Continuity across months.

CONTINUITY_REASON = {
    "beginning balance not reported": "not reported: beginning",
    f"August 2026 {rf.ENDING_SCHEDULED} not reported": "not reported: previous ending",
    "no August 2026 tape row for the loan": "no previous row",
}
AUGUST = Period(2026, 8)


@st.composite
def continuities(draw) -> tuple[Decimal | None, rf.Ending | None]:
    beginning = draw(reported)
    if draw(st.booleans()):
        return beginning, None
    ending = beginning if beginning is not None and draw(st.booleans()) else draw(reported)
    return beginning, rf.Ending(ending)


@given(continuities())
@EXAMPLES
def test_continuity_agrees_with_lean(lean, case):
    beginning, previous = case
    found = rf.continuity(beginning, previous, AUGUST)
    assert {
        "status": found.status.value,
        "previous_ending": oracle.cents(found.previous_ending),
        "gap": oracle.cents(found.gap),
        "reason": None if found.reason is None else CONTINUITY_REASON[found.reason],
    } == lean.ask(oracle.continuity_case(beginning, previous))


# The delinquency partition.

statuses = st.none() | st.sampled_from(
    ["0", "A", "B", "1", "2", "3", "4", "5", "01", "b", "a", "00", "6", "X", "9"]
)
pools = st.lists(
    st.builds(
        PoolLoan,
        loan=st.text(alphabet="0123456789-A", min_size=1, max_size=4),
        status=statuses | st.text(alphabet="0123456789ABab ", max_size=3),
        balance=st.none() | small | money,
    ),
    max_size=30,
)


def python_partition(loans: list[PoolLoan]) -> dict[str, object]:
    found = partition(loans)

    def tally(found_tally) -> dict[str, int | None]:
        return {
            "loans": found_tally.loans,
            "balance": oracle.cents(found_tally.balance),
            "unreported": len(found_tally.balance_not_reported),
        }

    return {
        "buckets": [{"bucket": bucket.value, **tally(t)} for bucket, t in found.buckets.items()],
        "pool": tally(found.pool),
    }


@given(pools)
@EXAMPLES
def test_partition_agrees_with_lean(lean, loans):
    assert python_partition(loans) == lean.ask(oracle.partition_case(loans))


def test_partition_of_the_scenario_pool(lean):
    loans = [
        PoolLoan("1", "0", Decimal("50000000.00")),
        PoolLoan("2", "3", Decimal("30000000.00")),
        PoolLoan("3", "0", Decimal("8000000.00")),
        PoolLoan("4", "1", Decimal("4000000.00")),
        PoolLoan("5", "B", Decimal("12000000.00")),
        PoolLoan("7", None, Decimal("9000000.00")),
    ]
    answer = lean.ask(oracle.partition_case(loans))
    assert answer["pool"] == {"loans": 6, "balance": 11_300_000_000, "unreported": 0}
    assert [bucket["bucket"] for bucket in answer["buckets"]] == [b.value for b in Bucket]
    assert python_partition(loans) == answer


# The weighted-average coupon.

rates = st.builds(
    lambda numerator, places: Decimal(numerator).scaleb(-places),
    st.integers(min_value=0, max_value=10**7),
    st.integers(min_value=0, max_value=8),
)
rated_pools = st.lists(
    st.builds(
        RatedLoan,
        loan=st.text(alphabet="0123456789", min_size=1, max_size=3),
        balance=st.none() | st.just(Decimal("0.00")) | small | money,
        rate=st.none() | st.just(Decimal("0.00")) | st.just(Decimal("0.25")) | rates,
        rate_note=st.none() | st.just("interest rate is not a number: 'n/a'"),
    ),
    max_size=30,
)


@given(rated_pools)
@EXAMPLES
def test_wac_agrees_with_lean(lean, loans):
    found = wac(loans)
    case, scale = oracle.wac_case(loans)
    answer = oracle.lean_wac(lean.ask(case), scale)
    if found.rate is None:
        assert answer is None
        return
    assert answer is not None
    assert found.lowest is not None and found.highest is not None
    assert answer == oracle.LeanWac(found.rate, Fraction(found.lowest), Fraction(found.highest))
    assert found.lowest <= found.rate <= found.highest


def test_wac_of_the_scenario_pool(lean):
    loans = [
        RatedLoan("1", Decimal("50000000.00"), Decimal("0.0450")),
        RatedLoan("2", Decimal("30000000.00"), Decimal("0.0525")),
        RatedLoan("7", Decimal("20000000.00"), Decimal("0.0600")),
    ]
    case, scale = oracle.wac_case(loans)
    assert oracle.lean_wac(lean.ask(case), scale) == oracle.LeanWac(
        Fraction(5025, 100000), Fraction(45, 1000), Fraction(6, 100)
    )
    zero, scale = oracle.wac_case([RatedLoan("1", Decimal("0.00"), Decimal("0.05"))])
    assert lean.ask(zero) is None


# The decision table.


def test_lean_prints_the_committed_table(lean):
    assert lean.ask(oracle.TABLE_CASE) == oracle.committed_table()
    assert table.lean_rows(lean.ask(oracle.TABLE_CASE)) == table.docstring_rows()


def test_decide_agrees_with_lean_on_every_combination(lean):
    for combination in table.combinations():
        answer = lean.ask(
            oracle.decision_case(
                answer=combination.answer,
                support=combination.support,
                evidence_found=combination.evidence_found,
            )
        )
        outcome, reason = table.python_decision(combination)
        assert answer == {"outcome": outcome.value, "reason": reason}, combination.label


def test_the_oracle_refuses_what_it_cannot_read(lean):
    with pytest.raises(oracle.OracleError, match="unknown kind: nothing"):
        lean.ask({"kind": "nothing"})
    with pytest.raises(oracle.OracleError, match="not a band: middling"):
        lean.ask({"kind": "decision", "answer": "middling", "support": "low"})
    assert lean.ask(oracle.TABLE_CASE) == oracle.committed_table()  # still running
