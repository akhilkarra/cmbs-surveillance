"""Turn a reported asset into a typed loan-period row, or say why it cannot be typed.

Pure functions, no database. Every value keeps "not reported" apart from zero: an empty or
missing tag becomes None, never 0 or "". A value that is reported but cannot be read (a word
where an amount belongs, a date that does not exist) is a reason to quarantine the loan, never
something to guess at. Property values are read from every `<property>` of the loan and
combined by `cmbs.tape.properties`.
"""

import datetime as dt
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from decimal import Decimal

from cmbs.absee.schedule_al import Asset
from cmbs.tape.properties import PropertyValues, combine

# XML Schema decimals, the type Schedule AL declares: "50000000.00", "-12000000", "0.88", and
# also "+1", "5." and ".00000000" (BANK5 2026-5YR23 writes zero amounts that way). No exponent,
# no thousands separators.
_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
CENT = Decimal("0.01")
# Money columns are numeric(18, 2): an amount must be below 10^16 in size to be stored.
MONEY_LIMIT = Decimal(10) ** 16


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
    # The rate for the period as a fraction (0.03768 is 3.768%), every digit as reported.
    interest_rate: Decimal | None
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
    """Where a value comes from: a Schedule AL tag, and how to read it."""

    tag: str
    label: str
    read: Callable[[str, str], object]


# In row order, so reasons come out in the order the fields are listed.
LOAN_SOURCES: dict[str, Source] = {
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
    "interest_rate": Source("reportPeriodInterestRatePercentage", "interest rate", read_decimal),
}
PROPERTY_SOURCES: dict[str, Source] = {
    "property_type": Source("propertyTypeCode", "property type", read_code),
    "property_name": Source("propertyName", "property name", read_code),
    "defeased_status": Source("DefeasedStatusCode", "defeased status", read_code),
    "financials_start": Source(
        "mostRecentFinancialsStartDate", "most recent financials start", read_date
    ),
    "financials_end": Source(
        "mostRecentFinancialsEndDate", "most recent financials end", read_date
    ),
    "noi": Source("mostRecentNetOperatingIncomeAmount", "most recent NOI", read_money),
    "ncf": Source("mostRecentNetCashFlowAmount", "most recent NCF", read_money),
    "debt_service": Source("mostRecentDebtServiceAmount", "most recent debt service", read_money),
    "dscr": Source(
        "mostRecentDebtServiceCoverageNetOperatingIncomePercentage",
        "most recent DSCR",
        read_decimal,
    ),
    "occupancy": Source(
        "mostRecentPhysicalOccupancyPercentage", "most recent occupancy", read_decimal
    ),
}

# The money fields of a loan row, in row order, with the label a quarantine reason uses. A
# portfolio's NOI and NCF are sums, so they are checked on the row, not only as read.
MONEY_LABELS = {
    name: source.label
    for name, source in (LOAN_SOURCES | PROPERTY_SOURCES).items()
    if source.read is read_money and name in ROW_FIELDS
}
OUT_OF_ORDER = "most recent financials end before they start"


def _read(sources: dict[str, Source], tags: Mapping[str, str], prefix: str, reasons: list[str]):
    values: dict[str, object] = {}
    for name, source in sources.items():
        try:
            values[name] = source.read(tags.get(source.tag, "").strip(), prefix + source.label)
        except Unreadable as error:
            reasons.append(str(error))
    return values


def read_property(tags: Mapping[str, str], prefix: str, reasons: list[str]) -> PropertyValues:
    """One property's values. With several properties, a property whose own statement ends
    before it starts is a reason too: the loan row would not show which property it was."""
    values = _read(PROPERTY_SOURCES, tags, prefix, reasons)
    start, end = values.get("financials_start"), values.get("financials_end")
    if prefix and isinstance(start, dt.date) and isinstance(end, dt.date) and end < start:
        reasons.append(prefix + OUT_OF_ORDER)
    return PropertyValues(**values)  # pyright: ignore[reportArgumentType]


def to_row(asset: Asset) -> LoanPeriodRow | list[str]:
    """The typed row, or every reason it cannot be typed, in field order."""
    reasons = [] if asset.asset_number else ["loan number is not reported"]
    values = _read(LOAN_SOURCES, asset.tags, "", reasons)
    several = len(asset.properties) > 1
    properties = [
        read_property(tags, f"property {number} " if several else "", reasons)
        for number, tags in enumerate(asset.properties, start=1)
    ]
    if reasons:
        return reasons
    combined = combine(properties)
    row = LoanPeriodRow(
        loan=asset.asset_number,
        **values,  # pyright: ignore[reportArgumentType]
        property_count=combined.property_count,
        property_type=combined.property_type,
        property_name=combined.property_name,
        defeased_status=combined.defeased_status,
        financials_start=combined.financials_start,
        financials_end=combined.financials_end,
        financials_end_newest=combined.financials_end_newest,
        noi=combined.noi,
        ncf=combined.ncf,
        dscr=combined.dscr,
        occupancy=combined.occupancy,
        property_note=combined.note,
    )
    too_large = [
        f"{label} is too large to store"
        for name, label in MONEY_LABELS.items()
        if (value := getattr(row, name)) is not None and abs(value) >= MONEY_LIMIT
    ]
    return too_large or row
