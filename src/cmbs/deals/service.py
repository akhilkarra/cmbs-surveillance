"""Look up and register deals.

One trust is one deal, whatever it is called. A deal has its own name and may have aliases
(`silver.deal_alias`), such as the issuing entity's EDGAR name; looking up or registering any
of them reaches the same deal. Names are compared with runs of whitespace collapsed. A CIK
belongs to one deal: registering a new name with a CIK another deal holds makes the name an
alias of that deal, and a name already registered apart from it is refused, never merged.

Registration is idempotent and safe under concurrency: the row is inserted with
`ON CONFLICT DO NOTHING`, then locked and re-read, and registrations naming one CIK take turns
on an advisory lock.
"""

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from cmbs.deals.tables import Deal, DealAlias
from cmbs.errors import Refused

# The advisory-lock space for registrations naming a CIK; the CIK is the key within it.
CIK_LOCK_SPACE = 0x0C1C


class ConflictingDeal(Refused):
    """A deal was registered again with a different CIK."""


class SeparateDeals(Refused):
    """A name registered as its own deal was given the CIK of another deal: one trust, two deals."""


def normalized(name: str) -> str:
    """Runs of whitespace collapsed to one space, ends trimmed."""
    return " ".join(name.split())


def _locked(db: Session, condition) -> Deal:
    return db.scalars(
        select(Deal).where(condition).with_for_update().execution_options(populate_existing=True)
    ).one()


def by_name(db: Session, name: str) -> Deal | None:
    """The deal a name refers to, by its own name or an alias; None if unknown."""
    name = normalized(name)
    alias = db.get(DealAlias, name)
    if alias is not None:
        return db.get(Deal, alias.deal_id)
    return db.scalars(select(Deal).where(Deal.name == name)).first()


def by_cik(db: Session, cik: int) -> Deal | None:
    """The deal holding a CIK; None if none does."""
    return db.scalars(select(Deal).where(Deal.cik == cik).order_by(Deal.id).limit(1)).first()


def resolve(db: Session, name: str, *, cik: int | None = None) -> Deal | None:
    """The existing deal `get_or_create` would return, or None if it would create one.

    Raises the refusals `get_or_create` would raise, and writes nothing, so a caller can check
    before it writes anything else.
    """
    named = by_name(db, name)
    if cik is None:
        return named
    if named is not None and named.cik is not None and named.cik != cik:
        raise ConflictingDeal(f"{named.name} already has CIK {named.cik}, not {cik}")
    owner = by_cik(db, cik)
    if owner is None:
        return named
    if named is not None and named.id != owner.id:
        raise SeparateDeals(
            f'"{normalized(name)}" is registered apart from "{owner.name}", which already has '
            f"CIK {cik}; they are the same trust"
        )
    return owner


def get_or_create(db: Session, name: str, *, cik: int | None = None) -> Deal:
    """The deal a name refers to, created if new, with its row locked.

    With a CIK, the result is the one deal holding that CIK: a new name of it becomes an alias.
    A CIK may be learned later, never changed.
    """
    name = normalized(name)
    if cik is not None:
        db.execute(
            text("SELECT pg_advisory_xact_lock(CAST(:space AS integer), CAST(:cik AS integer))"),
            {"space": CIK_LOCK_SPACE, "cik": cik},
        )
    existing = resolve(db, name, cik=cik)  # under the CIK lock, so no other holder can appear
    if existing is not None:
        deal = _locked(db, Deal.id == existing.id)
        if by_name(db, name) is None:
            _add_alias(db, name, deal)
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


def _add_alias(db: Session, name: str, deal: Deal) -> None:
    db.execute(
        insert(DealAlias)
        .values(name=name, deal_id=deal.id)
        .on_conflict_do_nothing(index_elements=[DealAlias.name])
    )
    stored = db.get_one(DealAlias, name, populate_existing=True)
    if stored.deal_id != deal.id:
        raise SeparateDeals(f'"{name}" already names another deal, not "{deal.name}"')
