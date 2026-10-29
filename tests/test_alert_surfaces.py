"""`cmbs alerts`, `cmbs book link` and the /alerts and /book/whole-loans routes."""

import datetime as dt

import pytest
import typer
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cmbs import clock
from cmbs.alerts.commands import alert_line, superseded_line
from cmbs.alerts.lifecycle import State
from cmbs.alerts.rules import Severity
from cmbs.alerts.service import AlertView, CoveredLoan, Supersession
from cmbs.api.app import create_app
from cmbs.book.commands import note_ref
from cmbs.book.service import NoteRef, PositionLine, load_positions
from cmbs.cli import app as cli
from cmbs.domain import money
from tests.conftest import needs_db
from tests.test_alert_service import AUGUST, F1, F2, SEPTEMBER, TRANSFER, accession, load_month

runner = CliRunner()
HEADERS = {"X-CMBS-User": "pat"}


def test_an_alert_line_names_the_loans_state_and_exposure():
    view = AlertView(
        id=12,
        period=SEPTEMBER,
        severity=Severity.HIGH,
        title="transferred to special servicing",
        message="transferred to special servicing on 2026-09-02",
        added=(),
        settings_version=7,
        as_of=dt.date(2026, 9, 29),
        raised_at=dt.datetime(2026, 9, 30, tzinfo=dt.UTC),
        state=State.NEW,
        state_text="new",
        note=None,
        assigned_to=None,
        loans=(
            CoveredLoan(F1, "1", "a", None, money("25,000,000.00"), ("E", "F")),
            CoveredLoan(F2, "3", "b", None, money("40,000,000.00"), ("D",)),
        ),
    )
    assert alert_line(view) == (
        f"#12 high   {F1} loan 1; {F2} loan 3: transferred to special servicing on "
        "2026-09-02 [new] exposure $65,000,000.00 (E, F, D)"
    )
    unheld = AlertView(
        **{**view.__dict__, "loans": (CoveredLoan(F1, "7", "a", None, money("0"), ()),)}
    )
    assert alert_line(unheld).endswith("[new] exposure $0.00")
    message = "2 loans no longer reported: on the August 2026 tape, not on the September 2026 tape"
    on_deal = AlertView(
        **{
            **view.__dict__,
            "severity": Severity.MEDIUM,
            "message": f"{message} (loans 3, 5)",
            "loans": (CoveredLoan(F1, None, "a", None, money("25"), ("E",), loans=("3", "5")),),
        }
    )
    assert alert_line(on_deal) == (
        f"#12 medium {F1}: {message} (loans 3, 5) [new] exposure $25.00 (E)"
    )


@pytest.mark.parametrize(
    ("text", "note"),
    [
        ("Fixture Trust 2026-F1/1", NoteRef(F1, "1")),
        (" Some/Trust / 2A ", NoteRef("Some/Trust", "2A")),
    ],
)
def test_a_note_is_named_deal_slash_loan(text, note):
    assert note_ref(text) == note


@pytest.mark.parametrize("text", ["no slash", "/1", "Deal/", " / "])
def test_a_note_without_deal_or_loan_is_a_bad_parameter(text):
    with pytest.raises(typer.BadParameter, match="^name a note as DEAL/LOAN"):
        note_ref(text)


@pytest.fixture
def months(seeded):
    load_month(seeded, F1, AUGUST)
    load_month(seeded, F1, SEPTEMBER, {("7", TRANSFER): "09-02-2026"})
    load_positions(
        seeded,
        user="quinn",
        as_of=dt.date(2026, 9, 30),
        lines=[
            PositionLine(F1, "E", money("20,000,000.00")),
            PositionLine(F1, "F", money("5,000,000.00")),
        ],
    )
    seeded.commit()
    clock.set_now(dt.datetime(2026, 9, 30, 12, tzinfo=dt.UTC))
    return seeded


