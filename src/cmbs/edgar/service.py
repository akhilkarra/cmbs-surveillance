"""Run filing discovery for a deal and register what it found.

The deal goes into the deal registry with its issuing-entity CIK (when it has one), every
filing into the filings registry, and the discovery itself into a log, so the coverage report
can tell a deal that was looked up and found nothing from one never looked up.
"""

import datetime as dt
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.access.roles import Action
from cmbs.access.service import authorize
from cmbs.audit.service import record
from cmbs.deals.service import get_or_create
from cmbs.deals.tables import Deal
from cmbs.edgar.discovery import Discovery, discover
from cmbs.edgar.replay import Source
from cmbs.edgar.tables import FilingDiscovery
from cmbs.filings.service import register_filing
from cmbs.filings.tables import Filing


def register(db: Session, found: Discovery, *, user: str) -> Deal:
    """Store a discovery: the deal, each filing, and the log entry. Idempotent per filing."""
    deal = get_or_create(db, found.deal, cik=found.match.cik)
    if found.match.edgar_name is not None:
        # get_or_create holds the deal's row lock, so this update cannot race another.
        deal.edgar_name = found.match.edgar_name
    for filing in found.filings:
        register_filing(
            db,
            accession=filing.accession,
            cik=filing.cik,
            form=filing.form,
            filing_date=filing.filing_date,
            deal_id=deal.id,
            report_date=filing.report_date,
            period=filing.period.first_day if filing.period is not None else None,
            primary_document=filing.primary_document,
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


def discover_deal(db: Session, source: Source, name: str, on: dt.date, *, user: str) -> Discovery:
    """Look a deal up on EDGAR as of `on`, register the result and audit it.

    Any user who may read the panel may look a deal up: discovery stores public facts only.
    The lookup runs before anything is written, so a failed lookup changes nothing.
    """
    actor = authorize(db, user, Action.READ_PANEL)
    found = discover(source, name, on)
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
    """The deal's registered filings filed on or before `on`, newest first."""
    return db.scalars(
        select(Filing)
        .where(Filing.deal_id == deal_id, Filing.filing_date <= on)
        .order_by(Filing.filing_date.desc(), Filing.accession.desc())
    ).all()
