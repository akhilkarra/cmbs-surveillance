"""What a run loads, and how its outcome reads. Pure functions, no database.

A deal-month is loaded when both its loan tape (ABS-EE) and its distribution report (10-D) are
stored; it needs both, because the tape lacks what the report carries (comments, appraisal
reductions, delinquency detail) and the report is reconciled with the tape.

A deal's months load in order, with no gaps after its first loaded month, so each month's
alerts are always built against a loaded month before it:
- a refresh tries every month after the deal's first loaded month that is not loaded yet, up to
  the newest month with both filings, oldest first. A deal with nothing loaded yet gets only its
  newest month; history before that is what a backfill is for.
- a backfill tries every month of its range that is not loaded yet, oldest first.

A month that is not loaded, for any reason, stops that deal for the run, and is recorded with
the reason; the next run starts from it. A month only missing a filing ("no 10-D filed for
September 2026") waits for the filing: the run goes on to other deals and ends "completed with
problems". A month that fails to load (a filing that cannot be fetched, parsed or loaded) needs
fixing: the run ends "failed" and says where it stopped, but still refreshes its other deals.

A loaded month that a check finds fault with (a quarantined loan, a reconciliation flag, a
balance roll-forward break, a comment that disagrees with the tape) ends the run "completed
with problems", never "succeeded".

Amendments (ABS-EE/A, 10-D/A) are not loaded by runs yet: EDGAR does not say which filing an
amendment corrects, and the loaders need that link. Each run says which months have one.
"""

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from cmbs.domain import Period
from cmbs.edgar.archives import filing_base_url

TAPE_FORM = "ABS-EE"
REPORT_FORM = "10-D"
AMENDMENT_FORMS = (f"{TAPE_FORM}/A", f"{REPORT_FORM}/A")


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
    amendments: tuple[FilingRef, ...] = ()

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

    def notes(self) -> tuple[str, ...]:
        """'September 2026 has an ABS-EE/A that runs do not load yet', once per form."""
        forms = sorted({filing.form for filing in self.amendments})
        return tuple(
            f"{self.period.label} has {'an' if form.startswith('A') else 'a'} {form} "
            "that runs do not load yet"
            for form in forms
        )


@dataclass(frozen=True)
class FilingLink:
    """A filing a month's numbers came from, with its folder on EDGAR."""

    form: str
    accession: str
    url: str

    def as_json(self) -> dict[str, str]:
        return {"form": self.form, "accession": self.accession, "url": self.url}


def links(month: MonthFilings) -> tuple[FilingLink, ...]:
    """The month's ABS-EE and 10-D, as far as it has them, each linked to its EDGAR folder."""
    return tuple(
        FilingLink(filing.form, filing.accession, filing_base_url(filing.cik, filing.accession))
        for filing in (month.tape, month.report)
        if filing is not None
    )


def _later(current: FilingRef | None, candidate: FilingRef) -> FilingRef:
    """Of two originals for one month, the one filed last (then the higher accession)."""
    if current is None:
        return candidate
    return max(current, candidate, key=lambda f: (f.filing_date, f.accession))


def monthly_filings(filings: Iterable[FilingRef]) -> dict[Period, MonthFilings]:
    """Each month's original ABS-EE and 10-D, and its amendments. Other forms are ignored."""
    tapes: dict[Period, FilingRef] = {}
    reports: dict[Period, FilingRef] = {}
    amendments: dict[Period, list[FilingRef]] = {}
    for filing in filings:
        if filing.form == TAPE_FORM:
            tapes[filing.period] = _later(tapes.get(filing.period), filing)
        elif filing.form == REPORT_FORM:
            reports[filing.period] = _later(reports.get(filing.period), filing)
        elif filing.form in AMENDMENT_FORMS:
            amendments.setdefault(filing.period, []).append(filing)
    return {
        period: MonthFilings(
            period,
            tapes.get(period),
            reports.get(period),
            tuple(sorted(amendments.get(period, []), key=lambda f: (f.filing_date, f.accession))),
        )
        for period in sorted(tapes.keys() | reports.keys() | amendments.keys())
    }


def months_between(start: Period, end: Period) -> list[Period]:
    """Every month from `start` to `end`, both included; empty when `end` is before `start`."""
    return [start.shift(n) for n in range(start.months_until(end) + 1)]


def refresh_months(available: Mapping[Period, MonthFilings], loaded: set[Period]) -> list[Period]:
    """The months a refresh tries: after the first loaded, not loaded yet, up to the newest
    complete month. Every calendar month counts, so a month with no filing at all is tried too
    and says so."""
    complete = [period for period, month in available.items() if month.complete]
    if not complete:
        return []
    newest = max(complete)
    if not loaded:
        return [newest]
    return [p for p in months_between(min(loaded).shift(1), newest) if p not in loaded]


