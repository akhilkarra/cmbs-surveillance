"""The coverage report over HTTP. Money travels as strings, never floats."""

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from cmbs import clock
from cmbs.api.deps import DB, User
from cmbs.coverage.service import CoverageReport, configure_vendor_source, render, report

router = APIRouter(prefix="/coverage", tags=["coverage"])


class VendorSourceIn(BaseModel):
    deal: str
    source: str


def as_json(result: CoverageReport) -> dict[str, Any]:
    def day(value: dt.date | None) -> str | None:
        return value.isoformat() if value is not None else None

    return {
        "on": result.on.isoformat(),
        "positions_as_of": day(result.positions_as_of),
        "total": str(result.total),
        "deals": [
            {
                "deal": row.deal,
                "classes": list(row.classes),
                "exposure": str(row.exposure),
                "coverage": row.coverage.value,
                "detail": row.detail,
                "overdue_expected_by": day(row.overdue_expected_by),
                "vendor_sources": list(row.vendor_sources),
                "checked_as_of": day(row.checked_as_of),
            }
            for row in result.deals
        ],
        "by_class": [
            {"coverage": c.coverage.value, "exposure": str(c.exposure), "share": c.share}
            for c in result.by_class
        ],
        "text": render(result),
    }


@router.get("")
def coverage_report(
    user: User, db: DB, on: Annotated[dt.date | None, Query()] = None
) -> dict[str, Any]:
    """The coverage of every deal in the book as of `on` (default today)."""
    return as_json(report(db, user=user, on=on or clock.today()))


@router.post("/vendor-sources")
def add_vendor_source(body: VendorSourceIn, user: User, db: DB) -> dict[str, Any]:
    stored = configure_vendor_source(db, user=user, deal=body.deal, source=body.source)
    return {"deal": " ".join(body.deal.split()), "source": stored.name}
