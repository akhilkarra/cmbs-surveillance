"""Who may do what, checked against the database and recorded when refused.

Role changes need `Action.MANAGE_USERS` and are audited with who granted or revoked which role,
for whom, and the roles before and after. Nobody changes their own roles, so an operator cannot
give themselves the right to approve settings.
"""

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from cmbs.access.roles import Action, Role, acting_role, format_roles, refusal_message
from cmbs.access.tables import User, UserRole
from cmbs.errors import Refused


class PermissionRefused(Refused):
    """The user's roles do not permit the action."""


class UnknownUser(Refused):
    pass


class SelfRoleChange(PermissionRefused):
    """Nobody changes their own roles."""


class RoleUnchanged(Refused):
    """A grant of a role already held, or a revocation of one not held."""


GRANT_ROLE = "grant role"
REVOKE_ROLE = "revoke role"
USER_SUBJECT = "user"


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


def role_change_problem(
    *, by: str, user: str, role: Role, current: frozenset[Role], grant: bool
) -> Refused | None:
    """Why `by` may not grant (or revoke) `role` for `user`, who holds `current`; None if fine.

    Pure: the caller has already checked that `by` may manage users.
    """
    if by == user:
        return SelfRoleChange("nobody changes their own roles")
    if grant and role in current:
        return RoleUnchanged(f"{user} already holds the role {role.value}")
    if not grant and role not in current:
        return RoleUnchanged(f"{user} does not hold the role {role.value}")
    return None


def grant_role(db: Session, *, by: str, user: str, role: Role) -> frozenset[Role]:
    """Give `user` the role `role`; returns their roles afterwards."""
    return _change_role(db, by=by, user=user, role=role, grant=True)


def revoke_role(db: Session, *, by: str, user: str, role: Role) -> frozenset[Role]:
    """Take the role `role` from `user`; returns their roles afterwards."""
    return _change_role(db, by=by, user=user, role=role, grant=False)


def _change_role(db: Session, *, by: str, user: str, role: Role, grant: bool) -> frozenset[Role]:
    from cmbs.audit.service import record, refuse

    actor = authorize(db, by, Action.MANAGE_USERS)
    action = GRANT_ROLE if grant else REVOKE_ROLE
    # Lock the target user, so concurrent changes to one user's roles queue and each reads the
    # roles the previous one left.
    target = db.get(User, user, with_for_update=True, populate_existing=True)
    before = frozenset() if target is None else roles_of(db, user)
    problem = (
        UnknownUser(f"no user named {user!r}")
        if target is None
        else role_change_problem(by=by, user=user, role=role, current=before, grant=grant)
    )
    if problem is not None:
        refuse(
            db,
            problem,
            user=by,
            role=actor.role.value,
            action=action,
            subject=USER_SUBJECT,
            subject_key=user,
        )
    if grant:
        db.add(UserRole(user_name=user, role=role.value))
        after = before | {role}
    else:
        db.execute(delete(UserRole).where(UserRole.user_name == user, UserRole.role == role.value))
        after = before - {role}
    db.flush()
    record(
        db,
        user=by,
        role=actor.role.value,
        action=action,
        subject=USER_SUBJECT,
        subject_key=user,
        field="roles",
        before=format_roles(before),
        after=format_roles(after),
        detail={"role": role.value},
    )
    return after


def all_users(db: Session, by: str) -> dict[str, frozenset[Role]]:
    """Every user and their roles, by name. Needs `Action.MANAGE_USERS`."""
    authorize(db, by, Action.MANAGE_USERS)
    found: dict[str, set[Role]] = {name: set() for name in db.scalars(select(User.name))}
    for name, role in db.execute(select(UserRole.user_name, UserRole.role)):
        found[name].add(Role(role))
    return {name: frozenset(found[name]) for name in sorted(found)}
