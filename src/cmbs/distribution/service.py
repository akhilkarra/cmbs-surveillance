"""Load a distribution report into the panel, and read back what reports said about a loan.

Loading replaces a filing's items, so the panel holds exactly what the latest parse found and
never a value the report no longer contains. The report is parsed and its period checked
before anything is written; then the deal, the filing and the file are registered through the
shared services, and the report row is inserted with `ON CONFLICT DO NOTHING`, locked and
re-read. Under that lock the filing's comments, appraisal reductions and delinquency rows are
deleted and the new ones inserted, in the caller's one transaction. A concurrent load of the
same filing waits for the lock, so the result is always one parse's items, never a mix.
Loading the same file twice stores the same rows with the same values.

Reads are point in time: an item is visible only from its filing's date.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.access.roles import Action
from cmbs.access.service import authorize
from cmbs.audit.service import record, refuse
from cmbs.deals import service as deals
from cmbs.distribution import values
from cmbs.distribution.parse import ParsedReport, parse_report
from cmbs.distribution.tables import (
    DistributionReport,
    ReportAppraisalReduction,
    ReportComment,
    ReportDelinquency,
)
from cmbs.domain import Period
from cmbs.errors import Refused
from cmbs.filings.service import register_filing, store_file
from cmbs.filings.tables import Filing

FORM = "10-D"
AUDIT_ACTION = "parse distribution report"


class WrongPeriod(Refused):
    """The report's own distribution date disagrees with the period given, or is missing."""


class ConflictingReport(Refused):
    """The filing's report was already parsed from another file."""


class WouldLoseItems(Refused):
    """A reload whose parse finds nothing would erase the items an earlier load stored."""


class UnknownDeal(Refused):
    pass


@dataclass(frozen=True)
class LoadedReport:
    accession: str
    deal: str
    period: Period
    administrator: str
    summary: str
    comments: int
    appraisal_reductions: int
    delinquencies: int


def report_period(parsed: ParsedReport, stated: Period | None) -> Period:
    """The period the report covers: the one stated, checked against the report's own date."""
    found = Period.of(parsed.distribution_date) if parsed.distribution_date else None
    if stated is None:
        if found is None:
            raise WrongPeriod("the report does not state its distribution date; give the period")
        return found
    if found is not None and found != stated:
        raise WrongPeriod(f"the report is for {found}, not {stated}")
    return stated


def item_rows(
    parsed: ParsedReport, *, accession: str, deal_id: int, period: dt.date
) -> dict[type, list[dict[str, Any]]]:
    """The rows to insert for each item table."""
    common = {"accession": accession, "deal_id": deal_id, "period": period}
    return {
        ReportComment: [
            {**common, "loan_id": c.loan, "source_table": c.source_table, "comment": c.text}
            for c in parsed.comments
        ],
        ReportAppraisalReduction: [
            {
                **common,
                "loan_id": a.loan,
                "source_table": a.source_table,
                "amount": a.amount,
                "reduction_date": a.reduction_date,
            }
            for a in parsed.appraisal_reductions
        ],
        ReportDelinquency: [
            {
                **common,
                "loan_id": d.loan,
                "source_table": d.source_table,
                "status": d.status,
                "paid_through": d.paid_through,
                "current_advances": d.current_advances,
                "outstanding_advances": d.outstanding_advances,
                "actual_balance": d.actual_balance,
            }
            for d in parsed.delinquencies
        ],
    }


ITEM_TABLES = (ReportComment, ReportAppraisalReduction, ReportDelinquency)


def stored_items(db: Session, accession: str) -> int:
    """How many comments, appraisal reductions and delinquency rows the filing has stored."""
    return sum(
        db.scalar(select(func.count()).select_from(table).where(table.accession == accession)) or 0
        for table in ITEM_TABLES
    )


