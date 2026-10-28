"""Register filings and store their files.

Both operations are idempotent and safe under concurrency: the row is inserted with
`ON CONFLICT DO NOTHING`, then locked and re-read, so two sessions registering the same filing
at once both end up with the one stored row instead of a duplicate-key error. Every check runs
before anything is changed, so a refused registration leaves the stored row untouched.
"""

import datetime as dt
import hashlib

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.errors import Refused
from cmbs.filings.tables import Filing, FilingFile


class ConflictingFiling(Refused):
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
    """Register a filing, or add facts to one already registered.

    The identifying facts (CIK, form, filing date) must match. Each optional fact may be filled
    in later, but never changed: a different non-empty value raises ConflictingFiling.
    """
    optional = {
        "deal_id": deal_id,
        "report_date": report_date,
        "period": period,
        "amends": amends,
        "primary_document": primary_document,
    }
    db.execute(
        insert(Filing)
        .values(accession=accession, cik=cik, form=form, filing_date=filing_date, **optional)
        .on_conflict_do_nothing(index_elements=[Filing.accession])
    )
    stored = db.get(Filing, accession, with_for_update=True, populate_existing=True)
    if stored is None:  # pragma: no cover - the insert above guarantees the row
        raise RuntimeError(f"filing {accession} vanished after insert")
    if (stored.cik, stored.form, stored.filing_date) != (cik, form, filing_date):
        raise ConflictingFiling(f"{accession} is already registered with different facts")
    missing = {}
    for field, value in optional.items():
        current = getattr(stored, field)
        if value is None or current == value:
            continue
        if current is not None:
            raise ConflictingFiling(f"{accession} already has {field} {current!r}, not {value!r}")
        missing[field] = value
    for field, value in missing.items():
        setattr(stored, field, value)
    db.flush()
    return stored


def sha256_of(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def store_file(db: Session, *, accession: str, name: str, content: bytes) -> FilingFile:
    """Idempotent for identical bytes; a different file under the same name is an error."""
    digest = sha256_of(content)
    db.execute(
        insert(FilingFile)
        .values(
            accession=accession,
            name=name,
            sha256=digest,
            size=len(content),
            content=content,
            fetched_at=clock.now(),
        )
        .on_conflict_do_nothing(index_elements=[FilingFile.accession, FilingFile.name])
    )
    stored = db.get(FilingFile, (accession, name), populate_existing=True)
    if stored is None:  # pragma: no cover - the insert above guarantees the row
        raise RuntimeError(f"file {accession}/{name} vanished after insert")
    if stored.sha256 != digest:
        raise ConflictingFiling(f"{accession}/{name} is already stored with other content")
    return stored
