"""`cmbs discover` and POST /discovery, replaying tests/fixtures/edgar."""

import datetime as dt
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cmbs.api.app import create_app
from cmbs.cli import app as cli
from cmbs.deals.service import by_name
from cmbs.edgar import commands, discovery, replay, search
from cmbs.edgar.discovery import Discovery, FoundFiling, Match
from tests.conftest import needs_db

RECORDINGS = Path(__file__).parent / "fixtures" / "edgar"
runner = CliRunner()


@pytest.fixture
def replayed(monkeypatch) -> None:
    monkeypatch.setenv(replay.REPLAY_ENV, str(RECORDINGS))


def test_describe_summarizes_the_match_and_the_months_covered():
    found = Discovery(
        deal="COMM 2014-CR18",
        on=dt.date(2026, 10, 27),
        match=Match(1610486, "COMM 2014-CCRE18 Mortgage Trust", "EDGAR names the trust x"),
        filings=(
            FoundFiling("a", 1, "10-D", dt.date(2024, 9, 27), dt.date(2024, 9, 17)),
            FoundFiling("b", 1, "10-D", dt.date(2024, 8, 27), dt.date(2024, 8, 16)),
            FoundFiling("c", 1, "15-15D", dt.date(2024, 10, 9)),
            FoundFiling("d", 1, "8-K", dt.date(2014, 6, 27)),
        ),
    )
    assert commands.describe(found) == [
        "COMM 2014-CR18: CIK 1610486 (COMM 2014-CCRE18 Mortgage Trust)",
        "  note: EDGAR names the trust x",
        "  4 filings on or before 2026-10-27",
        "  10-D: 2 months, August 2024 to September 2024",
        "  other forms: 15-15D, 8-K",
    ]


def test_describe_a_deal_without_an_entity():
    found = Discovery("HYT 2024-RGCY", dt.date(2026, 10, 27), Match(None), ())
    assert commands.describe(found) == [
        "HYT 2024-RGCY: no issuing entity on EDGAR",
        "  0 filings on or before 2026-10-27",
    ]


@needs_db
def test_cli_discovers_a_deal_and_prints_its_coverage(seeded, replayed):
    result = runner.invoke(
        cli, ["discover", "deal", "WFRBS 2014-C25", "--on", "2026-10-27", "--user", "alex"]
    )
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "WFRBS 2014-C25: CIK 1623838 (WFRBS Commercial Mortgage Trust 2014-C25)",
        "  171 filings on or before 2026-10-27",
        "  10-D: 141 months, January 2015 to September 2026",
        "  other forms: 10-K, 10-K/A, 424B5, 8-K, FWP",
        "  coverage: distribution reports only (issued before November 2016, no loan tape)",
    ]


@needs_db
def test_cli_prints_a_refusal_and_exits_1(seeded, replayed):
    result = runner.invoke(cli, ["discover", "deal", "BANK 2018-BNK13", "--user", "olly"])
    assert result.exit_code == 1
    assert result.output.strip() == "BANK 2018-BNK13: operator may not read the panel"


@needs_db
def test_cli_discovers_every_deal_in_the_book(seeded, replayed, tmp_path):
    book = tmp_path / "book.csv"
    book.write_text('deal,class,face amount\nHYT 2024-RGCY,E,"30,000,000.00"\n')
    loaded = runner.invoke(
        cli, ["book", "load", str(book), "--as-of", "2026-09-30", "--user", "olly"]
    )
    assert loaded.exit_code == 0, loaded.output
    result = runner.invoke(cli, ["discover", "book", "--on", "2026-10-27", "--user", "quinn"])
    assert result.exit_code == 0, result.output
    assert result.output == "HYT 2024-RGCY: no issuing entity on EDGAR\n"


@needs_db
def test_cli_book_discovery_needs_a_book(seeded, replayed):
    result = runner.invoke(cli, ["discover", "book", "--on", "2026-10-27", "--user", "quinn"])
    assert result.exit_code == 1
    assert result.output.strip() == "no positions file is dated on or before 2026-10-27"


