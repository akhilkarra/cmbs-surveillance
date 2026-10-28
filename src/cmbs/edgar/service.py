"""Run filing discovery for a deal and register what it found.

The deal goes into the deal registry with its issuing-entity CIK (when it has one), every
filing into the filings registry, and the discovery itself into a log, so the coverage report
can tell a deal that was looked up and found nothing from one never looked up.

One trust is one deal, whatever it is called: a lookup that resolves to a CIK already held by
a deal registered under another name records the name as an alias of that deal. A depositor's
filing (an ABS-15G) can cover several private deals, so it is linked to each through
`silver.deal_filing` and never takes a `deal_id` of its own.

EDGAR is fetched with no database transaction open: the user is authorized in one short
transaction, the fetch runs, and the result is registered in a second transaction that
authorizes again, since roles may change while the fetch runs.
"""

import datetime as dt
from collections.abc import Sequence

from sqlalchemy import or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.access.roles import Action
from cmbs.access.service import authorize
from cmbs.audit.service import record
from cmbs.db import engine
from cmbs.deals.service import ConflictingDeal, by_name, get_or_create
from cmbs.deals.tables import Deal
from cmbs.edgar.discovery import Discovery, discover
from cmbs.edgar.replay import Source
from cmbs.edgar.tables import DealAlias, DealFiling, FilingDiscovery
from cmbs.errors import Refused
from cmbs.filings.service import register_filing
from cmbs.filings.tables import Filing

# Names one Postgres advisory-lock space per CIK, so two names of one trust registered at once
# cannot both create a deal for it.
CIK_LOCK_SPACE = 0x0C1C


class DiscoveryInFuture(Refused):
    """Filings cannot be known as of a date that has not come yet."""


class SeparateDeals(Refused):
    """A name registered as its own deal turns out to be a trust already registered."""


def _normalized(name: str) -> str:
    return " ".join(name.split())


def deal_named(db: Session, name: str) -> Deal | None:
    """The deal a name refers to, by its own name or an alias; None if unknown."""
    name = _normalized(name)
    alias = db.get(DealAlias, name)
    if alias is not None:
        return db.get_one(Deal, alias.deal_id)
    return by_name(db, name)


