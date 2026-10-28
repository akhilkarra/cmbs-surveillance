"""`cmbs coverage` and the /coverage routes over a discovered book (replayed EDGAR)."""

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cmbs.api.app import create_app
from cmbs.book.service import PositionLine, load_positions
from cmbs.cli import app as cli
from cmbs.edgar.replay import Replay
from cmbs.edgar.service import discover_deal
from tests.conftest import needs_db

pytestmark = needs_db
runner = CliRunner()
RECORDINGS = Path(__file__).parent / "fixtures" / "edgar"


@pytest.fixture
def book(seeded):
    load_positions(
        seeded,
        user="olly",
        as_of=dt.date(2026, 9, 30),
        lines=[
            PositionLine("BANK 2018-BNK13", "D", Decimal("12000000.00")),
            PositionLine("HYT 2024-RGCY", "E", Decimal("30000000.00")),
        ],
    )
    for deal in ("BANK 2018-BNK13", "HYT 2024-RGCY"):
        discover_deal(seeded, Replay(RECORDINGS), deal, dt.date(2026, 10, 27), user="quinn")
    seeded.commit()
    return seeded


def test_cli_prints_the_report(book):
    result = runner.invoke(cli, ["coverage", "report", "--on", "2026-10-27", "--user", "pat"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith(
        "Coverage report as of 2026-10-27, positions as of 2026-09-30\n"
    )
    assert "71% of the book's exposure cannot be surveilled from public filings." in result.output


def test_cli_configures_a_vendor_source(book):
    added = runner.invoke(
        cli, ["coverage", "vendor", "HYT 2024-RGCY", "tape feed", "--user", "olly"]
    )
    assert added.exit_code == 0, added.output
    assert added.output == "HYT 2024-RGCY: vendor source tape feed\n"
    result = runner.invoke(cli, ["coverage", "report", "--user", "pat"])
    assert "HYT 2024-RGCY: tape feed" in result.output


def test_cli_prints_refusals(book):
    report = runner.invoke(cli, ["coverage", "report", "--user", "olly"])
    assert (report.exit_code, report.output.strip()) == (1, "operator may not read the panel")
    vendor = runner.invoke(cli, ["coverage", "vendor", "HYT 2024-RGCY", "feed", "--user", "pat"])
    assert vendor.output.strip() == "pm may not run refreshes and backfills"


def test_api_returns_the_report_with_money_as_text(book):
    client = TestClient(create_app())
    response = client.get("/coverage?on=2026-11-05", headers={"X-CMBS-User": "rae"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["on"], body["positions_as_of"], body["total"]) == (
        "2026-11-05",
        "2026-09-30",
        "42000000.00",
    )
    assert body["deals"][1] == {
        "deal": "BANK 2018-BNK13",
        "classes": ["D"],
        "exposure": "12000000.00",
        "coverage": "full",
        "detail": "loan tape and distribution reports monthly",
        "overdue_expected_by": None,
        "vendor_sources": [],
        "checked_as_of": "2026-10-27",
    }
    assert body["by_class"] == [
        {"coverage": "full", "exposure": "12000000.00", "share": 29},
        {"coverage": "not publicly reported", "exposure": "30000000.00", "share": 71},
    ]
    assert body["text"].startswith("Coverage report as of 2026-11-05")


def test_api_without_a_book_has_no_positions_date(seeded):
    client = TestClient(create_app())
    body = client.get("/coverage?on=2026-10-27", headers={"X-CMBS-User": "pat"}).json()
    assert (body["positions_as_of"], body["deals"], body["by_class"]) == (None, [], [])


def test_api_configures_vendor_sources(book):
    client = TestClient(create_app())
    response = client.post(
        "/coverage/vendor-sources",
        json={"deal": " HYT  2024-RGCY", "source": "tape feed"},
        headers={"X-CMBS-User": "quinn"},
    )
    assert response.json() == {"deal": "HYT 2024-RGCY", "source": "tape feed"}
    refused = client.post(
        "/coverage/vendor-sources",
        json={"deal": "HYT 2024-RGCY", "source": "feed"},
        headers={"X-CMBS-User": "alex"},
    )
    assert refused.status_code == 403
