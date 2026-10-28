"""`cmbs tape load` and `cmbs tape staleness`.

The tests share one module database (`app_database`); each loads under its own deal, so what
one test commits never changes what another sees.
"""

from pathlib import Path

import pytest
from sqlalchemy import func, select
from typer.testing import CliRunner

from cmbs.audit.tables import AuditEntry
from cmbs.cli import app
from cmbs.tape.tables import LoanPeriod
from tests.conftest import FIXTURES, needs_db
from tests.fixtures.tape.database import app_database_fixture, module_database_fixture  # noqa: F401
from tests.fixtures.tape.tapes import asset, tape

TAPE = FIXTURES / "schedule_al_minimal.xml"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def accession(deal: str) -> str:
    """Each test's deal ("Command Trust 3") files under its own accession (...-26-000103)."""
    return f"0009900001-26-{100 + int(deal.rsplit(' ', 1)[-1]):06d}"


def cik(deal: str) -> str:
    """Each test's deal is its own trust, so it has its own CIK (Command Trust 3: 9900103)."""
    return str(9_900_100 + int(deal.rsplit(" ", 1)[-1]))


def load(runner: CliRunner, deal: str, *, path: Path = TAPE, user: str = "quinn"):
    arguments = ["tape", "load", str(path), deal, accession(deal), "2026-09-29", "--user", user]
    return runner.invoke(app, [*arguments, "--cik", cik(deal)])


def loan_rows(db, deal: str) -> int:
    from cmbs.deals.tables import Deal

    query = select(func.count()).select_from(LoanPeriod).join(Deal).where(Deal.name == deal)
    return db.scalar(query) or 0


@needs_db
def test_load_prints_the_summary_and_audits_each_loan(app_database, runner):
    deal = "Command Trust 1"
    result = load(runner, deal)
    assert result.exit_code == 0, result.output
    assert result.output == (
        "Command Trust 1 September 2026 (0009900001-26-000101, filed 2026-09-29): "
        "loaded; 9 assets, 7 loans loaded, 0 loans quarantined\n"
    )
    assert loan_rows(app_database, deal) == 7
    users = set(
        app_database.scalars(
            select(AuditEntry.user_name).where(
                AuditEntry.subject == "loan", AuditEntry.subject_key.startswith(f"{deal}/")
            )
        )
    )
    assert users == {"quinn"}


@needs_db
def test_loading_again_says_nothing_changed(app_database, runner):
    load(runner, "Command Trust 2")
    result = load(runner, "Command Trust 2")
    assert result.exit_code == 0
    assert "already loaded, nothing changed" in result.output


@needs_db
def test_quarantined_loans_are_listed(app_database, runner, tmp_path):
    bad = tmp_path / "ex102.xml"
    bad.write_bytes(
        TAPE.read_bytes().replace(
            b"<reportPeriodEndActualBalanceAmount>12000000.00<",
            b"<reportPeriodEndActualBalanceAmount>-12000000.00<",
        )
    )
    result = load(runner, "Command Trust 3", path=bad, user="olly")
    assert result.exit_code == 0, result.output
    assert result.output.endswith(
        "1 loan quarantined\n  quarantined loan 5: ending balance is negative\n"
    )


@needs_db
def test_property_notes_are_listed(app_database, runner, tmp_path):
    portfolio = tmp_path / "ex102.xml"
    portfolio.write_bytes(
        tape(
            asset("1").replace(
                "</assets>",
                "<property><mostRecentFinancialsEndDate>06-30-2026</mostRecentFinancialsEndDate>"
                "</property><property></property></assets>",
            )
        )
    )
    result = load(runner, "Command Trust 4", path=portfolio, user="olly")
    assert result.exit_code == 0, result.output
    assert result.output.endswith(
        "1 loan with property notes\n  loan 1: 1 of 2 properties report no most recent "
        "financials, so the loan's NOI, NCF and DSCR are not reported\n"
    )


@needs_db
def test_a_user_who_may_not_load_is_refused(app_database, runner):
    result = load(runner, "Command Trust 5", user="pat")
    assert result.exit_code == 1
    assert "refused: pm may not run refreshes and backfills" in result.output
    assert loan_rows(app_database, "Command Trust 5") == 0


@needs_db
def test_staleness_prints_each_loan_and_the_summary(app_database, runner):
    load(runner, "Command Trust 6")
    staleness = ["tape", "staleness", "Command Trust 6", "2026-09-29", "--user", "pat"]
    result = runner.invoke(app, staleness)
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == "Command Trust 6 September 2026 as of 2026-09-29"
    assert lines[1] == "  loan 1: 73 days old at period end, 91 days old at filing"
    assert lines[5] == "  loan 5: no updated financials"
    assert lines[-1].startswith("4 active loans, 3 with updated financials, 1 without")


@needs_db
def test_staleness_of_an_unknown_deal_is_refused(app_database, runner):
    result = runner.invoke(app, ["tape", "staleness", "Nope", "2026-09-29", "--user", "pat"])
    assert result.exit_code == 1
    assert "refused: no deal named 'Nope'" in result.output
