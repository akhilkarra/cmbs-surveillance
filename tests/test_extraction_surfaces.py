"""`cmbs extract` and the /extraction routes over the fixture deal's September 10-D."""

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cmbs import clock
from cmbs.api.app import create_app
from cmbs.cli import app as cli
from cmbs.distribution.service import load_report
from cmbs.domain import Period
from cmbs.extraction.service import extract_comments, open_review_items
from cmbs.model.client import ModelClient
from cmbs.model.recordings import DbRecordings
from cmbs.model.scripted import ScriptedProvider, choice_answer, respond
from cmbs.model.types import Choice, NoulAnswer
from cmbs.testing.world import FIXTURE_CIK, FIXTURE_DEAL
from tests.conftest import FIXTURES, needs_db

pytestmark = needs_db
runner = CliRunner()
REPORT = FIXTURES / "distribution" / "fixture-trust-2026-f1_10d_2026-09.htm"
RECORDINGS = FIXTURES / "model"
USER = {"X-CMBS-User": "quinn"}


@pytest.fixture
def loaded(seeded):
    clock.set_now(dt.datetime(2026, 10, 1, 12, tzinfo=dt.UTC))
    load_report(
        seeded,
        user="quinn",
        deal=FIXTURE_DEAL,
        accession="0009900001-26-000009",
        cik=FIXTURE_CIK,
        filing_date=dt.date(2026, 9, 29),
        file_name=REPORT.name,
        content=REPORT.read_bytes(),
        period=Period(2026, 9),
    )
    seeded.commit()
    return seeded


def _cli(*args: str):
    return runner.invoke(cli, ["extract", *args])


def test_cli_imports_extracts_and_shows(loaded, tmp_path):
    imported = _cli("import-recordings", str(RECORDINGS), "--user", "quinn")
    assert imported.output == f"imported 1 recordings from {RECORDINGS}\n"
    result = _cli("comments", FIXTURE_DEAL, "September 2026", "--loan", "2", "--user", "quinn")
    assert result.exit_code == 0, result.output
    assert result.output.startswith(
        f"{FIXTURE_DEAL}, September 2026: 1 comment, 0 sent to the model\n"
    )
    shown = _cli("show", FIXTURE_DEAL, "2", "September 2026", "--user", "pat")
    assert shown.exit_code == 0, shown.output
    assert "borrower request: maturity extension (auto-accepted, sentence 3)\n" in shown.output
    exported = _cli("export-recordings", str(tmp_path), "--user", "quinn")
    assert exported.output == f"wrote 1 recordings to {tmp_path}\n"


def test_cli_replay_without_a_recording_stops_with_the_reason(loaded):
    result = _cli("comments", FIXTURE_DEAL, "September 2026", "--user", "quinn")
    assert result.exit_code == 1
    assert result.output.startswith("no recorded answer for this input: loan 2 of ")


