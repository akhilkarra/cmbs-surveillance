"""Read what distribution reports said about a loan, and reconcile a month with its tape."""

import datetime as dt
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from cmbs.access.roles import Action
from cmbs.access.service import authorize
from cmbs.api.deps import DB, User
from cmbs.distribution.reconcile import reconcile_month
from cmbs.distribution.service import comments_for_loan
from cmbs.domain import Period
from cmbs.errors import Refused

router = APIRouter(prefix="/reports", tags=["distribution reports"])


class BadPeriod(Refused):
    pass


class LoanComment(BaseModel):
    loan: str
    period: str
    accession: str
    filing_date: dt.date
    source_table: str
    comment: str


@router.get("/comments")
def loan_comments(
    user: User, db: DB, deal: str, loan: str, as_of: dt.date | None = None
) -> list[LoanComment]:
    """A loan's special-servicing comments, newest period first, as public on `as_of`."""
    authorize(db, user, Action.READ_PANEL)
    return [
        LoanComment(
            loan=item.loan,
            period=item.period.label,
            accession=item.accession,
            filing_date=item.filing_date,
            source_table=item.source_table,
            comment=item.comment,
        )
        for item in comments_for_loan(db, deal=deal, loan=loan, as_of=as_of)
    ]


class Disagreement(BaseModel):
    loan: str
    message: str
    sides: dict[str, Any]


class Reconciled(BaseModel):
    deal: str
    period: str
    report: str
    tape: str
    passed: bool
    disagreements: list[Disagreement]
    flags_raised: int
    text: str


@router.post("/reconcile")
def reconcile(
    user: User, db: DB, deal: str, period: str, as_of: dt.date | None = None
) -> Reconciled:
    """Compare a month's delinquency detail with the tape; flag each disagreement once."""
    try:
        month = Period.parse(period)
    except ValueError as error:
        raise BadPeriod(str(error)) from error
    result = reconcile_month(db, user=user, deal=deal, period=month, as_of=as_of)
    return Reconciled(
        deal=result.deal,
        period=result.period.label,
        report=result.report_accession,
        tape=result.tape_accession,
        passed=result.passed,
        disagreements=[
            Disagreement(loan=d.loan, message=d.message, sides=d.sides())
            for d in result.disagreements
        ],
        flags_raised=result.flags_raised,
        text=result.text(),
    )
