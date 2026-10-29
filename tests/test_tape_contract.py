"""The loan_period contract names each failure the way a reader sees it."""

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
from pandera.errors import SchemaErrors

from cmbs.tape import contract
from cmbs.tape.contract import violations
from cmbs.tape.convert import ROW_FIELDS, LoanPeriodRow

PERIOD_END = dt.date(2026, 9, 11)


def row(loan: str = "1", **values) -> LoanPeriodRow:
    fields: dict[str, Any] = dict.fromkeys(ROW_FIELDS)
    return LoanPeriodRow(**fields | {"loan": loan, "reporting_period_end": PERIOD_END} | values)


def test_good_rows_pass():
    rows = [
        row("1", ending_balance=Decimal("50000000.00"), beginning_balance=Decimal("0.00")),
        row("2", financials_start=dt.date(2026, 1, 1), financials_end=dt.date(2026, 1, 1)),
        row("3"),
    ]
    assert violations(rows) == {}


def test_no_rows_pass():
    assert violations([]) == {}


@pytest.mark.parametrize(
    ("values", "reason"),
    [
        ({"ending_balance": Decimal("-0.01")}, "ending balance is negative"),
        ({"beginning_balance": Decimal("-12000000.00")}, "beginning balance is negative"),
        (
            {"financials_start": dt.date(2026, 7, 1), "financials_end": dt.date(2026, 6, 30)},
            "most recent financials end before they start",
        ),
        ({"reporting_period_end": None}, "reporting period end is not reported"),
    ],
)
def test_each_rule_has_its_reason(values, reason):
    assert violations([row("1"), row("2", **values)]) == {1: [reason]}


def test_a_row_failing_several_rules_gets_every_reason_once():
    bad = row(
        "5",
        ending_balance=Decimal("-1.00"),
        beginning_balance=Decimal("-2.00"),
        financials_start=dt.date(2026, 7, 1),
        financials_end=dt.date(2026, 6, 30),
    )
    assert violations([bad]) == {
        0: [
            "beginning balance is negative",
            "ending balance is negative",
            "most recent financials end before they start",
        ]
    }


def test_a_loan_reported_twice_fails_both_times():
    assert violations([row("1"), row("2"), row("1")]) == {
        0: ["loan is reported more than once"],
        2: ["loan is reported more than once"],
    }


def test_a_missing_financials_date_is_not_out_of_order():
    rows = [
        row("1", financials_start=dt.date(2026, 7, 1)),
        row("2", financials_end=dt.date(2026, 6, 30)),
    ]
    assert violations(rows) == {}


def test_a_schema_problem_is_a_bug_not_a_quarantine(monkeypatch):
    monkeypatch.setitem(contract.FRAME_SCHEMA, "ending_balance", contract.pl.String)
    with pytest.raises(SchemaErrors):
        violations([row("1", ending_balance="abc")])
