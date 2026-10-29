"""`cmbs export` and the /export routes."""

import datetime as dt
import hashlib

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cmbs import clock
from cmbs.api.app import create_app
from cmbs.cli import app as cli
from cmbs.domain import Period
from cmbs.testing.bdd import in_session
from cmbs.testing.world import FIXTURE_DEAL
from tests.conftest import needs_db
from tests.fixtures.refresh.offline import offline_fixture  # noqa: F401
from tests.test_routine_world import backfill

pytestmark = needs_db
runner = CliRunner()
SEP = Period(2026, 9)
TODAY = dt.datetime(2026, 10, 7, 9, 0, tzinfo=dt.UTC)


def test_the_cli_exports_and_rebuilds(seeded, tmp_path):
    backfill(in_session, SEP, SEP)
    clock.set_now(TODAY)
    done = runner.invoke(
        cli,
        ["export", "panel", "--as-of", "2026-10-06", "--user", "quinn", "--out", str(tmp_path)],
    )
    assert done.exit_code == 0, done.output
    name = "panel_as-of-2026-10-06_settings-7_jev-1.14.0_prompt-3_export-1.csv"
    content = (tmp_path / name).read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    assert done.output == (
        f"export 1: 7 rows as of 2026-10-06, settings version 7, jev-1.14.0, prompt version 3; "
        f"{name}, SHA-256 {digest}\nwrote {tmp_path / name}\n"
    )
    bad = runner.invoke(cli, ["export", "panel", "--as-of", "6 Oct", "--user", "quinn"])
    assert bad.exit_code == 2
    refused = runner.invoke(cli, ["export", "panel", "--as-of", "2026-10-06", "--user", "pat"])
    assert (refused.exit_code, refused.output) == (1, "pm may not export\n")

    rebuilt = runner.invoke(cli, ["export", "rebuild", "--user", "quinn", "--deal", FIXTURE_DEAL])
    assert rebuilt.exit_code == 0, rebuilt.output
    assert rebuilt.output.startswith(
        "rebuilt 7 loan rows of 1 deal from 1 stored tape: identical, checksum "
    )
    nope = runner.invoke(cli, ["export", "rebuild", "--user", "quinn", "--deal", "X"])
    assert (nope.exit_code, nope.output) == (1, "no deal named 'X'\n")


def test_the_cli_rebuild_exits_1_when_the_panel_differs(seeded):
    from decimal import Decimal

    from sqlalchemy import text, update

    from cmbs.tape.tables import LoanPeriod

    backfill(in_session, SEP, SEP)

    def tamper(db):
        db.execute(text("ALTER TABLE silver.loan_period DISABLE TRIGGER ALL"))
        db.execute(update(LoanPeriod).where(LoanPeriod.loan == "1").values(noi=Decimal("1.00")))

    in_session(tamper)
    found = runner.invoke(cli, ["export", "rebuild", "--user", "olly"])
    assert found.exit_code == 1
    assert "1 differences" in found.output.splitlines()[0]
    assert found.output.splitlines()[1:] == [
        "1/2026-09-01/0009900001-26-000419/1 noi: stored Decimal('1.00'), "
        "rebuilt Decimal('2100000.00')"
    ]


def test_the_api_exports_and_rebuilds(seeded):
    backfill(in_session, SEP, SEP)
    clock.set_now(TODAY)
    client = TestClient(create_app())
    quant = {"X-CMBS-User": "quinn"}
    made = client.post(
        "/export/panel",
        params={"as_of": "2026-10-06", "format": "parquet", "deal": FIXTURE_DEAL},
        headers=quant,
    )
    assert made.status_code == 200, made.text
    name = "fixture-trust-2026-f1_as-of-2026-10-06_settings-7_jev-1.14.0_prompt-3_export-1.parquet"
    assert made.headers["content-disposition"] == f'attachment; filename="{name}"'
    assert made.headers["content-type"] == "application/vnd.apache.parquet"
    assert made.headers["x-export-sha256"] == hashlib.sha256(made.content).hexdigest()
    assert (made.headers["x-export-number"], made.headers["x-export-rows"]) == ("1", "7")
    csv = client.post("/export/panel", params={"as_of": "2026-10-06"}, headers=quant)
    assert csv.headers["content-type"].startswith("text/csv")
    refused = client.post(
        "/export/panel", params={"as_of": "2026-10-06"}, headers={"X-CMBS-User": "pat"}
    )
    assert (refused.status_code, refused.json()) == (403, {"detail": "pm may not export"})
    bad = client.post("/export/panel", params={"as_of": "2026-10-06", "format": "x"}, headers=quant)
    assert (bad.status_code, bad.json()) == (
        422,
        {"detail": "exports are csv or parquet, not 'x'"},
    )

    rebuilt = client.post("/export/rebuild", headers=quant)
    assert rebuilt.status_code == 200, rebuilt.text
    body = rebuilt.json()
    assert (body["deals"], body["tapes"], body["rows"], body["identical"]) == (
        [FIXTURE_DEAL],
        1,
        7,
        True,
    )
    assert body["stored_checksum"] == body["rebuilt_checksum"]
    assert body["differences"] == []