@needs_db
def test_cli_builds_lists_and_moves_an_alert_through_its_lifecycle(months):
    built = runner.invoke(
        cli, ["alerts", "build", "--deal", F1, "--period", "September 2026", "--user", "quinn"]
    )
    assert built.exit_code == 0, built.output
    first, line = built.output.splitlines()
    assert first == f"{F1} September 2026 as of 2026-09-29 (settings version 7): 1 new alert"
    number = line.split()[0].removeprefix("#")
    assert line == (
        f"#{number} high   {F1} loan 7: transferred to special servicing on 2026-09-02 [new] "
        "exposure $25,000,000.00 (E, F)"
    )
    listed = runner.invoke(
        cli, ["alerts", "list", "--deal", F1, "--period", "September 2026", "--user", "pat"]
    )
    assert listed.output == line + "\n"
    acked = runner.invoke(cli, ["alerts", "acknowledge", number, "--user", "pat"])
    assert acked.output == f"alert {number}: acknowledged by pat on 2026-09-30\n"
    given = runner.invoke(
        cli, ["alerts", "assign", number, "--to", "alex", "--note", "rent roll", "--user", "pat"]
    )
    assert given.output == f"alert {number}: assigned to alex: rent roll\n"
    mine = runner.invoke(cli, ["alerts", "work-list", "--user", "alex"])
    assert mine.output.startswith(f"#{number} high ")
    book = runner.invoke(cli, ["alerts", "book", "--period", "September 2026", "--user", "pat"])
    assert book.output.startswith(f"#{number} high ")
    closed = runner.invoke(cli, ["alerts", "close", number, "--note", "cured", "--user", "alex"])
    assert closed.output == f"alert {number}: closed by alex: cured\n"
    empty_book = runner.invoke(
        cli, ["alerts", "book", "--period", "September 2026", "--user", "pat"]
    )
    assert empty_book.output == "no open alerts on the book for September 2026\n"
    nobody = runner.invoke(cli, ["alerts", "work-list", "--user", "alex"])
    assert nobody.output == "no alerts are assigned to alex\n"


@needs_db
def test_cli_shows_added_sentences_notes_and_refusals(months):
    unbuilt = runner.invoke(
        cli, ["alerts", "list", "--deal", F1, "--period", "September 2026", "--user", "pat"]
    )
    assert unbuilt.output == "the September 2026 alerts have not been built\n"
    refused = runner.invoke(
        cli, ["alerts", "build", "--deal", F1, "--period", "September 2026", "--user", "pat"]
    )
    assert (refused.exit_code, refused.output.strip()) == (
        1,
        "pm may not run refreshes and backfills",
    )
    bad = runner.invoke(
        cli, ["alerts", "list", "--deal", F1, "--period", "Sept 2026", "--user", "pat"]
    )
    assert bad.exit_code == 2 and "not a period like 'September 2026'" in bad.output
    early = runner.invoke(
        cli,
        [
            "alerts",
            "build",
            "--deal",
            F1,
            "--period",
            "September 2026",
            "--as-of",
            "2026-09-28",
            "--user",
            "quinn",
        ],
    )
    assert (
        early.output.strip() == f"no September 2026 loan tape of {F1} had been filed by 2026-09-28"
    )
    nobody = runner.invoke(cli, ["alerts", "close", "1", "--note", "x", "--user", "quinn"])
    assert nobody.output.strip() == "quant may not close alerts"


@needs_db
def test_cli_prints_the_sentences_a_comment_alert_adds(seeded):
    from tests.test_alert_service import load_10d

    load_month(seeded, F1, AUGUST)
    load_10d(seeded, F1, AUGUST, {"2": "First."})
    load_month(seeded, F1, SEPTEMBER)
    load_10d(seeded, F1, SEPTEMBER, {"2": "First. Second sentence."})
    seeded.commit()
    clock.set_now(dt.datetime(2026, 9, 30, 12, tzinfo=dt.UTC))
    built = runner.invoke(
        cli, ["alerts", "build", "--deal", F1, "--period", "September 2026", "--user", "quinn"]
    )
    lines = built.output.splitlines()
    assert len(lines) == 3, built.output
    assert lines[1].endswith(
        f"{F1} loan 2: servicer comment changed: 1 sentence added [new] exposure $0.00"
    )
    assert lines[2] == "    + Second sentence."


