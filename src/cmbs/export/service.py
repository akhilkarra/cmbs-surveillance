"""Rebuild the panel from the stored raw filings, and export it as of a date.

**Rebuild** (`rebuild`) proves the panel is reproducible: it runs the loan tape loader again
over the raw files stored for each tape (`raw.filing_file`, byte for byte, SHA-256 checked) and
compares the rows it writes with the stored ones, field for field and by a checksum of the
ordered rows. It works inside a savepoint that is always rolled back, so it changes nothing:
the stored panel stays exactly as it was, whatever the rebuild found. The deals' rows are
locked first, so no load of those deals runs while the rebuild reads them. Every rebuild is
audited with both checksums.

**Export** (`export_panel`) writes the panel as of a day that has ended: the loan rows of each
tape published by then (`cmbs.panel.service.as_of`), and each loan-month's extracted comment
fields as `cmbs.extraction.service.comment_view` showed them that day, so a value still waiting
for review on that day reads "needs review" with no value, and a value an analyst overrode reads
"overridden". It is what had been published by the as-of date, as the panel knew it when the
export was taken: a later backfill can add rows published before that date, so the record says
when the export was taken. The file names the as-of date and the settings in force that day
(settings version, model, prompt version). Every export is recorded (who, when, as of, rows,
file hash) and audited.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, NoReturn

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.access.roles import Action
from cmbs.access.service import Actor, authorize
from cmbs.audit.service import record, refuse
from cmbs.deals.service import by_name
from cmbs.deals.tables import Deal
from cmbs.distribution.service import canonical_loan
from cmbs.domain import Period
from cmbs.errors import Refused
from cmbs.export import files
from cmbs.export.tables import FORMATS, PanelExport
from cmbs.extraction.service import comment_view
from cmbs.extraction.tables import Extraction
from cmbs.filings.service import sha256_of
from cmbs.filings.tables import Filing, FilingFile
from cmbs.panel import service as panel
from cmbs.panel.service import loan_order
from cmbs.settings.service import in_force
from cmbs.tape.service import load_tape
from cmbs.tape.tables import LoanPeriod, LoanTape, QuarantinedLoan, TapeAsset

REBUILD = "verify panel rebuild"
EXPORT = "export panel"
PANEL = "panel"

# Every stored column of a loan row, in the table's order, and of a quarantined loan.
LOAN_COLUMNS = tuple(column.name for column in LoanPeriod.__table__.columns)
QUARANTINE_COLUMNS = tuple(column.name for column in QuarantinedLoan.__table__.columns)


class UnknownDeal(Refused):
    pass


class NothingToRebuild(Refused):
    """No loan tape is stored for what was asked."""


class CorruptRawFile(Refused):
    """A stored raw file no longer matches the SHA-256 recorded when it was fetched."""


class UnknownFormat(Refused):
    pass


class DayNotOver(Refused):
    """An export is as of a day that has ended: today's filings may still arrive."""


def _refuse(db: Session, error: Refused, actor: Actor, action: str, key: str | None) -> NoReturn:
    refuse(
        db,
        error,
        user=actor.user,
        role=actor.role.value,
        action=action,
        subject=PANEL,
        subject_key=key,
    )


def _deals(db: Session, deal: str | None) -> list[Deal]:
    if deal is None:
        ids = set(db.scalars(select(LoanTape.deal_id).distinct()))
        return sorted((db.get_one(Deal, deal_id) for deal_id in ids), key=lambda d: d.name)
    found = by_name(db, deal)
    if found is None:
        raise UnknownDeal(f"no deal named {deal!r}")
    return [found]


# ---------------------------------------------------------------------------- rebuild


def _values(row: Any, columns: Sequence[str]) -> dict[str, Any]:
    return {column: getattr(row, column) for column in columns}


def _loan_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (row["deal_id"], row["period"], row["accession"], row["loan"])


def _ordered_loans(rows: dict[tuple[Any, ...], dict[str, Any]]) -> list[dict[str, Any]]:
    return [rows[key] for key in sorted(rows, key=lambda k: (k[0], k[1], k[2], loan_order(k[3])))]


@dataclass(frozen=True)
class PanelState:
    """The stored loan rows and quarantined loans of some deals, keyed and checksummed."""

    loans: dict[tuple[Any, ...], dict[str, Any]]
    quarantined: dict[tuple[Any, ...], dict[str, Any]]

    @property
    def checksum(self) -> str:
        return files.checksum(_ordered_loans(self.loans), LOAN_COLUMNS)

    @property
    def quarantine_checksum(self) -> str:
        ordered = [self.quarantined[key] for key in sorted(self.quarantined)]
        return files.checksum(ordered, QUARANTINE_COLUMNS)