def _lock(db: Session, deal_id: int) -> Deal:
    return db.scalars(
        select(Deal)
        .where(Deal.id == deal_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()


def _add_alias(db: Session, name: str, deal: Deal) -> None:
    db.execute(
        insert(DealAlias)
        .values(name=name, deal_id=deal.id)
        .on_conflict_do_nothing(index_elements=[DealAlias.name])
    )
    stored = db.get_one(DealAlias, name, populate_existing=True)
    if stored.deal_id != deal.id:
        raise SeparateDeals(f'"{name}" already names another deal, not "{deal.name}"')


def deal_for(db: Session, name: str, *, cik: int | None = None) -> Deal:
    """The deal a name refers to, created if new, with its row locked.

    With a CIK, the result is the one deal holding that CIK: a deal registered under another
    name gains this name as an alias. A name that is already a separate deal of its own is
    refused rather than merged, since other records may point at either.
    """
    name = _normalized(name)
    alias = db.get(DealAlias, name)
    if alias is not None:
        deal = _lock(db, alias.deal_id)
        if cik is not None and deal.cik != cik:
            if deal.cik is not None:
                raise ConflictingDeal(f"{deal.name} already has CIK {deal.cik}, not {cik}")
            deal.cik = cik
            db.flush()
        return deal
    if cik is None:
        return get_or_create(db, name)
    db.execute(
        text("SELECT pg_advisory_xact_lock(CAST(:space AS integer), CAST(:cik AS integer))"),
        {"space": CIK_LOCK_SPACE, "cik": cik},
    )
    owner = db.scalars(select(Deal).where(Deal.cik == cik).order_by(Deal.id).limit(1)).first()
    if owner is None:
        return get_or_create(db, name, cik=cik)
    named = by_name(db, name)
    if named is not None and named.id != owner.id:
        raise SeparateDeals(
            f'"{name}" is registered apart from "{owner.name}", which already has CIK {cik}; '
            "they are the same trust"
        )
    if named is None:
        _add_alias(db, name, owner)
    return _lock(db, owner.id)


def register(db: Session, found: Discovery, *, user: str) -> Deal:
    """Store a discovery: the deal, each filing, and the log entry. Idempotent per filing."""
    deal = deal_for(db, found.deal, cik=found.match.cik)
    if found.match.edgar_name is not None:
        # deal_for holds the deal's row lock, so this update cannot race another.
        deal.edgar_name = found.match.edgar_name
    own = found.match.cik is not None
    for filing in found.filings:
        register_filing(
            db,
            accession=filing.accession,
            cik=filing.cik,
            form=filing.form,
            filing_date=filing.filing_date,
            deal_id=deal.id if own else None,
            report_date=filing.report_date,
            period=filing.period.first_day if filing.period is not None else None,
            primary_document=filing.primary_document,
        )
        if not own:
            db.execute(
                insert(DealFiling)
                .values(deal_id=deal.id, accession=filing.accession)
                .on_conflict_do_nothing()
            )
    db.add(
        FilingDiscovery(
            deal_id=deal.id,
            as_of=found.on,
            checked_at=clock.now(),
            checked_by=user,
            cik=found.match.cik,
            edgar_name=found.match.edgar_name,
            note=found.match.note,
            filings=len(found.filings),
        )
    )
    db.flush()
    return deal


def discover_deal(source: Source, name: str, on: dt.date, *, user: str) -> Discovery:
    """Look a deal up on EDGAR as of `on`, register the result and audit it.

    Any user who may read the panel may look a deal up: discovery stores public facts only,
    and every lookup is audited. `on` may not be after today. Each step runs in its own
    transaction (see the module docstring), so a failed fetch changes nothing.
    """
    with engine.session() as db:
        authorize(db, user, Action.READ_PANEL)
    today = clock.today()
    if on > today:
        raise DiscoveryInFuture(
            f"cannot look up filings as of {on.isoformat()}, which is after today "
            f"({today.isoformat()})"
        )
    found = discover(source, name, on)
    with engine.session() as db:
        actor = authorize(db, user, Action.READ_PANEL)
        deal = register(db, found, user=user)
        record(
            db,
            user=actor.user,
            role=actor.role.value,
            action="discover filings",
            subject="deal",
            subject_key=deal.name,
            after=(
                f"CIK {found.match.cik}, {len(found.filings)} filings"
                if found.match.cik is not None
                else f"no issuing entity, {len(found.filings)} filings"
            ),
            detail={
                "on": on.isoformat(),
                "name": found.deal,
                "cik": found.match.cik,
                "edgar_name": found.match.edgar_name,
                "note": found.match.note,
                "filings": len(found.filings),
            },
        )
    return found


def latest_discovery(
    db: Session, deal_id: int, on: dt.date | None = None
) -> FilingDiscovery | None:
    """The deal's most recent discovery, by the date it ran as of; only up to `on` if given."""
    query = select(FilingDiscovery).where(FilingDiscovery.deal_id == deal_id)
    if on is not None:
        query = query.where(FilingDiscovery.as_of <= on)
    return db.scalars(
        query.order_by(FilingDiscovery.as_of.desc(), FilingDiscovery.id.desc()).limit(1)
    ).one_or_none()


def filings_of(db: Session, deal_id: int, on: dt.date) -> Sequence[Filing]:
    """The deal's filings, its own and the depositor's that name it, filed by `on`, newest first."""
    linked = select(DealFiling.accession).where(DealFiling.deal_id == deal_id)
    return db.scalars(
        select(Filing)
        .where(
            or_(Filing.deal_id == deal_id, Filing.accession.in_(linked)),
            Filing.filing_date <= on,
        )
        .order_by(Filing.filing_date.desc(), Filing.accession.desc())
    ).all()
