"""What a run loads, and how its outcome reads. Pure functions, no database.

A deal-month is loaded when both its loan tape (ABS-EE) and its distribution report (10-D) are
stored; it needs both, because the tape lacks what the report carries (comments, appraisal
reductions, delinquency detail) and the report is reconciled with the tape.

Which months a run tries:
- a refresh brings a deal up to date: every month after the newest one loaded, up to the newest
  month with both filings. A deal with nothing loaded yet gets only its newest month; history is
  what a backfill is for.
- a backfill tries every month of its range that is not loaded yet.
Months are tried oldest first, because each month's alerts compare it with the month before.

A month missing one of its filings is not loaded and says which; the run goes on to the next
month, since nothing can be done until the filing appears. A month that fails to load (a filing
that cannot be parsed or is refused) stops that deal: later months would be loaded around a
gap, and a rerun picks up from the failed month once it is fixed. The run then fails, and says
where it stopped, but still refreshes its other deals.

Amendments (ABS-EE/A, 10-D/A) are not loaded by a run yet: EDGAR does not say which filing an
amendment corrects, and the loaders need that link.
"""

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from cmbs.domain import Period

TAPE_FORM = "ABS-EE"
REPORT_FORM = "10-D"


@dataclass(frozen=True)
class FilingRef:
    accession: str
    cik: int
    form: str
    filing_date: dt.date
    period: Period


@dataclass(frozen=True)
class MonthFilings:
    period: Period
    tape: FilingRef | None = None
    report: FilingRef | None = None

    @property
    def complete(self) -> bool:
        return self.tape is not None and self.report is not None

    def missing(self) -> str | None:
        """Which filing the month lacks, as the run summary says it; None when it has both."""
        absent = [
            form
            for form, filing in ((TAPE_FORM, self.tape), (REPORT_FORM, self.report))
            if filing is None
        ]
        if not absent:
            return None
        return f"no {' or '.join(absent)} filed for {self.period.label}"


def _later(current: FilingRef | None, candidate: FilingRef) -> FilingRef:
    """Of two originals for one month, the one filed last (then the higher accession)."""
    if current is None:
        return candidate
    return max(current, candidate, key=lambda f: (f.filing_date, f.accession))


def monthly_filings(filings: Iterable[FilingRef]) -> dict[Period, MonthFilings]:
    """Each month's original ABS-EE and 10-D. Other forms, amendments included, are ignored."""
    tapes: dict[Period, FilingRef] = {}
    reports: dict[Period, FilingRef] = {}
    for filing in filings:
        if filing.form == TAPE_FORM:
            tapes[filing.period] = _later(tapes.get(filing.period), filing)
        elif filing.form == REPORT_FORM:
            reports[filing.period] = _later(reports.get(filing.period), filing)
    return {
        period: MonthFilings(period, tapes.get(period), reports.get(period))
        for period in sorted(tapes.keys() | reports.keys())
    }


def refresh_months(available: Mapping[Period, MonthFilings], loaded: set[Period]) -> list[Period]:
    """The months a refresh tries: after the newest loaded, up to the newest complete month."""
    complete = [period for period, month in available.items() if month.complete]
    if not complete:
        return []
    newest = max(complete)
    if not loaded:
        return [newest]
    after = max(loaded)
    return sorted(p for p in available if after < p <= newest and p not in loaded)


def backfill_months(start: Period, end: Period, loaded: set[Period]) -> list[Period]:
    """Every month from `start` to `end`, both included, that is not loaded yet."""
    if end < start:
        raise ValueError(f"a backfill from {start} to {end} ends before it starts")
    months = [start.shift(n) for n in range(start.months_until(end) + 1)]
    return [period for period in months if period not in loaded]


# ---------------------------------------------------------------------------- outcomes


class MonthOutcome(StrEnum):
    LOADED = "loaded"
    NOT_LOADED = "not loaded"


