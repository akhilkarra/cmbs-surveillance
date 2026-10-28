"""Look up and register deals.

One trust is one deal, whatever it is called. A deal has its own name and may have aliases
(`silver.deal_alias`), such as the issuing entity's EDGAR name; looking up or registering any
of them reaches the same deal. Names are compared with runs of whitespace collapsed.

Registration is idempotent and safe under concurrency: the row is inserted with
`ON CONFLICT DO NOTHING`, then locked and re-read.
"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from cmbs.deals.tables import Deal, DealAlias
from cmbs.errors import Refused


class ConflictingDeal(Refused):
    """A deal was registered again with a different CIK."""


def normalized(name: str) -> str:
    """Runs of whitespace collapsed to one space, ends trimmed."""
    return " ".join(name.split())


def _locked(db: Session, condition) -> Deal:
    return db.scalars(
        select(Deal).where(condition).with_for_update().execution_options(populate_existing=True)
    ).one()


def get_or_create(db: Session, name: str, *, cik: int | None = None) -> Deal:
    """The deal a name refers to, by its own name or an alias, created if new, row locked.

    A CIK may be learned later, never changed.
    """
    name = normalized(name)
    alias = db.get(DealAlias, name)
    if alias is not None:
        deal = _locked(db, Deal.id == alias.deal_id)
    else:
        db.execute(
            insert(Deal)
            .values(name=name, cik=cik)
            .on_conflict_do_nothing(index_elements=[Deal.name])
        )
        deal = _locked(db, Deal.name == name)
    if cik is not None and deal.cik != cik:
        if deal.cik is not None:
            raise ConflictingDeal(f"{deal.name} already has CIK {deal.cik}, not {cik}")
        deal.cik = cik
        db.flush()
    return deal


def by_name(db: Session, name: str) -> Deal | None:
    """The deal a name refers to, by its own name or an alias; None if unknown."""
    name = normalized(name)
    alias = db.get(DealAlias, name)
    if alias is not None:
        return db.get(Deal, alias.deal_id)
    return db.scalars(select(Deal).where(Deal.name == name)).first()
