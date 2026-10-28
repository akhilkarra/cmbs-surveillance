"""Point-in-time reads of the loan panel.

A loan's values for a period became public on the filing date of the tape that reported them.
`as_of(day)` returns, for each deal, loan and period, the version from the latest filing dated
on or before `day`. An amendment is a later version; the original stays stored and readable,
so asking about a day before the amendment still shows the original.
"""

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from cmbs.analysis.staleness import StalenessReport, TapeLoan, measure
from cmbs.deals.service import by_name
from cmbs.deals.tables import Deal
from cmbs.domain import Period
from cmbs.errors import Refused
from cmbs.tape.tables import LoanPeriod

_DIGITS = re.compile(r"(\d+)")


class UnknownDeal(Refused):
    pass


class NothingPublished(Refused):
    """No tape of the deal had been filed by the date asked about."""


def loan_order(loan: str) -> tuple[tuple[int, str], ...]:
    """'2' before '10', and '2' before '2-001': numbers compare as numbers."""
    return tuple(
        (int(part), "") if part.isdigit() else (-1, part) for part in _DIGITS.split(loan) if part
    )


def _ordered(rows: Sequence[LoanPeriod]) -> list[LoanPeriod]:
    return sorted(rows, key=lambda row: (row.deal_id, row.period, loan_order(row.loan)))


def as_of(
    db: Session,
    day: dt.date,
    *,
    deal_id: int | None = None,
    period: Period | None = None,
    loan: str | None = None,
) -> list[LoanPeriod]:
    """The latest version of each (deal, loan, period) published on or before `day`."""
    query = select(LoanPeriod).where(LoanPeriod.filing_date <= day)
    if deal_id is not None:
        query = query.where(LoanPeriod.deal_id == deal_id)
    if period is not None:
        query = query.where(LoanPeriod.period == period.first_day)
    if loan is not None:
        query = query.where(LoanPeriod.loan == loan)
    query = query.ext(distinct_on(LoanPeriod.deal_id, LoanPeriod.loan, LoanPeriod.period)).order_by(
        LoanPeriod.deal_id,
        LoanPeriod.loan,
        LoanPeriod.period,
        LoanPeriod.filing_date.desc(),
        LoanPeriod.accession.desc(),
    )
    return _ordered(db.scalars(query).all())


def versions(db: Session, *, deal_id: int, loan: str, period: Period) -> list[LoanPeriod]:
    """Every stored version of a loan's period, oldest filing first."""
    query = (
        select(LoanPeriod)
        .where(
            LoanPeriod.deal_id == deal_id,
            LoanPeriod.loan == loan,
            LoanPeriod.period == period.first_day,
        )
        .order_by(LoanPeriod.filing_date, LoanPeriod.accession)
    )
    return list(db.scalars(query))


def deal_named(db: Session, name: str) -> Deal:
    deal = by_name(db, name)
    if deal is None:
        raise UnknownDeal(f"no deal named {name!r}")
    return deal


def periods(rows: Sequence[LoanPeriod]) -> list[Period]:
    return sorted({Period.of(row.period) for row in rows})


@dataclass(frozen=True)
class DealTape:
    """The latest period of one deal visible on a day, as the panel showed it then."""

    deal: str
    as_of: dt.date
    period: Period
    rows: list[LoanPeriod]


def tape_as_of(db: Session, deal: str, day: dt.date, period: Period | None = None) -> DealTape:
    """One period of a deal as the panel showed it on `day`; by default the latest period."""
    deal_row = deal_named(db, deal)
    visible = as_of(db, day, deal_id=deal_row.id, period=period)
    if not visible:
        which = f"{period.label} " if period else ""
        raise NothingPublished(f"no {which}loan tape of {deal} had been filed by {day}")
    latest = periods(visible)[-1]
    rows = [row for row in visible if Period.of(row.period) == latest]
    return DealTape(deal=deal, as_of=day, period=latest, rows=rows)


def tape_loan(row: LoanPeriod) -> TapeLoan:
    return TapeLoan(
        loan=row.loan,
        ending_balance=row.ending_balance,
        property_name=row.property_name,
        property_type=row.property_type,
        reporting_period_end=row.reporting_period_end,
        financials_end=row.financials_end,
        filing_date=row.filing_date,
    )


def staleness(
    db: Session, deal: str, day: dt.date, period: Period | None = None
) -> tuple[DealTape, StalenessReport]:
    """Staleness of the deal's tape as the panel showed it on `day` (latest period by default)."""
    tape = tape_as_of(db, deal, day, period)
    return tape, measure([tape_loan(row) for row in tape.rows])