@needs_db
def test_cli_links_a_whole_loan(seeded):
    load_month(seeded, F1, AUGUST)
    load_month(seeded, F2, AUGUST)
    seeded.commit()
    linked = runner.invoke(cli, ["book", "link", f"{F1}/1", f"{F2}/3", "--user", "quinn"])
    assert linked.exit_code == 0, linked.output
    assert linked.output.endswith(f": loan 1 of {F1}; loan 3 of {F2}\n")
    assert linked.output.startswith("whole loan ")
    refused = runner.invoke(cli, ["book", "link", f"{F1}/1", f"{F2}/3", "--user", "pat"])
    assert (refused.exit_code, refused.output.strip()) == (1, "pm may not link whole loans")


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@needs_db
def test_api_builds_reads_and_changes_alerts(months, client):
    built = client.post(
        "/alerts/build",
        json={"deal": F1, "period": "September 2026"},
        headers={"X-CMBS-User": "quinn"},
    )
    assert built.status_code == 200, built.text
    body = built.json()
    (alert,) = body.pop("alerts")
    assert isinstance(body.pop("vintage"), int)
    assert body == {
        "deal": F1,
        "period": "September 2026",
        "note": None,
        "as_of": "2026-09-29",
        "settings_version": 7,
        "raised": 1,
        "superseded": 0,
        "superseded_alerts": [],
    }
    number = alert.pop("id")
    assert alert == {
        "period": "September 2026",
        "severity": "high",
        "title": "transferred to special servicing",
        "message": "transferred to special servicing on 2026-09-02",
        "added": [],
        "settings_version": 7,
        "as_of": "2026-09-29",
        "raised_at": "2026-09-30T12:00:00Z",
        "state": "new",
        "state_text": "new",
        "note": None,
        "assigned_to": None,
        "exposure": "25000000.00",
        "classes": ["E", "F"],
        "balance": "9000000.00",
        "loans": [
            {
                "deal": F1,
                "loan": "7",
                "loans": [],
                "label": "7",
                "accession": accession(F1, SEPTEMBER, 1),
                "balance": "9000000.00",
                "exposure": "25000000.00",
                "classes": ["E", "F"],
                "superseded": False,
            }
        ],
        "superseded": None,
    }
    listed = client.get("/alerts", params={"deal": F1, "period": "September 2026"}, headers=HEADERS)
    assert [a["id"] for a in listed.json()["alerts"]] == [number]
    assert client.get(f"/alerts/{number}", headers=HEADERS).json()["state"] == "new"
    acked = client.post(f"/alerts/{number}/acknowledge", headers=HEADERS)
    assert acked.json()["state_text"] == "acknowledged by pat on 2026-09-30"
    given = client.post(
        f"/alerts/{number}/assign", json={"to": "alex", "note": "rent roll"}, headers=HEADERS
    )
    assert (given.json()["state_text"], given.json()["note"]) == ("assigned to alex", "rent roll")
    mine = client.get("/alerts/work-list", headers={"X-CMBS-User": "alex"})
    assert [a["id"] for a in mine.json()] == [number]
    book = client.get("/alerts/book", params={"period": "September 2026"}, headers=HEADERS)
    assert [a["id"] for a in book.json()] == [number]
    opened = client.get("/alerts/open", headers=HEADERS)
    assert [a["id"] for a in opened.json()] == [number]
    closed = client.post(
        f"/alerts/{number}/close", json={"note": "cured"}, headers={"X-CMBS-User": "alex"}
    )
    assert (closed.json()["state"], closed.json()["note"]) == ("closed", "cured")
    assert (
        client.get("/alerts/open", params={"period": "September 2026"}, headers=HEADERS).json()
        == []
    )


@needs_db
def test_api_refusals_and_bad_periods(months, client):
    refused = client.post(
        "/alerts/build", json={"deal": F1, "period": "September 2026"}, headers=HEADERS
    )
    assert (refused.status_code, refused.json()) == (
        403,
        {"detail": "pm may not run refreshes and backfills"},
    )
    bad = client.get("/alerts", params={"deal": F1, "period": "2026-09"}, headers=HEADERS)
    assert (bad.status_code, bad.json()) == (
        422,
        {"detail": "not a period like 'September 2026': '2026-09'"},
    )
    missing = client.get("/alerts/999999", headers=HEADERS)
    assert (missing.status_code, missing.json()) == (422, {"detail": "no alert 999999"})
    empty = client.get("/alerts", params={"deal": F1, "period": "September 2026"}, headers=HEADERS)
    assert empty.json() == {
        "deal": F1,
        "period": "September 2026",
        "note": "the September 2026 alerts have not been built",
        "alerts": [],
        "superseded_alerts": [],
    }


