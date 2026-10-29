"""Ask the panel over HTTP. GETs only: asking never changes anything."""

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, Query

from cmbs.api.deps import DB, User
from cmbs.ask.commands import answer_data, as_data
from cmbs.ask.service import ask, loan_facts

router = APIRouter(prefix="/ask", tags=["ask"])


@router.get("/facts")
def facts(
    user: User,
    db: DB,
    deal: str,
    loan: str,
    as_of: Annotated[dt.date | None, Query(description="Known on this date.")] = None,
) -> dict[str, Any]:
    """Every fact the panel holds for a loan, each with its filing and sentence."""
    return as_data(loan_facts(db, user, deal=deal, loan=loan, as_of=as_of))


@router.get("")
def question(
    user: User,
    db: DB,
    question: str,
    deal: str,
    loan: Annotated[str | None, Query(description="The loan, if none is named.")] = None,
    as_of: Annotated[dt.date | None, Query(description="Known on this date.")] = None,
) -> dict[str, Any]:
    """An answer drawn only from the panel's facts, each cited; a change is refused."""
    return answer_data(ask(db, user, question, deal=deal, loan=loan, as_of=as_of))
