"""`cmbs review` and the /review routes: the queue, its summary, declines, overrides, history and
label candidates, with refusals as the service words them."""

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cmbs import clock
from cmbs.api.app import create_app
from cmbs.cli import app as cli
from cmbs.testing.world import FIXTURE_DEAL
from tests.conftest import needs_db
from tests.test_review_world import SECOND_DEAL, loan_two, open_queue

pytestmark = needs_db
runner = CliRunner()
NOW = dt.datetime(2026, 10, 6, 9, tzinfo=dt.UTC)
REASON = "the request is for payment relief, not more time"


def _as(user: str) -> dict[str, str]:
    return {"X-CMBS-User": user}


def _cli(*args: str):
    return runner.invoke(cli, ["review", *args])


@pytest.fixture
def items(seeded) -> dict[str, int]:
    ids = open_queue(seeded)
    seeded.commit()
    clock.set_now(NOW)
    return ids


@pytest.fixture
def accepted(seeded):
    loan_two(seeded, answer="maturity extension", answer_p=0.93)
    seeded.commit()
    clock.set_now(NOW)
    return seeded


def test_cli_queue_summary_and_decline(items):
    shown = _cli("queue", "--user", "alex")
    assert shown.exit_code == 0, shown.output
    assert shown.output == (
        f"item {items['B']}: loan 3 of {SECOND_DEAL}, September 2026, borrower request "
        "(unsettled answer); exposure $40,000,000.00; since 2026-10-05, waiting 1 business day\n"
        f"item {items['C']}: loan 5 of {FIXTURE_DEAL}, September 2026, transfer reason "
        "(unsettled answer); exposure $25,000,000.00; since 2026-09-26, overdue, waiting 7 "
        "business days\n"
        f"item {items['A']}: loan 2 of {FIXTURE_DEAL}, September 2026, borrower request "
        "(unsettled answer); exposure $25,000,000.00; since 2026-10-01, waiting 3 business days\n"
    )
    earlier = _cli("queue", "--user", "alex", "--on", "2026-09-30")
    assert (
        earlier.output.startswith(f"item {items['C']}: loan 5") and earlier.output.count("\n") == 1
    )
    summary = _cli("summary", "--user", "rae", "--on", "2026-10-06")
    assert summary.output == (
        "3 open items on 2026-10-06, 1 overdue\n"
        "oldest waiting since 2026-09-26 (overdue, waiting 7 business days)\n"
        "transfer reason: 1\nborrower request: 2\n"
    )
    declined = _cli("decline", str(items["A"]), "too vague to settle", "--user", "alex")
    assert declined.output == f"item {items['A']}: borrower request not determinable by alex\n"
    refused = _cli("decline", str(items["A"]), "again", "--user", "alex")
    assert (refused.exit_code, refused.output) == (
        1,
        f"review item {items['A']} was already decided by alex\n",
    )
    assert _cli("queue", "--user", "olly").output == "operator may not read the panel\n"


def test_cli_queue_when_empty(seeded):
    clock.set_now(NOW)
    assert _cli("queue", "--user", "alex").output == "the review queue is empty\n"


def test_cli_override_history_and_candidates(accepted):
    args = [FIXTURE_DEAL, "2", "September 2026", "borrower request", "forbearance"]
    made = _cli("override", *args, "--sentence", "3", "--reason", REASON, "--user", "alex")
    assert made.exit_code == 0, made.output
    assert made.output == (
        "borrower request: forbearance (overridden by alex, sentence 3; the model said "
        "maturity extension)\n"
    )
    history = _cli("overrides", FIXTURE_DEAL, "2", "September 2026", "--user", "pat")
    assert history.output == (
        "2026-10-06T09:00:00+00:00 alex: borrower request maturity extension -> forbearance "
        f"(sentence 3): {REASON}\n"
    )
    assert _cli("overrides", FIXTURE_DEAL, "7", "September 2026", "--user", "pat").output == (
        "no overrides of loan 7 for September 2026\n"
    )
    candidates = _cli("candidates", "--user", "quinn")
    assert candidates.output == (
        f"loan 2 of {FIXTURE_DEAL}, September 2026 (0009900001-26-000009): borrower request "
        "forbearance, sentence 3 (override by alex)\n"
    )
    refused = _cli("override", *args, "--sentence", "3", "--user", "alex")
    assert (refused.exit_code, refused.output) == (1, "a reason is required\n")
    pm = _cli("override", *args, "--sentence", "3", "--reason", REASON, "--user", "pat")
    assert (pm.exit_code, pm.output) == (1, "pm may not override values\n")
    bad = _cli(
        "override", FIXTURE_DEAL, "2", "Sept 2026", "borrower request", "x", "--user", "alex"
    )
    assert bad.exit_code == 2


