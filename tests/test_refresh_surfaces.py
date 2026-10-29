"""`cmbs refresh`, `cmbs runs` and their routes, with EDGAR replayed from the environment."""

import datetime as dt
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cmbs import clock
from cmbs.api.app import create_app
from cmbs.book.service import PositionLine, load_positions
from cmbs.cli import app as cli
from cmbs.deals.service import get_or_create
from cmbs.domain import money
from cmbs.refresh.sources import ARCHIVE_REPLAY_ENV, EDGAR_REPLAY_ENV
from cmbs.testing.world import FIXTURE_CIKS
from tests.conftest import needs_db
from tests.fixtures.refresh.offline import offline_fixture  # noqa: F401

pytestmark = needs_db
runner = CliRunner()
FIXTURES = Path(__file__).parent / "fixtures"
F1, F2, HYT = "Fixture Trust 2026-F1", "Fixture Trust 2026-F2", "HYT 2024-RGCY"
USER = {"X-CMBS-User": "olly"}


@pytest.fixture(autouse=True)
def replayed(monkeypatch):
    edgar = os.pathsep.join(str(FIXTURES / d) for d in ("refresh/edgar", "edgar"))
    monkeypatch.setenv(EDGAR_REPLAY_ENV, edgar)
    monkeypatch.setenv(ARCHIVE_REPLAY_ENV, str(FIXTURES / "refresh" / "archive"))


@pytest.fixture
def book(seeded):
    lines = [
        PositionLine(F1, "E", money("20000000")),
        PositionLine(F2, "D", money("40000000")),
        PositionLine(HYT, "E", money("30000000")),
    ]
    load_positions(seeded, user="olly", as_of=dt.date(2026, 9, 30), lines=lines)
    seeded.commit()
    return seeded


def cli_run(*args: str):
    return runner.invoke(cli, list(args))


def test_the_cli_refreshes_and_reads_runs(book):
    clock.set_now(dt.datetime(2026, 10, 27, 9, 0, tzinfo=dt.UTC))
    done = cli_run("refresh", "book", "--user", "olly")
    assert done.exit_code == 0, done.output
    assert done.output.splitlines() == [
        "run 1: olly, 2026-10-27 09:00 to 09:00, 2 deals refreshed, 1 skipped, "
        "14 loan rows loaded, 0 alerts raised",
        "refresh book, succeeded",
        f"{F1}: refreshed",
        "  September 2026: loaded, 7 loan rows, 0 loans quarantined, 0 reconciliation flags",
        f"{F2}: refreshed",
        "  September 2026: loaded, 7 loan rows, 0 loans quarantined, 0 reconciliation flags",
        f"{HYT}: skipped, not publicly reported",
    ]
    listed = cli_run(
        "runs", "list", "--user", "rae", "--start", "2026-10-27", "--end", "2026-10-27"
    )
    assert listed.output == (
        "run 1: olly, 2026-10-27 09:00 to 09:00, 2 deals refreshed, 1 skipped, "
        "14 loan rows loaded, 0 alerts raised (refresh book, succeeded)\n"
    )
    assert cli_run("runs", "list", "--user", "rae", "--start", "2026-10-28").output == "no runs\n"
    shown = cli_run("runs", "show", "1", "--user", "olly")
    assert shown.output == done.output
    traced = cli_run("runs", "trace", F1, "7", "September 2026", "--user", "pat")
    assert traced.output == (
        f"loan 7 of {F1}, September 2026 balance $9,000,000.00, from 0009900001-26-000419 "
        "filed 2026-09-29; loaded by run 1 on 2026-10-27\n"
    )
    early = cli_run(
        "runs", "trace", F1, "7", "September 2026", "--user", "pat", "--as-of", "2026-09-28"
    )
    assert (early.exit_code, early.output) == (
        1,
        f"no September 2026 values of loan 7 of {F1} by 2026-09-28\n",
    )
    bad = cli_run("runs", "trace", F1, "7", "Sept 2026", "--user", "pat")
    assert bad.exit_code == 2
    assert cli_run("runs", "show", "9", "--user", "olly").output == "there is no run 9\n"
    assert cli_run("runs", "list", "--user", "pat").output == "pm may not see run history\n"


