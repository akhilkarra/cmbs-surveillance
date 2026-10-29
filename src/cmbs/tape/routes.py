"""Load a loan tape over HTTP: POST the EX-102 XML as the request body."""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Body
from pydantic import BaseModel

from cmbs.access.roles import Action
from cmbs.access.service import authorize
from cmbs.api.deps import DB, User
from cmbs.tape.service import ORIGINAL, LoadSummary, load_tape

router = APIRouter(prefix="/deals", tags=["tapes"])

XmlBody = Annotated[bytes, Body(media_type="application/xml", description="The EX-102 file.")]


class QuarantinedRow(BaseModel):
    loan: str
    reason: str


class TapeLoaded(BaseModel):
    deal: str
    accession: str
    period: str
    filing_date: dt.date
    file_sha256: str
    assets: int
    loans_loaded: int
    quarantined: list[QuarantinedRow]
    notes: list[str]
    already_loaded: bool
    summary: str

    @classmethod
    def of(cls, loaded: LoadSummary) -> "TapeLoaded":
        return cls(
            deal=loaded.deal,
            accession=loaded.accession,
            period=loaded.period.label,
            filing_date=loaded.filing_date,
            file_sha256=loaded.file_sha256,
            assets=loaded.assets,
            loans_loaded=loaded.loans_loaded,
            quarantined=[QuarantinedRow(loan=q.loan, reason=q.reason) for q in loaded.quarantined],
            notes=loaded.notes,
            already_loaded=loaded.already_loaded,
            summary=loaded.text(),
        )


@router.post("/{deal}/tapes")
def load(
    user: User,
    db: DB,
    deal: str,
    content: XmlBody,
    accession: str,
    filing_date: dt.date,
    file_name: str = "exh_102.xml",
    form: str = ORIGINAL,
    amends: str | None = None,
    cik: int | None = None,
) -> TapeLoaded:
    """Load one EX-102 file of an ABS-EE filing. Loading it again changes nothing."""
    actor = authorize(db, user, Action.RUN_REFRESH)
    loaded = load_tape(
        db,
        deal=deal,
        content=content,
        file_name=file_name,
        accession=accession,
        filing_date=filing_date,
        form=form,
        amends=amends,
        cik=cik,
        actor=actor,
    )
    return TapeLoaded.of(loaded)
