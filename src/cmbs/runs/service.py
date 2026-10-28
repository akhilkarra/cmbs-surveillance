"""Start, record and finish runs, and read the run history.

A run's summary is written as it goes (`record_progress`), so a run that stops without finishing
still shows what it did; `finish_run` writes the final summary and outcome once. A run whose
process died is finished as failed by the next run that claims one of its deals (`abandon`).

Nothing deletes a run: the history is kept however old it gets.

Reading the history needs `Action.SEE_RUN_HISTORY`. Tracing a loaded value to its run is part of
the panel (`Action.READ_PANEL`): the audit entry written when a loan row was loaded names the
run and the time.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.access.roles import Action
from cmbs.access.service import authorize
from cmbs.audit.service import LOAN, day_bounds
from cmbs.audit.tables import AuditEntry
from cmbs.deals.service import by_name
from cmbs.domain import Period, format_money
from cmbs.errors import Refused
from cmbs.panel import service as panel
from cmbs.runs.tables import RUN_STATUSES, Run
from cmbs.tape.service import LOAD_ACTION

RUNNING = "running"
FAILED = "failed"


class RunAlreadyFinished(Refused):
    """A run's outcome is final once recorded."""


class UnknownRun(Refused):
    pass


class NotLoaded(Refused):
    """The panel holds no value for the loan and month asked about."""


def start_run(db: Session, *, kind: str, started_by: str) -> Run:
    """Start a run with the next number from the database sequence."""
    run = Run(kind=kind, started_by=started_by, started_at=clock.now(), status=RUNNING)
    db.add(run)
    db.flush()
    return run


def _locked_running(db: Session, run: Run) -> Run:
    # Lock and re-read the row: another session may have finished this run since it was loaded.
    db.refresh(run, with_for_update=True)
    if run.status != RUNNING:
        raise RunAlreadyFinished(f"run {run.id} already finished as {run.status!r}")
    return run


def record_progress(db: Session, run: Run, summary: dict[str, Any]) -> Run:
    """Replace a running run's summary with what it has done so far."""
    _locked_running(db, run)
    run.summary = summary
    db.flush()
    return run


def finish_run(db: Session, run: Run, *, status: str, summary: dict[str, Any]) -> Run:
    if status not in RUN_STATUSES or status == RUNNING:
        raise ValueError(f"a run cannot finish as {status!r}")
    _locked_running(db, run)
    run.status = status
    run.summary = summary
    run.ended_at = clock.now()
    db.flush()
    return run


def abandon(db: Session, run: Run, reason: str) -> Run:
    """Finish a run that stopped without finishing as failed, keeping what it recorded."""
    _locked_running(db, run)
    summary = dict(run.summary or {})
    summary["stopped"] = {**(summary.get("stopped") or {}), "error": reason}
    return finish_run(db, run, status=FAILED, summary=summary)


# ---------------------------------------------------------------------------- the history


@dataclass(frozen=True)
class RunView:
    number: int
    kind: str
    started_by: str
    started_at: dt.datetime
    ended_at: dt.datetime | None
    status: str
    summary: dict[str, Any]

    @classmethod
    def of(cls, run: Run) -> "RunView":
        return cls(
            number=run.id,
            kind=run.kind,
            started_by=run.started_by,
            started_at=run.started_at,
            ended_at=run.ended_at,
            status=run.status,
            summary=dict(run.summary or {}),
        )

    @property
    def stopped(self) -> dict[str, Any] | None:
        return self.summary.get("stopped")

    def period(self) -> str:
        """'2026-10-27 09:00 to 09:04', with the end's date when it ended on another day."""
        start = self.started_at.astimezone(dt.UTC)
        text = start.strftime("%Y-%m-%d %H:%M")
        if self.ended_at is None:
            return f"{text}, still running"
        end = self.ended_at.astimezone(dt.UTC)
        same_day = end.date() == start.date()
        return f"{text} to {end.strftime('%H:%M' if same_day else '%Y-%m-%d %H:%M')}"

    def headline(self) -> str:
        """'run 43: olly, 2026-10-27 09:00 to 09:04, 2 deals refreshed, 1 skipped, 14 loan rows
        loaded, 3 alerts raised'. Alerts not built read 'alerts not built'."""
        s = self.summary
        alerts = s.get("alerts_raised")
        parts = [
            f"run {self.number}: {self.started_by}",
            self.period(),
            f"{s.get('deals_refreshed', 0)} deals refreshed",
            f"{s.get('deals_skipped', 0)} skipped",
            f"{s.get('loan_rows', 0)} loan rows loaded",
            "alerts not built" if alerts is None else f"{alerts} alerts raised",
        ]
        if s.get("deals_failed"):
            parts.insert(3, f"{s['deals_failed']} failed")
        return ", ".join(parts)

    def text(self) -> str:
        """The headline, the outcome and where it stopped, then each deal's lines."""
        lines = [self.headline(), f"{self.kind}, {self.status}"]
        stopped = self.stopped
        if stopped:
            where = ", ".join(str(stopped[key]) for key in ("deal", "period") if stopped.get(key))
            error = stopped.get("error")
            detail = stopped.get("detail")
            line = f"stopped at {where}: {error}" if where else f"stopped: {error}"
            lines.append(f"{line} ({detail})" if detail else line)
        for deal in self.summary.get("deals", []):
            head = f"{deal['deal']}: {deal['outcome']}"
            if deal.get("reason"):
                head += f", {deal['reason']}"
            lines.append(head)
            for month in deal.get("months", []):
                if month["outcome"] == "loaded":
                    lines.append(f"  {month['period']}: loaded, {month['loan_rows']} loan rows")
                else:
                    line = f"  {month['period']}: {month['outcome']}, {month['reason']}"
                    lines.append(f"{line} ({month['detail']})" if month.get("detail") else line)
        return "\n".join(lines)


