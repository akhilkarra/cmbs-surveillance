"""Start refresh runs over HTTP. A run is synchronous: the response is the finished run.

EDGAR is called live unless CMBS_EDGAR_REPLAY and CMBS_ARCHIVE_REPLAY name recordings.
"""

from fastapi import APIRouter, Query
from fastapi.concurrency import run_in_threadpool

from cmbs.api.deps import User
from cmbs.domain import Period
from cmbs.errors import Refused
from cmbs.refresh import service
from cmbs.runs.routes import RunOut

router = APIRouter(prefix="/refresh", tags=["refresh"])


class BadMonth(Refused):
    pass


def _month(text: str) -> Period:
    try:
        return Period.parse(text)
    except ValueError as error:
        raise BadMonth(str(error)) from error


# A run opens its own transactions, one per step, so these routes take no request session.
@router.post("/book")
async def refresh_book(user: User) -> RunOut:
    """Refresh every deal in today's book."""
    report = await run_in_threadpool(service.refresh_book, user)
    return RunOut.of(report.run)


@router.post("/deals/{deal}")
async def refresh_deal(user: User, deal: str) -> RunOut:
    """Bring one deal up to date."""
    report = await run_in_threadpool(service.refresh_deal, user, deal)
    return RunOut.of(report.run)


@router.post("/deals/{deal}/backfill")
async def backfill(
    user: User,
    deal: str,
    start: str = Query(description="First month, like 'August 2025'."),
    end: str = Query(description="Last month, like 'September 2026'."),
) -> RunOut:
    """Load every month of a deal from `start` to `end` that is not loaded yet, oldest first."""
    first, last = _month(start), _month(end)
    report = await run_in_threadpool(service.backfill, user, deal, first, last)
    return RunOut.of(report.run)
