"""Look up and register deals."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from cmbs.deals.tables import Deal


def get_or_create(db: Session, name: str, *, cik: int | None = None) -> Deal:
    deal = db.scalars(select(Deal).where(Deal.name == name)).first()
    if deal is None:
        deal = Deal(name=name, cik=cik)
        db.add(deal)
        db.flush()
    elif cik is not None and deal.cik is None:
        deal.cik = cik
        db.flush()
    return deal


def by_name(db: Session, name: str) -> Deal | None:
    return db.scalars(select(Deal).where(Deal.name == name)).first()
