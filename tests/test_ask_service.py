"""Asking the panel against a database, through the service, `cmbs ask` and /ask."""

import datetime as dt
import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cmbs import clock
from cmbs.access.service import PermissionRefused
from cmbs.api.app import create_app
from cmbs.ask.answer import Kind
from cmbs.ask.service import (
    NoLoan,
    UnknownDeal,
    UnknownLoan,
    ask,
    loan_facts,
    tape_value,
)
from cmbs.cli import app as cli
from cmbs.domain import Period
from cmbs.testing.bdd import changed_tables, data_snapshot, in_session
from cmbs.testing.world import FIXTURE_DEAL
from tests.conftest import needs_db
from tests.fixtures.refresh.offline import offline_fixture  # noqa: F401
from tests.test_routine_world import backfill, wait_for_review

AUG, SEP = Period(2026, 8), Period(2026, 9)
ASKED = dt.datetime(2026, 10, 6, 12, 0, tzinfo=dt.UTC)
REPORT = "0009900001-26-000418"
TAPE = "0009900001-26-000419"
FOLDER = "https://www.sec.gov/Archives/edgar/data/9900001/"
QUESTION = "Why is loan 2 in special servicing and what does the borrower want?"


def test_tape_values_read_as_reported():
    assert tape_value("ending_balance", Decimal("30000000.00")) == "$30,000,000.00"
    assert tape_value("noi", Decimal("2100000.00")) == "$2,100,000.00"
    assert tape_value("payment_status", "3") == "90+ days (code 3)"
    assert tape_value("workout_strategy", "11") == "full payoff (code 11)"
    assert tape_value("dscr", Decimal("1.45")) == "1.45x"
    assert tape_value("occupancy", Decimal("0.88")) == "88%"
    assert tape_value("occupancy", Decimal("0.8850")) == "88.5%"
    assert tape_value("paid_through_date", dt.date(2026, 5, 1)) == "2026-05-01"
    assert tape_value("property_type", "LO") == "LO"


@pytest.fixture
def panel(seeded):
    backfill(in_session, AUG, SEP)
    clock.set_now(ASKED)
    return seeded


@needs_db
def test_facts_cite_the_comment_by_sentence_and_the_tape_by_field(panel):
    found = in_session(lambda db: loan_facts(db, "pat", deal=FIXTURE_DEAL, loan="2"))
    assert (found.deal, found.loan, found.as_of) == (FIXTURE_DEAL, "2", ASKED.date())
    comment = [(f.name, f.value, f.status, f.citation.sentence) for f in found.facts[:8]]  # pyright: ignore[reportOptionalMemberAccess]
    assert comment == [
        ("transfer_reason", "imminent monetary default", "auto-accepted", 1),
        ("transfer_date", "2025-03-06", "auto-accepted", 1),
        ("dscr", "0.71x", "auto-accepted", 2),
        ("dscr_as_of", "2024-12-31", "auto-accepted", 2),
        ("occupancy", "61.4%", "auto-accepted", 2),
        ("occupancy_as_of", "2024-12-31", "auto-accepted", 2),
        ("borrower_request", "maturity extension", "auto-accepted", 3),
        ("request_status", "pending", "auto-accepted", 4),
    ]
    first = found.facts[0].citation
    assert first is not None
    assert (first.form, first.accession, first.filing_date, first.url) == (
        "10-D",
        REPORT,
        dt.date(2026, 9, 29),
        FOLDER + "000990000126000418/",
    )
    assert first.quote == (
        "The loan transferred to Special Servicing effective 3/6/2025 due to imminent "
        "monetary default."
    )
    tape = [(f.name, f.value, f.citation.tape_field) for f in found.facts[8:]]  # pyright: ignore[reportOptionalMemberAccess]
    assert tape == [
        ("tape.ending_balance", "$30,000,000.00", "reportPeriodEndActualBalanceAmount"),
        ("tape.payment_status", "90+ days (code 3)", "paymentStatusLoanCode"),
        (
            "tape.special_servicer_transfer_date",
            "2025-03-06",
            "mostRecentSpecialServicerTransferDate",
        ),
        ("tape.workout_strategy", "full payoff (code 11)", "workoutStrategyCode"),
        ("tape.property_name", "Lakeside Hotel Portfolio", "propertyName"),
        ("tape.property_type", "LO", "propertyTypeCode"),
        ("tape.financials_end", "2024-12-31", "mostRecentFinancialsEndDate"),
    ]
    assert {f.period for f in found.facts} == {"September 2026"}
    assert {f.citation.accession for f in found.facts[8:]} == {TAPE}  # pyright: ignore[reportOptionalMemberAccess]
    assert found.text().splitlines()[0] == f"loan 2 of {FIXTURE_DEAL}, as known on 2026-10-06:"


