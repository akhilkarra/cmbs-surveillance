"""Write and read the audit trail. Every write goes through `record`.

The trail is append-only. The service refuses every change or deletion (and records the
attempt); the database trigger on `app.audit_entry` is the backstop for anything that goes
around the service.

Reading: a loan's own change history is part of the panel (`Action.READ_PANEL`), so anyone who
may read the panel sees the history of what they can see; reading the whole log, by loan, user,
run or date, needs `Action.READ_AUDIT_LOG` (risk and operators).
"""

import datetime as dt
from collections.abc import Sequence
from typing import Any, NoReturn

from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from cmbs import clock
from cmbs.access.roles import Action
from cmbs.access.service import authorize
from cmbs.audit.tables import AuditEntry
from cmbs.errors import Refused

LOAN = "loan"
AUDIT_ENTRY = "audit entry"
APPEND_ONLY = "the audit log is append-only"
NEWEST_FIRST = (AuditEntry.at.desc(), AuditEntry.id.desc())


class AppendOnly(Refused):
    """Audit entries are never changed or removed."""


class BadRange(Refused):
    """A date range that ends before it starts."""


class BadFilter(Refused):
    """A loan named without its deal."""


def loan_key(deal: str, loan: str) -> str:
    """The audit subject key of one loan of one deal: 'Fixture Trust 2026-F1/2'.

    Every feature that records a change to a loan uses `subject=LOAN` and this key, so a loan's
    history can be read in one place.
    """
    return f"{deal}/{loan}"


def record(
    db: Session,
    *,
    user: str,
    action: str,
    role: str | None = None,
    run_id: int | None = None,
    subject: str | None = None,
    subject_key: str | None = None,
    field: str | None = None,
    before: str | None = None,
    after: str | None = None,
    reason: str | None = None,
    outcome: str = "done",
    detail: dict[str, Any] | None = None,
) -> AuditEntry:
    entry = AuditEntry(
        at=clock.now(),
        user_name=user,
        role=role,
        run_id=run_id,
        action=action,
        subject=subject,
        subject_key=subject_key,
        field=field,
        before=before,
        after=after,
        reason=reason,
        outcome=outcome,
        detail=detail,
    )
    db.add(entry)
    db.flush()
    return entry


_refusal_engines: dict[str, Engine] = {}


def _refusal_engine(db: Session) -> Engine:
    """An unpooled engine on the caller's database, used only to record refusals.

    The caller is already holding a connection from its pool. Borrowing a second one from the
    same pool could wait, or time out, when the pool is busy, and the refusal would go
    unrecorded. A fresh connection per refusal avoids the pool entirely; refusals are rare.
    """
    url = db.get_bind().engine.url
    key = url.render_as_string(hide_password=False)
    if key not in _refusal_engines:
        _refusal_engines[key] = create_engine(url, poolclass=NullPool)
    return _refusal_engines[key]


def record_refusal(
    db: Session,
    *,
    user: str,
    action: object,
    message: str,
    role: str | None = None,
    subject: str | None = None,
    subject_key: str | None = None,
) -> None:
    """Record a refusal in its own transaction, on its own connection.

    The caller's transaction usually rolls back because of the refusal; the record of it must
    survive that, so it is committed separately on the same database.
    """
    with Session(_refusal_engine(db)) as separate:
        record(
            separate,
            user=user,
            action=str(action),
            role=role,
            subject=subject,
            subject_key=subject_key,
            outcome="refused",
            reason=message,
        )
        separate.commit()


def refuse(
    db: Session,
    error: Refused,
    *,
    user: str,
    action: object,
    role: str | None = None,
    subject: str | None = None,
    subject_key: str | None = None,
) -> NoReturn:
    """Record `error` as a refusal of `action`, then raise it."""
    record_refusal(
        db,
        user=user,
        action=action,
        message=str(error),
        role=role,
        subject=subject,
        subject_key=subject_key,
    )
    raise error


def entries_for_subject(db: Session, subject: str, key: str) -> Sequence[AuditEntry]:
    """Newest first."""
    query = (
        select(AuditEntry)
        .where(AuditEntry.subject == subject, AuditEntry.subject_key == key)
        .order_by(*NEWEST_FIRST)
    )
    return db.scalars(query).all()


def _midnight(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(), dt.UTC)