def backfill_months(start: Period, end: Period, loaded: set[Period]) -> list[Period]:
    """Every month from `start` to `end`, both included, that is not loaded yet."""
    if end < start:
        raise ValueError(f"a backfill from {start} to {end} ends before it starts")
    return [period for period in months_between(start, end) if period not in loaded]


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
    # Alerts the month's build raised (`cmbs.alerts.service.build_alerts`).
    alerts: int = 0
    # True when the month failed to load, rather than missing a filing: the run fails.
    failed: bool = False
    notes: tuple[str, ...] = ()
    # Comments the month's extraction read, and the fields of them it sent to review. None when
    # the run was given no model to extract with.
    comments: int | None = None
    review_items: int = 0
    # What the month's checks flagged: balance roll-forward breaks, and comments that disagree
    # with the tape. None when the checks did not run, which is itself a problem.
    rollforward_breaks: int | None = None
    text_flags: int | None = None
    filings: tuple[FilingLink, ...] = ()

    @property
    def unchecked(self) -> bool:
        """Loaded, but its checks did not run: nothing says the month is sound."""
        return self.outcome is MonthOutcome.LOADED and (
            self.rollforward_breaks is None or self.text_flags is None
        )

    @property
    def problems(self) -> bool:
        """Loaded, but a check found fault (a quarantined loan, a reconciliation flag, a
        roll-forward break, a comment that disagrees with the tape) or did not run."""
        found = self.quarantined or self.flags or self.rollforward_breaks or self.text_flags
        return bool(found) or self.unchecked

    def text(self) -> str:
        return "\n".join(month_lines(self.as_json()))

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
            "notes": list(self.notes),
            "comments": self.comments,
            "review_items": self.review_items,
            "rollforward_breaks": self.rollforward_breaks,
            "text_flags": self.text_flags,
            "filings": [filing.as_json() for filing in self.filings],
        }


@dataclass(frozen=True)
class DealResult:
    deal: str
    outcome: DealOutcome
    reason: str | None = None
    months: tuple[MonthResult, ...] = field(default_factory=tuple)
    # Notes on months this run did not try (amendments of months loaded earlier).
    notes: tuple[str, ...] = ()
    # True when the deal was skipped for a reason that is a problem: another run held it.
    problem: bool = False
    # The newest month loaded once the run was done with the deal; None when none is.
    loaded_through: Period | None = None

    @property
    def stopped_at(self) -> MonthResult | None:
        return next((month for month in self.months if month.failed), None)

    def text(self) -> str:
        return "\n".join(deal_lines(self.as_json()))

    def as_json(self) -> dict[str, Any]:
        return {
            "deal": self.deal,
            "outcome": self.outcome.value,
            "reason": self.reason,
            "months": [month.as_json() for month in self.months],
            "notes": list(self.notes),
            "problem": self.problem,
            "loaded_through": self.loaded_through.label if self.loaded_through else None,
        }


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"


def month_lines(month: Mapping[str, Any]) -> list[str]:
    """A month of a stored run summary as text, its notes below it."""
    if month["outcome"] == MonthOutcome.LOADED.value:
        line = (
            f"{month['period']}: loaded, {_count(month.get('loan_rows', 0), 'loan row')}, "
            f"{_count(month.get('quarantined', 0), 'loan')} quarantined, "
            f"{_count(month.get('flags', 0), 'reconciliation flag')}"
        )
    else:
        line = f"{month['period']}: {month['outcome']}, {month['reason']}"
        if month.get("detail"):
            line += f" ({month['detail']})"
    return [line, *(f"  {note}" for note in month.get("notes", []))]


def deal_lines(deal: Mapping[str, Any]) -> list[str]:
    """A deal of a stored run summary as text: its outcome, then each month indented."""
    head = f"{deal['deal']}: {deal['outcome']}"
    if deal.get("reason"):
        head += f", {deal['reason']}"
    elif deal["outcome"] == DealOutcome.REFRESHED.value and not deal.get("months"):
        head += ", nothing new to load"
    lines = [head]
    for month in deal.get("months", []):
        lines += [f"  {line}" for line in month_lines(month)]
    lines += [f"  {note}" for note in deal.get("notes", [])]
    return lines


def deal_outcome(
    deal: str,
    months: Sequence[MonthResult],
    notes: Sequence[str] = (),
    loaded_before: Iterable[Period] = (),
) -> DealResult:
    """Failed if a month failed to load, otherwise refreshed; loaded through the newest month
    loaded before the run or by it."""
    failed = any(month.failed for month in months)
    outcome = DealOutcome.FAILED if failed else DealOutcome.REFRESHED
    loaded = {*loaded_before, *(m.period for m in months if m.outcome is MonthOutcome.LOADED)}
    return DealResult(
        deal, outcome, None, tuple(months), tuple(notes), loaded_through=max(loaded, default=None)
    )


def run_status(deals: Sequence[DealResult]) -> str:
    """failed when a deal failed; completed with problems when a month was not loaded, a check
    found fault with a loaded month (a quarantined loan, a reconciliation flag, a roll-forward
    break) or another run held a deal; succeeded otherwise. Skipping a deal that is not
    publicly reported is not a problem: it is the answer."""
    if any(deal.outcome is DealOutcome.FAILED for deal in deals):
        return "failed"
    months = [month for deal in deals for month in deal.months]
    if any(deal.problem for deal in deals) or any(
        m.outcome is MonthOutcome.NOT_LOADED or m.problems for m in months
    ):
        return "completed with problems"
    return "succeeded"


def alerts_raised(months: Iterable[MonthResult]) -> int:
    """Alerts raised by the loaded months (`cmbs.alerts`, built as each month loads)."""
    return sum(m.alerts for m in months if m.outcome is MonthOutcome.LOADED)


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
        "quarantined": sum(m.quarantined for m in months),
        "flags": sum(m.flags for m in months),
        "alerts_raised": alerts_raised(months),
        "comments": sum(m.comments or 0 for m in months),
        "review_items": sum(m.review_items for m in months),
        "rollforward_breaks": sum(m.rollforward_breaks or 0 for m in months),
        "text_flags": sum(m.text_flags or 0 for m in months),
        "stopped": stopped(deals),
        "deals": [deal.as_json() for deal in deals],
    }
