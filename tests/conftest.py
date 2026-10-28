import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.db import engine

FIXTURES = Path(__file__).parent / "fixtures"
HAS_DB = bool(os.environ.get("DATABASE_URL"))
needs_db = pytest.mark.skipif(not HAS_DB, reason="DATABASE_URL not set")


@pytest.fixture
def schedule_al_path() -> Path:
    return FIXTURES / "schedule_al_minimal.xml"


@pytest.fixture(autouse=True)
def _release_clock() -> Iterator[None]:
    yield
    clock.set_now(None)


@pytest.fixture(scope="session")
def db_template() -> Iterator[str]:
    """A migrated template database for this test process."""
    from cmbs.testing import databases

    os.environ.setdefault("CMBS_TEST_SERVER_URL", os.environ["DATABASE_URL"])
    name = f"cmbs_pytest_template_{os.getpid()}"
    databases.create_template(name)
    yield name
    databases.drop(name)


@pytest.fixture
def db_url(db_template, monkeypatch, request) -> Iterator[str]:
    """A fresh database cloned from the template, exported as DATABASE_URL for this test."""
    from cmbs.testing import databases

    name = f"cmbs_pytest_{os.getpid()}_{abs(hash(request.node.nodeid)) % 10**8}"
    url = databases.clone(db_template, name)
    monkeypatch.setenv("DATABASE_URL", url)
    yield url
    engine.dispose(url)
    databases.drop(name)


@pytest.fixture
def db(db_url) -> Iterator[Session]:
    """A session on the test's own database; committed work is visible to other sessions."""
    with engine.session() as session:
        yield session


@pytest.fixture
def seeded(db) -> Session:
    """The fixture world: six users and settings version 7."""
    from cmbs.testing import world

    world.seed(db)
    db.commit()
    return db
