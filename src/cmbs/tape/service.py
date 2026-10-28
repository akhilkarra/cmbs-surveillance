"""Load an ABS-EE loan tape (EX-102) through raw, bronze and silver.

raw     the filing is registered and the file stored byte for byte with its SHA-256
bronze  the loaded tape (with its amendment depth) and every asset exactly as reported,
        including companion notes and portfolio properties
silver  one typed row per top-level loan, checked by the loan_period contract first; a loan
        that fails is quarantined with its reason and not loaded

Every check runs before anything is written: the form, the file, the period, the deal's CIK,
the amendment chain, a clash with another tape of the same day, and the facts already
registered for the filing. The deal row is locked first and the filing row second, by every
load, so loads of a deal take turns; the checks that depend on other loads (already loaded,
same-day clash) run again under those locks before writing. A repeated load changes nothing.

With an actor, every loan row written is audited, and every refusal is recorded in the audit
trail in its own transaction (`cmbs.audit.service.refuse`), so the caller's rollback keeps it.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, fields

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from cmbs.absee.schedule_al import Asset, AssetKind, parse_assets
from cmbs.access.service import Actor
from cmbs.audit.service import LOAN, loan_key, record, refuse
from cmbs.deals.service import ConflictingDeal, get_or_create
from cmbs.deals.tables import Deal
from cmbs.domain import Period
from cmbs.errors import Refused
from cmbs.filings.service import ConflictingFiling, register_filing, sha256_of, store_file
from cmbs.filings.tables import Filing, FilingFile
from cmbs.tape import contract
from cmbs.tape.convert import LoanPeriodRow, read_date, to_row
from cmbs.tape.tables import LoanPeriod, LoanTape, QuarantinedLoan, TapeAsset

ORIGINAL = "ABS-EE"
AMENDMENT = "ABS-EE/A"
TAPE_FORMS = (ORIGINAL, AMENDMENT)
TAPE_ACTION = "load loan tape"
LOAD_ACTION = "load loan row"
QUARANTINE_ACTION = "quarantine loan"
FILING = "filing"
MAX_AMENDMENT_DEPTH = 100


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
    notes: list[str]

    def text(self) -> str:
        status = "already loaded, nothing changed" if self.already_loaded else "loaded"
        text = (
            f"{self.deal} {self.period.label} ({self.accession}, filed {self.filing_date}): "
            f"{status}; {self.assets} assets, {_loans(self.loans_loaded)} loaded, "
            f"{_loans(len(self.quarantined))} quarantined"
        )
        if self.notes:
            text += f", {_loans(len(self.notes))} with property notes"
        return text


def _loans(count: int) -> str:
    return f"{count} {'loan' if count == 1 else 'loans'}"


# ---------------------------------------------------------------------------- pure checks


def check_form(form: str, amends: str | None) -> None:
    if form == ORIGINAL and amends is not None:
        raise TapeRefused(f"an {ORIGINAL} amends nothing; file the correction as {AMENDMENT}")
    if form == AMENDMENT and amends is None:
        raise TapeRefused(f"an {AMENDMENT} must name the filing it amends")
    if form not in TAPE_FORMS:
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


# ---------------------------------------------------------------------------- database checks


def _locked_deal(db: Session, name: str) -> Deal | None:
    query = select(Deal).where(Deal.name == name).with_for_update()
    return db.scalars(query.execution_options(populate_existing=True)).first()


def _cik(name: str, deal: Deal | None, cik: int | None) -> int:
    known = deal.cik if deal is not None else None
    if known is not None and cik is not None and known != cik:
        raise ConflictingDeal(f"{name} already has CIK {known}, not {cik}")
    resolved = cik if cik is not None else known
    if resolved is None:
        raise TapeRefused(f"{name} has no CIK, so its filings cannot be registered")
    return resolved


def amendment_chain(db: Session, amends: str | None) -> list[Filing]:
    """The filings `amends` points to, nearest first, as far as they are registered."""
    chain: list[Filing] = []
    while amends is not None and len(chain) < MAX_AMENDMENT_DEPTH:
        filing = db.get(Filing, amends)
        if filing is None:
            break
        chain.append(filing)
        amends = filing.amends
    return chain


def _check_amended(
    db: Session, amends: str | None, deal_id: int | None, period: Period, filing_date: dt.date
) -> int:
    """The new tape's amendment depth: 0 for an original, one more than what it amends."""
    if amends is None:
        return 0
    original = db.get(Filing, amends)
    if (
        original is None
        or deal_id is None
        or original.deal_id != deal_id
        or original.period != period.first_day
        or original.form not in TAPE_FORMS
    ):
        raise TapeRefused(f"{amends} is not a registered {period.label} loan tape of this deal")
    if original.filing_date > filing_date:
        raise TapeRefused(
            f"an amendment filed on {filing_date} cannot correct {amends}, "
            f"filed later on {original.filing_date}"
        )
    return len(amendment_chain(db, amends))


