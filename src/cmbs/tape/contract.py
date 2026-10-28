"""The loan_period contract: what a typed loan row must satisfy before it is loaded.

Checked with Pandera over a Polars frame. Each check is named by the reason a reader sees when
a loan fails it ("ending balance is negative"), so a failure maps straight to a quarantine
reason. The silver table carries the same rules as CHECK constraints, so a row that slipped
past this contract would still be refused by the database.
"""

import datetime as dt
from collections.abc import Sequence
from typing import Any

import pandera.polars as pa
import polars as pl
from pandera.errors import SchemaErrors

from cmbs.tape.convert import LoanPeriodRow

MONEY = pl.Decimal(38, 2)
FRAME_SCHEMA: dict[str, Any] = {
    "loan": pl.String,
    "reporting_period_end": pl.Date,
    "beginning_balance": MONEY,
    "ending_balance": MONEY,
    "financials_start": pl.Date,
    "financials_end": pl.Date,
}

NEGATIVE_ENDING_BALANCE = "ending balance is negative"
NEGATIVE_BEGINNING_BALANCE = "beginning balance is negative"
FINANCIALS_OUT_OF_ORDER = "most recent financials end before they start"
REPORTED_TWICE = "loan is reported more than once"
# Pandera's own checks, under Pandera's names; the failure's column says which field failed.
_UNIQUE = "field_uniqueness"
_NOT_NULLABLE = "not_nullable"


def _not_negative(column: str) -> pl.Expr:
    return pl.col(column).is_null() | (pl.col(column) >= 0)


class LoanPeriodContract(pa.DataFrameModel):
    loan: str = pa.Field(nullable=False, unique=True)
    reporting_period_end: dt.date = pa.Field(nullable=False)
    beginning_balance: MONEY = pa.Field(nullable=True)  # pyright: ignore[reportInvalidTypeForm]
    ending_balance: MONEY = pa.Field(nullable=True)  # pyright: ignore[reportInvalidTypeForm]
    financials_start: dt.date = pa.Field(nullable=True)
    financials_end: dt.date = pa.Field(nullable=True)

    @pa.check("ending_balance", name=NEGATIVE_ENDING_BALANCE)
    @classmethod
    def ending_balance_not_negative(cls, data: Any) -> Any:
        return data.lazyframe.select(_not_negative(data.key))

    @pa.check("beginning_balance", name=NEGATIVE_BEGINNING_BALANCE)
    @classmethod
    def beginning_balance_not_negative(cls, data: Any) -> Any:
        return data.lazyframe.select(_not_negative(data.key))

    @pa.dataframe_check(name=FINANCIALS_OUT_OF_ORDER)
    @classmethod
    def financials_in_order(cls, data: Any) -> Any:
        start, end = pl.col("financials_start"), pl.col("financials_end")
        return data.lazyframe.select(start.is_null() | end.is_null() | (end >= start))

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = False


def frame(rows: Sequence[LoanPeriodRow]) -> pl.DataFrame:
    return pl.DataFrame(
        [{name: getattr(row, name) for name in FRAME_SCHEMA} for row in rows],
        schema=FRAME_SCHEMA,
        orient="row",
    )


def _reason(check: str, column: str) -> str:
    if check == _UNIQUE:
        return REPORTED_TWICE
    if check == _NOT_NULLABLE:
        return f"{column.replace('_', ' ')} is not reported"
    return check


def violations(rows: Sequence[LoanPeriodRow]) -> dict[int, list[str]]:
    """For each failing row (by index in `rows`), its reasons in the contract's check order."""
    try:
        LoanPeriodContract.validate(frame(rows), lazy=True)
    except SchemaErrors as errors:
        found: dict[int, list[str]] = {}
        for case in errors.failure_cases.iter_rows(named=True):
            if case["index"] is None:
                raise  # a schema problem (a missing column, a wrong type): a bug, not data
            found.setdefault(case["index"], []).append(_reason(case["check"], case["column"]))
        return found
    return {}
