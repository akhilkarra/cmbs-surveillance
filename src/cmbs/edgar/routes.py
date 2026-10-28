"""Filing discovery over HTTP: look a deal up on EDGAR and register what exists."""

import datetime as dt
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from cmbs import clock
from cmbs.api.deps import DB, User
from cmbs.coverage.service import classify_deal
from cmbs.edgar.discovery import Discovery
from cmbs.edgar.replay import default_source
from cmbs.edgar.service import deal_named, discover_deal

router = APIRouter(prefix="/discovery", tags=["discovery"])


class DiscoveryRequest(BaseModel):
    deal: str
    on: dt.date | None = None


def as_json(found: Discovery) -> dict[str, Any]:
    return {
        "deal": found.deal,
        "on": found.on.isoformat(),
        "cik": found.match.cik,
        "edgar_name": found.match.edgar_name,
        "note": found.match.note,
        "filings": [
            {
                "accession": f.accession,
                "cik": f.cik,
                "form": f.form,
                "filing_date": f.filing_date.isoformat(),
                "report_date": f.report_date.isoformat() if f.report_date else None,
                "period": f.period.label if f.period else None,
            }
            for f in found.filings
        ],
    }


@router.post("")
def run_discovery(request: DiscoveryRequest, user: User, db: DB) -> dict[str, Any]:
    """Look the deal up as of `on` (default today), register its filings, and class it.

    The lookup opens its own transactions; this request's session is used only afterwards.
    """
    on = request.on or clock.today()
    found = discover_deal(default_source(), request.deal, on, user=user)
    deal = deal_named(db, found.deal)
    if deal is None:  # pragma: no cover - discover_deal registers the deal
        raise RuntimeError(f"{found.deal} was not registered")
    classified = classify_deal(db, deal, on)
    return {**as_json(found), "coverage": classified.coverage.value, "detail": classified.detail}