def test_cli_records_responses_for_replay(tmp_path, monkeypatch):
    url = search.typeahead_url("HYT 2024-RGCY")
    fts = search.full_text_url('"HYT 2024-RGCY"', "ABS-15G")
    responses = {
        url: {"hits": {"total": {"value": 0}, "hits": []}},
        fts: {"hits": {"total": {"value": 0}, "hits": []}},
    }
    monkeypatch.setattr(replay.client, "get_json", lambda u: responses[u])
    monkeypatch.setattr(replay.client, "pause", lambda seconds: None)
    into = tmp_path / "recordings"
    result = runner.invoke(
        cli, ["discover", "record", "HYT 2024-RGCY", "--into", str(into), "--on", "2026-10-27"]
    )
    assert result.exit_code == 0, result.output
    assert result.output == "recorded HYT 2024-RGCY: 0 filings\n"
    manifest = json.loads((into / "recordings.json").read_text())
    assert set(manifest["responses"]) == {url, fts}


def test_cli_record_prints_a_refusal(tmp_path):
    result = runner.invoke(cli, ["discover", "record", " ", "--into", str(tmp_path)])
    assert result.exit_code == 1
    assert result.output.strip() == "name the deal to look up"


@needs_db
def test_api_discovers_a_deal(seeded, replayed):
    client = TestClient(create_app())
    response = client.post(
        "/discovery",
        json={"deal": "HYT 2024-RGCY", "on": "2026-10-27"},
        headers={"X-CMBS-User": "alex"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "deal": "HYT 2024-RGCY",
        "on": "2026-10-27",
        "cik": None,
        "edgar_name": None,
        "note": None,
        "filings": [
            {
                "accession": "0001539497-24-001576",
                "cik": 850779,
                "form": "ABS-15G",
                "filing_date": "2024-08-06",
                "report_date": "2024-08-06",
                "period": None,
            }
        ],
        "coverage": "not publicly reported",
        "detail": "only the depositor's ABS-15G of 2024-08-06",
    }


@needs_db
def test_api_lists_periods_by_month(seeded, replayed):
    client = TestClient(create_app())
    response = client.post(
        "/discovery",
        json={"deal": "BANK 2018-BNK13", "on": "2026-10-27"},
        headers={"X-CMBS-User": "quinn"},
    )
    body = response.json()
    assert body["coverage"] == "full"
    assert body["filings"][0] == {
        "accession": "0001888524-26-019175",
        "cik": 1744707,
        "form": "ABS-EE",
        "filing_date": "2026-10-27",
        "report_date": "2026-10-19",
        "period": "October 2026",
    }


@needs_db
def test_api_refuses_users_who_cannot_read_the_panel(seeded, replayed):
    client = TestClient(create_app())
    response = client.post(
        "/discovery", json={"deal": "BANK 2018-BNK13"}, headers={"X-CMBS-User": "olly"}
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "operator may not read the panel"}


@needs_db
def test_cli_keeps_each_deal_found_when_another_is_refused(seeded, replayed):
    result = runner.invoke(
        cli, ["discover", "deal", " ", "HYT 2024-RGCY", "--on", "2026-10-27", "--user", "alex"]
    )
    assert result.exit_code == 1
    assert "' ': name the deal to look up" in result.output
    assert "HYT 2024-RGCY: no issuing entity on EDGAR" in result.output
    assert "  coverage: not publicly reported" in result.output
    seeded.expire_all()
    assert by_name(seeded, "HYT 2024-RGCY") is not None


@needs_db
def test_cli_book_discovery_reports_each_refused_deal_and_exits_1(seeded, monkeypatch, tmp_path):
    monkeypatch.setenv(replay.REPLAY_ENV, str(RECORDINGS))
    book = tmp_path / "book.csv"
    book.write_text("deal,class,face amount\nHYT 2024-RGCY,E,1\nKSL 2026-HT3,A,1\n")
    runner.invoke(cli, ["book", "load", str(book), "--as-of", "2026-09-30", "--user", "olly"])

    def ambiguous(source, name, on):
        if name == "KSL 2026-HT3":
            raise discovery.AmbiguousDeal("several EDGAR entities match")
        return real(source, name, on)

    real = discovery.discover
    monkeypatch.setattr("cmbs.edgar.service.discover", ambiguous)
    result = runner.invoke(cli, ["discover", "book", "--on", "2026-10-27", "--user", "quinn"])
    assert result.exit_code == 1
    assert result.output.splitlines() == [
        "HYT 2024-RGCY: no issuing entity on EDGAR",
        "KSL 2026-HT3: several EDGAR entities match",
    ]
