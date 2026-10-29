"""Exports and rebuild checks over HTTP. Both are POSTs: each is recorded in the audit log."""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel

from cmbs.api.deps import DB, User
from cmbs.export.service import export_panel, rebuild

router = APIRouter(prefix="/export", tags=["export"])

MEDIA_TYPES = {"csv": "text/csv", "parquet": "application/vnd.apache.parquet"}


@router.post("/panel")
def export(
    user: User,
    db: DB,
    as_of: Annotated[dt.date, Query(description="Known on this date.")],
    fmt: Annotated[str, Query(alias="format", description="csv or parquet.")] = "csv",
    deal: Annotated[
        str | None, Query(description="One deal; the whole panel when left out.")
    ] = None,
) -> Response:
    """The panel as known on `as_of`, as a file named for what produced it."""
    made = export_panel(db, user, as_of=as_of, fmt=fmt, deal=deal)
    return Response(
        content=made.content,
        media_type=MEDIA_TYPES[fmt],
        headers={
            "Content-Disposition": f'attachment; filename="{made.file_name}"',
            "X-Export-Number": str(made.number),
            "X-Export-SHA256": made.sha256,
            "X-Export-Rows": str(made.rows),
        },
    )


class RebuildOut(BaseModel):
    deals: list[str]
    tapes: int
    rows: int
    stored_checksum: str
    rebuilt_checksum: str
    identical: bool
    differences: list[str]


@router.post("/rebuild")
def rebuild_panel(user: User, db: DB, deal: str | None = None) -> RebuildOut:
    """Rebuild the panel from the stored raw filings and compare; changes nothing."""
    found = rebuild(db, user, deal=deal)
    return RebuildOut(
        deals=found.deals,
        tapes=found.tapes,
        rows=found.rows,
        stored_checksum=found.stored_checksum,
        rebuilt_checksum=found.rebuilt_checksum,
        identical=found.identical,
        differences=[difference.text() for difference in found.differences],
    )