def day_bounds(
    start: dt.date | None, end: dt.date | None
) -> tuple[dt.datetime | None, dt.datetime | None]:
    """Inclusive calendar days in UTC as a half-open range [from, until).

    `end` 2026-09-30 covers all of that day, so the range stops at 2026-10-01 00:00 UTC.
    """
    if start is not None and end is not None and end < start:
        raise BadRange(f"the range ends on {end}, before it starts on {start}")
    since = None if start is None else _midnight(start)
    until = None if end is None else _midnight(end + dt.timedelta(days=1))
    return since, until


def loan_history(db: Session, reader: str, *, deal: str, loan: str) -> Sequence[AuditEntry]:
    """Every recorded change to one loan, newest first. Part of the panel: anyone who may read
    the panel sees the history of what they can see."""
    authorize(db, reader, Action.READ_PANEL)
    return entries_for_subject(db, LOAN, loan_key(deal, loan))


def read_log(
    db: Session,
    reader: str,
    *,
    user: str | None = None,
    start: dt.date | None = None,
    end: dt.date | None = None,
    deal: str | None = None,
    loan: str | None = None,
    run: int | None = None,
) -> Sequence[AuditEntry]:
    """The whole log, optionally narrowed to one user, the loans of one deal (or one of its
    loans), one run and calendar days (UTC, inclusive), newest first. Needs
    `Action.READ_AUDIT_LOG`."""
    authorize(db, reader, Action.READ_AUDIT_LOG)
    if loan is not None and deal is None:
        raise BadFilter(f"name the deal of loan {loan}")
    since, until = day_bounds(start, end)
    query = select(AuditEntry)
    if user is not None:
        query = query.where(AuditEntry.user_name == user)
    if deal is not None:
        key = AuditEntry.subject_key
        query = query.where(
            AuditEntry.subject == LOAN,
            key == loan_key(deal, loan)
            if loan is not None
            else key.startswith(loan_key(deal, ""), autoescape=True),
        )
    if run is not None:
        query = query.where(AuditEntry.run_id == run)
    if since is not None:
        query = query.where(AuditEntry.at >= since)
    if until is not None:
        query = query.where(AuditEntry.at < until)
    return db.scalars(query.order_by(*NEWEST_FIRST)).all()


def delete_entry(db: Session, user: str, entry_id: int) -> NoReturn:
    """Always refused: the attempt is recorded and nothing is removed."""
    refuse(
        db,
        AppendOnly(APPEND_ONLY),
        user=user,
        action="delete audit entry",
        subject=AUDIT_ENTRY,
        subject_key=str(entry_id),
    )


def change_entry(db: Session, user: str, entry_id: int) -> NoReturn:
    """Always refused: the attempt is recorded and nothing is changed."""
    refuse(
        db,
        AppendOnly(APPEND_ONLY),
        user=user,
        action="change audit entry",
        subject=AUDIT_ENTRY,
        subject_key=str(entry_id),
    )


def entry_view(entry: AuditEntry) -> dict[str, Any]:
    """An entry as the API returns it."""
    return {
        "id": entry.id,
        "at": entry.at.isoformat(),
        "user": entry.user_name,
        "role": entry.role,
        "run": entry.run_id,
        "action": entry.action,
        "subject": entry.subject,
        "subject_key": entry.subject_key,
        "field": entry.field,
        "before": entry.before,
        "after": entry.after,
        "reason": entry.reason,
        "outcome": entry.outcome,
        "detail": entry.detail,
    }


def render_entry(entry: AuditEntry) -> str:
    """One entry on one line: when, who (as which role, in which run), what, before and after,
    and why. A refusal says so."""
    who = entry.user_name + (f" ({entry.role})" if entry.role else "")
    if entry.run_id is not None:
        who += f" in run {entry.run_id}"
    line = f"{entry.at.isoformat()} {who}: {entry.action}"
    if entry.subject_key is not None:
        line += f" {entry.subject_key}"
    if entry.field is not None:
        line += f" {entry.field}"
    if entry.before is not None or entry.after is not None:
        line += f": {entry.before or '-'} -> {entry.after or '-'}"
    if entry.outcome == "refused":
        line += f" REFUSED: {entry.reason}"
    elif entry.reason is not None:
        line += f" ({entry.reason})"
    return line


def render_entries(entries: Sequence[AuditEntry]) -> str:
    if not entries:
        return "no audit entries\n"
    return "\n".join(render_entry(entry) for entry in entries) + "\n"