@needs_db
def test_facts_are_point_in_time(panel):
    early = in_session(
        lambda db: loan_facts(db, "pat", deal=FIXTURE_DEAL, loan="2", as_of=dt.date(2026, 9, 30))
    )
    # The comments were extracted on 2026-10-01; the September tape was public on 2026-09-29.
    assert [f.name for f in early.facts][:1] == ["tape.ending_balance"]
    assert {f.citation.accession for f in early.facts} == {TAPE}  # pyright: ignore[reportOptionalMemberAccess]
    august = in_session(
        lambda db: loan_facts(db, "pat", deal=FIXTURE_DEAL, loan="2", as_of=dt.date(2026, 9, 1))
    )
    assert {f.period for f in august.facts} == {"August 2026"}
    with pytest.raises(UnknownLoan) as nothing:
        in_session(
            lambda db: loan_facts(db, "pat", deal=FIXTURE_DEAL, loan="2", as_of=dt.date(2026, 8, 1))
        )
    assert str(nothing.value) == (
        f"the panel holds nothing for loan 2 of {FIXTURE_DEAL} by 2026-08-01"
    )


@needs_db
def test_a_field_awaiting_review_is_a_fact_without_a_value(panel):
    in_session(lambda db: wait_for_review(db, field="workout_strategy"))
    found = in_session(lambda db: loan_facts(db, "alex", deal=FIXTURE_DEAL, loan="2"))
    waiting = [f for f in found.facts if f.name == "workout_strategy"]
    assert [(f.value, f.status, f.citation) for f in waiting] == [(None, "needs review", None)]
    answer = in_session(
        lambda db: ask(db, "rae", "What is the workout strategy for loan 2?", deal=FIXTURE_DEAL)
    )
    assert (answer.kind, answer.stated, answer.awaiting_review) == (
        Kind.ANSWERED,
        [],
        ["workout strategy"],
    )


@needs_db
def test_asking_reads_and_never_writes(panel):
    before = data_snapshot()
    answer = in_session(lambda db: ask(db, "pat", QUESTION, deal=FIXTURE_DEAL))
    assert [f.name for f in answer.stated] == [
        "transfer_reason",
        "transfer_date",
        "borrower_request",
        "request_status",
        "tape.special_servicer_transfer_date",
    ]
    refused = in_session(lambda db: ask(db, "pat", "Mark loan 2 as resolved", deal=FIXTURE_DEAL))
    assert refused.kind is Kind.READ_ONLY
    no_loan = in_session(lambda db: ask(db, "pat", "Delete everything", deal=FIXTURE_DEAL))
    assert (no_loan.kind, no_loan.loan) == (Kind.READ_ONLY, "")
    given = in_session(
        lambda db: ask(db, "pat", "What does the borrower want?", deal=FIXTURE_DEAL, loan="2")
    )
    assert [f.name for f in given.stated] == ["borrower_request", "request_status"]
    assert changed_tables(before, data_snapshot()) == []


@needs_db
def test_ask_refusals(panel):
    with pytest.raises(PermissionRefused) as quant:
        in_session(lambda db: ask(db, "quinn", QUESTION, deal=FIXTURE_DEAL))
    assert str(quant.value) == "quant may not ask questions"
    with pytest.raises(NoLoan) as unnamed:
        in_session(lambda db: ask(db, "pat", "What is the balance?", deal=FIXTURE_DEAL))
    assert str(unnamed.value) == "name the loan the question is about, like 'loan 2'"
    with pytest.raises(UnknownDeal) as deal:
        in_session(lambda db: ask(db, "pat", QUESTION, deal="Nope"))
    assert str(deal.value) == "no deal named 'Nope'"
    with pytest.raises(UnknownDeal):
        in_session(lambda db: ask(db, "pat", "Mark loan 2 as resolved", deal="Nope"))
    with pytest.raises(UnknownLoan):
        in_session(lambda db: loan_facts(db, "pat", deal=FIXTURE_DEAL, loan="99"))


