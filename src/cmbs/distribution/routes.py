"""Read what distribution reports said about a loan."""

import datetime as dt

from fastapi import APIRouter
from pydantic import BaseModel

from cmbs.access.roles import Action
from cmbs.access.service import authorize
from cmbs.api.deps import DB, User
from cmbs.distribution.service import comments_for_loan

router = APIRouter(prefix="/reports", tags=["distribution reports"])


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
