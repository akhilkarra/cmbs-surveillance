"""How old the "most recent financials" in an ABS-EE loan tape are.

- Excluded loans: defeased, residential co-ops (property type "CH", which report no operating
  statements), paid off (ending balance zero) and loans whose balance is not reported. The
  rest are active.
- Staleness: reporting period end minus the most recent financials end date, in days. The
  same gap is also measured from the filing date, which is when a point-in-time user first
  sees the tape.
- An active loan without most-recent financials is listed as "no updated financials". It is
  never counted as zero days and stays out of the percentiles.

Pure functions over loans from either source: `LoanRecord`s parsed from a file on disk, or
`TapeLoan`s read from the point-in-time panel.
"""

import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction
from pathlib import Path
from typing import Protocol

from cmbs.absee.schedule_al import LoanRecord, iter_loans

COOPERATIVE_HOUSING = "CH"
DEFEASED = "Defeased"
FULLY_DEFEASED = "F"
SIX_MONTHS_DAYS = 183
NO_UPDATED_FINANCIALS = "no updated financials"
AT_SECURITIZATION_ONLY = "only at-securitization financials so far"


class Exclusion(StrEnum):
    DEFEASED = "defeased"
    COOPERATIVE = "co-op"
    BALANCE_NOT_REPORTED = "balance not reported"
    PAID_OFF = "paid off"


class ReportedLoan(Protocol):
    @property
    def ending_balance(self) -> Decimal | float | None: ...
    @property
    def property_name(self) -> str | None: ...
    @property
    def property_type(self) -> str | None: ...
    @property
    def defeased_status(self) -> str | None: ...


def exclusion(loan: ReportedLoan) -> Exclusion | None:
    """Why a loan is left out of staleness, or None when it is active.

    Defeased means fully defeased: DefeasedStatusCode "F", or the property name "Defeased" that
    servicers also use. A partially defeased loan ("P") still reports and stays active.
    """
    if loan.property_name == DEFEASED or loan.defeased_status == FULLY_DEFEASED:
        return Exclusion.DEFEASED
    if loan.property_type == COOPERATIVE_HOUSING:
        return Exclusion.COOPERATIVE
    if loan.ending_balance is None:
        return Exclusion.BALANCE_NOT_REPORTED
    if loan.ending_balance <= 0:
        return Exclusion.PAID_OFF
    return None


def is_active(loan: ReportedLoan) -> bool:
    return exclusion(loan) is None


def nearest_rank_percentile(values: Sequence[int], q: float) -> int | None:
    """The nearest-rank percentile: the ceil(q * n)-th smallest value, for q in (0, 1].

    q is read as the decimal it is written as (0.9, not the float just above it), so
    ceil(0.9 * 10) is 9. None for no values.
    """
    if not 0 < q <= 1:
        raise ValueError(f"a percentile is between 0 (excluded) and 1, not {q}")
    if not values:
        return None
    rank = math.ceil(Fraction(str(q)) * len(values))
    return sorted(values)[rank - 1]


@dataclass(frozen=True)
class DealStaleness:
    active_loans: int
    days_vs_period_end: list[int]
    days_vs_filing: list[int]

    @property
    def loans_with_financials(self) -> int:
        return len(self.days_vs_period_end)

    @property
    def over_six_months(self) -> int:
        return sum(days > SIX_MONTHS_DAYS for days in self.days_vs_period_end)


def deal_staleness(loans: Sequence[LoanRecord], filing_date: dt.date) -> DealStaleness:
    active = [loan for loan in loans if is_active(loan)]
    with_financials = [loan for loan in active if loan.financials_end and loan.reporting_period_end]
    return DealStaleness(
        active_loans=len(active),
        days_vs_period_end=[
            (loan.reporting_period_end - loan.financials_end).days  # type: ignore[operator]
            for loan in with_financials
        ],
        days_vs_filing=[
            (filing_date - loan.financials_end).days  # type: ignore[operator]
            for loan in with_financials
        ],
    )


def file_staleness(path: Path | str, filing_date: dt.date) -> DealStaleness:
    return deal_staleness(list(iter_loans(path)), filing_date)


@dataclass(frozen=True)
class TapeLoan:
    """A loan as the panel shows it: values from one filing, with that filing's date."""

    loan: str
    ending_balance: Decimal | None
    property_name: str | None
    property_type: str | None
    defeased_status: str | None
    reporting_period_end: dt.date
    financials_end: dt.date | None
    filing_date: dt.date


@dataclass(frozen=True)
class LoanStaleness:
    loan: str
    excluded: Exclusion | None
    financials_end: dt.date | None
    days_vs_period_end: int | None
    days_vs_filing: int | None

    @property
    def status(self) -> str:
        if self.excluded is not None:
            return f"excluded: {self.excluded}"
        if self.days_vs_period_end is None:
            return NO_UPDATED_FINANCIALS
        return (
            f"{self.days_vs_period_end} days old at period end, "
            f"{self.days_vs_filing} days old at filing"
        )


def loan_staleness(loan: TapeLoan) -> LoanStaleness:
    excluded = exclusion(loan)
    end = loan.financials_end
    if excluded is not None or end is None:
        return LoanStaleness(loan.loan, excluded, end, None, None)
    return LoanStaleness(
        loan=loan.loan,
        excluded=None,
        financials_end=end,
        days_vs_period_end=(loan.reporting_period_end - end).days,
        days_vs_filing=(loan.filing_date - end).days,
    )


@dataclass(frozen=True)
class StalenessSummary:
    active_loans: int
    loans_with_financials: int
    loans_without_financials: int
    median_days: int | None
    p90_days: int | None
    over_six_months: int
    median_days_vs_filing: int | None

    @property
    def note(self) -> str | None:
        if self.active_loans and not self.loans_with_financials:
            return AT_SECURITIZATION_ONLY
        return None

    def text(self) -> str:
        parts = [
            f"{self.active_loans} active loans",
            f"{self.loans_with_financials} with updated financials",
            f"{self.loans_without_financials} without",
        ]
        if self.loans_with_financials:
            parts += [
                f"median {self.median_days} days",
                f"90th percentile {self.p90_days} days",
                f"{self.over_six_months} over six months",
                f"median {self.median_days_vs_filing} days at filing",
            ]
        if self.note:
            parts.append(self.note)
        return ", ".join(parts)


def summarize(loans: Sequence[LoanStaleness]) -> StalenessSummary:
    active = [loan for loan in loans if loan.excluded is None]
    at_period_end = [
        loan.days_vs_period_end for loan in active if loan.days_vs_period_end is not None
    ]
    at_filing = [loan.days_vs_filing for loan in active if loan.days_vs_filing is not None]
    return StalenessSummary(
        active_loans=len(active),
        loans_with_financials=len(at_period_end),
        loans_without_financials=len(active) - len(at_period_end),
        median_days=nearest_rank_percentile(at_period_end, 0.5),
        p90_days=nearest_rank_percentile(at_period_end, 0.9),
        over_six_months=sum(days > SIX_MONTHS_DAYS for days in at_period_end),
        median_days_vs_filing=nearest_rank_percentile(at_filing, 0.5),
    )


@dataclass(frozen=True)
class StalenessReport:
    loans: list[LoanStaleness]
    summary: StalenessSummary

    def loan(self, loan: str) -> LoanStaleness:
        for row in self.loans:
            if row.loan == loan:
                return row
        raise KeyError(f"no loan {loan!r} in the report")


def measure(loans: Sequence[TapeLoan]) -> StalenessReport:
    measured = [loan_staleness(loan) for loan in loans]
    return StalenessReport(loans=measured, summary=summarize(measured))
