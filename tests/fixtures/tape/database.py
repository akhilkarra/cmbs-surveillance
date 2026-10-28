"""One database per test module, and a session per test that rolls back.

Cloning a database per test costs a CREATE and a DROP DATABASE, each forcing a Postgres
checkpoint; under mutation testing, where a surviving mutant reruns every test that reaches it,
those checkpoints dominated the run. Tests that only need their own writes to vanish afterwards
use `tx`: a session on the module's database inside a transaction that is rolled back when the
test ends. `session.commit()` inside a test commits a savepoint only.

Not for tests whose work must be seen by another connection (the API, the CLI, a second
thread) or that record refusals, which `cmbs.audit.service` commits on its own connection:
those keep the per-test `db` and `seeded` fixtures from tests/conftest.py.

Import both fixtures into a test module to use them (they register as `module_database`
and `tx`):
    from tests.fixtures.tape.database import module_database_fixture, tx_fixture  # noqa: F401
"""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

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
    databases.drop(name)


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
