"""The book over HTTP: load a positions file, read exposure. Money travels as strings."""

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from cmbs import clock
from cmbs.api.deps import DB, User
from cmbs.book.service import exposure_for, load_positions, position_line

router = APIRouter(prefix="/book", tags=["book"])


class PositionIn(BaseModel):
    deal: str
    tranche: str = Field(alias="class")
    face_amount: str = Field(description="Money as text, e.g. '20,000,000.00'.")


class PositionsFileIn(BaseModel):
    as_of: dt.date
    positions: list[PositionIn]
    source_name: str | None = None


@router.post("/positions")
def load(body: PositionsFileIn, user: User, db: DB) -> dict[str, Any]:
    lines = [
        position_line(p.deal, p.tranche, p.face_amount, where=f"position {number}: ")
        for number, p in enumerate(body.positions, start=1)
    ]
    loaded = load_positions(
        db, user=user, as_of=body.as_of, lines=lines, source_name=body.source_name
    )
    return {
        "id": loaded.id,
        "as_of": loaded.as_of.isoformat(),
        "positions": len(lines),
        "deals": len({line.deal for line in lines}),
    }


@router.get("/exposure")
def exposure(user: User, db: DB, on: Annotated[dt.date | None, Query()] = None) -> dict[str, Any]:
    day = on or clock.today()
    held = exposure_for(db, user=user, on=day)
    return {"on": day.isoformat(), "exposure": {deal: str(amount) for deal, amount in held.items()}}
