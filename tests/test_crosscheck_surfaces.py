"""`cmbs crosscheck` and the /checks routes: thin adapters over the service."""

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cmbs import clock
from cmbs.api.app import create_app
from cmbs.cli import app as cli
from tests.conftest import needs_db
from tests.test_crosscheck_service import DEAL, at, two_months

pytestmark = needs_db
runner = CliRunner()
USER = {"X-CMBS-User": "quinn"}


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def loaded(seeded):
    two_months(seeded)
    clock.set_now(at(dt.date(2026, 10, 1)))
    return seeded


def test_cli_runs_the_checks_and_lists_flags(loaded):
    result = runner.invoke(
        cli, ["crosscheck", "run", "--deal", DEAL, "--period", "September 2026", "--user", "quinn"]
    )
    assert result.exit_code == 0, result.output
    assert result.output == (
        f"{DEAL}, September 2026, as of 2026-10-01\n"
        "2 raised, 0 already raised\n"
        "  loan 7: flag: balance does not roll forward\n"
        "  loan 7: flag: beginning balance does not match last month's ending balance\n"
        "roll-forward: 1 roll-forward break, 1 passed, 1 not checked\n"
    )
    listed = runner.invoke(cli, ["crosscheck", "flags", "--deal", DEAL, "--user", "rae"])
    assert listed.exit_code == 0, listed.output
    lines = listed.output.splitlines()
    assert lines[0].endswith(" September 2026 loan 7: flag: balance does not roll forward")
    assert "    gap: $10,000.00" in lines
    empty = runner.invoke(
        cli, ["crosscheck", "flags", "--deal", DEAL, "--loan", "1", "--user", "rae"]
    )
    assert empty.output == "no flags\n"


def test_cli_runs_one_check_and_refuses_unknown_ones(loaded):
    only = runner.invoke(
        cli,
        [
            "crosscheck",
            "run",
            "--deal",
            DEAL,
            "--period",
            "September 2026",
            "--only",
            "text-vs-tape",
            "--user",
            "quinn",
        ],
    )
    assert only.output == f"{DEAL}, September 2026, as of 2026-10-01\n0 raised, 0 already raised\n"
    bad = runner.invoke(
        cli,
        ["crosscheck", "run", "--deal", DEAL, "--period", "September 2026", "--only", "x"],
        env={"CMBS_USER": "quinn"},
    )
    assert bad.exit_code == 2
    assert "choose one of: all, text-vs-tape, rollforward" in bad.output


def test_cli_prints_the_rollforward_without_raising_flags(loaded):
    result = runner.invoke(
        cli,
        [
            "crosscheck",
            "rollforward",
            "--deal",
            DEAL,
            "--period",
            "September 2026",
            "--as-of",
            "2026-10-01",
            "--user",
            "pat",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        f"{DEAL}, September 2026, as of 2026-10-01: 1 roll-forward break, 1 passed, 1 not checked",
        "loan 1: roll-forward passed: unscheduled principal, other principal adjustments, "
        "realized loss not reported",
        "loan 7: roll-forward break: balance does not roll forward by $10,000.00; beginning "
        "balance does not match last month's ending balance by $10,000.00",
        "loan 9: roll-forward not checked: beginning balance not reported",
    ]
    flags = runner.invoke(cli, ["crosscheck", "flags", "--deal", DEAL, "--user", "pat"])
    assert flags.output == "no flags\n"
    early = runner.invoke(
        cli,
        [
            "crosscheck",
            "rollforward",
            "--deal",
            DEAL,
            "--period",
            "September 2026",
            "--as-of",
            "2026-09-01",
            "--user",
            "pat",
        ],
    )
    assert early.output == f"{DEAL}, September 2026, as of 2026-09-01: no loan tape filed\n"


def test_cli_prints_refusals_and_bad_months(loaded):
    refused = runner.invoke(
        cli, ["crosscheck", "run", "--deal", DEAL, "--period", "September 2026", "--user", "pat"]
    )
    assert (refused.exit_code, refused.output.strip()) == (
        1,
        "pm may not run refreshes and backfills",
    )
    unknown = runner.invoke(
        cli,
        ["crosscheck", "rollforward", "--deal", "Nope", "--period", "September 2026"],
        env={"CMBS_USER": "pat"},
    )
    assert unknown.output.strip() == "no deal named 'Nope'"
    month = runner.invoke(
        cli, ["crosscheck", "flags", "--deal", DEAL, "--period", "Sept", "--user", "pat"]
    )
    assert month.exit_code == 2
    assert "not a period like 'September 2026'" in month.output


def test_api_runs_and_reads(loaded, client):
    ran = client.post("/checks/run", json={"deal": DEAL, "period": "September 2026"}, headers=USER)
    assert ran.status_code == 200, ran.text
    body = ran.json()
    assert [(f["loan"], f["kind"], f["new"]) for f in body["flags"]] == [
        ("7", "flag", True),
        ("7", "flag", True),
    ]
    assert [r["status"] for r in body["rollforward"]] == ["passed", "break", "not checked"]
    assert body["text_vs_tape"] == []

    roll = client.get(
        "/checks/rollforward",
        params={"deal": DEAL, "period": "September 2026", "as_of": "2026-10-01"},
        headers={"X-CMBS-User": "pat"},
    )
    assert roll.status_code == 200, roll.text
    seven = roll.json()[1]
    assert (seven["loan"], seven["roll"]["gap"], seven["continuity"]["gap"]) == (
        "7",
        "10000.00",
        "10000.00",
    )
    text = client.get(
        "/checks/text-vs-tape",
        params={"deal": DEAL, "period": "September 2026"},
        headers={"X-CMBS-User": "pat"},
    )
    assert (text.status_code, text.json()) == (200, [])
    flags = client.get(
        "/checks/flags", params={"deal": DEAL, "loan": "7"}, headers={"X-CMBS-User": "rae"}
    )
    assert [(f["loan"], f["period"], f["kind"]) for f in flags.json()] == [
        ("7", "September 2026", "flag"),
        ("7", "September 2026", "flag"),
    ]
    month = client.get(
        "/checks/flags",
        params={"deal": DEAL, "period": "September 2026"},
        headers={"X-CMBS-User": "rae"},
    )
    assert len(month.json()) == 2


def test_api_refusals(loaded, client):
    pm = client.post(
        "/checks/run",
        json={"deal": DEAL, "period": "September 2026"},
        headers={"X-CMBS-User": "pat"},
    )
    assert (pm.status_code, pm.json()) == (
        403,
        {"detail": "pm may not run refreshes and backfills"},
    )
    operator = client.get(
        "/checks/rollforward",
        params={"deal": DEAL, "period": "September 2026"},
        headers={"X-CMBS-User": "olly"},
    )
    assert (operator.status_code, operator.json()["detail"]) == (
        403,
        "operator may not read the panel",
    )
    unknown = client.get(
        "/checks/text-vs-tape",
        params={"deal": "Nope", "period": "September 2026"},
        headers={"X-CMBS-User": "pat"},
    )
    assert (unknown.status_code, unknown.json()["detail"]) == (422, "no deal named 'Nope'")
    bad = client.get(
        "/checks/flags", params={"deal": DEAL, "period": "Sept"}, headers={"X-CMBS-User": "pat"}
    )
    assert bad.status_code == 422
    nobody = client.get("/checks/flags", params={"deal": DEAL})
    assert nobody.status_code == 401
