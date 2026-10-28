"""Reconcile a 10-D's delinquency detail with the loan tape of the same deal and month.

The report lists the loans that are behind on payments (its "Delinquency Loan Detail"); the
tape gives every loan's payment status (Schedule AL `paymentStatusLoanCode`). They should agree:

- a loan the report lists must be behind on the tape: in its grace period, late, 30 days or
  more delinquent, or a matured balloon not paid off. When the report states a code the tape
  uses as well and both are behind, the two codes must be the same.
- a loan the tape shows 30 days or more delinquent must be listed by the report, when the report
  has a delinquency table at all.

A disagreement raises a check flag that keeps both sides ("report says delinquent, tape says
current"). Neither source is changed: which one is right is for an analyst to find out. Raising
the same flag again for the same month adds nothing, so reconciling twice is harmless.

Both sides are read point in time, as of a day: the latest report for the month filed by then,
and the tape the panel shows as current on that day.
"""

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.access.roles import Action
from cmbs.access.service import authorize
from cmbs.audit.service import record, refuse
from cmbs.checks.service import raise_flag
from cmbs.checks.tables import CheckFlag
from cmbs.deals.service import by_name
from cmbs.deals.tables import Deal
from cmbs.distribution.layouts import DELINQUENCY_TABLES, Administrator
from cmbs.distribution.service import UnknownDeal
from cmbs.distribution.tables import DistributionReport, ReportDelinquency
from cmbs.domain import Period
from cmbs.errors import Refused
from cmbs.filings.tables import Filing
from cmbs.panel import service as panel

CHECK = "report_tape_reconciliation"
AUDIT_ACTION = "reconcile report with tape"

# Schedule AL payment status codes (paymentStatusLoanCode), the CREFC loan status codes that
# certificate administrators also print in their delinquency tables.
PAYMENT_STATUS: Mapping[str, str] = {
    "0": "current",
    "A": "in its grace period",
    "B": "late but less than 30 days delinquent",
    "1": "30 to 59 days delinquent",
    "2": "60 to 89 days delinquent",
    "3": "90 days or more delinquent",
    "4": "a performing matured balloon",
    "5": "a non-performing matured balloon",
}
# Codes the report must list: 30 days or more delinquent, or matured and not paid off.
DELINQUENT = frozenset({"1", "2", "3", "5"})
# Codes a loan the report lists may have: delinquent, or in grace or late within 30 days.
BEHIND = DELINQUENT | {"A", "B"}


class NothingToReconcile(Refused):
    """The report or the tape for the month had not been filed by the day asked about."""


@dataclass(frozen=True)
class ReportLine:
    """A loan as the report's delinquency detail lists it."""

    loan: str
    status: str | None
    source_table: str
    accession: str

    def side(self) -> dict[str, Any]:
        return {
            "accession": self.accession,
            "source_table": self.source_table,
            "listed_as_delinquent": True,
            "status": self.status,
        }


@dataclass(frozen=True)
class TapeLine:
    """A loan's payment status on the tape; `status` None means not reported."""

    loan: str
    status: str | None
    accession: str

    def side(self) -> dict[str, Any]:
        return {"accession": self.accession, "payment_status": self.status}


@dataclass(frozen=True)
class Disagreement:
    loan: str
    message: str
    report: ReportLine | None
    tape: TapeLine | None

    def sides(self) -> dict[str, Any]:
        return {
            "report": self.report.side() if self.report else None,
            "tape": self.tape.side() if self.tape else None,
        }


def code(status: str | None) -> str | None:
    """A status as a code: whitespace dropped, letters upper case; None when not reported."""
    if status is None or not status.strip():
        return None
    return status.strip().upper()


def status_name(status: str) -> str:
    return PAYMENT_STATUS.get(status, f"payment status {status!r}, which is not a known code")


def _listed(report: ReportLine, tape: TapeLine | None) -> str | None:
    """Why a loan the report lists disagrees with the tape; None when they agree."""
    if tape is None:
        return "report says delinquent, tape does not list the loan"
    tape_code = code(tape.status)
    if tape_code is None:
        return "report says delinquent, tape reports no payment status"
    if tape_code not in BEHIND:
        return f"report says delinquent, tape says {status_name(tape_code)}"
    report_code = code(report.status)
    if report_code is not None and report_code in PAYMENT_STATUS and report_code != tape_code:
        return f"report says {status_name(report_code)}, tape says {status_name(tape_code)}"
    return None


def compare(
    report: Mapping[str, ReportLine], tape: Mapping[str, TapeLine], *, report_has_table: bool
) -> list[Disagreement]:
    """Every disagreement between the report's delinquency detail and the tape, by loan."""
    found = []
    for loan in sorted(report.keys() | tape.keys(), key=panel.loan_order):
        listed, row = report.get(loan), tape.get(loan)
        if listed is not None:
            message = _listed(listed, row)
        elif row is not None and report_has_table and code(row.status) in DELINQUENT:
            tape_code = code(row.status) or ""
            message = f"tape says {status_name(tape_code)}, report does not list the loan"
        else:
            message = None
        if message is not None:
            found.append(Disagreement(loan, message, listed, row))
    return found


def has_delinquency_table(administrator: str, summary: str, rows: int) -> bool:
    """Whether the report had a delinquency table: rows were read from it, or the parse did not
    note it missing ("no Delinquency Loan Detail table")."""
    if rows:
        return True
    specs = DELINQUENCY_TABLES.get(Administrator(administrator), ())
    return bool(specs) and not any(f"no {spec.name} table" in summary for spec in specs)


