"""Load an ABS-EE loan tape (EX-102) through raw, bronze and silver.

raw     the filing is registered and the file stored byte for byte with its SHA-256
bronze  every asset exactly as reported, including companion notes and portfolio properties
silver  one typed row per top-level loan, checked by the loan_period contract first; a loan
        that fails is quarantined with its reason and not loaded

The load is idempotent and safe under concurrency. The deal row and then the filing row are
locked (in that order, by every load), so two loads of the same filing run one after the other;
the second finds the tape already stored and changes nothing. Every check runs before any
bronze or silver row is written. Writes use ON CONFLICT DO NOTHING as a second guard.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, fields

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from cmbs.absee.schedule_al import Asset, AssetKind, parse_assets
from cmbs.access.service import Actor
from cmbs.audit.service import record
from cmbs.deals.service import get_or_create
from cmbs.domain import Period
from cmbs.errors import Refused
from cmbs.filings.service import register_filing, store_file
from cmbs.filings.tables import Filing
from cmbs.tape import contract
from cmbs.tape.convert import LoanPeriodRow, read_date, to_row
from cmbs.tape.tables import LoanPeriod, QuarantinedLoan, TapeAsset

ORIGINAL = "ABS-EE"
AMENDMENT = "ABS-EE/A"
LOAD_ACTION = "load loan row"
QUARANTINE_ACTION = "quarantine loan"


class TapeRefused(Refused):
    """The tape as a whole cannot be loaded."""


@dataclass(frozen=True)
class Quarantined:
    loan: str
    position: int
    reason: str


@dataclass(frozen=True)
class Plan:
    """What a tape will write: loans to load and loans to quarantine, by asset."""

    loaded: list[tuple[Asset, LoanPeriodRow]]
    quarantined: list[tuple[Asset, str]]


@dataclass(frozen=True)
class LoadSummary:
    deal: str
    accession: str
    period: Period
    filing_date: dt.date
    file_sha256: str
    assets: int
    loans_loaded: int
    quarantined: list[Quarantined]
    already_loaded: bool

    def text(self) -> str:
        count = len(self.quarantined)
        status = "already loaded, nothing changed" if self.already_loaded else "loaded"
        return (
            f"{self.deal} {self.period.label} ({self.accession}, filed {self.filing_date}): "
            f"{status}; {self.assets} assets, {self.loans_loaded} loans loaded, "
            f"{count} {'loan' if count == 1 else 'loans'} quarantined"
        )


def check_form(form: str, amends: str | None) -> None:
    if form == ORIGINAL and amends is not None:
        raise TapeRefused(f"an {ORIGINAL} amends nothing; file the correction as {AMENDMENT}")
    if form == AMENDMENT and amends is None:
        raise TapeRefused(f"an {AMENDMENT} must name the filing it amends")
    if form not in (ORIGINAL, AMENDMENT):
        raise TapeRefused(f"a loan tape is filed as {ORIGINAL} or {AMENDMENT}, not {form}")


def reporting_period_ends(assets: Sequence[Asset]) -> list[dt.date]:
    """Every readable reporting period end in the file; unreadable ones are left to the contract."""
    ends = []
    for asset in assets:
        try:
            end = read_date(asset.tag("reportingPeriodEndDate"), "reporting period end")
        except ValueError:
            continue
        if end is not None:
            ends.append(end)
    return ends


def tape_period(assets: Sequence[Asset], filing_date: dt.date) -> Period:
    """The one distribution month the tape reports on. It must be over before the filing.

    A tape shows only the determination date (`reportingPeriodEndDate`, 09-11-2026), not the
    distribution date (2026-09-17). For CMBS both fall in the same month, so the determination
    date's month is the distribution month that filing discovery registers as the period.
    """
    ends = reporting_period_ends(assets)
    if not ends:
        raise TapeRefused("the tape reports no reporting period end")
    periods = sorted({Period.of(end) for end in ends})
    if len(periods) > 1:
        raise TapeRefused(
            f"the tape reports more than one period: {', '.join(p.label for p in periods)}"
        )
    if filing_date < max(ends):
        raise TapeRefused(
            f"a tape filed on {filing_date} cannot report a period ending {max(ends)}"
        )
    return periods[0]


def plan(assets: Sequence[Asset]) -> Plan:
    """Type every top-level loan and check it against the loan_period contract."""
    typed: list[tuple[Asset, LoanPeriodRow]] = []
    quarantined: list[tuple[Asset, str]] = []
    for asset in assets:
        if asset.kind is not AssetKind.LOAN:
            continue
        row = to_row(asset)
        if isinstance(row, list):
            quarantined.append((asset, "; ".join(row)))
        else:
            typed.append((asset, row))
    failures = contract.violations([row for _, row in typed])
    loaded = []
    for index, (asset, row) in enumerate(typed):
        if index in failures:
            quarantined.append((asset, "; ".join(failures[index])))
        else:
            loaded.append((asset, row))
    quarantined.sort(key=lambda item: item[0].position)
    return Plan(loaded=loaded, quarantined=quarantined)


def _check_amended(
    db: Session, amends: str, deal_id: int, period: Period, filing_date: dt.date
) -> None:
    original = db.get(Filing, amends)
    if (
        original is None
        or original.deal_id != deal_id
        or original.period != period.first_day
        or original.form not in (ORIGINAL, AMENDMENT)
    ):
        raise TapeRefused(f"{amends} is not a registered {period.label} loan tape of this deal")
    if original.filing_date > filing_date:
        raise TapeRefused(
            f"an amendment filed on {filing_date} cannot correct {amends}, "
            f"filed later on {original.filing_date}"
        )


def _loaded_files(db: Session, accession: str) -> set[str]:
    query = select(TapeAsset.file_name).where(TapeAsset.accession == accession).distinct()
    return set(db.scalars(query))


def load_tape(
    db: Session,
    *,
    deal: str,
    content: bytes,
    file_name: str,
    accession: str,
    filing_date: dt.date,
    form: str = ORIGINAL,
    amends: str | None = None,
    cik: int | None = None,
    actor: Actor | None = None,
    run_id: int | None = None,
) -> LoadSummary:
    """Load one EX-102 file of one filing. Loading it again changes nothing.

    With an actor, every loan row written (or quarantined) gets an audit entry naming the
    actor and, when given, the run.
    """
    if run_id is not None and actor is None:
        raise ValueError("a run's writes are audited, so a run id needs an actor")
    check_form(form, amends)
    assets = parse_assets(content)
    if not assets:
        raise TapeRefused("the tape lists no assets")
    period = tape_period(assets, filing_date)

    deal_row = get_or_create(db, deal, cik=cik)  # locks the deal: loads of a deal take turns
    if deal_row.cik is None:
        raise TapeRefused(f"{deal} has no CIK, so its filings cannot be registered")
    if amends is not None:
        _check_amended(db, amends, deal_row.id, period, filing_date)
    register_filing(  # locks the filing row
        db,
        accession=accession,
        cik=deal_row.cik,
        form=form,
        filing_date=filing_date,
        deal_id=deal_row.id,
        period=period.first_day,
        amends=amends,
    )
    loaded_from = _loaded_files(db, accession)  # read under the filing lock
    if loaded_from - {file_name}:
        raise TapeRefused(f"{accession} is already loaded from {', '.join(sorted(loaded_from))}")
    already_loaded = file_name in loaded_from
    stored = store_file(db, accession=accession, name=file_name, content=content)

    if not already_loaded:
        work = plan(assets)
        _write_bronze(db, accession, file_name, assets)
        _write_silver(
            db, deal_row.id, accession, file_name, stored.sha256, filing_date, period, work
        )
        if actor is not None:
            _audit(db, deal, accession, period, work, actor, run_id)
    return _summary(
        db, deal, accession, file_name, period, filing_date, stored.sha256, already_loaded
    )


def _write_bronze(db: Session, accession: str, file_name: str, assets: Sequence[Asset]) -> None:
    db.execute(
        insert(TapeAsset)
        .values(
            [
                {
                    "accession": accession,
                    "file_name": file_name,
                    "position": asset.position,
                    "asset_number": asset.asset_number,
                    "kind": asset.kind.value,
                    "tags": asset.tags,
                    "properties": asset.properties,
                }
                for asset in assets
            ]
        )
        .on_conflict_do_nothing()
    )


def _write_silver(
    db: Session,
    deal_id: int,
    accession: str,
    file_name: str,
    sha256: str,
    filing_date: dt.date,
    period: Period,
    work: Plan,
) -> None:
    common = {
        "deal_id": deal_id,
        "accession": accession,
        "file_name": file_name,
        "filing_date": filing_date,
        "period": period.first_day,
    }
    if work.loaded:
        db.execute(
            insert(LoanPeriod)
            .values(
                [
                    {
                        **common,
                        **{field.name: getattr(row, field.name) for field in fields(row)},
                        "file_sha256": sha256,
                        "asset_position": asset.position,
                    }
                    for asset, row in work.loaded
                ]
            )
            .on_conflict_do_nothing()
        )
    if work.quarantined:
        db.execute(
            insert(QuarantinedLoan)
            .values(
                [
                    {
                        **common,
                        "loan": asset.asset_number,
                        "asset_position": asset.position,
                        "reason": reason,
                    }
                    for asset, reason in work.quarantined
                ]
            )
            .on_conflict_do_nothing()
        )


def _audit(
    db: Session,
    deal: str,
    accession: str,
    period: Period,
    work: Plan,
    actor: Actor,
    run_id: int | None,
) -> None:
    detail = {"accession": accession, "period": period.first_day.isoformat()}
    entries = [(asset, LOAD_ACTION, None) for asset, _ in work.loaded]
    entries += [(asset, QUARANTINE_ACTION, reason) for asset, reason in work.quarantined]
    for asset, action, reason in sorted(entries, key=lambda entry: entry[0].position):
        record(
            db,
            user=actor.user,
            role=actor.role.value,
            run_id=run_id,
            action=action,
            subject="loan",
            subject_key=f"{deal}/{asset.asset_number}",
            reason=reason,
            detail=detail,
        )


def _summary(
    db: Session,
    deal: str,
    accession: str,
    file_name: str,
    period: Period,
    filing_date: dt.date,
    sha256: str,
    already_loaded: bool,
) -> LoadSummary:
    """Counted from what is stored, so a repeated load reports the same numbers."""
    assets = db.scalar(
        select(func.count())
        .select_from(TapeAsset)
        .where(TapeAsset.accession == accession, TapeAsset.file_name == file_name)
    )
    loaded = db.scalar(
        select(func.count()).select_from(LoanPeriod).where(LoanPeriod.accession == accession)
    )
    quarantined = db.scalars(
        select(QuarantinedLoan)
        .where(QuarantinedLoan.accession == accession, QuarantinedLoan.file_name == file_name)
        .order_by(QuarantinedLoan.asset_position)
    ).all()
    return LoadSummary(
        deal=deal,
        accession=accession,
        period=period,
        filing_date=filing_date,
        file_sha256=sha256,
        assets=assets or 0,
        loans_loaded=loaded or 0,
        quarantined=[Quarantined(q.loan, q.asset_position, q.reason) for q in quarantined],
        already_loaded=already_loaded,
    )


def quarantined_loans(db: Session, accession: str) -> list[QuarantinedLoan]:
    query = (
        select(QuarantinedLoan)
        .where(QuarantinedLoan.accession == accession)
        .order_by(QuarantinedLoan.asset_position)
    )
    return list(db.scalars(query))


def stored_assets(db: Session, accession: str) -> list[TapeAsset]:
    query = select(TapeAsset).where(TapeAsset.accession == accession).order_by(TapeAsset.position)
    return list(db.scalars(query))
