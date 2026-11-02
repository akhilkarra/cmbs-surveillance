"""The committee brief over HTTP. A GET only: briefing never changes anything."""

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, Query

from cmbs.api.deps import DB, User
from cmbs.brief.commands import brief_data
from cmbs.brief.service import brief

router = APIRouter(prefix="/brief", tags=["brief"])


@router.get("/deal")
def deal(
    user: User,
    db: DB,
    deal: str,
    as_of: Annotated[dt.date | None, Query(description="Known on this date.")] = None,
) -> dict[str, Any]:
    """The committee brief on the deal's loans in special servicing, every value cited."""
    return brief_data(brief(db, user, deal=deal, as_of=as_of))
