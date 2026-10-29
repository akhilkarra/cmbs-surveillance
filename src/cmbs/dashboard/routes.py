"""The dashboard's pages over HTTP, read-only.

Months travel as '2026-09' and come back with their label; money and ratios as strings, so no
digit is lost. The web app formats these values and computes nothing.
"""

import datetime as dt

from fastapi import APIRouter

from cmbs.api.deps import DB, User
from cmbs.dashboard import service
from cmbs.dashboard.service import BookPage, LoanPage

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/book")
def book(user: User, db: DB, month: str | None = None) -> BookPage:
    """The month's open alerts on the book (the latest month built by default), most severe
    first, then by exposure and the loan's balance."""
    period = service.parse_month(month) if month is not None else None
    return service.book_page(db, reader=user, period=period)


@router.get("/loan")
def loan(
    user: User,
    db: DB,
    deal: str,
    loan: str,
    month: str | None = None,
    as_of: dt.date | None = None,
) -> LoanPage:
    """One loan as the panel shows it on `as_of` (today), for a month (the deal's latest)."""
    period = service.parse_month(month) if month is not None else None
    return service.loan_page(db, reader=user, deal=deal, loan=loan, period=period, as_of=as_of)
