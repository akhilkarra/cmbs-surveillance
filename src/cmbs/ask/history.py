"""A loan's history month by month, as each month's tape was filed (grill log Q98). Read-only.

One entry per month the panel holds for the loan on the day asked about
(`cmbs.panel.service.as_of`: each month's current tape filed by then), oldest first, each citing
that month's ABS-EE. Each entry gives the month's ending balance, payment status, DSCR,
occupancy and financials date exactly as reported: nothing is computed, and a value the month
did not report reads "not reported", never zero.
"""

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.access.roles import Action
from cmbs.access.service import authorize
from cmbs.ask.answer import TAPE_FORM, Citation
from cmbs.ask.service import UnknownDeal, UnknownLoan, tape_value
from cmbs.deals.service import by_name
from cmbs.domain import Period
from cmbs.edgar.archives import filing_base_url
from cmbs.panel import service as panel
from cmbs.tape.tables import LoanPeriod

NOT_REPORTED = "not reported"


@dataclass(frozen=True)
class Column:
    column: str
    label: str
    tag: str


# What each month gives, in the order an entry reads.
COLUMNS = (
    Column("ending_balance", "ending balance", "reportPeriodEndActualBalanceAmount"),
    Column("payment_status", "payment status", "paymentStatusLoanCode"),
    Column("dscr", "DSCR", "mostRecentDebtServiceCoverageNetOperatingIncomePercentage"),
    Column("occupancy", "occupancy", "mostRecentPhysicalOccupancyPercentage"),
    Column("financials_end", "financials as of", "mostRecentFinancialsEndDate"),
)


@dataclass(frozen=True)
class Value:
    label: str
    value: str | None
    tape_field: str

    def text(self) -> str:
        return f"{self.label} {NOT_REPORTED if self.value is None else self.value}"


@dataclass(frozen=True)
class Month:
    period: str
    month: str
    values: list[Value]
    citation: Citation

    def reading(self) -> str:
        """'ending balance $30,000,000.00; payment status 30 days (code 1); DSCR 0.96x; ...'."""
        return "; ".join(value.text() for value in self.values)

    def text(self) -> str:
        cited = f"{self.citation.form} {self.citation.accession}"
        return f"{self.period}: {self.reading()} ({cited}, filed {self.citation.filing_date})"


@dataclass(frozen=True)
class LoanHistory:
    deal: str
    loan: str
    as_of: dt.date
    months: list[Month] = field(default_factory=list)

    def text(self) -> str:
        head = f"loan {self.loan} of {self.deal}, month by month as filed by {self.as_of}:"
        return "\n".join([head, *(f"- {month.text()}" for month in self.months)])


def month_of(row: LoanPeriod, cik: int) -> Month:
    period = Period.of(row.period)
    values = []
    for spec in COLUMNS:
        value = getattr(row, spec.column)
        values.append(
            Value(spec.label, None if value is None else tape_value(spec.column, value), spec.tag)
        )
    return Month(
        period=period.label,
        month=f"{period.year:04d}-{period.month:02d}",
        values=values,
        citation=Citation(
            form=TAPE_FORM,
            accession=row.accession,
            filing_date=row.filing_date,
            url=filing_base_url(cik, row.accession),
        ),
    )


def history(
    db: Session, reader: str, *, deal: str, loan: str, as_of: dt.date | None = None
) -> LoanHistory:
    """The loan's months as the panel holds them on `as_of` (today by default), oldest first."""
    authorize(db, reader, Action.ASK)
    day = as_of or clock.today()
    deal_row = by_name(db, deal)
    if deal_row is None:
        raise UnknownDeal(f"no deal named {deal!r}")
    rows = panel.as_of(db, day, deal_id=deal_row.id, loan=loan)
    if not rows:
        raise UnknownLoan(f"the panel holds no month of loan {loan} of {deal_row.name} by {day}")
    cik = deal_row.cik or 0
    return LoanHistory(deal_row.name, loan, day, [month_of(row, cik) for row in rows])
