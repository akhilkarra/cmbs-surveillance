"""Checks over HTTP: run them, read the roll-forward and text versus tape, read the flags.

Money travels as text with two decimals; a missing amount is null, never zero.
"""

import datetime as dt
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from cmbs import clock
from cmbs.access.roles import Action
from cmbs.access.service import authorize
from cmbs.api.deps import DB, User
from cmbs.crosscheck import report, service
from cmbs.domain import Period

router = APIRouter(prefix="/checks", tags=["checks"])
Day = Annotated[dt.date | None, Query(description="Read as known on this day (default today).")]


def _period(text: str) -> Period:
    try:
        return Period.parse(text)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


class RunIn(BaseModel):
    deal: str
    period: str
    checks: Literal["all", "text-vs-tape", "rollforward"] = "all"


@router.post("/run")
def run(body: RunIn, user: User, db: DB) -> dict[str, Any]:
    """Run the checks on the month as known today; flags already raised are not raised again."""
    month = _period(body.period)
    summary = service.CHECKS[body.checks](db, user=user, deal=body.deal, period=month)
    return report.summary_json(summary)


@router.get("/rollforward")
def rollforward(
    user: User, db: DB, deal: str, period: str, as_of: Day = None
) -> list[dict[str, Any]]:
    authorize(db, user, Action.READ_PANEL)
    month = _period(period)
    results = service.rollforward(
        db, deal_id=service.deal_id(db, deal), period=month, as_of=as_of or clock.today()
    )
    return [report.rollforward_json(result) for result in results]


@router.get("/text-vs-tape")
def text_vs_tape(
    user: User, db: DB, deal: str, period: str, as_of: Day = None
) -> list[dict[str, Any]]:
    month = _period(period)
    results = service.text_vs_tape(
        db,
        reader=user,
        deal_id=service.deal_id(db, deal),
        period=month,
        as_of=as_of or clock.today(),
    )
    return [report.loan_findings_json(loan) for loan in results]


@router.get("/flags")
def flags(
    user: User, db: DB, deal: str, period: str | None = None, loan: str | None = None
) -> list[dict[str, Any]]:
    month = _period(period) if period is not None else None
    views = service.flags(db, reader=user, deal=deal, period=month, loan=loan)
    return [report.flag_json(view) for view in views]