def _check_same_day(
    db: Session,
    deal: str,
    deal_id: int | None,
    accession: str,
    amends: str | None,
    period: Period,
    filing_date: dt.date,
) -> None:
    """Two tapes of one period filed the same day must be linked by amendment, or the panel
    could not tell which one is current."""
    if deal_id is None:
        return
    others = db.scalars(
        select(Filing).where(
            Filing.deal_id == deal_id,
            Filing.period == period.first_day,
            Filing.filing_date == filing_date,
            Filing.form.in_(TAPE_FORMS),
            Filing.accession != accession,
        )
    ).all()
    above = {filing.accession for filing in amendment_chain(db, amends)}
    for other in sorted(others, key=lambda filing: filing.accession):
        below = {filing.accession for filing in amendment_chain(db, other.amends)}
        if other.accession not in above and accession not in below:
            raise TapeRefused(
                f"{accession} and {other.accession} are both {period.label} tapes of {deal} "
                f"filed on {filing_date}, and neither amends the other"
            )


def _check_filing(
    db: Session,
    accession: str,
    *,
    cik: int,
    form: str,
    filing_date: dt.date,
    deal_id: int | None,
    period: Period,
    amends: str | None,
) -> None:
    """The facts `register_filing` would refuse, checked before anything is written."""
    existing = db.get(Filing, accession)
    if existing is None:
        return
    if (existing.cik, existing.form, existing.filing_date) != (cik, form, filing_date):
        raise ConflictingFiling(f"{accession} is already registered with different facts")
    if existing.deal_id is not None and existing.deal_id != deal_id:
        raise ConflictingFiling(f"{accession} is already registered to another deal")
    for field, value in (("period", period.first_day), ("amends", amends)):
        current = getattr(existing, field)
        if value is not None and current is not None and current != value:
            raise ConflictingFiling(f"{accession} already has {field} {current!r}, not {value!r}")


def _loaded(db: Session, accession: str, file_name: str, sha256: str) -> LoanTape | None:
    """The tape already loaded from this filing, if any; another file or other bytes refuse."""
    tape = db.get(LoanTape, accession, populate_existing=True)
    if tape is not None and tape.file_name != file_name:
        raise TapeRefused(f"{accession} is already loaded from {tape.file_name}")
    stored = db.get(FilingFile, (accession, file_name))
    if stored is not None and stored.sha256 != sha256:
        raise ConflictingFiling(f"{accession}/{file_name} is already stored with other content")
    return tape