class DealOutcome(StrEnum):
    REFRESHED = "refreshed"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class MonthResult:
    period: Period
    outcome: MonthOutcome
    reason: str | None = None
    detail: str | None = None
    loan_rows: int = 0
    quarantined: int = 0
    flags: int = 0
    alerts: int | None = None
    # True when the month failed to load, rather than missing a filing: the deal stops here.
    failed: bool = False

    def text(self) -> str:
        if self.outcome is MonthOutcome.LOADED:
            text = f"{self.period.label}: loaded, {_count(self.loan_rows, 'loan row')}"
            if self.quarantined:
                text += f", {_count(self.quarantined, 'loan')} quarantined"
            if self.flags:
                text += f", {_count(self.flags, 'reconciliation flag')}"
            return text
        text = f"{self.period.label}: not loaded, {self.reason}"
        return f"{text} ({self.detail})" if self.detail else text

    def as_json(self) -> dict[str, Any]:
        return {
            "period": self.period.label,
            "outcome": self.outcome.value,
            "reason": self.reason,
            "detail": self.detail,
            "loan_rows": self.loan_rows,
            "quarantined": self.quarantined,
            "flags": self.flags,
            "alerts": self.alerts,
            "failed": self.failed,
        }


@dataclass(frozen=True)
class DealResult:
    deal: str
    outcome: DealOutcome
    reason: str | None = None
    months: tuple[MonthResult, ...] = field(default_factory=tuple)

    @property
    def stopped_at(self) -> MonthResult | None:
        return next((month for month in self.months if month.failed), None)

    def text(self) -> str:
        head = f"{self.deal}: {self.outcome.value}"
        if self.reason:
            head += f", {self.reason}"
        elif self.outcome is DealOutcome.REFRESHED and not self.months:
            head += ", nothing new to load"
        return "\n".join([head, *(f"  {month.text()}" for month in self.months)])

    def as_json(self) -> dict[str, Any]:
        return {
            "deal": self.deal,
            "outcome": self.outcome.value,
            "reason": self.reason,
            "months": [month.as_json() for month in self.months],
        }


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"


def deal_outcome(deal: str, months: Sequence[MonthResult]) -> DealResult:
    """Failed if a month failed to load, otherwise refreshed."""
    failed = any(month.failed for month in months)
    outcome = DealOutcome.FAILED if failed else DealOutcome.REFRESHED
    return DealResult(deal, outcome, None, tuple(months))


def run_status(deals: Sequence[DealResult]) -> str:
    """failed when a deal failed; completed with problems when a month was not loaded or a loan
    was quarantined; succeeded otherwise. Skipping a deal is not a problem: it is the answer."""
    if any(deal.outcome is DealOutcome.FAILED for deal in deals):
        return "failed"
    months = [month for deal in deals for month in deal.months]
    if any(m.outcome is MonthOutcome.NOT_LOADED or m.quarantined for m in months):
        return "completed with problems"
    return "succeeded"


def alerts_raised(months: Iterable[MonthResult]) -> int | None:
    """Alerts raised by the loaded months; None when alerts were not built for any of them."""
    counts = [m.alerts for m in months if m.outcome is MonthOutcome.LOADED]
    if not counts or any(count is None for count in counts):
        return None
    return sum(count for count in counts if count is not None)


def stopped(deals: Sequence[DealResult]) -> dict[str, Any] | None:
    """Where the run first failed: the deal, the month (None if it failed before any month) and
    the error, as stored in the summary."""
    for deal in deals:
        if deal.outcome is not DealOutcome.FAILED:
            continue
        month = deal.stopped_at
        if month is None:
            return {"deal": deal.deal, "period": None, "error": deal.reason, "detail": None}
        return {
            "deal": deal.deal,
            "period": month.period.label,
            "error": month.reason,
            "detail": month.detail,
        }
    return None


def summary(deals: Sequence[DealResult]) -> dict[str, Any]:
    """The run's summary as stored: totals, where it stopped, and every deal with its months."""
    months = [month for deal in deals for month in deal.months]
    return {
        "deals_refreshed": sum(d.outcome is DealOutcome.REFRESHED for d in deals),
        "deals_skipped": sum(d.outcome is DealOutcome.SKIPPED for d in deals),
        "deals_failed": sum(d.outcome is DealOutcome.FAILED for d in deals),
        "months_loaded": sum(m.outcome is MonthOutcome.LOADED for m in months),
        "months_not_loaded": sum(m.outcome is MonthOutcome.NOT_LOADED for m in months),
        "loan_rows": sum(m.loan_rows for m in months),
        "alerts_raised": alerts_raised(months),
        "stopped": stopped(deals),
        "deals": [deal.as_json() for deal in deals],
    }
