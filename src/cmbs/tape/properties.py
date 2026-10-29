"""Property-level values of a loan, and how several properties combine into one loan row.

Pure functions, no database. A loan's `<property>` elements are each one real property, never
an aggregate. Some shelves (BANK 2018) split a portfolio into sub-assets ("4-001") and give
the parent loan a single property; newer ones (BANK5 2026) nest every property of a portfolio
inside the loan's asset. A loan with one property is read as is. For a loan with several:

- NOI and NCF are the sum over properties, only when every property reports one for the same
  statement period. Otherwise they are not reported, and the row says why.
- DSCR is total NOI over total debt service, only when every property reports both. One
  property's DSCR never stands in for the loan's.
- Occupancy is not reported: neither a sum nor an average of physical occupancy is meaningful.
- The financials end date is the oldest one any property reports (the conservative choice for
  staleness); the newest is kept beside it.
- The property type is the common type, or "MU" (CREFC mixed use) when they differ, and the
  name is "Portfolio (N properties)".
- Defeasance is the common status; "P" (partial) when only some properties are defeased.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

MIXED_USE = "MU"
FULLY_DEFEASED = "F"
PARTIALLY_DEFEASED = "P"
DSCR_PLACES = Decimal("0.0001")

NOT_REPORTED = "so the loan's NOI, NCF and DSCR are not reported"
MISSING_FINANCIALS = (
    "{missing} of {count} properties report no most recent financials, " + NOT_REPORTED
)
DIFFERENT_PERIODS = "properties report financials for different periods, " + NOT_REPORTED
MISSING_NOI = "not every property reports NOI, so the loan's NOI and DSCR are not reported"
MISSING_NCF = "not every property reports NCF, so the loan's NCF is not reported"
MISSING_DEBT_SERVICE = "not every property reports debt service, so the loan's DSCR is not reported"
NO_DEBT_SERVICE = "total debt service is not positive, so the loan's DSCR is not reported"


@dataclass(frozen=True)
class PropertyValues:
    """One property's most recent values, typed."""

    property_type: str | None = None
    property_name: str | None = None
    defeased_status: str | None = None
    financials_start: dt.date | None = None
    financials_end: dt.date | None = None
    noi: Decimal | None = None
    ncf: Decimal | None = None
    debt_service: Decimal | None = None
    dscr: Decimal | None = None
    occupancy: Decimal | None = None


@dataclass(frozen=True)
class LoanProperties:
    """The property values a loan row carries."""

    property_count: int
    property_type: str | None = None
    property_name: str | None = None
    defeased_status: str | None = None
    financials_start: dt.date | None = None
    financials_end: dt.date | None = None
    financials_end_newest: dt.date | None = None
    noi: Decimal | None = None
    ncf: Decimal | None = None
    dscr: Decimal | None = None
    occupancy: Decimal | None = None
    note: str | None = None


def single(values: PropertyValues) -> LoanProperties:
    return LoanProperties(
        property_count=1,
        property_type=values.property_type,
        property_name=values.property_name,
        defeased_status=values.defeased_status,
        financials_start=values.financials_start,
        financials_end=values.financials_end,
        financials_end_newest=values.financials_end,
        noi=values.noi,
        ncf=values.ncf,
        dscr=values.dscr,
        occupancy=values.occupancy,
    )


def common_type(types: Sequence[str | None]) -> str | None:
    reported = {kind for kind in types if kind is not None}
    if len(reported) > 1:
        return MIXED_USE
    return reported.pop() if reported else None


def common_defeasance(statuses: Sequence[str | None]) -> str | None:
    if len(set(statuses)) == 1:
        return statuses[0]
    if any(status in (FULLY_DEFEASED, PARTIALLY_DEFEASED) for status in statuses):
        return PARTIALLY_DEFEASED
    return None


def total(values: Sequence[Decimal | None]) -> Decimal | None:
    """The sum when every value is reported, otherwise not reported."""
    reported = [value for value in values if value is not None]
    return sum(reported, Decimal(0)) if len(reported) == len(values) else None


@dataclass(frozen=True)
class Financials:
    noi: Decimal | None = None
    ncf: Decimal | None = None
    dscr: Decimal | None = None
    notes: tuple[str, ...] = ()


def portfolio_financials(properties: Sequence[PropertyValues]) -> Financials:
    """NOI, NCF and DSCR of a loan with several properties, with why any is not reported."""
    count = len(properties)
    missing = sum(p.financials_end is None for p in properties)
    if missing == count:
        return Financials()  # nothing reported yet, as for a new issue: nothing to explain
    if missing:
        return Financials(notes=(MISSING_FINANCIALS.format(missing=missing, count=count),))
    if len({(p.financials_start, p.financials_end) for p in properties}) > 1:
        return Financials(notes=(DIFFERENT_PERIODS,))
    noi = total([p.noi for p in properties])
    ncf = total([p.ncf for p in properties])
    debt_service = total([p.debt_service for p in properties])
    notes = []
    dscr = None
    if noi is None:
        notes.append(MISSING_NOI)
    elif debt_service is None:
        notes.append(MISSING_DEBT_SERVICE)
    elif debt_service <= 0:
        notes.append(NO_DEBT_SERVICE)
    else:
        dscr = (noi / debt_service).quantize(DSCR_PLACES, rounding=ROUND_HALF_UP)
    if ncf is None:
        notes.append(MISSING_NCF)
    return Financials(noi=noi, ncf=ncf, dscr=dscr, notes=tuple(notes))


def combine(properties: Sequence[PropertyValues]) -> LoanProperties:
    if not properties:
        return LoanProperties(property_count=0)
    if len(properties) == 1:
        return single(properties[0])
    count = len(properties)
    dated = sorted(
        (p for p in properties if p.financials_end is not None),
        key=lambda p: (p.financials_end, p.financials_start or dt.date.min),
    )
    financials = portfolio_financials(properties)
    return LoanProperties(
        property_count=count,
        property_type=common_type([p.property_type for p in properties]),
        property_name=f"Portfolio ({count} properties)",
        defeased_status=common_defeasance([p.defeased_status for p in properties]),
        financials_start=dated[0].financials_start if dated else None,
        financials_end=dated[0].financials_end if dated else None,
        financials_end_newest=dated[-1].financials_end if dated else None,
        noi=financials.noi,
        ncf=financials.ncf,
        dscr=financials.dscr,
        occupancy=None,
        note="; ".join(financials.notes) or None,
    )
