"""Point-in-time reads of a deal's loans and their financials staleness.

`as_of` defaults to today. Money and ratios are decimals, sent as strings so no digit is lost.
"""

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter
from pydantic import BaseModel

from cmbs import clock
from cmbs.access.roles import Action
from cmbs.access.service import authorize
from cmbs.analysis.staleness import LoanStaleness, StalenessSummary
from cmbs.api.deps import DB, User
from cmbs.panel import service
from cmbs.panel.service import DealTape, NothingPublished, Superseded
from cmbs.tape.tables import LoanPeriod

router = APIRouter(prefix="/deals", tags=["panel"])


class LoanRow(BaseModel):
    loan: str
    period: dt.date
    accession: str
    filing_date: dt.date
    file_sha256: str
    reporting_period_end: dt.date
    beginning_balance: Decimal | None
    scheduled_principal: Decimal | None
    unscheduled_principal: Decimal | None
    other_principal_adjustments: Decimal | None
    realized_loss: Decimal | None
    ending_balance: Decimal | None
    paid_through_date: dt.date | None
    payment_status: str | None
    special_servicer_transfer_date: dt.date | None
    master_servicer_return_date: dt.date | None
    workout_strategy: str | None
    modification_code: str | None
    property_count: int
    property_type: str | None
    property_name: str | None
    defeased_status: str | None
    financials_start: dt.date | None
    financials_end: dt.date | None
    financials_end_newest: dt.date | None
    noi: Decimal | None
    ncf: Decimal | None
    dscr: Decimal | None
    occupancy: Decimal | None
    property_note: str | None

    @classmethod
    def of(cls, row: LoanPeriod) -> "LoanRow":
        return cls.model_validate({name: getattr(row, name) for name in cls.model_fields})


class SupersededRow(BaseModel):
    """A loan an earlier tape reported that the current tape leaves out, and why."""

    loan: str
    accession: str
    filing_date: dt.date
    superseded_by: str
    reason: str

    @classmethod
    def of(cls, row: Superseded) -> "SupersededRow":
        return cls(
            loan=row.loan,
            accession=row.accession,
            filing_date=row.filing_date,
            superseded_by=row.superseded_by,
            reason=row.reason,
        )


class DealLoans(BaseModel):
    deal: str
    as_of: dt.date
    period: str | None
    accession: str | None
    loans: list[LoanRow]
    superseded: list[SupersededRow]


class LoanStalenessRow(BaseModel):
    loan: str
    status: str
    financials_end: dt.date | None
    days_vs_period_end: int | None
    days_vs_filing: int | None

    @classmethod
    def of(cls, loan: LoanStaleness) -> "LoanStalenessRow":
        return cls(
            loan=loan.loan,
            status=loan.status,
            financials_end=loan.financials_end,
            days_vs_period_end=loan.days_vs_period_end,
            days_vs_filing=loan.days_vs_filing,
        )


class SummaryRow(BaseModel):
    active_loans: int
    loans_with_financials: int
    loans_without_financials: int
    median_days: int | None
    p90_days: int | None
    over_six_months: int
    median_days_vs_filing: int | None
    note: str | None

    @classmethod
    def of(cls, summary: StalenessSummary) -> "SummaryRow":
        return cls(
            active_loans=summary.active_loans,
            loans_with_financials=summary.loans_with_financials,
            loans_without_financials=summary.loans_without_financials,
            median_days=summary.median_days,
            p90_days=summary.p90_days,
            over_six_months=summary.over_six_months,
            median_days_vs_filing=summary.median_days_vs_filing,
            note=summary.note,
        )


class DealStalenessResponse(BaseModel):
    deal: str
    as_of: dt.date
    period: str
    summary: SummaryRow
    loans: list[LoanStalenessRow]


@router.get("/{deal}/loans")
def deal_loans(user: User, db: DB, deal: str, as_of: dt.date | None = None) -> DealLoans:
    """The deal's latest period as published on `as_of`; no loans if nothing was filed yet."""
    authorize(db, user, Action.READ_PANEL)
    day = as_of or clock.today()
    try:
        tape: DealTape | None = service.tape_as_of(db, deal, day)
    except NothingPublished:
        tape = None
    return DealLoans(
        deal=deal,
        as_of=day,
        period=tape.period.label if tape else None,
        accession=tape.accession if tape else None,
        loans=[LoanRow.of(row) for row in tape.rows] if tape else [],
        superseded=[SupersededRow.of(row) for row in tape.superseded] if tape else [],
    )


@router.get("/{deal}/staleness")
def deal_staleness(
    user: User, db: DB, deal: str, as_of: dt.date | None = None
) -> DealStalenessResponse:
    """How old each loan's most recent financials were on `as_of`, with the deal summary."""
    authorize(db, user, Action.READ_PANEL)
    tape, report = service.staleness(db, deal, as_of or clock.today())
    return DealStalenessResponse(
        deal=deal,
        as_of=tape.as_of,
        period=tape.period.label,
        summary=SummaryRow.of(report.summary),
        loans=[LoanStalenessRow.of(loan) for loan in report.loans],
    )
