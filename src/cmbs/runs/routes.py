"""The run history and value tracing over HTTP."""

import datetime as dt
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from cmbs.api.deps import DB, User
from cmbs.domain import Period
from cmbs.errors import Refused
from cmbs.runs import service
from cmbs.runs.service import RunView

router = APIRouter(prefix="/runs", tags=["runs"])


class BadMonth(Refused):
    pass


class RunOut(BaseModel):
    number: int
    kind: str
    started_by: str
    started_at: dt.datetime
    ended_at: dt.datetime | None
    status: str
    headline: str
    stopped: dict[str, Any] | None
    summary: dict[str, Any]

    @classmethod
    def of(cls, view: RunView) -> "RunOut":
        return cls(
            number=view.number,
            kind=view.kind,
            started_by=view.started_by,
            started_at=view.started_at,
            ended_at=view.ended_at,
            status=view.status,
            headline=view.headline(),
            stopped=view.stopped,
            summary=view.summary,
        )


class TraceOut(BaseModel):
    deal: str
    loan: str
    period: str
    balance: str | None
    accession: str
    filing_date: dt.date
    run: int | None
    loaded_by: str | None
    loaded_at: dt.datetime | None
    text: str


@router.get("")
def run_history(
    user: User, db: DB, start: dt.date | None = None, end: dt.date | None = None
) -> list[RunOut]:
    """Runs started from `start` to `end` (UTC calendar days, inclusive), newest first."""
    return [RunOut.of(view) for view in service.history(db, user, start=start, end=end)]


@router.get("/trace")
def trace(
    user: User, db: DB, deal: str, loan: str, period: str, as_of: dt.date | None = None
) -> TraceOut:
    """Where a loan's balance for a month came from: its filing and the run that loaded it."""
    try:
        month = Period.parse(period)
    except ValueError as error:
        raise BadMonth(str(error)) from error
    found = service.trace(db, user, deal=deal, loan=loan, period=month, as_of=as_of)
    return TraceOut(
        deal=found.deal,
        loan=found.loan,
        period=found.period.label,
        balance=None if found.balance is None else str(found.balance),
        accession=found.accession,
        filing_date=found.filing_date,
        run=found.run,
        loaded_by=found.loaded_by,
        loaded_at=found.loaded_at,
        text=found.text(),
    )


@router.get("/{number}")
def read_run(user: User, db: DB, number: int) -> RunOut:
    return RunOut.of(service.read_run(db, user, number))
