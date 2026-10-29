"""Reading the audit log by deal, loan and run; the one-line rendering; `cmbs audit` and the
/audit filters. The log stays append-only through every surface."""

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from typer.testing import CliRunner

from cmbs import clock
from cmbs.access.service import PermissionRefused
from cmbs.api.app import create_app
from cmbs.audit.service import BadFilter, loan_key, read_log, record, render_entries, render_entry
from cmbs.audit.tables import AuditEntry
from cmbs.cli import app as cli
from cmbs.testing.world import FIXTURE_DEAL
from tests.conftest import needs_db

runner = CliRunner()
AT = dt.datetime(2026, 9, 30, 15, tzinfo=dt.UTC)
OTHER = "Fixture Trust 2026-F1A"  # a deal whose name starts with the fixture deal's


def _entry(**values) -> AuditEntry:
    defaults = {"at": AT, "user_name": "alex", "action": "override value", "outcome": "done"}
    return AuditEntry(**(defaults | values))


@pytest.mark.parametrize(
    ("values", "line"),
    [
        ({}, "2026-09-30T15:00:00+00:00 alex: override value"),
        (
            {
                "role": "analyst",
                "subject_key": "D/2",
                "field": "occupancy",
                "before": "0.91",
                "after": "0.88",
                "reason": "statement",
            },
            "2026-09-30T15:00:00+00:00 alex (analyst): override value D/2 occupancy: "
            "0.91 -> 0.88 (statement)",
        ),
        (
            {"user_name": "olly", "role": "operator", "run_id": 42, "action": "load loan row"},
            "2026-09-30T15:00:00+00:00 olly (operator) in run 42: load loan row",
        ),
        (
            {"before": "", "after": "forbearance"},
            "2026-09-30T15:00:00+00:00 alex: override value: - -> forbearance",
        ),
        (
            {"before": "x", "after": None},
            "2026-09-30T15:00:00+00:00 alex: override value: x -> -",
        ),
        (
            {"outcome": "refused", "reason": "pm may not override values", "user_name": "pat"},
            "2026-09-30T15:00:00+00:00 pat: override value REFUSED: pm may not override values",
        ),
    ],
)
def test_an_entry_on_one_line(values, line):
    assert render_entry(_entry(**values)) == line


def test_entries_rendered():
    assert render_entries([]) == "no audit entries\n"
    assert render_entries([_entry(), _entry(user_name="olly")]) == (
        "2026-09-30T15:00:00+00:00 alex: override value\n"
        "2026-09-30T15:00:00+00:00 olly: override value\n"
    )


@pytest.fixture
def logged(seeded):
    """Entries for loans 2 and 7 of the fixture deal, loan 2 of a deal whose name extends it,
    one from run 42, and a settings change that names no loan."""
    for at, user, deal, loan, run in [
        (AT, "alex", FIXTURE_DEAL, "2", None),
        (AT + dt.timedelta(hours=1), "olly", FIXTURE_DEAL, "7", 42),
        (AT + dt.timedelta(hours=2), "alex", OTHER, "2", None),
    ]:
        with clock.frozen(at):
            record(
                seeded,
                user=user,
                action="load loan row" if run else "override value",
                run_id=run,
                subject="loan",
                subject_key=loan_key(deal, loan),
            )
    with clock.frozen(AT + dt.timedelta(hours=3)):
        record(seeded, user="rae", action="approve settings", subject="settings", subject_key="8")
    seeded.commit()
    return seeded


def _keys(entries) -> list[str | None]:
    return [entry.subject_key for entry in entries]


@needs_db
def test_the_log_by_deal_loan_and_run(logged):
    assert _keys(read_log(logged, "rae", deal=FIXTURE_DEAL)) == [
        f"{FIXTURE_DEAL}/7",
        f"{FIXTURE_DEAL}/2",
    ]
    assert _keys(read_log(logged, "olly", deal=FIXTURE_DEAL, loan="2")) == [f"{FIXTURE_DEAL}/2"]
    assert _keys(read_log(logged, "rae", deal=OTHER)) == [f"{OTHER}/2"]
    assert _keys(read_log(logged, "rae", run=42)) == [f"{FIXTURE_DEAL}/7"]
    assert _keys(read_log(logged, "rae", run=43)) == []
    assert _keys(read_log(logged, "rae", user="alex", deal=FIXTURE_DEAL)) == [f"{FIXTURE_DEAL}/2"]
    assert len(read_log(logged, "rae")) == 4
    with pytest.raises(BadFilter, match="^name the deal of loan 2$"):
        read_log(logged, "rae", loan="2")
    with pytest.raises(PermissionRefused, match="^analyst may not read the audit log by user$"):
        read_log(logged, "alex", deal=FIXTURE_DEAL)


