"""Start and finish runs."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.errors import Refused
from cmbs.runs.tables import RUN_STATUSES, Run


class RunAlreadyFinished(Refused):
    """A run's outcome is final once recorded."""


def start_run(db: Session, *, kind: str, started_by: str, run_id: int | None = None) -> Run:
    """Start a run with the next number, or with `run_id` when a scenario pins it ("run 42").

    A pinned number moves the sequence past it, so later runs never collide with it.
    """
    run = Run(kind=kind, started_by=started_by, started_at=clock.now(), status="running")
    if run_id is not None:
        run.id = run_id
    db.add(run)
    db.flush()
    if run_id is not None:
        db.execute(
            text(
                "SELECT setval(pg_get_serial_sequence('app.run', 'id'), "
                "(SELECT max(id) FROM app.run))"
            )
        )
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
