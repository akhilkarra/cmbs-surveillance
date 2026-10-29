"""Migration 0013 fills each stored loan row's interest rate from the tag kept in bronze.

Runs on its own scratch database: a tape is loaded at head, the rates are taken away by going
down to 0012, and coming back up must restore exactly what the loader reads.
"""

import datetime as dt
import os
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.orm import Session

from cmbs.db import engine
from cmbs.tape.service import load_tape
from tests.conftest import needs_db
from tests.fixtures.tape.tapes import asset, tape

pytestmark = needs_db
PYPROJECT = Path(__file__).parents[1] / "pyproject.toml"
SCRATCH_DB = f"cmbs_test_invariants_migration_{os.getpid()}"
RATE = "reportPeriodInterestRatePercentage"


@pytest.fixture
def scratch_url(monkeypatch) -> Iterator[str]:
    server = create_engine(os.environ["DATABASE_URL"], isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH_DB} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {SCRATCH_DB}"))
    url = make_url(os.environ["DATABASE_URL"]).set(database=SCRATCH_DB)
    rendered = url.render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", rendered)
    yield rendered
    engine.dispose(rendered)
    with server.connect() as conn:
        conn.execute(text(f"DROP DATABASE {SCRATCH_DB} WITH (FORCE)"))
    server.dispose()


def rates(url: str) -> dict[str, Decimal | None]:
    bind = create_engine(url)
    with bind.connect() as conn:
        rows = conn.execute(text("SELECT loan, interest_rate FROM silver.loan_period")).all()
    bind.dispose()
    return dict(sorted((loan, rate) for loan, rate in rows))


def test_going_up_to_0013_fills_rates_from_bronze(scratch_url):
    config = Config(toml_file=str(PYPROJECT))
    command.upgrade(config, "head")
    content = tape(
        asset("1", reportPeriodInterestRatePercentage="0.045"),
        asset("2", reportPeriodInterestRatePercentage=" .07747000 "),
        asset("3", reportPeriodInterestRatePercentage="0.00"),
        asset("4"),
        asset("5", reportPeriodInterestRatePercentage="5."),
    )
    with Session(create_engine(scratch_url)) as db:
        load_tape(
            db,
            deal="Migration Trust",
            content=content,
            file_name="migration-trust.xml",
            accession="0009900400-26-000001",
            filing_date=dt.date(2026, 9, 29),
            cik=9_900_400,
        )
        db.commit()
    loaded = {
        "1": Decimal("0.045"),
        "2": Decimal("0.07747000"),
        "3": Decimal("0.00"),
        "4": None,
        "5": Decimal("5"),
    }
    assert rates(scratch_url) == loaded

    command.downgrade(config, "0012")
    bind = create_engine(scratch_url)
    with bind.begin() as conn:
        # A value the loader would never have stored as a rate: the backfill must leave it null.
        conn.execute(
            text(
                "UPDATE bronze.loan_tape_asset SET tags = jsonb_set(tags, :path, '\"n/a\"') "
                "WHERE asset_number = '5'"
            ),
            {"path": "{" + RATE + "}"},
        )
    bind.dispose()
    command.upgrade(config, "head")
    assert rates(scratch_url) == loaded | {"5": None}
