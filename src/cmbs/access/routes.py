"""Who am I: the calling user's roles."""

from fastapi import APIRouter

from cmbs.access.service import roles_of
from cmbs.api.deps import DB, User

router = APIRouter(prefix="/me", tags=["access"])


@router.get("")
def whoami(user: User, db: DB) -> dict[str, object]:
    return {"user": user, "roles": sorted(role.value for role in roles_of(db, user))}