@dataclass(frozen=True)
class Reconciliation:
    deal: str
    period: Period
    report_accession: str
    tape_accession: str
    disagreements: tuple[Disagreement, ...]
    flags_raised: int

    @property
    def passed(self) -> bool:
        return not self.disagreements

    def text(self) -> str:
        head = (
            f"{self.deal} {self.period.label}: report {self.report_accession}, "
            f"tape {self.tape_accession}: "
        )
        if self.passed:
            return head + "the report and the tape agree"
        lines = [head + f"{len(self.disagreements)} loans disagree"]
        lines += [f"  loan {d.loan}: {d.message}" for d in self.disagreements]
        return "\n".join(lines)


def _latest_report(db: Session, deal_id: int, period: Period, day: dt.date) -> DistributionReport:
    report = db.scalars(
        select(DistributionReport)
        .join(Filing, Filing.accession == DistributionReport.accession)
        .where(
            DistributionReport.deal_id == deal_id,
            DistributionReport.period == period.first_day,
            Filing.filing_date <= day,
        )
        .order_by(Filing.filing_date.desc(), DistributionReport.accession.desc())
        .limit(1)
    ).one_or_none()
    if report is None:
        raise NothingToReconcile(f"no {period.label} distribution report had been filed by {day}")
    return report


def _report_lines(db: Session, accession: str) -> dict[str, ReportLine]:
    """The loans the report lists; a loan listed in two tables counts once (first table)."""
    rows = db.scalars(
        select(ReportDelinquency)
        .where(ReportDelinquency.accession == accession)
        .order_by(ReportDelinquency.loan_id, ReportDelinquency.source_table)
    )
    lines: dict[str, ReportLine] = {}
    for row in rows:
        lines.setdefault(
            row.loan_id, ReportLine(row.loan_id, row.status, row.source_table, accession)
        )
    return lines


def _already_flagged(db: Session, deal_id: int, period: Period, item: Disagreement) -> bool:
    return (
        db.scalars(
            select(CheckFlag.id).where(
                CheckFlag.deal_id == deal_id,
                CheckFlag.loan == item.loan,
                CheckFlag.period == period.first_day,
                CheckFlag.check == CHECK,
                CheckFlag.message == item.message,
            )
        ).first()
        is not None
    )


def reconcile(
    db: Session, *, deal_id: int, period: Period, as_of: dt.date | None = None
) -> Reconciliation:
    """Compare the month's report with its tape, as of `as_of` (today when not given), and flag
    each disagreement once. Refused when either had not been filed by then."""
    day = as_of or clock.today()
    deal = db.get_one(Deal, deal_id)
    report = _latest_report(db, deal_id, period, day)
    tapes = panel.current_tapes(db, day, deal_id=deal_id, period=period)
    if not tapes:
        raise NothingToReconcile(f"no {period.label} loan tape had been filed by {day}")
    tape_accession = tapes[0].accession
    tape = {
        row.loan: TapeLine(row.loan, row.payment_status, row.accession)
        for row in panel.as_of(db, day, deal_id=deal_id, period=period)
    }
    listed = _report_lines(db, report.accession)
    has_table = has_delinquency_table(report.administrator, report.summary, len(listed))
    disagreements = compare(listed, tape, report_has_table=has_table)
    raised = 0
    for item in disagreements:
        if _already_flagged(db, deal_id, period, item):
            continue
        raise_flag(
            db,
            deal_id=deal_id,
            loan=item.loan,
            check=CHECK,
            message=item.message,
            period=period.first_day,
            sides=item.sides(),
        )
        raised += 1
    return Reconciliation(
        deal=deal.name,
        period=period,
        report_accession=report.accession,
        tape_accession=tape_accession,
        disagreements=tuple(disagreements),
        flags_raised=raised,
    )


def flags(db: Session, *, deal_id: int, period: Period) -> Sequence[CheckFlag]:
    """The reconciliation flags raised for a deal-month, oldest first."""
    return db.scalars(
        select(CheckFlag)
        .where(
            CheckFlag.deal_id == deal_id,
            CheckFlag.period == period.first_day,
            CheckFlag.check == CHECK,
        )
        .order_by(CheckFlag.id)
    ).all()


def reconcile_month(
    db: Session, *, user: str, deal: str, period: Period, as_of: dt.date | None = None
) -> Reconciliation:
    """`reconcile` for a user who may load filings, audited; refusals are audited too."""
    actor = authorize(db, user, Action.RUN_REFRESH)
    try:
        deal_row = by_name(db, deal)
        if deal_row is None:
            raise UnknownDeal(f"no deal named {deal!r}")
        result = reconcile(db, deal_id=deal_row.id, period=period, as_of=as_of)
    except Refused as refusal:
        refuse(
            db,
            refusal,
            user=actor.user,
            role=actor.role.value,
            action=AUDIT_ACTION,
            subject="deal",
            subject_key=deal,
        )
    record(
        db,
        user=actor.user,
        role=actor.role.value,
        action=AUDIT_ACTION,
        subject="deal",
        subject_key=deal_row.name,
        detail={
            "period": period.label,
            "report": result.report_accession,
            "tape": result.tape_accession,
            "disagreements": len(result.disagreements),
            "flags_raised": result.flags_raised,
        },
    )
    return result