runner = CliRunner()


@needs_db
def test_the_cli_gives_facts_and_answers(panel):
    facts = runner.invoke(cli, ["ask", "facts", FIXTURE_DEAL, "2", "--user", "pat"])
    assert facts.exit_code == 0, facts.output
    lines = facts.output.splitlines()
    assert lines[0] == f"loan 2 of {FIXTURE_DEAL}, as known on 2026-10-06:"
    assert lines[1] == (
        "- transfer reason: imminent monetary default (September 2026, auto-accepted; 10-D "
        f'{REPORT}, sentence 1: "The loan transferred to Special Servicing effective 3/6/2025 '
        'due to imminent monetary default.")'
    )
    data = json.loads(
        runner.invoke(cli, ["ask", "facts", FIXTURE_DEAL, "2", "--user", "pat", "--json"]).output
    )
    assert (data["deal"], data["loan"], data["as_of"]) == (FIXTURE_DEAL, "2", "2026-10-06")
    assert data["facts"][6]["citation"]["sentence"] == 3
    early = runner.invoke(
        cli, ["ask", "facts", FIXTURE_DEAL, "2", "--user", "pat", "--as-of", "2026-08-31"]
    )
    assert "(August 2026, reported; ABS-EE" in early.output
    bad_day = ["ask", "facts", FIXTURE_DEAL, "2", "--as-of", "x", "--user", "pat"]
    assert runner.invoke(cli, bad_day).exit_code == 2

    answered = runner.invoke(
        cli, ["ask", "question", QUESTION, "--deal", FIXTURE_DEAL, "--user", "pat"]
    )
    assert answered.exit_code == 0, answered.output
    assert answered.output.splitlines()[0] == f"From the panel, for loan 2 of {FIXTURE_DEAL}:"
    as_json = json.loads(
        runner.invoke(
            cli,
            ["ask", "question", "Mark loan 2 as resolved", "--deal", FIXTURE_DEAL, "--user",
             "pat", "--json"],
        ).output
    )  # fmt: skip
    assert as_json["kind"] == "read-only"
    assert as_json["text"].startswith("asking is read-only")
    refused = runner.invoke(
        cli, ["ask", "question", QUESTION, "--deal", FIXTURE_DEAL, "--user", "quinn"]
    )
    assert (refused.exit_code, refused.output) == (1, "quant may not ask questions\n")
    nofacts = runner.invoke(cli, ["ask", "facts", "Nope", "2", "--user", "pat"])
    assert (nofacts.exit_code, nofacts.output) == (1, "no deal named 'Nope'\n")


@needs_db
def test_the_api_gives_facts_and_answers(panel):
    client = TestClient(create_app())
    pm = {"X-CMBS-User": "pat"}
    facts = client.get("/ask/facts", params={"deal": FIXTURE_DEAL, "loan": "2"}, headers=pm)
    assert facts.status_code == 200, facts.text
    body = facts.json()
    assert body["facts"][0]["citation"]["accession"] == REPORT
    answer = client.get("/ask", params={"question": QUESTION, "deal": FIXTURE_DEAL}, headers=pm)
    assert answer.status_code == 200, answer.text
    assert answer.json()["kind"] == "answered"
    assert answer.json()["text"].startswith("From the panel")
    dated = client.get(
        "/ask",
        params={"question": "What is the balance?", "deal": FIXTURE_DEAL, "loan": "2",
                "as_of": "2026-09-01"},
        headers=pm,
    )  # fmt: skip
    assert dated.json()["stated"][0]["period"] == "August 2026"
    refused = client.get(
        "/ask", params={"question": QUESTION, "deal": FIXTURE_DEAL}, headers={"X-CMBS-User": "olly"}
    )
    assert (refused.status_code, refused.json()) == (
        403,
        {"detail": "operator may not ask questions"},
    )
    missing = client.get("/ask/facts", params={"deal": FIXTURE_DEAL, "loan": "99"}, headers=pm)
    assert missing.status_code == 422