def test_the_cli_backfills_and_a_failed_run_exits_1(seeded):
    for name, cik in ((F2, FIXTURE_CIKS[F2]), ("Unrecorded Trust 2026-X", None)):
        get_or_create(seeded, name, cik=cik)
    seeded.commit()
    clock.set_now(dt.datetime(2026, 10, 28, 9, 0, tzinfo=dt.UTC))
    done = cli_run(
        "refresh",
        "backfill",
        F2,
        "--from",
        "August 2026",
        "--to",
        "September 2026",
        "--user",
        "quinn",
    )
    assert done.exit_code == 0, done.output
    assert done.output.splitlines()[1:] == [
        "backfill, succeeded",
        f"{F2}: refreshed",
        "  August 2026: loaded, 7 loan rows, 0 loans quarantined, 0 reconciliation flags",
        "  September 2026: loaded, 7 loan rows, 0 loans quarantined, 0 reconciliation flags",
    ]
    failed = cli_run("refresh", "deal", "Unrecorded Trust 2026-X", "--user", "quinn")
    assert failed.exit_code == 1
    assert failed.output.splitlines()[1] == "refresh deal, failed"
    unknown = cli_run("refresh", "deal", "Fixture Trust 2026-F9", "--user", "quinn")
    assert (unknown.exit_code, unknown.output) == (1, "no deal named 'Fixture Trust 2026-F9'\n")
    deal = cli_run("refresh", "deal", F2, "--user", "quinn")
    assert deal.exit_code == 0
    assert deal.output.splitlines()[2] == f"{F2}: refreshed, nothing new to load"
    refused = cli_run("refresh", "book", "--user", "pat")
    assert (refused.exit_code, refused.output) == (1, "pm may not run refreshes and backfills\n")
    bad = cli_run(
        "refresh", "backfill", F2, "--from", "Aug 2026", "--to", "September 2026", "--user", "quinn"
    )
    assert bad.exit_code == 2


def test_the_api_refreshes_and_reads_runs(book):
    client = TestClient(create_app())
    refreshed = client.post("/refresh/book", headers=USER)
    assert refreshed.status_code == 200, refreshed.text
    run = refreshed.json()
    assert (run["number"], run["kind"], run["started_by"], run["status"]) == (
        1,
        "refresh book",
        "olly",
        "succeeded",
    )
    assert [d["outcome"] for d in run["summary"]["deals"]] == ["refreshed", "refreshed", "skipped"]
    assert run["stopped"] is None
    assert run["headline"].startswith("run 1: olly, ")
    deal = client.post(f"/refresh/deals/{F2}", headers={"X-CMBS-User": "quinn"})
    assert deal.json()["kind"] == "refresh deal"
    backfill = client.post(
        f"/refresh/deals/{F1}/backfill",
        params={"start": "December 2025", "end": "January 2026"},
        headers={"X-CMBS-User": "quinn"},
    )
    assert backfill.status_code == 200, backfill.text
    months = backfill.json()["summary"]["deals"][0]["months"]
    assert [m["period"] for m in months] == ["December 2025", "January 2026"]
    bad = client.post(
        f"/refresh/deals/{F1}/backfill",
        params={"start": "Dec 2025", "end": "January 2026"},
        headers={"X-CMBS-User": "quinn"},
    )
    assert (bad.status_code, bad.json()) == (
        422,
        {"detail": "not a period like 'September 2026': 'Dec 2025'"},
    )
    refused = client.post("/refresh/book", headers={"X-CMBS-User": "pat"})
    assert (refused.status_code, refused.json()) == (
        403,
        {"detail": "pm may not run refreshes and backfills"},
    )

    history = client.get("/runs", headers=USER)
    assert [r["number"] for r in history.json()] == [3, 2, 1]
    assert client.get("/runs/1", headers=USER).json() == run
    assert client.get("/runs/9", headers=USER).json() == {"detail": "there is no run 9"}
    traced = client.get(
        "/runs/trace",
        params={"deal": F1, "loan": "7", "period": "January 2026"},
        headers={"X-CMBS-User": "pat"},
    )
    assert traced.status_code == 200, traced.text
    body = traced.json()
    assert (body["run"], body["loaded_by"], body["balance"], body["period"]) == (
        3,
        "quinn",
        "9000000.00",
        "January 2026",
    )
    assert body["text"].endswith("loaded by run 3 on " + body["loaded_at"][:10])
    bad_month = client.get(
        "/runs/trace",
        params={"deal": F1, "loan": "7", "period": "Jan 2026"},
        headers={"X-CMBS-User": "pat"},
    )
    assert bad_month.status_code == 422
    assert client.get("/runs", headers={"X-CMBS-User": "pat"}).status_code == 403
