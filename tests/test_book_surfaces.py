"""`cmbs book` and the /book routes."""

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cmbs.api.app import create_app
from cmbs.cli import app as cli
from tests.conftest import needs_db

pytestmark = needs_db
runner = CliRunner()
BOOK_CSV = (
    "deal,class,face amount\n"
    'Fixture Trust 2026-F1,E,"20,000,000.00"\n'
    'Fixture Trust 2026-F1,F,"5,000,000.00"\n'
    'Fixture Trust 2026-F2,D,"40,000,000.00"\n'
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_cli_loads_a_file_and_reports_exposure(seeded, tmp_path):
    path = tmp_path / "positions.csv"
    path.write_text(BOOK_CSV)
    loaded = runner.invoke(
        cli, ["book", "load", str(path), "--as-of", "2026-09-30", "--user", "quinn"]
    )
    assert loaded.exit_code == 0, loaded.output
    assert loaded.output.endswith(": 3 positions in 2 deals as of 2026-09-30\n")
    exposure = runner.invoke(cli, ["book", "exposure", "--on", "2026-10-01", "--user", "pat"])
    assert exposure.output == (
        "Fixture Trust 2026-F1: $25,000,000.00\nFixture Trust 2026-F2: $40,000,000.00\n"
    )


def test_cli_says_when_the_book_is_empty(seeded):
    result = runner.invoke(cli, ["book", "exposure", "--on", "2026-10-01", "--user", "pat"])
    assert result.exit_code == 0
    assert result.output == "the book holds nothing as of 2026-10-01\n"


def test_cli_prints_refusals(seeded, tmp_path):
    path = tmp_path / "positions.csv"
    path.write_text(BOOK_CSV)
    refused = runner.invoke(
        cli, ["book", "load", str(path), "--as-of", "2026-09-30", "--user", "pat"]
    )
    assert refused.exit_code == 1
    assert refused.output.strip() == "pm may not load positions"
    path.write_text("deal,class\n")
    invalid = runner.invoke(
        cli, ["book", "load", str(path), "--as-of", "2026-09-30", "--user", "olly"]
    )
    assert invalid.output.strip() == "the positions file has no face amount column"
    reader = runner.invoke(cli, ["book", "exposure", "--user", "olly"])
    assert reader.output.strip() == "operator may not read the panel"


def test_api_loads_positions_and_returns_exposure_as_text(seeded, client):
    body = {
        "as_of": "2026-09-30",
        "positions": [
            {"deal": "Fixture Trust 2026-F1", "class": "E", "face_amount": "20,000,000.00"},
            {"deal": " Fixture  Trust 2026-F1", "class": " F ", "face_amount": "5000000"},
        ],
        "source_name": "desk.csv",
    }
    loaded = client.post("/book/positions", json=body, headers={"X-CMBS-User": "olly"})
    assert loaded.status_code == 200, loaded.text
    assert {k: v for k, v in loaded.json().items() if k != "id"} == {
        "as_of": "2026-09-30",
        "positions": 2,
        "deals": 1,
    }
    exposure = client.get("/book/exposure?on=2026-09-30", headers={"X-CMBS-User": "pat"})
    assert exposure.json() == {
        "on": "2026-09-30",
        "exposure": {"Fixture Trust 2026-F1": "25000000.00"},
    }


def test_api_refuses_a_bad_amount_and_a_user_without_the_right(seeded, client):
    bad = {
        "as_of": "2026-09-30",
        "positions": [{"deal": "X", "class": "A", "face_amount": "lots"}],
    }
    response = client.post("/book/positions", json=bad, headers={"X-CMBS-User": "olly"})
    assert response.status_code == 422
    assert response.json() == {"detail": "position 1: 'lots' is not a face amount"}
    good = {**bad, "positions": [{"deal": "X", "class": "A", "face_amount": "1"}]}
    refused = client.post("/book/positions", json=good, headers={"X-CMBS-User": "alex"})
    assert refused.status_code == 403
    assert refused.json() == {"detail": "analyst may not load positions"}