def test_cli_live_without_a_key_stops_before_sending(loaded, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    result = _cli("comments", FIXTURE_DEAL, "September 2026", "--live", "--user", "quinn")
    assert (result.exit_code, result.output) == (
        1,
        "set TYPESAFE_API_KEY to ask the model provider\n",
    )


def test_cli_rejects_a_bad_period_and_a_missing_extraction(loaded):
    bad = _cli("comments", FIXTURE_DEAL, "Sept 2026", "--user", "quinn")
    assert bad.exit_code == 2
    missing = _cli("show", FIXTURE_DEAL, "2", "August 2026", "--user", "pat")
    assert (missing.exit_code, missing.output) == (
        1,
        "loan 2 has no extracted comment for August 2026\n",
    )


@pytest.fixture
def waiting(loaded):
    """Loan 2's borrower request waiting for review."""

    def answer(request):
        value = request.questions["borrower_request.value"]
        cited = request.questions["borrower_request.sentence"]
        assert isinstance(value, Choice) and isinstance(cited, Choice)
        return respond(
            request,
            {
                "borrower_request.value": choice_answer(value, "forbearance", 0.5),
                "borrower_request.support": NoulAnswer(noul=0.9),
                "borrower_request.sentence": choice_answer(cited, list(cited.criteria)[2], 0.9),
            },
        )

    client = ModelClient(DbRecordings(loaded), provider=ScriptedProvider(answer), replay_only=False)
    extract_comments(loaded, user="quinn", deal=FIXTURE_DEAL, period=Period(2026, 9), client=client)
    loaded.commit()
    return open_review_items(loaded, "alex")[0]


def test_cli_shows_and_decides_a_review_item(waiting):
    shown = _cli("review", str(waiting.id), "--user", "alex")
    assert shown.exit_code == 0, shown.output
    assert shown.output.startswith(
        f"item {waiting.id}: loan 2 of {FIXTURE_DEAL}, September 2026, borrower request "
        "(open: unsettled answer)\n"
    )
    assert "probability: 0.50000\n" in shown.output
    decided = _cli("decide", str(waiting.id), "forbearance", "--sentence", "3", "--user", "alex")
    assert decided.output == "borrower request: forbearance (human-reviewed by alex)\n"
    refused = _cli("decide", str(waiting.id), "forbearance", "--sentence", "3", "--user", "alex")
    assert (refused.exit_code, refused.output) == (
        1,
        f"review item {waiting.id} was already decided by alex\n",
    )


def test_api_runs_extraction_and_reads_fields(loaded):
    client = TestClient(create_app())
    imported = runner.invoke(
        cli, ["extract", "import-recordings", str(RECORDINGS), "--user", "quinn"]
    )
    assert imported.exit_code == 0
    run = client.post(
        "/extraction/runs", json={"deal": FIXTURE_DEAL, "period": "September 2026"}, headers=USER
    )
    assert run.status_code == 200, run.text
    assert run.json()["comments"][0]["outcomes"]["dscr"] == "auto-accept"
    assert run.json()["comments"][0]["replayed"] is True
    read = client.get(
        "/extraction/comments",
        params={"deal": FIXTURE_DEAL, "loan": "2", "period": "September 2026"},
        headers={"X-CMBS-User": "pat"},
    )
    assert read.status_code == 200, read.text
    body = read.json()
    assert (body["accession"], body["filing_date"], body["period"]) == (
        "0009900001-26-000009",
        "2026-09-29",
        "September 2026",
    )
    dscr = next(field for field in body["fields"] if field["field"] == "dscr")
    assert (dscr["display"], dscr["sentence"], dscr["answer_probability"]) == (
        "0.71x",
        2,
        "0.97000",
    )


def test_api_refuses_like_the_service(loaded):
    client = TestClient(create_app())
    replay = client.post(
        "/extraction/runs", json={"deal": FIXTURE_DEAL, "period": "September 2026"}, headers=USER
    )
    assert replay.status_code == 422
    assert replay.json()["detail"].startswith("no recorded answer for this input")
    forbidden = client.post(
        "/extraction/runs",
        json={"deal": FIXTURE_DEAL, "period": "September 2026"},
        headers={"X-CMBS-User": "pat"},
    )
    assert forbidden.status_code == 403
    bad_period = client.get(
        "/extraction/comments",
        params={"deal": FIXTURE_DEAL, "loan": "2", "period": "soon"},
        headers=USER,
    )
    assert bad_period.status_code == 422
    missing = client.get(
        "/extraction/comments",
        params={"deal": FIXTURE_DEAL, "loan": "2", "period": "September 2026"},
        headers=USER,
    )
    assert (missing.status_code, missing.json()["detail"]) == (
        404,
        "loan 2 has no extracted comment",
    )


def test_api_lists_and_decides_review_items(waiting):
    client = TestClient(create_app())
    alex = {"X-CMBS-User": "alex"}
    [listed] = client.get("/extraction/review-items", headers=alex).json()
    assert (listed["id"], listed["probability"], listed["reason"]) == (
        waiting.id,
        "0.50000",
        "unsettled answer",
    )
    one = client.get(f"/extraction/review-items/{waiting.id}", headers=alex).json()
    assert one == listed
    decided = client.post(
        f"/extraction/review-items/{waiting.id}/decision",
        json={"value": "forbearance", "sentence": 3},
        headers=alex,
    )
    assert decided.status_code == 200, decided.text
    assert (decided.json()["status"], decided.json()["decided_by"]) == ("human-reviewed", "alex")
    assert client.get("/extraction/review-items", headers=alex).json() == []
