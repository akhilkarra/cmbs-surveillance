"""Look up and register deals.

Registration is idempotent and safe under concurrency: the row is inserted with
`ON CONFLICT DO NOTHING`, then locked and re-read.
"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from cmbs.deals.tables import Deal
from cmbs.errors import Refused


class ConflictingDeal(Refused):
    """A deal was registered again with a different CIK."""


def get_or_create(db: Session, name: str, *, cik: int | None = None) -> Deal:
    """The deal with this name, created if needed. A CIK may be learned later, never changed."""
    db.execute(
        insert(Deal).values(name=name, cik=cik).on_conflict_do_nothing(index_elements=[Deal.name])
    )
    deal = db.scalars(
        select(Deal)
        .where(Deal.name == name)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    if cik is not None and deal.cik != cik:
        if deal.cik is not None:
            raise ConflictingDeal(f"{name} already has CIK {deal.cik}, not {cik}")
        deal.cik = cik
        db.flush()
    return deal


def by_name(db: Session, name: str) -> Deal | None:
    return db.scalars(select(Deal).where(Deal.name == name)).first()
