"""Migration 0013 fills each stored loan row's interest rate and its note from the tag kept in
bronze, exactly as the tape loader reads them.

Runs on its own scratch database: a tape is loaded at head, the columns are taken away by going
down to 0012, and coming back up must restore exactly what the loader stored.
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


def rates(url: str) -> dict[str, tuple[Decimal | None, str | None]]:
    bind = create_engine(url)
    with bind.connect() as conn:
        rows = conn.execute(
            text("SELECT loan, interest_rate, interest_rate_note FROM silver.loan_period")
        ).all()
    bind.dispose()
    return {loan: (rate, note) for loan, rate, note in sorted(rows)}


# Every kind of rate text: usable, zero, missing, empty, not a number, a percent written for a
# fraction, negative, and the limit itself.
RATES = {
    "1": "0.045",
    "2": " .07747000 ",
    "3": "0.00",
    "4": None,
    "5": "",
    "6": "n/a",
    "7": "4.5",
    "8": "-0.01",
    "9": "0.25",
    "10": "5.",
}


def test_the_backfill_reads_rates_exactly_as_the_loader_does(scratch_url):
    config = Config(toml_file=str(PYPROJECT))
    command.upgrade(config, "head")
    content = tape(
        *(
            asset(loan) if rate is None else asset(loan, reportPeriodInterestRatePercentage=rate)
            for loan, rate in RATES.items()
        )
    )
    bind = create_engine(scratch_url)
    with Session(bind) as db:
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
    bind.dispose()
    loaded = rates(scratch_url)
    # Every loan loads, whatever its rate; a rate that cannot be used carries the reason.
    assert loaded == {
        "1": (Decimal("0.045"), None),
        "10": (Decimal("5"), "interest rate '5.' is outside 0 to 0.25"),
        "2": (Decimal("0.07747000"), None),
        "3": (Decimal("0.00"), None),
        "4": (None, None),
        "5": (None, None),
        "6": (None, "interest rate is not a number: 'n/a'"),
        "7": (Decimal("4.5"), "interest rate '4.5' is outside 0 to 0.25"),
        "8": (Decimal("-0.01"), "interest rate '-0.01' is outside 0 to 0.25"),
        "9": (Decimal("0.25"), None),
    }

    # Going down drops the columns; coming back up must fill them from bronze identically.
    command.downgrade(config, "0012")
    command.upgrade(config, "head")
    assert rates(scratch_url) == loaded