def panel_state(db: Session, deal_ids: Sequence[int]) -> PanelState:
    loans = {
        _loan_key(values): values
        for values in (
            _values(row, LOAN_COLUMNS)
            for row in db.scalars(select(LoanPeriod).where(LoanPeriod.deal_id.in_(deal_ids)))
        )
    }
    quarantined = {
        (row.accession, row.file_name, row.asset_position): _values(row, QUARANTINE_COLUMNS)
        for row in db.scalars(select(QuarantinedLoan).where(QuarantinedLoan.deal_id.in_(deal_ids)))
    }
    return PanelState(loans, quarantined)


@dataclass(frozen=True)
class Rebuild:
    deals: list[str]
    tapes: int
    rows: int
    stored_checksum: str
    rebuilt_checksum: str
    differences: list[files.Difference]

    @property
    def identical(self) -> bool:
        return not self.differences and self.stored_checksum == self.rebuilt_checksum

    def text(self) -> str:
        head = (
            f"rebuilt {self.rows} loan rows of {len(self.deals)} "
            f"{'deal' if len(self.deals) == 1 else 'deals'} from {self.tapes} stored "
            f"{'tape' if self.tapes == 1 else 'tapes'}: "
        )
        if self.identical:
            head += f"identical, checksum {self.stored_checksum}"
        else:
            head += (
                f"{len(self.differences)} differences, stored checksum {self.stored_checksum}, "
                f"rebuilt {self.rebuilt_checksum}"
            )
        return "\n".join([head, *(difference.text() for difference in self.differences)])


def _raw_tapes(db: Session, deal_ids: Sequence[int]) -> list[tuple[LoanTape, Filing, bytes]]:
    """Each stored tape with its filing and raw bytes, originals before their amendments."""
    tapes = db.scalars(
        select(LoanTape)
        .where(LoanTape.deal_id.in_(deal_ids))
        .order_by(LoanTape.filing_date, LoanTape.amendment_depth, LoanTape.accession)
    ).all()
    found = []
    for tape in tapes:
        filing = db.get_one(Filing, tape.accession)
        stored = db.get_one(FilingFile, (tape.accession, tape.file_name))
        if sha256_of(stored.content) != stored.sha256 or stored.sha256 != tape.file_sha256:
            raise CorruptRawFile(
                f"the stored file {tape.accession}/{tape.file_name} does not match its SHA-256"
            )
        found.append((tape, filing, stored.content))
    return found


def rebuild(db: Session, user: str, *, deal: str | None = None) -> Rebuild:
    """Rebuild the panel of one deal (or every deal with a tape) from the stored raw filings and
    compare it with the stored panel. Nothing stored changes."""
    actor = authorize(db, user, Action.RUN_REFRESH)
    try:
        deals = _deals(db, deal)
        if not deals:
            raise NothingToRebuild("no loan tape is stored")
        for row in deals:  # loads of these deals wait until the rebuild is done
            db.get(Deal, row.id, with_for_update=True, populate_existing=True)
        ids = [row.id for row in deals]
        tapes = _raw_tapes(db, ids)
        if not tapes:
            raise NothingToRebuild(f"no loan tape of {deals[0].name} is stored")
    except Refused as error:
        _refuse(db, error, actor, REBUILD, deal)
    stored = panel_state(db, ids)
    names = {row.id: row.name for row in deals}
    savepoint = db.begin_nested()
    try:
        for table in (LoanPeriod, QuarantinedLoan):
            db.execute(delete(table).where(table.deal_id.in_(ids)))
        accessions = [tape.accession for tape, _, _ in tapes]
        db.execute(delete(TapeAsset).where(TapeAsset.accession.in_(accessions)))
        db.execute(delete(LoanTape).where(LoanTape.accession.in_(accessions)))
        db.expire_all()
        for tape, filing, content in tapes:
            load_tape(
                db,
                deal=names[tape.deal_id],
                content=content,
                file_name=tape.file_name,
                accession=tape.accession,
                filing_date=tape.filing_date,
                form=filing.form,
                amends=filing.amends,
                cik=filing.cik,
            )
        rebuilt = panel_state(db, ids)
    finally:
        savepoint.rollback()
    db.expire_all()
    found = files.differences(stored.loans, rebuilt.loans, LOAN_COLUMNS)
    found += files.differences(stored.quarantined, rebuilt.quarantined, QUARANTINE_COLUMNS)
    result = Rebuild(
        deals=[row.name for row in deals],
        tapes=len(tapes),
        rows=len(rebuilt.loans),
        stored_checksum=stored.checksum,
        rebuilt_checksum=rebuilt.checksum,
        differences=found,
    )
    record(
        db,
        user=actor.user,
        role=actor.role.value,
        action=REBUILD,
        subject=PANEL,
        subject_key=deal,
        after="identical" if result.identical else f"{len(found)} differences",
        detail={
            "deals": result.deals,
            "tapes": result.tapes,
            "rows": result.rows,
            "stored_checksum": result.stored_checksum,
            "rebuilt_checksum": result.rebuilt_checksum,
            "quarantine_checksums": [stored.quarantine_checksum, rebuilt.quarantine_checksum],
            "differences": len(found),
        },
    )
    return result