def check_reload(
    stored: DistributionReport,
    items: int,
    *,
    accession: str,
    file_name: str,
    parsed: ParsedReport,
) -> None:
    """Refuse a load that would replace an earlier one wrongly.

    The filing's report must come from the same file. A parse that finds no item at all
    (a cover page, or a layout the parser no longer recognises) must not erase the items an
    earlier load stored.
    """
    if stored.file_name != file_name:
        raise ConflictingReport(
            f"{accession} was already parsed from {stored.file_name}, not {file_name}"
        )
    found = len(parsed.comments) + len(parsed.appraisal_reductions) + len(parsed.delinquencies)
    if items and not found:
        raise WouldLoseItems(
            f"{accession}: this parse of {file_name} finds no comments, appraisal reductions or "
            f"delinquencies, so the {items} items stored from it are kept"
        )


def load_report(
    db: Session,
    *,
    user: str,
    deal: str,
    accession: str,
    cik: int,
    filing_date: dt.date,
    file_name: str,
    content: bytes,
    period: Period | None = None,
) -> LoadedReport:
    """Parse a 10-D's EX-99.1 and store what it says about each loan.

    Every refusal after the permission check is audited on its own connection, so the record
    survives the rollback. The checks run before anything is written, and again under the lock
    on the report row, where a concurrent load of the same filing may have got there first.
    """
    actor = authorize(db, user, Action.RUN_REFRESH)
    try:
        parsed = parse_report(content)
        covered = report_period(parsed, period)
        earlier = db.get(DistributionReport, accession)
        if earlier is not None:
            check_reload(
                earlier,
                stored_items(db, accession),
                accession=accession,
                file_name=file_name,
                parsed=parsed,
            )
        deal_row = deals.get_or_create(db, deal, cik=cik)
        register_filing(
            db,
            accession=accession,
            cik=cik,
            form=FORM,
            filing_date=filing_date,
            deal_id=deal_row.id,
            period=covered.first_day,
        )
        store_file(db, accession=accession, name=file_name, content=content)
        db.execute(
            insert(DistributionReport)
            .values(
                accession=accession,
                file_name=file_name,
                deal_id=deal_row.id,
                period=covered.first_day,
                administrator=parsed.administrator.value,
                distribution_date=parsed.distribution_date,
                summary=parsed.summary,
            )
            .on_conflict_do_nothing(index_elements=[DistributionReport.accession])
        )
        # The lock on the report row makes the replacement below one step for any other load
        # of this filing: a second load waits here, then replaces what the first one stored.
        stored = db.get(DistributionReport, accession, with_for_update=True, populate_existing=True)
        if stored is None:  # pragma: no cover - the insert above guarantees the row
            raise RuntimeError(f"report {accession} vanished after insert")
        check_reload(
            stored,
            stored_items(db, accession),
            accession=accession,
            file_name=file_name,
            parsed=parsed,
        )
    except Refused as refusal:
        refuse(
            db,
            refusal,
            user=actor.user,
            action=AUDIT_ACTION,
            role=actor.role.value,
            subject="filing",
            subject_key=accession,
        )
    stored.administrator = parsed.administrator.value
    stored.distribution_date = parsed.distribution_date
    stored.summary = parsed.summary
    rows = item_rows(parsed, accession=accession, deal_id=deal_row.id, period=covered.first_day)
    for table, batch in rows.items():
        db.execute(delete(table).where(table.accession == accession))
        if batch:
            db.execute(insert(table).values(batch))
    db.flush()
    counts = {
        "comments": len(parsed.comments),
        "appraisal_reductions": len(parsed.appraisal_reductions),
        "delinquencies": len(parsed.delinquencies),
    }
    record(
        db,
        user=actor.user,
        role=actor.role.value,
        action=AUDIT_ACTION,
        subject="filing",
        subject_key=accession,
        detail={"file": file_name, **counts},
    )
    return LoadedReport(
        accession=accession,
        deal=deal,
        period=covered,
        administrator=parsed.administrator.value,
        summary=parsed.summary,
        **counts,
    )


