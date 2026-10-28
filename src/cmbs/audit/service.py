"""Write and read the audit trail. Every write goes through `record`."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.audit.tables import AuditEntry


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


def record_refusal(db: Session, *, user: str, action: object, message: str) -> None:
    """Record a refusal in its own transaction.

    The caller's transaction usually rolls back because of the refusal; the record of it must
    survive that, so it is committed separately on the same database.
    """
    with Session(db.get_bind()) as separate:
        record(separate, user=user, action=str(action), outcome="refused", reason=message)
        separate.commit()


def entries_for_subject(db: Session, subject: str, key: str) -> Sequence[AuditEntry]:
    """Newest first."""
    query = (
        select(AuditEntry)
        .where(AuditEntry.subject == subject, AuditEntry.subject_key == key)
        .order_by(AuditEntry.at.desc(), AuditEntry.id.desc())
    )
    return db.scalars(query).all()
