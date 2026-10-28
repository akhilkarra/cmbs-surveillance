"""Request-scoped dependencies: a database session and the calling user."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from cmbs.db import engine

USER_HEADER = "X-CMBS-User"


def db_session() -> Iterator[Session]:
    with engine.session() as db:
        yield db


def current_user(x_cmbs_user: Annotated[str | None, Header()] = None) -> str:
    """The demo identifies users by name in a header; single sign-on replaces this later (Q37)."""
    if not x_cmbs_user:
        raise HTTPException(status_code=401, detail=f"send the {USER_HEADER} header")
    return x_cmbs_user


# Declare `User` before `DB` in route signatures: FastAPI resolves dependencies in order, so a
# request without a user is rejected before a database connection is opened.
User = Annotated[str, Depends(current_user)]
DB = Annotated[Session, Depends(db_session)]
