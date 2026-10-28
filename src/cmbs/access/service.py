"""Who may do what, checked against the database and recorded when refused."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from cmbs.access.roles import Action, Role, acting_role, refusal_message
from cmbs.access.tables import User, UserRole
from cmbs.errors import Refused


class PermissionRefused(Refused):
    """The user's roles do not permit the action."""


class UnknownUser(Refused):
    pass


@dataclass(frozen=True)
class Actor:
    """A named user acting under one role."""

    user: str
    role: Role


def roles_of(db: Session, user: str) -> frozenset[Role]:
    if db.get(User, user) is None:
        raise UnknownUser(f"no user named {user!r}")
    rows = db.scalars(select(UserRole.role).where(UserRole.user_name == user))
    return frozenset(Role(value) for value in rows)


def authorize(db: Session, user: str, action: Action) -> Actor:
    """The actor to record for `action`, or PermissionRefused after auditing the refusal."""
    from cmbs.audit.service import record_refusal

    try:
        roles = roles_of(db, user)
    except UnknownUser as unknown:
        record_refusal(db, user=user, action=action, message=str(unknown))
        raise
    role = acting_role(roles, action)
    if role is None:
        message = refusal_message(roles, action)
        record_refusal(db, user=user, action=action, message=message)
        raise PermissionRefused(message)
    return Actor(user=user, role=role)


def ensure_user(db: Session, user: str, roles: set[Role] | frozenset[Role]) -> None:
    """Create the user if missing and give them exactly these roles. For setup, not audited."""
    if db.get(User, user) is None:
        db.add(User(name=user))
        db.flush()
    current = roles_of(db, user)
    for role in current - set(roles):
        doomed = db.get(UserRole, (user, role.value))
        if doomed is not None:
            db.delete(doomed)
    for role in set(roles) - current:
        db.add(UserRole(user_name=user, role=role.value))
    db.flush()
