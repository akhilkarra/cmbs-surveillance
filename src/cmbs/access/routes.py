"""Who am I, who holds which roles, and role changes."""

from fastapi import APIRouter

from cmbs.access.roles import ROLE_ORDER, Role
from cmbs.access.service import all_users, grant_role, revoke_role, roles_of
from cmbs.api.deps import DB, User

router = APIRouter(tags=["access"])


def _roles(roles: frozenset[Role]) -> list[str]:
    return [role.value for role in ROLE_ORDER if role in roles]


@router.get("/me")
def whoami(user: User, db: DB) -> dict[str, object]:
    return {"user": user, "roles": sorted(role.value for role in roles_of(db, user))}


@router.get("/users")
def list_users(user: User, db: DB) -> dict[str, list[str]]:
    """Every user and their roles. Needs the right to manage users."""
    return {name: _roles(roles) for name, roles in all_users(db, user).items()}


@router.put("/users/{name}/roles/{role}")
def grant(user: User, db: DB, name: str, role: Role) -> dict[str, object]:
    """Give `name` the role; audited."""
    return {"user": name, "roles": _roles(grant_role(db, by=user, user=name, role=role))}


@router.delete("/users/{name}/roles/{role}")
def revoke(user: User, db: DB, name: str, role: Role) -> dict[str, object]:
    """Take the role from `name`; audited."""
    return {"user": name, "roles": _roles(revoke_role(db, by=user, user=name, role=role))}