# ---------------------------------------------------------------------------- the load


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
    actor and, when given, the run, and a refusal is recorded before it is raised.
    """
    if run_id is not None and actor is None:
        raise ValueError("a run's writes are audited, so a run id needs an actor")
    try:
        return _load(
            db,
            deal=deal,
            content=content,
            file_name=file_name,
            accession=accession,
            filing_date=filing_date,
            form=form,
            amends=amends,
            cik=cik,
            actor=actor,
            run_id=run_id,
        )
    except Refused as error:
        if actor is None:
            raise
        refuse(
            db,
            error,
            user=actor.user,
            action=TAPE_ACTION,
            role=actor.role.value,
            subject=FILING,
            subject_key=accession,
        )


def _load(
    db: Session,
    *,
    deal: str,
    content: bytes,
    file_name: str,
    accession: str,
    filing_date: dt.date,
    form: str,
    amends: str | None,
    cik: int | None,
    actor: Actor | None,
    run_id: int | None,
) -> LoadSummary:
    check_form(form, amends)
    assets = parse_assets(content)
    if not assets:
        raise TapeRefused("the tape lists no assets")
    period = tape_period(assets, filing_date)
    sha256 = sha256_of(content)

    existing_deal = _locked_deal(db, deal)  # loads of a deal take turns
    deal_cik = _cik(deal, existing_deal, cik)
    deal_id = existing_deal.id if existing_deal is not None else None
    depth = _check_amended(db, amends, deal_id, period, filing_date)
    _check_filing(
        db,
        accession,
        cik=deal_cik,
        form=form,
        filing_date=filing_date,
        deal_id=deal_id,
        period=period,
        amends=amends,
    )
    if _loaded(db, accession, file_name, sha256) is not None:
        return _summary(db, deal, accession, period, filing_date, already_loaded=True)
    _check_same_day(db, deal, deal_id, accession, amends, period, filing_date)

    deal_row = get_or_create(db, deal, cik=deal_cik)
    register_filing(  # locks the filing row
        db,
        accession=accession,
        cik=deal_cik,
        form=form,
        filing_date=filing_date,
        deal_id=deal_row.id,
        period=period.first_day,
        amends=amends,
    )
    # A load that held the locks before this one may have changed what the checks saw.
    if _loaded(db, accession, file_name, sha256) is not None:
        return _summary(db, deal, accession, period, filing_date, already_loaded=True)
    _check_same_day(db, deal, deal_row.id, accession, amends, period, filing_date)

    store_file(db, accession=accession, name=file_name, content=content)
    work = plan(assets)
    db.add(
        LoanTape(
            accession=accession,
            file_name=file_name,
            file_sha256=sha256,
            deal_id=deal_row.id,
            period=period.first_day,
            filing_date=filing_date,
            amendment_depth=depth,
        )
    )
    db.flush()
    _write_bronze(db, accession, file_name, assets)
    _write_silver(db, deal_row.id, accession, file_name, sha256, filing_date, period, work)
    if actor is not None:
        _audit(db, deal, accession, period, work, actor, run_id)
    return _summary(db, deal, accession, period, filing_date, already_loaded=False)


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
            subject=LOAN,
            subject_key=loan_key(deal, asset.asset_number),
            reason=reason,
            detail=detail,
        )


def _summary(
    db: Session,
    deal: str,
    accession: str,
    period: Period,
    filing_date: dt.date,
    *,
    already_loaded: bool,
) -> LoadSummary:
    """Counted from what is stored, so a repeated load reports the same numbers."""
    tape = db.get(LoanTape, accession)
    if tape is None:  # pragma: no cover - only called once the tape is stored
        raise RuntimeError(f"tape {accession} vanished")
    assets = db.scalar(
        select(func.count()).select_from(TapeAsset).where(TapeAsset.accession == accession)
    )
    rows = db.execute(
        select(LoanPeriod.loan, LoanPeriod.property_note)
        .where(LoanPeriod.accession == accession)
        .order_by(LoanPeriod.asset_position)
    ).all()
    return LoadSummary(
        deal=deal,
        accession=accession,
        period=period,
        filing_date=filing_date,
        file_sha256=tape.file_sha256,
        assets=assets or 0,
        loans_loaded=len(rows),
        quarantined=[
            Quarantined(q.loan, q.asset_position, q.reason)
            for q in quarantined_loans(db, accession)
        ],
        already_loaded=already_loaded,
        notes=[f"loan {loan}: {note}" for loan, note in rows if note is not None],
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