@needs_db
def test_a_deal_name_with_wildcards_matches_only_itself(logged):
    with clock.frozen(AT):
        record(logged, user="alex", action="x", subject="loan", subject_key="100%_Trust/2")
    logged.commit()
    assert _keys(read_log(logged, "rae", deal="100%_Trust")) == ["100%_Trust/2"]
    assert _keys(read_log(logged, "rae", deal="100%")) == []


def _cli(*args: str):
    return runner.invoke(cli, ["audit", *args])


@needs_db
def test_cli_reads_a_loan_history_and_the_log(logged):
    history = _cli("loan", FIXTURE_DEAL, "2", "--user", "pat")
    assert history.exit_code == 0, history.output
    assert history.output == (f"2026-09-30T15:00:00+00:00 alex: override value {FIXTURE_DEAL}/2\n")
    by_run = _cli("log", "--user", "olly", "--run", "42")
    assert by_run.output == (
        f"2026-09-30T16:00:00+00:00 olly in run 42: load loan row {FIXTURE_DEAL}/7\n"
    )
    by_user = _cli(
        "log", "--user", "rae", "--by", "alex", "--start", "2026-09-30", "--end", "2026-09-30"
    )
    assert by_user.output.splitlines() == [
        f"2026-09-30T17:00:00+00:00 alex: override value {OTHER}/2",
        f"2026-09-30T15:00:00+00:00 alex: override value {FIXTURE_DEAL}/2",
    ]
    loan = _cli("log", "--user", "rae", "--deal", FIXTURE_DEAL, "--loan", "7")
    assert loan.output.startswith("2026-09-30T16:00:00+00:00 olly in run 42")
    none = _cli("log", "--user", "rae", "--start", "2026-10-01")
    assert none.output == "no audit entries\n"
    refused = _cli("log", "--user", "pat", "--by", "alex")
    assert (refused.exit_code, refused.output) == (1, "pm may not read the audit log by user\n")
    bad = _cli("log", "--user", "rae", "--start", "2026-10-02", "--end", "2026-10-01")
    assert (bad.exit_code, bad.output) == (
        1,
        "the range ends on 2026-10-01, before it starts on 2026-10-02\n",
    )
    operator = _cli("loan", FIXTURE_DEAL, "2", "--user", "olly")
    assert (operator.exit_code, operator.output) == (1, "operator may not read the panel\n")


@needs_db
def test_api_filters_by_deal_loan_and_run(logged):
    client = TestClient(create_app())
    headers = {"X-CMBS-User": "olly"}
    by_run = client.get("/audit", headers=headers, params={"run": 42})
    assert [(e["subject_key"], e["run"], e["user"]) for e in by_run.json()] == [
        (f"{FIXTURE_DEAL}/7", 42, "olly")
    ]
    by_loan = client.get("/audit", headers=headers, params={"deal": FIXTURE_DEAL, "loan": "2"})
    assert [e["subject_key"] for e in by_loan.json()] == [f"{FIXTURE_DEAL}/2"]
    no_deal = client.get("/audit", headers=headers, params={"loan": "2"})
    assert (no_deal.status_code, no_deal.json()) == (422, {"detail": "name the deal of loan 2"})


@needs_db
def test_the_database_refuses_changes_that_go_around_the_service(logged):
    for statement in ("UPDATE app.audit_entry SET reason = 'x'", "DELETE FROM app.audit_entry"):
        with pytest.raises(DBAPIError, match="the audit log is append-only"):
            logged.execute(text(statement))
        logged.rollback()
    assert len(read_log(logged, "rae")) == 4
