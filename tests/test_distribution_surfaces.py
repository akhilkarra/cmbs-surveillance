"""`cmbs reports parse` and `GET /reports/comments` (cmbs.distribution.commands and .routes)."""

from pathlib import Path

import click
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cmbs.api.app import create_app
from cmbs.cli import app as cli
from cmbs.db.registry import all_command_modules, all_route_modules
from tests.conftest import needs_db

REPORT = (
    Path(__file__).parent / "fixtures" / "distribution" / "fixture-trust-2026-f1_10d_2026-09.htm"
)
ACCESSION = "0009900001-26-000009"
ARGS = [
    "reports",
    "parse",
    str(REPORT),
    "--deal",
    "Fixture Trust 2026-F1",
    "--accession",
    ACCESSION,
    "--cik",
    "9900001",
    "--filing-date",
    "2026-09-29",
]


def test_the_command_group_and_router_are_discovered():
    assert "cmbs.distribution.commands" in {module.__name__ for module in all_command_modules()}
    assert "cmbs.distribution.routes" in {module.__name__ for module in all_route_modules()}


def usage_text(output: str) -> str:
    """A usage error as plain words: without colours, the panel's border, or line wraps.

    Typer draws usage errors with Rich, which styles them when the environment asks for colour
    (as CI does) and wraps them to the terminal width.
    """
    return " ".join(click.unstyle(output).replace("│", " ").split())


def test_a_bad_period_is_a_usage_error():
    result = CliRunner().invoke(cli, [*ARGS, "--user", "quinn", "--period", "Smarch 2026"])
    assert result.exit_code == 2
    assert "not a period like 'September 2026'" in usage_text(result.output)


def test_the_user_is_required():
    result = CliRunner().invoke(cli, ARGS, env={"CMBS_USER": ""})
    assert result.exit_code == 2
    assert "Missing option '--user'" in usage_text(result.output)


def test_usage_text_strips_styles_borders_and_wraps():
    assert usage_text("\x1b[31m│\x1b[0m Missing option \x1b[1m'--user'\x1b[0m.\n│ next  │") == (
        "Missing option '--user'. next"
    )


@needs_db
def test_parse_prints_the_summary(seeded):
    result = CliRunner().invoke(
        cli, [*ARGS, "--period", "September 2026"], env={"CMBS_USER": "quinn"}
    )
    assert result.exit_code == 0, result.output
    assert result.stdout == (
        f"Fixture Trust 2026-F1 September 2026 ({ACCESSION})\n"
        "computershare format\n"
        "1 special-servicing comments\n"
        "1 appraisal reductions\n"
        "2 loans in delinquency detail\n"
    )


@needs_db
def test_parse_reports_a_refusal(seeded):
    result = CliRunner().invoke(cli, [*ARGS, "--user", "pat"])
    assert result.exit_code == 1
    assert result.stderr == "pm may not run refreshes and backfills\n"
    assert result.stdout == ""


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_comments_need_a_user(client):
    response = client.get("/reports/comments", params={"deal": "x", "loan": "1"})
    assert response.status_code == 401


@needs_db
def test_a_loans_comments(seeded, client):
    assert CliRunner().invoke(cli, [*ARGS, "--user", "quinn"]).exit_code == 0
    params = {"deal": "Fixture Trust 2026-F1", "loan": "02", "as_of": "2026-10-01"}
    response = client.get("/reports/comments", params=params, headers={"X-CMBS-User": "pat"})
    assert response.status_code == 200
    [comment] = response.json()
    assert {key: value for key, value in comment.items() if key != "comment"} == {
        "loan": "2",
        "period": "September 2026",
        "accession": ACCESSION,
        "filing_date": "2026-09-29",
        "source_table": "Specially Serviced Loan Detail - Part 2",
    }
    assert comment["comment"].startswith("The loan transferred to Special Servicing effective")
    early = client.get(
        "/reports/comments",
        params={**params, "as_of": "2026-09-28"},
        headers={"X-CMBS-User": "pat"},
    )
    assert early.json() == []


@needs_db
def test_comments_are_refused_to_roles_that_do_not_read_the_panel(seeded, client):
    response = client.get(
        "/reports/comments",
        params={"deal": "Fixture Trust 2026-F1", "loan": "2"},
        headers={"X-CMBS-User": "olly"},
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "operator may not read the panel"}


@needs_db
def test_comments_for_an_unknown_deal(seeded, client):
    response = client.get(
        "/reports/comments", params={"deal": "Nobody", "loan": "2"}, headers={"X-CMBS-User": "pat"}
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "no deal named 'Nobody'"}
