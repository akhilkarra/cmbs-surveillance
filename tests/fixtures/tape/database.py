"""One database per test module, and a session per test that rolls back.

Cloning a database per test costs a CREATE and a DROP DATABASE, each forcing a Postgres
checkpoint; under mutation testing, where a surviving mutant reruns every test that reaches it,
those checkpoints dominated the run. Tests that only need their own writes to vanish afterwards
use `tx`: a session on the module's database inside a transaction that is rolled back when the
test ends. `session.commit()` inside a test commits a savepoint only.

Tests whose work another connection must see (the API, the CLI) use `app_database`: the same
module database, exported as DATABASE_URL, where commits stay for the rest of the module, so
each test writes under its own accession numbers. Tests that need a database to themselves
(counting refusals, which `cmbs.audit.service` commits on its own connection, or two sessions
racing) keep the per-test `db` and `seeded` fixtures from tests/conftest.py.

Import the fixtures into a test module to use them (they register as `module_database`,
`tx` and `app_database`):
    from tests.fixtures.tape.database import module_database_fixture, tx_fixture  # noqa: F401
    from tests.fixtures.tape.database import app_database_fixture  # noqa: F401
"""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from cmbs.db import engine as cmbs_engine
from cmbs.db.registry import load_all_tables


@pytest.fixture(scope="module", name="module_database")
def module_database_fixture(db_template, request) -> Iterator[Engine]:
    """A database cloned once for the test module, with the fixture world seeded."""
    from cmbs.testing import databases, world

    load_all_tables()
    module = request.module.__name__.rsplit(".", 1)[-1]
    name = f"cmbs_pytest_{os.getpid()}_{module}"[:63]
    bind = create_engine(databases.clone(db_template, name))
    with Session(bind) as db:
        world.seed(db)
        db.commit()
    yield bind
    bind.dispose()
    cmbs_engine.dispose(bind.url.render_as_string(hide_password=False))
    databases.drop(name)


@pytest.fixture(name="app_database")
def app_database_fixture(module_database: Engine, monkeypatch) -> Iterator[Session]:
    """The module's database as DATABASE_URL, for the API and CLI, and a session on it.

    Writes are committed and stay for the rest of the module, so tests that write use their
    own accession numbers or deals.
    """
    monkeypatch.setenv("DATABASE_URL", module_database.url.render_as_string(hide_password=False))
    with Session(module_database) as session:
        yield session


@pytest.fixture(name="tx")
def tx_fixture(module_database: Engine) -> Iterator[Session]:
    """A session whose every write is rolled back when the test ends."""
    connection = module_database.connect()
    outer = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()
