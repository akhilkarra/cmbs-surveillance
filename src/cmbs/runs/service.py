"""Start and finish runs."""

from typing import Any

from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.runs.tables import RUN_STATUSES, Run


def start_run(db: Session, *, kind: str, started_by: str, run_id: int | None = None) -> Run:
    """`run_id` lets a scenario pin the number it talks about ("run 42"); production omits it."""
    run = Run(kind=kind, started_by=started_by, started_at=clock.now(), status="running")
    if run_id is not None:
        run.id = run_id
    db.add(run)
    db.flush()
    return run


def finish_run(db: Session, run: Run, *, status: str, summary: dict[str, Any]) -> Run:
    if status not in RUN_STATUSES or status == "running":
        raise ValueError(f"a run cannot finish as {status!r}")
    run.status = status
    run.summary = summary
    run.ended_at = clock.now()
    db.flush()
    return run
