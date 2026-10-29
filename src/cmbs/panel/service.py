"""Point-in-time reads of the loan panel.

A loan's values for a period became public on the filing date of the tape that reported them.
For each deal and period, the current tape on a day is the latest one filed on or before it:
latest filing date first, then the deepest amendment (an amendment ranks above the filing it
amends; the loader refuses two unlinked tapes of one period filed the same day). `as_of(day)`
returns the loan rows of each current tape. Earlier tapes stay stored and readable through
`versions`, so asking about a day before an amendment still shows the original.

A loan an earlier tape reported but the current tape omits or quarantines is left out of
`as_of`, and `superseded` says which loans those are and why: the latest filing is the
authority for its period, and an older value shown as current would mislead.
"""

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from cmbs.analysis.staleness import StalenessReport, TapeLoan, measure
from cmbs.deals.service import by_name
from cmbs.deals.tables import Deal
from cmbs.domain import Period
from cmbs.errors import Refused
from cmbs.tape.tables import LoanPeriod, LoanTape, QuarantinedLoan

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


def _current(day: dt.date, deal_id: int | None, period: Period | None) -> Select[LoanTape]:
    """The current tape of each (deal, period) on `day`, ordered by deal and period."""
    query = select(LoanTape).where(LoanTape.filing_date <= day)
    if deal_id is not None:
        query = query.where(LoanTape.deal_id == deal_id)
    if period is not None:
        query = query.where(LoanTape.period == period.first_day)
    return query.ext(distinct_on(LoanTape.deal_id, LoanTape.period)).order_by(
        LoanTape.deal_id,
        LoanTape.period,
        LoanTape.filing_date.desc(),
        LoanTape.amendment_depth.desc(),
    )


def current_tapes(
    db: Session, day: dt.date, *, deal_id: int | None = None, period: Period | None = None
) -> list[LoanTape]:
    return list(db.scalars(_current(day, deal_id, period)))


def as_of(
    db: Session,
    day: dt.date,
    *,
    deal_id: int | None = None,
    period: Period | None = None,
    loan: str | None = None,
) -> list[LoanPeriod]:
    """The loan rows of each (deal, period) as its current tape on `day` reports them."""
    current = _current(day, deal_id, period).with_only_columns(LoanTape.accession).subquery()
    query = select(LoanPeriod).join(current, LoanPeriod.accession == current.c.accession)
    if loan is not None:
        query = query.where(LoanPeriod.loan == loan)
    return _ordered(db.scalars(query).all())


@dataclass(frozen=True)
class Superseded:
    """A loan an earlier tape reported that the current tape for its period does not show."""

    deal_id: int
    loan: str
    period: dt.date
    accession: str
    filing_date: dt.date
    superseded_by: str
    reason: str


def superseded(
    db: Session, day: dt.date, *, deal_id: int | None = None, period: Period | None = None
) -> list[Superseded]:
    """Loans left out of `as_of(day)` because the current tape omits or quarantines them."""
    current = {
        (t.deal_id, t.period): t for t in current_tapes(db, day, deal_id=deal_id, period=period)
    }
    found: list[Superseded] = []
    for (deal, month), tape in sorted(current.items()):
        shown = set(
            db.scalars(select(LoanPeriod.loan).where(LoanPeriod.accession == tape.accession))
        )
        quarantined = {
            q.loan: q.reason
            for q in db.scalars(
                select(QuarantinedLoan).where(QuarantinedLoan.accession == tape.accession)
            )
        }
        earlier = db.scalars(
            select(LoanPeriod)
            .join(LoanTape, LoanTape.accession == LoanPeriod.accession)
            .where(
                LoanPeriod.deal_id == deal,
                LoanPeriod.period == month,
                LoanTape.filing_date <= day,
                LoanPeriod.accession != tape.accession,
                LoanPeriod.loan.not_in(shown),
            )
            .order_by(LoanPeriod.loan, LoanTape.filing_date, LoanTape.amendment_depth)
        ).all()
        latest_earlier = {row.loan: row for row in earlier}  # the last version of each loan
        for row in _ordered(list(latest_earlier.values())):
            reason = (
                f"quarantined in {tape.accession}: {quarantined[row.loan]}"
                if row.loan in quarantined
                else f"not reported in {tape.accession}"
            )
            found.append(
                Superseded(
                    deal_id=deal,
                    loan=row.loan,
                    period=month,
                    accession=row.accession,
                    filing_date=row.filing_date,
                    superseded_by=tape.accession,
                    reason=reason,
                )
            )
    return found


def versions(db: Session, *, deal_id: int, loan: str, period: Period) -> list[LoanPeriod]:
    """Every stored version of a loan's period, in the order the panel ranks them."""
    query = (
        select(LoanPeriod)
        .join(LoanTape, LoanTape.accession == LoanPeriod.accession)
        .where(
            LoanPeriod.deal_id == deal_id,
            LoanPeriod.loan == loan,
            LoanPeriod.period == period.first_day,
        )
        .order_by(LoanTape.filing_date, LoanTape.amendment_depth)
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
    """One period of one deal as the panel showed it on a day."""

    deal: str
    as_of: dt.date
    period: Period
    accession: str
    rows: list[LoanPeriod]
    superseded: list[Superseded]


def tape_as_of(db: Session, deal: str, day: dt.date, period: Period | None = None) -> DealTape:
    """One period of a deal as the panel showed it on `day`; by default the latest period."""
    deal_row = deal_named(db, deal)
    tapes = current_tapes(db, day, deal_id=deal_row.id, period=period)
    if not tapes:
        which = f"{period.label} " if period else ""
        raise NothingPublished(f"no {which}loan tape of {deal} had been filed by {day}")
    tape = tapes[-1]  # ordered by period: the latest
    month = Period.of(tape.period)
    return DealTape(
        deal=deal,
        as_of=day,
        period=month,
        accession=tape.accession,
        rows=as_of(db, day, deal_id=deal_row.id, period=month),
        superseded=superseded(db, day, deal_id=deal_row.id, period=month),
    )


def tape_loan(row: LoanPeriod) -> TapeLoan:
    return TapeLoan(
        loan=row.loan,
        ending_balance=row.ending_balance,
        property_name=row.property_name,
        property_type=row.property_type,
        defeased_status=row.defeased_status,
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
