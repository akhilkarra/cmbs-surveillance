"""Scratch Postgres databases cloned from a migrated template.

The template is migrated once per process; each test or scenario gets its own copy, which
Postgres makes by copying files (fast) rather than re-running migrations.
"""

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, make_url, text

PYPROJECT = Path(__file__).resolve().parents[3] / "pyproject.toml"


def server_url() -> str:
    url = os.environ.get("CMBS_TEST_SERVER_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("set DATABASE_URL to a Postgres server for database tests")
    return url


# A test that waits on a lock is a bug (or a mutant), not a slow test: fail it fast.
TEST_CONNECTION_OPTIONS = "-c lock_timeout=5000 -c statement_timeout=30000"


def _url_for(name: str) -> str:
    url = make_url(server_url()).set(database=name)
    url = url.update_query_dict({"options": TEST_CONNECTION_OPTIONS})
    return url.render_as_string(hide_password=False)


def _admin(sql: str) -> None:
    engine = create_engine(server_url(), isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(text(sql))
    engine.dispose()


def create_template(name: str) -> str:
    """Drop and recreate `name`, migrate it to head, and return its URL."""
    _admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    _admin(f'CREATE DATABASE "{name}"')
    url = _url_for(name)
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        command.upgrade(Config(toml_file=str(PYPROJECT)), "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
    return url


def clone(template: str, name: str) -> str:
    _admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    _admin(f'CREATE DATABASE "{name}" TEMPLATE "{template}"')
    return _url_for(name)


def drop(name: str) -> None:
    _admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
