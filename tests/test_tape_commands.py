"""`cmbs tape load` and `cmbs tape staleness`."""

import pytest
from sqlalchemy import func, select
from typer.testing import CliRunner

from cmbs.audit.tables import AuditEntry
from cmbs.cli import app
from cmbs.tape.tables import LoanPeriod
from tests.conftest import FIXTURES, needs_db

DEAL = "Fixture Trust 2026-F1"
ACCESSION = "0009900001-26-000101"
TAPE = FIXTURES / "schedule_al_minimal.xml"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def load(runner: CliRunner, *extra: str, user: str = "quinn"):
    arguments = ["tape", "load", str(TAPE), DEAL, ACCESSION, "2026-09-29", "--user", user]
    return runner.invoke(app, [*arguments, "--cik", "9900001", *extra])


@needs_db
def test_load_prints_the_summary_and_audits_each_loan(seeded, runner):
    result = load(runner)
    assert result.exit_code == 0, result.output
    assert result.output == (
        "Fixture Trust 2026-F1 September 2026 (0009900001-26-000101, filed 2026-09-29): "
        "loaded; 9 assets, 7 loans loaded, 0 loans quarantined\n"
    )
    assert seeded.scalar(select(func.count()).select_from(LoanPeriod)) == 7
    users = set(seeded.scalars(select(AuditEntry.user_name).where(AuditEntry.subject == "loan")))
    assert users == {"quinn"}


@needs_db
def test_loading_again_says_nothing_changed(seeded, runner):
    load(runner)
    result = load(runner)
    assert result.exit_code == 0
    assert "already loaded, nothing changed" in result.output


@needs_db
def test_quarantined_loans_are_listed(seeded, runner, tmp_path):
    bad = tmp_path / "ex102.xml"
    bad.write_bytes(
        TAPE.read_bytes().replace(
            b"<reportPeriodEndActualBalanceAmount>12000000.00<",
            b"<reportPeriodEndActualBalanceAmount>-12000000.00<",
        )
    )
    arguments = ["tape", "load", str(bad), DEAL, ACCESSION, "2026-09-29", "--user", "olly"]
    result = runner.invoke(app, [*arguments, "--cik", "9900001"])
    assert result.exit_code == 0, result.output
    assert result.output.endswith(
        "1 loan quarantined\n  quarantined loan 5: ending balance is negative\n"
    )


@needs_db
def test_a_user_who_may_not_load_is_refused(seeded, runner):
    result = load(runner, user="pat")
    assert result.exit_code == 1
    assert "refused: pm may not run refreshes and backfills" in result.output
    assert seeded.scalar(select(func.count()).select_from(LoanPeriod)) == 0


@needs_db
def test_staleness_prints_each_loan_and_the_summary(seeded, runner):
    load(runner)
    result = runner.invoke(app, ["tape", "staleness", DEAL, "2026-09-29", "--user", "pat"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == "Fixture Trust 2026-F1 September 2026 as of 2026-09-29"
    assert lines[1] == "  loan 1: 73 days old at period end, 91 days old at filing"
    assert lines[5] == "  loan 5: no updated financials"
    assert lines[-1].startswith("4 active loans, 3 with updated financials, 1 without")


@needs_db
def test_staleness_of_an_unknown_deal_is_refused(seeded, runner):
    result = runner.invoke(app, ["tape", "staleness", "Nope", "2026-09-29", "--user", "pat"])
    assert result.exit_code == 1
    assert "refused: no deal named 'Nope'" in result.output
