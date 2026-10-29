"""Read the audit log. Entries cannot be changed or removed; trying is refused and recorded."""

import datetime as dt
from typing import Any

from fastapi import APIRouter

from cmbs.api.deps import DB, User
from cmbs.audit.service import change_entry, delete_entry, entry_view, loan_history, read_log

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/loan")
def history_of_loan(user: User, db: DB, deal: str, loan: str) -> list[dict[str, Any]]:
    """One loan's change history, newest first. Anyone who may read the panel."""
    return [entry_view(entry) for entry in loan_history(db, user, deal=deal, loan=loan)]


@router.get("")
def the_log(
    user: User,
    db: DB,
    by: str | None = None,
    start: dt.date | None = None,
    end: dt.date | None = None,
    deal: str | None = None,
    loan: str | None = None,
    run: int | None = None,
) -> list[dict[str, Any]]:
    """The log, optionally by one user, one loan of a deal, one run and between two dates
    (inclusive), newest first. Risk and operators only."""
    found = read_log(db, user, user=by, start=start, end=end, deal=deal, loan=loan, run=run)
    return [entry_view(entry) for entry in found]


@router.delete("/{entry_id}")
def delete(user: User, db: DB, entry_id: int) -> None:
    """Always refused: the log is append-only. The attempt is recorded."""
    delete_entry(db, user, entry_id)


@router.patch("/{entry_id}")
def change(user: User, db: DB, entry_id: int) -> None:
    """Always refused: the log is append-only. The attempt is recorded."""
    change_entry(db, user, entry_id)
