"""`cmbs quality` and the /quality routes: thin adapters over the service."""

import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cmbs import clock
from cmbs.api.app import create_app
from cmbs.cli import app as cli
from tests.conftest import needs_db
from tests.test_quality_service import NOW, decided_comment

pytestmark = needs_db
runner = CliRunner()
BR = "borrower_request"
GOLDEN = {
    "items": [
        {"key": "G1", "comment": "c1", "labels": {"alex": {BR: "forbearance"}}},
        {"key": "G2", "comment": "c2", "labels": {"alex": {BR: None}, "morgan": {BR: None}}},
    ]
}
PREDICTIONS = {
    "model": "jev-1.14.0",
    "prompt_version": 3,
    "settings_version": 7,
    "predictions": {
        "G1": {BR: {"value": "forbearance", "automatic": True}},
        "G2": {BR: {"value": None, "automatic": False}},
    },
}


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_cli_loads_builds_and_shows(seeded, tmp_path):
    clock.set_now(NOW)
    golden = tmp_path / "golden.json"
    golden.write_text(json.dumps(GOLDEN))
    loaded = runner.invoke(cli, ["quality", "load-golden", str(golden), "--user", "quinn"])
    assert (loaded.exit_code, loaded.output) == (0, "2 golden comments in golden.json, 2 new\n")
    predictions = tmp_path / "predictions.json"
    predictions.write_text(json.dumps(PREDICTIONS))
    built = runner.invoke(cli, ["quality", "report", str(predictions), "--user", "quinn"])
    assert built.exit_code == 0, built.output
    assert built.output.startswith(
        "quality report 1: model jev-1.14.0, prompt version 3, settings version 7; 2 golden "
        "comments; built 2026-10-20 by quinn\n"
    )
    shown = runner.invoke(cli, ["quality", "show", "--user", "rae"])
    assert shown.output == built.output
    numbered = runner.invoke(cli, ["quality", "show", "1", "--user", "rae"])
    assert numbered.output == built.output


def test_cli_refuses_before_reading_and_says_what_is_wrong(seeded, tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{")
    refused = runner.invoke(cli, ["quality", "load-golden", str(broken), "--user", "alex"])
    assert (refused.exit_code, refused.output.strip()) == (1, "analyst may not propose settings")
    unreadable = runner.invoke(cli, ["quality", "load-golden", str(broken), "--user", "quinn"])
    assert unreadable.output.strip() == (
        "broken.json is not JSON: Expecting property name enclosed in double quotes at line 1"
    )
    missing = runner.invoke(cli, ["quality", "show", "--user", "rae"])
    assert (missing.exit_code, missing.output.strip()) == (1, "there is no quality report")


def test_cli_candidates(seeded):
    decided_comment(seeded)
    clock.set_now(NOW)
    none = runner.invoke(cli, ["quality", "candidates", "--user", "pat"])
    assert none.output == "no candidates waiting\n"
    proposed = runner.invoke(cli, ["quality", "extend", "--user", "quinn"])
    assert proposed.exit_code == 0, proposed.output
    counts, line = proposed.output.strip().splitlines()
    assert counts == "1 proposed, 0 decisions skipped"
    number = line.split()[0].lstrip("#")
    assert line.endswith(
        "proposed: Fixture Trust 2026-F1 loan 2, September 2026: transfer reason imminent "
        "monetary default (sentence 1) by alex"
    )
    again = runner.invoke(cli, ["quality", "extend", "--user", "quinn"])
    assert again.output == "0 proposed, 1 decision skipped\n"
    waiting = runner.invoke(cli, ["quality", "candidates", "--user", "pat"])
    assert waiting.output.strip() == line
    rejected = runner.invoke(
        cli, ["quality", "reject", number, "--reason", "wrong", "--user", "morgan"]
    )
    assert rejected.output.strip().endswith("; rejected: wrong")
    late = runner.invoke(cli, ["quality", "confirm", number, "--user", "morgan"])
    assert (late.exit_code, late.output.strip()) == (
        1,
        f"golden item {number} is already rejected",
    )


def test_api(seeded, client):
    clock.set_now(NOW)
    quant = {"X-CMBS-User": "quinn"}
    loaded = client.post(
        "/quality/golden-set", json={"source_name": "g.json", **GOLDEN}, headers=quant
    )
    assert (loaded.status_code, loaded.json()) == (200, {"items": 2, "new": 2})
    built = client.post("/quality/reports", json=PREDICTIONS, headers=quant)
    assert built.status_code == 200, built.text
    body = built.json()
    assert (body["id"], body["model"], body["settings_version"], body["band"]) == (
        1,
        "jev-1.14.0",
        7,
        "not holding",
    )
    assert body["fields"][BR]["precision"]["rate"] == "1.000"
    reader = {"X-CMBS-User": "rae"}
    assert client.get("/quality/reports/latest", headers=reader).json() == body
    assert client.get("/quality/reports/1", headers=reader).json() == body
    assert client.get("/quality/reports/2", headers=reader).status_code == 422
    refused = client.post("/quality/reports", json={"model": ""}, headers=reader)
    assert (refused.status_code, refused.json()["detail"]) == (
        403,
        "risk may not propose settings",
    )
    bad = client.post("/quality/reports", json={"model": ""}, headers=quant)
    assert (bad.status_code, bad.json()["detail"]) == (422, "the model must be non-empty text")


def test_api_candidates(seeded, client):
    decided_comment(seeded)
    clock.set_now(NOW)
    proposed = client.post("/quality/candidates", headers={"X-CMBS-User": "quinn"})
    assert proposed.json()["skipped"] == 0
    (candidate,) = proposed.json()["proposed"]
    assert (candidate["loan"], candidate["period"], candidate["status"]) == (
        "2",
        "September 2026",
        "proposed",
    )
    listed = client.get("/quality/candidates", headers={"X-CMBS-User": "pat"})
    assert [c["id"] for c in listed.json()] == [candidate["id"]]
    same = client.post(
        f"/quality/candidates/{candidate['id']}/confirm", headers={"X-CMBS-User": "alex"}
    )
    assert (same.status_code, same.json()["detail"]) == (
        422,
        "alex decided this label; a second analyst must confirm it",
    )
    confirmed = client.post(
        f"/quality/candidates/{candidate['id']}/confirm", headers={"X-CMBS-User": "morgan"}
    )
    assert (confirmed.status_code, confirmed.json()["status"]) == (200, "golden")
    rejected = client.post(
        f"/quality/candidates/{candidate['id']}/reject",
        json={"reason": "no"},
        headers={"X-CMBS-User": "morgan"},
    )
    assert rejected.json()["detail"] == f"golden item {candidate['id']} is already golden"