# ---------------------------------------------------------------------------- export

# The export's columns: the deal by name, then every stored column of a loan row but the deal's
# internal id, then the extracted comment fields.
EXPORT_LOAN_COLUMNS = tuple(column for column in LOAN_COLUMNS if column != "deal_id")
EXPORT_COLUMNS = ("deal", *EXPORT_LOAN_COLUMNS, *files.comment_columns())


def end_of(day: dt.date) -> dt.datetime:
    """The last instant of a day (UTC): what was known on the day includes the whole day."""
    return dt.datetime.combine(day, dt.time.max, dt.UTC)


@dataclass(frozen=True)
class Export:
    number: int
    file_name: str
    content: bytes
    rows: int
    sha256: str
    as_of: dt.date
    settings_version: int
    model: str
    prompt_version: int

    def text(self) -> str:
        return (
            f"export {self.number}: {self.rows} rows as of {self.as_of.isoformat()}, settings "
            f"version {self.settings_version}, {self.model}, prompt version "
            f"{self.prompt_version}; {self.file_name}, SHA-256 {self.sha256}"
        )


def _extracted(db: Session, deal_id: int) -> set[tuple[str, dt.date]]:
    """The (loan, period) pairs of the deal that have an extracted comment."""
    found = db.execute(
        select(Extraction.loan_id, Extraction.period)
        .where(Extraction.deal_id == deal_id)
        .distinct()
    )
    return {(loan, period) for loan, period in found}


def export_rows(db: Session, reader: str, deals: Sequence[Deal], day: dt.date) -> list[dict]:
    """The panel as known on `day`: every deal's loan rows, then their extracted fields."""
    rows: list[dict[str, Any]] = []
    for deal in deals:
        extracted = _extracted(db, deal.id)
        for loan in panel.as_of(db, day, deal_id=deal.id):
            view = (
                comment_view(
                    db, reader, deal=deal.name, loan=loan.loan, period=Period.of(loan.period),
                    as_of=day,
                )
                if (canonical_loan(loan.loan), loan.period) in extracted
                else None
            )  # fmt: skip
            rows.append(
                {
                    "deal": deal.name,
                    **_values(loan, EXPORT_LOAN_COLUMNS),
                    **files.comment_values(view),
                }
            )
    return rows


def export_panel(
    db: Session, user: str, *, as_of: dt.date, fmt: str = "csv", deal: str | None = None
) -> Export:
    """The panel as known at the end of `as_of`, a day before today, as a CSV or Parquet file;
    recorded and audited. One deal, or every deal with a tape."""
    actor = authorize(db, user, Action.EXPORT)
    try:
        if as_of >= clock.today():
            raise DayNotOver("an export is as of a day that has ended")
        if fmt not in FORMATS:
            raise UnknownFormat(f"exports are {' or '.join(FORMATS)}, not {fmt!r}")
        deals = _deals(db, deal)
        settings = in_force(db, end_of(as_of))  # an export names the settings it was made under
    except Refused as error:
        _refuse(db, error, actor, EXPORT, deal)
    rows = export_rows(db, actor.user, deals, as_of)
    # The record is written once, complete (the table refuses updates): its number comes first,
    # because the file names it.
    number = db.scalar(text("SELECT nextval(pg_get_serial_sequence('app.panel_export', 'id'))"))
    name = files.file_name(
        number=number,
        deal=deals[0].name if deal is not None else None,
        as_of=as_of,
        settings_version=settings.version,
        model=settings.values.model,
        prompt_version=settings.values.prompt_version,
        fmt=fmt,
    )
    provenance = {
        "as_of": as_of.isoformat(),
        "settings_version": str(settings.version),
        "model": settings.values.model,
        "prompt_version": str(settings.values.prompt_version),
        "exported_by": actor.user,
        "export": str(number),
    }
    content = (
        files.to_csv(rows, EXPORT_COLUMNS)
        if fmt == "csv"
        else files.to_parquet(rows, EXPORT_COLUMNS, provenance)
    )
    record_row = PanelExport(
        id=number,
        exported_by=actor.user,
        exported_at=clock.now(),
        as_of=as_of,
        deal_id=deals[0].id if deal is not None else None,
        format=fmt,
        file_name=name,
        row_count=len(rows),
        sha256=sha256_of(content),
        settings_version=settings.version,
        model=settings.values.model,
        prompt_version=settings.values.prompt_version,
    )
    db.add(record_row)
    db.flush()
    record(
        db,
        user=actor.user,
        role=actor.role.value,
        action=EXPORT,
        subject="export",
        subject_key=str(record_row.id),
        after=name,
        detail={**provenance, "rows": len(rows), "sha256": record_row.sha256, "format": fmt},
    )
    return Export(
        number=record_row.id,
        file_name=name,
        content=content,
        rows=len(rows),
        sha256=record_row.sha256,
        as_of=as_of,
        settings_version=settings.version,
        model=settings.values.model,
        prompt_version=settings.values.prompt_version,
    )
