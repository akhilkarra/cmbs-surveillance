"""The pool's delinquency buckets and WAC over HTTP. Money is text with two decimals, the WAC
an exact fraction beside its percent, and anything not reported is null or listed."""

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from cmbs.api.deps import DB, User
from cmbs.domain import Period
from cmbs.invariants import report, service

router = APIRouter(prefix="/invariants", tags=["invariants"])


@router.get("/pool")
def pool(
    user: User,
    db: DB,
    deal: str,
    period: Annotated[
        str | None, Query(description="'September 2026'; the latest by default.")
    ] = None,
    as_of: Annotated[dt.date | None, Query(description="Read as known on this day.")] = None,
) -> dict[str, Any]:
    try:
        month = Period.parse(period) if period is not None else None
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    found = service.pool(db, reader=user, deal=deal, period=month, as_of=as_of)
    return report.pool_json(found)