def test_cli_candidates_when_none(seeded):
    assert _cli("candidates", "--user", "quinn").output == "no label candidates\n"


def test_api_queue_summary_and_decline(items):
    client = TestClient(create_app())
    entries = client.get("/review/queue", headers=_as("alex"))
    assert entries.status_code == 200
    first = entries.json()[0]
    assert {key: first[key] for key in ("id", "loan", "deal", "exposure", "mark")} == {
        "id": items["B"],
        "loan": "3",
        "deal": SECOND_DEAL,
        "exposure": "40000000.00",
        "mark": "waiting 1 business day",
    }
    assert (first["waiting_since"], first["business_days_waiting"], first["overdue"]) == (
        "2026-10-05",
        1,
        False,
    )
    assert [e["id"] for e in entries.json()] == [items["B"], items["C"], items["A"]]
    earlier = client.get("/review/queue", headers=_as("alex"), params={"on": "2026-09-30"})
    assert [e["id"] for e in earlier.json()] == [items["C"]]
    summary = client.get("/review/queue/summary", headers=_as("rae"))
    assert summary.json() == {
        "on": "2026-10-06",
        "open": 3,
        "overdue": 1,
        "oldest_since": "2026-09-26",
        "oldest_business_days_waiting": 7,
        "by_field": {"transfer reason": 1, "borrower request": 2},
    }
    missing = client.get(f"/review/items/{items['A']}/decline", headers=_as("pat"))
    assert (missing.status_code, missing.json()) == (
        404,
        {"detail": f"review item {items['A']} was not declined"},
    )
    declined = client.post(
        f"/review/items/{items['A']}/decline", headers=_as("alex"), json={"reason": "vague"}
    )
    assert (declined.json()["status"], declined.json()["marked"]) == (
        "not determinable",
        "not determinable by alex",
    )
    reason = client.get(f"/review/items/{items['A']}/decline", headers=_as("pat"))
    assert reason.json() == {"item": items["A"], "reason": "vague"}
    pm = client.post(
        f"/review/items/{items['B']}/decline", headers=_as("pat"), json={"reason": "vague"}
    )
    assert (pm.status_code, pm.json()) == (403, {"detail": "pm may not decide review items"})


def test_api_override_shows_everywhere_the_value_does(accepted):
    client = TestClient(create_app())
    body = {
        "deal": FIXTURE_DEAL,
        "loan": "2",
        "period": "September 2026",
        "field": "borrower_request",
        "value": "forbearance",
        "sentence": 3,
        "reason": REASON,
    }
    made = client.post("/review/overrides", headers=_as("alex"), json=body)
    assert made.status_code == 200, made.json()
    shown = {key: made.json()[key] for key in ("status", "value", "overridden_by", "marked")}
    assert shown == {
        "status": "overridden",
        "value": "forbearance",
        "overridden_by": "alex",
        "marked": "overridden by alex",
    }
    assert made.json()["override_reason"] == REASON
    assert made.json()["overridden_at"] == "2026-10-06T09:00:00+00:00"
    # The extraction read shows the override too.
    read = client.get(
        "/extraction/comments",
        headers=_as("pat"),
        params={"deal": FIXTURE_DEAL, "loan": "2", "period": "September 2026"},
    )
    [request] = [f for f in read.json()["fields"] if f["field"] == "borrower_request"]
    assert (request["value"], request["model_value"], request["overridden_by"]) == (
        "forbearance",
        "maturity extension",
        "alex",
    )
    history = client.get(
        "/review/overrides",
        headers=_as("pat"),
        params={"deal": FIXTURE_DEAL, "loan": "2", "period": "September 2026"},
    )
    assert [(h["by"], h["before"], h["display"], h["sentence"]) for h in history.json()] == [
        ("alex", "maturity extension", "forbearance", 3)
    ]
    candidates = client.get("/review/label-candidates", headers=_as("quinn"))
    assert [(c["loan"], c["value"], c["source"], c["by"]) for c in candidates.json()] == [
        ("2", "forbearance", "override", "alex")
    ]
    refused = client.post("/review/overrides", headers=_as("alex"), json=body | {"sentence": 9})
    assert (refused.status_code, refused.json()) == (
        422,
        {"detail": "sentence 9 does not exist in this comment"},
    )
    pm = client.post("/review/overrides", headers=_as("pat"), json=body)
    assert (pm.status_code, pm.json()) == (403, {"detail": "pm may not override values"})
    bad = client.post("/review/overrides", headers=_as("alex"), json=body | {"period": "Sept"})
    assert bad.status_code == 422
    bad_read = client.get(
        "/review/overrides",
        headers=_as("pat"),
        params={"deal": FIXTURE_DEAL, "loan": "2", "period": "Sept"},
    )
    assert bad_read.status_code == 422
