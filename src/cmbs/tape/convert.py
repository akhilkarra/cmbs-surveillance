"""Turn a reported asset into a typed loan-period row, or say why it cannot be typed.

Pure functions, no database. Every value keeps "not reported" apart from zero: an empty or
missing tag becomes None, never 0 or "". A value that is reported but cannot be read (a word
where an amount belongs, a date that does not exist) is a reason to quarantine the loan, never
something to guess at.
"""

import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass, fields
from decimal import Decimal

from cmbs.absee.schedule_al import Asset

# XML Schema decimals, the type Schedule AL declares: "50000000.00", "-12000000", "0.88", and
# also "+1", "5." and ".00000000" (BANK5 2026-5YR23 writes zero amounts that way). No exponent,
# no thousands separators.
_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
CENT = Decimal("0.01")


@dataclass(frozen=True)
class LoanPeriodRow:
    """One top-level loan as one tape reports it."""

    loan: str
    reporting_period_end: dt.date | None
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
    property_type: str | None
    property_name: str | None
    financials_start: dt.date | None
    financials_end: dt.date | None
    noi: Decimal | None
    dscr: Decimal | None
    occupancy: Decimal | None


ROW_FIELDS = tuple(field.name for field in fields(LoanPeriodRow))


class Unreadable(ValueError):
    """A reported value that cannot be read; the message is the quarantine reason."""


def read_code(text: str, label: str) -> str | None:
    return text or None


def read_decimal(text: str, label: str) -> Decimal | None:
    if not text:
        return None
    if not _NUMBER.match(text):
        raise Unreadable(f"{label} is not a number: {text!r}")
    return Decimal(text)


def read_money(text: str, label: str) -> Decimal | None:
    """Dollars and cents. A third decimal place is refused, never rounded away."""
    value = read_decimal(text, label)
    if value is None:
        return None
    if value != value.quantize(CENT):
        raise Unreadable(f"{label} has more than two decimal places: {text!r}")
    return value.quantize(CENT)


def read_date(text: str, label: str) -> dt.date | None:
    """MM-DD-YYYY, as Schedule AL writes dates."""
    if not text:
        return None
    try:
        return dt.datetime.strptime(text, "%m-%d-%Y").date()
    except ValueError:
        raise Unreadable(f"{label} is not a date: {text!r}") from None


@dataclass(frozen=True)
class Source:
    """Where a row field comes from: a Schedule AL tag, on the loan or on its property."""

    tag: str
    label: str
    read: Callable[[str, str], object]
    on_property: bool = False

    def value(self, asset: Asset) -> object:
        text = asset.property_tag(self.tag) if self.on_property else asset.tag(self.tag)
        return self.read(text, self.label)


# In row order, so reasons come out in the order the fields are listed.
SOURCES: dict[str, Source] = {
    "reporting_period_end": Source("reportingPeriodEndDate", "reporting period end", read_date),
    "beginning_balance": Source(
        "reportPeriodBeginningScheduleLoanBalanceAmount", "beginning balance", read_money
    ),
    "scheduled_principal": Source("scheduledPrincipalAmount", "scheduled principal", read_money),
    "unscheduled_principal": Source(
        "unscheduledPrincipalCollectedAmount", "unscheduled principal", read_money
    ),
    "other_principal_adjustments": Source(
        "otherPrincipalAdjustmentAmount", "other principal adjustments", read_money
    ),
    "realized_loss": Source("realizedLossToTrustAmount", "realized loss", read_money),
    "ending_balance": Source("reportPeriodEndActualBalanceAmount", "ending balance", read_money),
    "paid_through_date": Source("paidThroughDate", "paid-through date", read_date),
    "payment_status": Source("paymentStatusLoanCode", "payment status", read_code),
    "special_servicer_transfer_date": Source(
        "mostRecentSpecialServicerTransferDate", "special servicer transfer date", read_date
    ),
    "master_servicer_return_date": Source(
        "mostRecentMasterServicerReturnDate", "master servicer return date", read_date
    ),
    "workout_strategy": Source("workoutStrategyCode", "workout strategy", read_code),
    "modification_code": Source("modificationCode", "modification code", read_code),
    "property_type": Source("propertyTypeCode", "property type", read_code, on_property=True),
    "property_name": Source("propertyName", "property name", read_code, on_property=True),
    "financials_start": Source(
        "mostRecentFinancialsStartDate", "most recent financials start", read_date, True
    ),
    "financials_end": Source(
        "mostRecentFinancialsEndDate", "most recent financials end", read_date, True
    ),
    "noi": Source("mostRecentNetOperatingIncomeAmount", "most recent NOI", read_money, True),
    "dscr": Source(
        "mostRecentDebtServiceCoverageNetOperatingIncomePercentage",
        "most recent DSCR",
        read_decimal,
        True,
    ),
    "occupancy": Source(
        "mostRecentPhysicalOccupancyPercentage", "most recent occupancy", read_decimal, True
    ),
}


def to_row(asset: Asset) -> LoanPeriodRow | list[str]:
    """The typed row, or every reason it cannot be typed, in field order."""
    values: dict[str, object] = {"loan": asset.asset_number}
    reasons = [] if asset.asset_number else ["loan number is not reported"]
    for name, source in SOURCES.items():
        try:
            values[name] = source.value(asset)
        except Unreadable as error:
            reasons.append(str(error))
    if reasons:
        return reasons
    return LoanPeriodRow(**values)  # pyright: ignore[reportArgumentType]
