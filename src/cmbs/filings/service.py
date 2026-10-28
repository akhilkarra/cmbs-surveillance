"""Register filings and store their files."""

import datetime as dt
import hashlib

from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.filings.tables import Filing, FilingFile


class ConflictingFiling(ValueError):
    """The same accession number was registered with different facts."""


def register_filing(
    db: Session,
    *,
    accession: str,
    cik: int,
    form: str,
    filing_date: dt.date,
    deal_id: int | None = None,
    report_date: dt.date | None = None,
    period: dt.date | None = None,
    amends: str | None = None,
    primary_document: str | None = None,
) -> Filing:
    """Idempotent: registering the same filing again returns the stored row."""
    existing = db.get(Filing, accession)
    if existing is not None:
        if (existing.cik, existing.form, existing.filing_date) != (cik, form, filing_date):
            raise ConflictingFiling(f"{accession} is already registered with different facts")
        if deal_id is not None and existing.deal_id is None:
            existing.deal_id = deal_id
            db.flush()
        return existing
    filing = Filing(
        accession=accession,
        cik=cik,
        form=form,
        filing_date=filing_date,
        deal_id=deal_id,
        report_date=report_date,
        period=period,
        amends=amends,
        primary_document=primary_document,
    )
    db.add(filing)
    db.flush()
    return filing


def sha256_of(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def store_file(db: Session, *, accession: str, name: str, content: bytes) -> FilingFile:
    """Idempotent for identical bytes; a different file under the same name is an error."""
    digest = sha256_of(content)
    existing = db.get(FilingFile, (accession, name))
    if existing is not None:
        if existing.sha256 != digest:
            raise ConflictingFiling(f"{accession}/{name} is already stored with other content")
        return existing
    stored = FilingFile(
        accession=accession,
        name=name,
        sha256=digest,
        size=len(content),
        content=content,
        fetched_at=clock.now(),
    )
    db.add(stored)
    db.flush()
    return stored
