"""Database connections from DATABASE_URL, one engine per URL."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from functools import cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

_engines: dict[str, Engine] = {}


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set; see .env.example.")
    return url


@cache
def _load_tables() -> None:
    """Import every feature's tables once, so foreign keys between features always resolve.

    Without this, a process that imports only one feature's service (say filings) cannot flush,
    because the table it references (silver.deal) was never declared in this process.
    """
    from cmbs.db.registry import load_all_tables

    load_all_tables()


def engine() -> Engine:
    _load_tables()
    url = database_url()
    if url not in _engines:
        _engines[url] = create_engine(url, pool_pre_ping=True)
    return _engines[url]


def dispose(url: str | None = None) -> None:
    """Close pooled connections for one URL, or all of them. Tests drop databases after use."""
    for key in [url] if url is not None else list(_engines):
        cached = _engines.pop(key, None)
        if cached is not None:
            cached.dispose()


@contextmanager
def session() -> Iterator[Session]:
    """A unit of work: commits on success, rolls back on any error."""
    with Session(engine(), expire_on_commit=False) as db:
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