def summary_of(db: Session, accession: str) -> str | None:
    stored = db.get(DistributionReport, accession)
    return None if stored is None else stored.summary


@dataclass(frozen=True)
class Seen:
    """Where and when an item became public."""

    accession: str
    period: Period
    filing_date: dt.date
    source_table: str
    loan: str


@dataclass(frozen=True)
class StoredComment(Seen):
    comment: str


@dataclass(frozen=True)
class StoredAppraisalReduction(Seen):
    amount: Decimal
    reduction_date: dt.date | None


@dataclass(frozen=True)
class StoredDelinquency(Seen):
    status: str | None
    paid_through: dt.date | None
    current_advances: Decimal | None
    outstanding_advances: Decimal | None
    actual_balance: Decimal | None


def canonical_loan(text: str) -> str:
    """A loan id as stored: '011' -> '11'. Text that is not a prospectus id is kept as given."""
    return values.loan_id(text) or text.strip()


def _deal_id(db: Session, name: str) -> int:
    deal = deals.by_name(db, name)
    if deal is None:
        raise UnknownDeal(f"no deal named {name!r}")
    return deal.id


type _Item = ReportComment | ReportAppraisalReduction | ReportDelinquency


def _visible[T: _Item](
    db: Session,
    table: type[T],
    *,
    deal: str,
    as_of: dt.date | None,
    loan: str | None = None,
    period: Period | None = None,
) -> Sequence[tuple[T, dt.date]]:
    """Items of `table` for a deal, filed on or before `as_of` (today when not given)."""
    query: Select[T, dt.date] = (
        select(table, Filing.filing_date)
        .join(Filing, Filing.accession == table.accession)
        .where(
            table.deal_id == _deal_id(db, deal),
            Filing.filing_date <= (as_of or clock.today()),
        )
        .order_by(table.period.desc(), table.loan_id, table.source_table)
    )
    if loan is not None:
        query = query.where(table.loan_id == canonical_loan(loan))
    if period is not None:
        query = query.where(table.period == period.first_day)
    return [(item, filed) for item, filed in db.execute(query)]


def _seen(item: _Item, filed: dt.date) -> dict[str, Any]:
    return {
        "accession": item.accession,
        "period": Period.of(item.period),
        "filing_date": filed,
        "source_table": item.source_table,
        "loan": item.loan_id,
    }


def comments_for_loan(
    db: Session, *, deal: str, loan: str, as_of: dt.date | None = None
) -> list[StoredComment]:
    """Every special-servicing comment on the loan, newest period first."""
    return [
        StoredComment(**_seen(item, filed), comment=item.comment)
        for item, filed in _visible(db, ReportComment, deal=deal, as_of=as_of, loan=loan)
    ]


def appraisal_reductions(
    db: Session,
    *,
    deal: str,
    loan: str | None = None,
    period: Period | None = None,
    as_of: dt.date | None = None,
) -> list[StoredAppraisalReduction]:
    rows = _visible(db, ReportAppraisalReduction, deal=deal, as_of=as_of, loan=loan, period=period)
    return [
        StoredAppraisalReduction(
            **_seen(item, filed),
            amount=item.amount,
            reduction_date=item.reduction_date,
        )
        for item, filed in rows
    ]


def delinquencies(
    db: Session,
    *,
    deal: str,
    period: Period | None = None,
    loan: str | None = None,
    as_of: dt.date | None = None,
) -> list[StoredDelinquency]:
    """The loans each report lists as delinquent, as reported, for reconciliation with the tape."""
    rows = _visible(db, ReportDelinquency, deal=deal, as_of=as_of, loan=loan, period=period)
    return [
        StoredDelinquency(
            **_seen(item, filed),
            status=item.status,
            paid_through=item.paid_through,
            current_advances=item.current_advances,
            outstanding_advances=item.outstanding_advances,
            actual_balance=item.actual_balance,
        )
        for item, filed in rows
    ]
