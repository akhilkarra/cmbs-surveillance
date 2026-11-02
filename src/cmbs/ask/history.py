"""A loan's history month by month, as each month's tape was filed (grill log Q98, Q99).
Read-only.

One entry per month of the deal's tapes on the day asked about (each month's current tape filed
by then, `cmbs.panel.service.current_tapes`), from the first that names the loan, oldest first,
each citing that month's ABS-EE. An entry gives the month's property name, ending balance,
payment status, DSCR, occupancy and financials date exactly as reported: nothing is computed, and
a value the month did not report reads "not reported", never zero. The property name shows a
month whose tape is another deal's (docs/MESS-REGISTER.md M-029).

No month is skipped (Q99): a month whose tape names the loan but whose load quarantined it reads
"quarantined: <reason>" (with the property name its stored asset gives), and a month whose tape
omits it reads "not on the tape".
"""

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import select
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
from cmbs.tape.tables import LoanPeriod, LoanTape, QuarantinedLoan

NOT_REPORTED = "not reported"
NOT_ON_TAPE = "not on the tape"


@dataclass(frozen=True)
class Column:
    column: str
    label: str
    tag: str


# What each month gives, in the order an entry reads.
COLUMNS = (
    Column("property_name", "property", "propertyName"),
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
    # Why the month's tape gives no row of the loan; None when it does.
    note: str | None = None

    def reading(self) -> str:
        """'property Lakeside Hotel; ending balance $30,000,000.00; payment status 30 days
        (code 1); ...', or 'quarantined: <reason>; property ...', or 'not on the tape'."""
        parts = [value.text() for value in self.values]
        return "; ".join(parts if self.note is None else [self.note, *parts])

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


def _citation(accession: str, filing_date: dt.date, cik: int) -> Citation:
    return Citation(
        form=TAPE_FORM,
        accession=accession,
        filing_date=filing_date,
        url=filing_base_url(cik, accession),
    )


def _named(period: Period) -> tuple[str, str]:
    return period.label, f"{period.year:04d}-{period.month:02d}"


def month_of(row: LoanPeriod, cik: int) -> Month:
    period, month = _named(Period.of(row.period))
    values = []
    for spec in COLUMNS:
        value = getattr(row, spec.column)
        values.append(
            Value(spec.label, None if value is None else tape_value(spec.column, value), spec.tag)
        )
    return Month(period, month, values, _citation(row.accession, row.filing_date, cik))


def missing_month(
    tape: LoanTape, cik: int, reasons: list[str] | None, property_name: str | None
) -> Month:
    """A month whose tape quarantined the loan (with the reasons and the asset's property
    name), or omits it (`reasons` None)."""
    period, month = _named(Period.of(tape.period))
    cited = _citation(tape.accession, tape.filing_date, cik)
    if reasons is None:
        return Month(period, month, [], cited, NOT_ON_TAPE)
    values = [Value("property", property_name, "propertyName")]
    return Month(period, month, values, cited, f"quarantined: {'; '.join(reasons)}")


def history(
    db: Session, reader: str, *, deal: str, loan: str, as_of: dt.date | None = None
) -> LoanHistory:
    """The loan's months as the panel holds them on `as_of` (today by default), oldest first,
    from the first tape that names it to the latest."""
    panel.snapshot(db)
    authorize(db, reader, Action.ASK)
    day = as_of or clock.today()
    deal_row = by_name(db, deal)
    if deal_row is None:
        raise UnknownDeal(f"no deal named {deal!r}")
    tapes = panel.current_tapes(db, day, deal_id=deal_row.id)
    rows = {row.accession: row for row in panel.as_of(db, day, deal_id=deal_row.id, loan=loan)}
    held: dict[str, list[str]] = {}
    for item in db.scalars(
        select(QuarantinedLoan)
        .where(
            QuarantinedLoan.deal_id == deal_row.id,
            QuarantinedLoan.loan == loan,
            QuarantinedLoan.accession.in_([tape.accession for tape in tapes]),
        )
        .order_by(QuarantinedLoan.accession, QuarantinedLoan.asset_position)
    ):
        reasons = held.setdefault(item.accession, [])
        if item.reason not in reasons:
            reasons.append(item.reason)
    named = [i for i, tape in enumerate(tapes) if tape.accession in rows or tape.accession in held]
    if not named:
        raise UnknownLoan(f"the panel holds no month of loan {loan} of {deal_row.name} by {day}")
    cik = deal_row.cik or 0
    months = []
    for tape in tapes[named[0] :]:
        row = rows.get(tape.accession)
        if row is not None:
            months.append(month_of(row, cik))
            continue
        reasons = held.get(tape.accession)
        name = None
        if reasons is not None:
            name = panel.property_names(db, tape.accession, {loan}).get(loan)
        months.append(missing_month(tape, cik, reasons, name))
    return LoanHistory(deal_row.name, loan, day, months)
