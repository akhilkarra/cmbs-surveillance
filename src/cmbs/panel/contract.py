"""The as_of contract: a model input built "as of" a date uses only data public by that date.

A model input row names the date it is built as of and, for every value it uses, the filing
date of the tape the value came from. A value published after the as-of date is look-ahead:
the row is rejected with the reason "uses data published after YYYY-MM-DD". Checked with
Pandera over a Polars frame, the same way the loan_period contract checks tape rows.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pandera.polars as pa
import polars as pl
from pandera.errors import SchemaErrors

from cmbs.tape.tables import LoanPeriod

LOOK_AHEAD = "uses data published after its as-of date"


@dataclass(frozen=True)
class ModelInput:
    """One value a model reads, with the filing that published it."""

    deal_id: int
    loan: str
    period: dt.date
    as_of: dt.date
    field: str
    value: Decimal | dt.date | str | None
    accession: str
    published: dt.date


def model_input(version: LoanPeriod, field: str, as_of: dt.date) -> ModelInput:
    """Take `field` from a stored version of a loan's period, for a row built as of `as_of`."""
    return ModelInput(
        deal_id=version.deal_id,
        loan=version.loan,
        period=version.period,
        as_of=as_of,
        field=field,
        value=getattr(version, field),
        accession=version.accession,
        published=version.filing_date,
    )


class AsOfContract(pa.DataFrameModel):
    loan: str = pa.Field(nullable=False)
    as_of: dt.date = pa.Field(nullable=False)
    published: dt.date = pa.Field(nullable=False)

    @pa.dataframe_check(name=LOOK_AHEAD)
    @classmethod
    def published_by_as_of(cls, data: Any) -> Any:
        return data.lazyframe.select(pl.col("published") <= pl.col("as_of"))

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = False


@dataclass(frozen=True)
class Rejection:
    row: ModelInput
    reason: str


def rejections(rows: Sequence[ModelInput]) -> list[Rejection]:
    """Every row that uses data published after its as-of date, in input order."""
    frame = pl.DataFrame(
        [{"loan": row.loan, "as_of": row.as_of, "published": row.published} for row in rows],
        schema={"loan": pl.String, "as_of": pl.Date, "published": pl.Date},
        orient="row",
    )
    try:
        AsOfContract.validate(frame, lazy=True)
    except SchemaErrors as errors:
        failed = {case["index"] for case in errors.failure_cases.iter_rows(named=True)}
        if None in failed:
            raise  # a schema problem, not look-ahead: a bug in the caller
        return [
            Rejection(rows[index], f"uses data published after {rows[index].as_of.isoformat()}")
            for index in sorted(failed)
        ]
    return []
