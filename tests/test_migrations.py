"""Migrations run up, down and up again on a real Postgres. Skipped without DATABASE_URL."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, make_url, text

from cmbs.db.models import LAYERS

PYPROJECT = Path(__file__).parents[1] / "pyproject.toml"
SCRATCH_DB = "cmbs_test_migrations"

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")


@pytest.fixture
def scratch_url(monkeypatch) -> Iterator[str]:
    """A throwaway database on the same server, so the dev database is never downgraded."""
    server = create_engine(os.environ["DATABASE_URL"], isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH_DB} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {SCRATCH_DB}"))
    url = make_url(os.environ["DATABASE_URL"]).set(database=SCRATCH_DB)
    rendered = url.render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", rendered)
    yield rendered
    with server.connect() as conn:
        conn.execute(text(f"DROP DATABASE {SCRATCH_DB} WITH (FORCE)"))
    server.dispose()


def installed(url: str) -> tuple[set[str], set[str]]:
    engine = create_engine(url)
    with engine.connect() as conn:
        extensions = set(conn.scalars(text("SELECT extname FROM pg_extension")))
        schemas = set(conn.scalars(text("SELECT nspname FROM pg_namespace")))
    engine.dispose()
    return extensions, schemas


def test_upgrade_downgrade_upgrade(scratch_url):
    config = Config(toml_file=str(PYPROJECT))

    command.upgrade(config, "head")
    extensions, schemas = installed(scratch_url)
    assert {"vector", "pg_trgm"} <= extensions
    assert set(LAYERS) <= schemas

    command.downgrade(config, "base")
    extensions, schemas = installed(scratch_url)
    assert not {"vector", "pg_trgm"} & extensions
    assert not set(LAYERS) & schemas

    command.upgrade(config, "head")
    extensions, schemas = installed(scratch_url)
    assert {"vector", "pg_trgm"} <= extensions
    assert set(LAYERS) <= schemas