@needs_db
def test_api_links_a_whole_loan(seeded, client):
    load_month(seeded, F1, AUGUST)
    load_month(seeded, F2, AUGUST)
    seeded.commit()
    body = {"notes": [{"deal": F1, "loan": "1"}, {"deal": F2, "loan": "3"}]}
    linked = client.post("/book/whole-loans", json=body, headers={"X-CMBS-User": "quinn"})
    assert linked.status_code == 200, linked.text
    assert linked.json()["notes"] == body["notes"]
    assert isinstance(linked.json()["id"], int)
    refused = client.post("/book/whole-loans", json=body, headers=HEADERS)
    assert (refused.status_code, refused.json()) == (403, {"detail": "pm may not link whole loans"})


@needs_db
def test_cli_and_api_show_alerts_a_later_vintage_superseded(seeded, client):
    from tests.test_alert_service import STATUS, amend_september

    load_month(seeded, F1, AUGUST, {("4", STATUS): "0"})
    original = load_month(seeded, F1, SEPTEMBER, {("4", STATUS): "1"})
    seeded.commit()
    clock.set_now(dt.datetime(2026, 9, 30, 12, tzinfo=dt.UTC))
    runner.invoke(
        cli, ["alerts", "build", "--deal", F1, "--period", "September 2026", "--user", "quinn"]
    )
    amendment = amend_september(
        seeded, {("4", STATUS): "0"}, on=dt.date(2026, 10, 10), number=3, amends=original
    )
    seeded.commit()
    clock.set_now(dt.datetime(2026, 10, 11, 12, tzinfo=dt.UTC))
    rebuilt = runner.invoke(
        cli, ["alerts", "build", "--deal", F1, "--period", "September 2026", "--user", "quinn"]
    )
    reason = f"no longer shown by September 2026 as published on 2026-10-10 (tape {amendment})"
    first, gone = rebuilt.output.splitlines()
    assert first == (
        f"{F1} September 2026 as of 2026-10-10 (settings version 7): 0 new alerts, 1 superseded; "
        "no changes since August 2026"
    )
    assert gone.startswith("superseded #") and gone.endswith(
        f"loan 4: payment status worsened: current to 30 days [new] exposure $0.00: {reason}"
    )
    listed = runner.invoke(
        cli, ["alerts", "list", "--deal", F1, "--period", "September 2026", "--user", "pat"]
    )
    assert listed.output.splitlines() == ["no changes since August 2026", gone]
    body = client.get(
        "/alerts", params={"deal": F1, "period": "September 2026"}, headers=HEADERS
    ).json()
    assert body["alerts"] == []
    (superseded,) = body["superseded_alerts"]
    assert superseded["superseded"]["reason"] == reason
    assert superseded["superseded"]["at"] == "2026-10-11T12:00:00Z"
    assert superseded["loans"][0]["superseded"] is True


def test_a_superseded_line_says_why():
    base = AlertView(
        id=3,
        period=SEPTEMBER,
        severity=Severity.MEDIUM,
        title="t",
        message="m",
        added=(),
        settings_version=7,
        as_of=dt.date(2026, 9, 29),
        raised_at=dt.datetime(2026, 9, 30, tzinfo=dt.UTC),
        state=State.NEW,
        state_text="new",
        note=None,
        assigned_to=None,
        loans=(CoveredLoan(F1, "4", "a", None, money("0"), (), superseded=True),),
    )
    assert superseded_line(base) == (
        f"superseded #3 medium {F1} loan 4: m [new] exposure $0.00: no longer shown by this "
        "deal's latest build; another deal still shows it"
    )
    gone = AlertView(
        **{**base.__dict__, "superseded": Supersession(9, base.raised_at, "no longer shown")}
    )
    assert superseded_line(gone).endswith("[new] exposure $0.00: no longer shown")
