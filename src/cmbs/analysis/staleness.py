"""How old the "most recent financials" in an ABS-EE loan tape are.

- Active loan: ending balance above zero, not defeased, and not a residential co-op
  (property type "CH"), since co-ops report no operating statements.
- Staleness: reporting period end minus the most recent financials end date, in days.
  The same gap is also measured from the filing date, which is when a point-in-time user
  first sees the tape.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from cmbs.absee.schedule_al import LoanRecord, iter_loans

COOPERATIVE_HOUSING = "CH"
DEFEASED = "Defeased"
SIX_MONTHS_DAYS = 183


def is_active(loan: LoanRecord) -> bool:
    return (
        loan.ending_balance > 0
        and loan.property_name != DEFEASED
        and loan.property_type != COOPERATIVE_HOUSING
    )


def nearest_rank_percentile(values: Sequence[int], q: float) -> int | None:
    """Percentile by rounding q * (n - 1) to the nearest index. None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1) + 0.5))]


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