def get_run(db: Session, number: int) -> Run:
    run = db.get(Run, number)
    if run is None:
        raise UnknownRun(f"there is no run {number}")
    return run


def read_run(db: Session, reader: str, number: int) -> RunView:
    authorize(db, reader, Action.SEE_RUN_HISTORY)
    return RunView.of(get_run(db, number))


def history(
    db: Session, reader: str, *, start: dt.date | None = None, end: dt.date | None = None
) -> list[RunView]:
    """Runs started on the calendar days from `start` to `end` (UTC, inclusive), newest first."""
    authorize(db, reader, Action.SEE_RUN_HISTORY)
    since, until = day_bounds(start, end)
    query = select(Run)
    if since is not None:
        query = query.where(Run.started_at >= since)
    if until is not None:
        query = query.where(Run.started_at < until)
    return [
        RunView.of(run) for run in db.scalars(query.order_by(Run.started_at.desc(), Run.id.desc()))
    ]


def month_bounds(period: Period) -> tuple[dt.date, dt.date]:
    """The first and last day of a month, for reading the history of one month."""
    return period.first_day, period.shift(1).first_day - dt.timedelta(days=1)


# ---------------------------------------------------------------------------- tracing values


@dataclass(frozen=True)
class Trace:
    """Where a loan's balance for a month came from: the filing, and the run that loaded it."""

    deal: str
    loan: str
    period: Period
    balance: Decimal | None
    accession: str
    filing_date: dt.date
    run: int | None
    loaded_by: str | None
    loaded_at: dt.datetime | None

    def text(self) -> str:
        value = "not reported" if self.balance is None else format_money(self.balance)
        head = (
            f"loan {self.loan} of {self.deal}, {self.period.label} balance {value}, "
            f"from {self.accession} filed {self.filing_date.isoformat()}"
        )
        if self.loaded_at is None:
            return f"{head}; its load is not in the audit trail"
        day = self.loaded_at.astimezone(dt.UTC).date().isoformat()
        if self.run is None:
            return f"{head}; loaded by {self.loaded_by} on {day}, outside a run"
        return f"{head}; loaded by run {self.run} on {day}"


def _load_entry(entries: Sequence[AuditEntry], accession: str, loan: str) -> AuditEntry | None:
    """The first audit entry that loaded this loan's row from this filing."""
    for entry in sorted(entries, key=lambda e: (e.at, e.id)):
        key = entry.subject_key or ""
        if (entry.detail or {}).get("accession") == accession and key.rsplit("/", 1)[-1] == loan:
            return entry
    return None


def trace(
    db: Session,
    reader: str,
    *,
    deal: str,
    loan: str,
    period: Period,
    as_of: dt.date | None = None,
) -> Trace:
    """The loan's balance for the month as the panel shows it on `as_of` (today by default),
    with the filing it came from and the run that loaded it."""
    authorize(db, reader, Action.READ_PANEL)
    deal_row = by_name(db, deal)
    if deal_row is None:
        raise panel.UnknownDeal(f"no deal named {deal!r}")
    day = as_of or clock.today()
    rows = panel.as_of(db, day, deal_id=deal_row.id, period=period, loan=loan)
    if not rows:
        raise NotLoaded(f"no {period.label} values of loan {loan} of {deal_row.name} by {day}")
    row = rows[0]
    entries = db.scalars(
        select(AuditEntry).where(
            AuditEntry.subject == LOAN,
            AuditEntry.action == LOAD_ACTION,
            AuditEntry.outcome == "done",
            AuditEntry.detail["accession"].astext == row.accession,
        )
    ).all()
    entry = _load_entry(entries, row.accession, loan)
    return Trace(
        deal=deal_row.name,
        loan=loan,
        period=period,
        balance=row.ending_balance,
        accession=row.accession,
        filing_date=row.filing_date,
        run=entry.run_id if entry else None,
        loaded_by=entry.user_name if entry else None,
        loaded_at=entry.at if entry else None,
    )
