"""A deal's pool as the panel showed it on a day: its delinquency buckets and its WAC.

Point in time: the loans are those of the month's current tape on `as_of` (default today), as
`cmbs.panel.service` reads them, each with the ending balance, payment status and interest rate
that tape reported. Nothing is written; reading needs the right to read the panel.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.access.roles import Action
from cmbs.access.service import authorize
from cmbs.domain import Period
from cmbs.invariants.partition import Partition, PoolLoan, partition
from cmbs.invariants.wac import RatedLoan, Wac, wac
from cmbs.panel import service as panel
from cmbs.tape.tables import LoanPeriod


@dataclass(frozen=True)
class Pool:
    deal: str
    period: Period
    as_of: dt.date
    accession: str
    partition: Partition
    wac: Wac


def pool_loans(rows: Sequence[LoanPeriod]) -> list[PoolLoan]:
    return [PoolLoan(row.loan, row.payment_status, row.ending_balance) for row in rows]


def rated_loans(rows: Sequence[LoanPeriod]) -> list[RatedLoan]:
    return [RatedLoan(row.loan, row.ending_balance, row.interest_rate) for row in rows]


def measure(db: Session, *, deal: str, period: Period | None, as_of: dt.date) -> Pool:
    """The pool of the deal's month (the latest by default) as known on `as_of`. Refuses an
    unknown deal, or a day before any tape of the month was filed. Authorizes nobody."""
    tape = panel.tape_as_of(db, deal, as_of, period)
    return Pool(
        deal=tape.deal,
        period=tape.period,
        as_of=as_of,
        accession=tape.accession,
        partition=partition(pool_loans(tape.rows)),
        wac=wac(rated_loans(tape.rows)),
    )


def pool(
    db: Session,
    *,
    reader: str,
    deal: str,
    period: Period | None = None,
    as_of: dt.date | None = None,
) -> Pool:
    """The pool for a reader with the right to read the panel."""
    authorize(db, reader, Action.READ_PANEL)
    return measure(db, deal=deal, period=period, as_of=as_of or clock.today())
