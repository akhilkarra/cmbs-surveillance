"""Start and finish runs."""

from typing import Any

from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.errors import Refused
from cmbs.runs.tables import RUN_STATUSES, Run


class RunAlreadyFinished(Refused):
    """A run's outcome is final once recorded."""


def start_run(db: Session, *, kind: str, started_by: str) -> Run:
    """Start a run with the next number from the database sequence."""
    run = Run(kind=kind, started_by=started_by, started_at=clock.now(), status="running")
    db.add(run)
    db.flush()
    return run


def finish_run(db: Session, run: Run, *, status: str, summary: dict[str, Any]) -> Run:
    if status not in RUN_STATUSES or status == "running":
        raise ValueError(f"a run cannot finish as {status!r}")
    # Lock and re-read the row: another session may have finished this run since it was loaded.
    db.refresh(run, with_for_update=True)
    if run.status != "running":
        raise RunAlreadyFinished(f"run {run.id} already finished as {run.status!r}")
    run.status = status
    run.summary = summary
    run.ended_at = clock.now()
    db.flush()
    return run
