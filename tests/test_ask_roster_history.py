"""The deal roster (Q96), a loan's history and the loss-sizing facts (Q98) in `cmbs ask`."""

import datetime as dt
import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cmbs import clock
from cmbs.access.service import PermissionRefused
from cmbs.api.app import create_app
from cmbs.ask.answer import Citation
from cmbs.ask.history import NOT_REPORTED, Month, Value, history
from cmbs.ask.roster import (
    ROSTER_FIELDS,
    Roster,
    RosterLoan,
    State,
    loans,
    ordered,
    paid_off_since,
    state_of,
    workout_text,
)
from cmbs.ask.service import (
    TagKind,
    UnknownDeal,
    UnknownLoan,
    asset_facts,
    loan_facts,
    tag_value,
)
from cmbs.cli import app as cli
from cmbs.db.models import SCHEMAS
from cmbs.domain import Period
from cmbs.runs.service import Freshness
from cmbs.tape.tables import LoanPeriod, TapeAsset
from cmbs.testing.bdd import changed_tables, data_snapshot, in_session
from cmbs.testing.world import FIXTURE_DEAL
from tests.conftest import needs_db
from tests.fixtures.refresh.offline import offline_fixture  # noqa: F401
from tests.test_routine_world import LOAD_DAY, backfill

JUL, AUG, SEP = Period(2026, 7), Period(2026, 8), Period(2026, 9)
FILED = dt.date(2026, 9, 29)
TAPE = "0009900001-26-000419"
AUGUST_TAPE = "0009900001-26-000417"
REPORT = "0009900001-26-000418"
ASKED = dt.datetime(2026, 10, 6, 12, 0, tzinfo=dt.UTC)


def cite(accession: str = TAPE) -> Citation:
    return Citation("ABS-EE", accession, FILED, "https://t/")


# ---------------------------------------------------------------------------- the state (Q96)


def test_paid_off_since_the_first_zero_month_after_the_last_balance():
    assert paid_off_since([]) is None
    assert paid_off_since([(JUL, Decimal(5)), (AUG, Decimal(0)), (SEP, Decimal(0))]) == AUG
    assert paid_off_since([(JUL, Decimal(0)), (AUG, Decimal(5)), (SEP, Decimal(0))]) == SEP
    assert paid_off_since([(JUL, Decimal(0)), (AUG, None), (SEP, Decimal(0))]) == JUL
    assert paid_off_since([(JUL, Decimal(0)), (AUG, Decimal(1))]) is None
    assert paid_off_since([(JUL, Decimal(0)), (AUG, None)]) == JUL
    assert paid_off_since([(JUL, None)]) is None


def test_the_state_is_read_from_the_dates_and_the_balance():
    transfer, back = dt.date(2023, 1, 10), dt.date(2023, 9, 15)
    assert state_of(transfer, None, None) == (State.SPECIAL_SERVICING, "2023-01-10")
    assert state_of(transfer, back, None) == (State.RETURNED, "2023-09-15")
    assert state_of(transfer, transfer, None) == (State.RETURNED, "2023-01-10")
    # A transfer after the last return: back with the special servicer.
    assert state_of(dt.date(2024, 2, 1), back, None) == (State.SPECIAL_SERVICING, "2024-02-01")
    assert state_of(None, back, None) == (State.RETURNED, "2023-09-15")
    assert state_of(None, None, None) == (State.NEVER_TRANSFERRED, None)
    # A loan that left the pool is paid off, whatever its dates say.
    assert state_of(transfer, None, AUG) == (State.PAID_OFF, "2026-08")


def test_workout_codes_are_shown_by_name():
    assert workout_text(None) is None
    assert workout_text("8") == "resolved (code 8)"
    assert workout_text("42") == "code 42"


def roster_loan(loan: str, state: State, since: str | None, workout: str | None = None):
    return RosterLoan(
        loan=loan,
        property_name="Harbor" if loan != "9" else None,
        ending_balance="$1.00",
        state=state,
        since=since,
        workout_strategy=workout,
        citation=cite(),
    )


def test_rows_read_with_their_state_and_citation():
    special = roster_loan("5", State.SPECIAL_SERVICING, "2024-02-01", "resolved (code 8)")
    assert special.reading() == (
        "in special servicing since 2024-02-01; tape workout strategy: resolved (code 8)"
    )
    assert special.text() == (
        "loan 5, Harbor, $1.00: in special servicing since 2024-02-01; tape workout strategy: "
        f"resolved (code 8) (ABS-EE {TAPE}: {', '.join(ROSTER_FIELDS)})"
    )
    never = roster_loan("9", State.NEVER_TRANSFERRED, None)
    assert never.reading() == "never transferred"
    assert never.text().startswith("loan 9, property name not reported, $1.00: never")
    paid = RosterLoan(
        "3", "Mill", "$0.00", State.PAID_OFF, "2026-08", None, cite(), ROSTER_FIELDS,
        cite(AUGUST_TAPE),
    )  # fmt: skip
    assert paid.text().endswith(f"; first zero balance in ABS-EE {AUGUST_TAPE})")


def test_rows_are_ordered_special_servicing_first_then_by_loan():
    rows = [
        roster_loan("10", State.NEVER_TRANSFERRED, None),
        roster_loan("3", State.PAID_OFF, "2026-08"),
        roster_loan("5", State.SPECIAL_SERVICING, "2024-02-01"),
        roster_loan("4", State.RETURNED, "2023-09-15"),
        roster_loan("2", State.SPECIAL_SERVICING, "2025-03-06"),
        roster_loan("1", State.NEVER_TRANSFERRED, None),
    ]
    assert [row.loan for row in ordered(rows)] == ["2", "5", "4", "3", "1", "10"]


def test_a_roster_opens_with_the_panel_s_freshness():
    fresh = Freshness("F1", dt.date(2026, 10, 1), SEP, 42, dt.date(2026, 10, 1))
    assert fresh.text() == "Panel current through September 2026 (run 42, 2026-10-01)"
    assert Freshness("F1", dt.date(2026, 10, 1), SEP, None, None).text() == (
        "Panel current through September 2026"
    )
    empty = Freshness("F1", dt.date(2026, 1, 1), None, None, None)
    assert empty.text() == "Panel holds no loan tape of F1 filed by 2026-01-01"
    nothing = Roster("F1", dt.date(2026, 1, 1), empty.text(), None, None, False)
    assert nothing.text() == empty.text()
    none_special = Roster("F1", dt.date(2026, 10, 6), fresh.text(), SEP.label, TAPE, True)
    assert none_special.text().splitlines()[1:] == [
        "F1, loans in special servicing as of 2026-10-06, September 2026 tape:",
        "- none",
    ]


# ---------------------------------------------------------------------------- tag facts


def test_tags_read_as_reported_and_never_guessed():
    assert tag_value(TagKind.MONEY, "1115458.57", "x") == "$1,115,458.57"
    assert (
        tag_value(TagKind.MONEY, "12.345", "x") == "'12.345' as reported, not readable as a money"
    )
    assert tag_value(TagKind.DATE, "09-10-2026", "x") == "2026-09-10"
    assert tag_value(TagKind.DATE, "2026-09-10", "x") == (
        "'2026-09-10' as reported, not readable as a date"
    )
    assert tag_value(TagKind.INDICATOR, "false", "x") == "no"
    assert tag_value(TagKind.INDICATOR, "0", "x") == "no"
    assert tag_value(TagKind.INDICATOR, "True", "x") == "yes"
    assert tag_value(TagKind.INDICATOR, "1", "x") == "yes"
    assert tag_value(TagKind.INDICATOR, "maybe", "x") == (
        "'maybe' as reported, not readable as yes or no"
    )
    assert tag_value(TagKind.CODE, "3", "x") == "code 3"


def test_asset_facts_cite_the_tape_field():
    row = LoanPeriod(
        loan="2", period=SEP.first_day, accession=TAPE, filing_date=FILED, file_name="f.xml"
    )
    asset = TapeAsset(
        tags={
            "totalPrincipalInterestAdvancedOutstandingAmount": " 100.00 ",
            "nonRecoverabilityIndicator": "true",
            "liquidationPrepaymentCode": "",
        }
    )
    assert asset_facts(None, row, 1) == []
    facts = asset_facts(asset, row, 9900001)
    assert [(f.label, f.value, f.topic) for f in facts] == [
        ("P&I advances outstanding", "$100.00", "advances"),
        ("advances deemed non-recoverable", "yes", "advances"),
    ]
    assert facts[0].text() == (
        "P&I advances outstanding: $100.00 (September 2026, reported; ABS-EE "
        f"{TAPE}, totalPrincipalInterestAdvancedOutstandingAmount)"
    )
    assert facts[0].name == "tape.totalPrincipalInterestAdvancedOutstandingAmount"
    assert facts[0].citation is not None
    assert facts[0].citation.url.endswith("/9900001/000990000126000419/")


# ---------------------------------------------------------------------------- history


def test_a_month_reads_its_values_and_says_what_was_not_reported():
    month = Month(
        "August 2026",
        "2026-08",
        [Value("ending balance", "$1.00", "b"), Value("DSCR", None, "d")],
        cite(AUGUST_TAPE),
    )
    assert month.reading() == f"ending balance $1.00; DSCR {NOT_REPORTED}"
    assert month.text() == (
        f"August 2026: ending balance $1.00; DSCR not reported (ABS-EE {AUGUST_TAPE}, "
        "filed 2026-09-29)"
    )


# ---------------------------------------------------------------------------- the panel


@pytest.fixture
def panel(seeded):
    backfill(in_session, AUG, SEP)
    clock.set_now(ASKED)
    return seeded


@needs_db
def test_the_roster_lists_every_loan_with_its_state(panel):
    before = data_snapshot(SCHEMAS)
    roster = in_session(lambda db: loans(db, "pat", deal=FIXTURE_DEAL))
    assert changed_tables(before, data_snapshot(SCHEMAS)) == []
    assert roster.freshness == (
        f"Panel current through September 2026 (run 1, {LOAD_DAY.date().isoformat()})"
    )
    assert (roster.period, roster.accession) == ("September 2026", TAPE)
    listed = [(row.loan, row.state, row.since) for row in roster.loans]
    assert listed == [
        ("2", State.SPECIAL_SERVICING, "2025-03-06"),
        ("6", State.PAID_OFF, "2026-08"),
        ("1", State.NEVER_TRANSFERRED, None),
        ("3", State.NEVER_TRANSFERRED, None),
        ("4", State.NEVER_TRANSFERRED, None),
        ("5", State.NEVER_TRANSFERRED, None),
        ("7", State.NEVER_TRANSFERRED, None),
    ]
    paid = roster.loans[1]
    assert paid.paid_off_citation is not None
    assert paid.paid_off_citation.accession == AUGUST_TAPE
    assert roster.loans[0].reading() == (
        "in special servicing since 2025-03-06; tape workout strategy: full payoff (code 11)"
    )
    lines = roster.text().splitlines()
    assert lines[1] == f"{FIXTURE_DEAL}, loans as of 2026-10-06, September 2026 tape:"
    assert lines[2] == f"- {roster.loans[0].text()}"
    special = in_session(lambda db: loans(db, "pat", deal=FIXTURE_DEAL, special_servicing=True))
    assert [row.loan for row in special.loans] == ["2"]
    early = in_session(lambda db: loans(db, "pat", deal=FIXTURE_DEAL, as_of=dt.date(2026, 9, 1)))
    assert early.freshness == "Panel current through August 2026"
    assert early.accession == AUGUST_TAPE
    before_any = in_session(
        lambda db: loans(db, "pat", deal=FIXTURE_DEAL, as_of=dt.date(2026, 1, 1))
    )
    assert (before_any.loans, before_any.period) == ([], None)


@needs_db
def test_the_roster_refuses_like_any_ask(panel):
    with pytest.raises(PermissionRefused):
        in_session(lambda db: loans(db, "quinn", deal=FIXTURE_DEAL))
    with pytest.raises(UnknownDeal):
        in_session(lambda db: loans(db, "pat", deal="Nope"))


@needs_db
def test_the_history_lists_each_month_as_filed(panel):
    found = in_session(lambda db: history(db, "pat", deal=FIXTURE_DEAL, loan="2"))
    assert [month.period for month in found.months] == ["August 2026", "September 2026"]
    september = found.months[-1]
    assert september.reading() == (
        "ending balance $30,000,000.00; payment status 90+ days (code 3); DSCR not reported; "
        "occupancy not reported; financials as of 2024-12-31"
    )
    assert (september.citation.accession, september.month) == (TAPE, "2026-09")
    assert found.text().splitlines()[0] == (
        f"loan 2 of {FIXTURE_DEAL}, month by month as filed by 2026-10-06:"
    )
    early = in_session(
        lambda db: history(db, "pat", deal=FIXTURE_DEAL, loan="2", as_of=dt.date(2026, 9, 1))
    )
    assert [month.period for month in early.months] == ["August 2026"]
    with pytest.raises(UnknownLoan):
        in_session(lambda db: history(db, "pat", deal=FIXTURE_DEAL, loan="99"))
    with pytest.raises(UnknownDeal):
        in_session(lambda db: history(db, "pat", deal="Nope", loan="2"))
    with pytest.raises(PermissionRefused):
        in_session(lambda db: history(db, "olly", deal=FIXTURE_DEAL, loan="2"))


@needs_db
def test_facts_include_the_appraisal_reduction_and_realized_loss(panel):
    found = in_session(lambda db: loan_facts(db, "pat", deal=FIXTURE_DEAL, loan="2"))
    reductions = [f for f in found.facts if f.label == "appraisal reduction amount"]
    assert [(f.value, f.period) for f in reductions] == [("$6,500,000.00", "September 2026")]
    cited = reductions[0].citation
    assert cited is not None and (cited.form, cited.accession) == ("10-D", REPORT)
    assert cited.dated == dt.date(2026, 8, 11)
    assert cited.report_table is not None
    early = in_session(
        lambda db: loan_facts(db, "pat", deal=FIXTURE_DEAL, loan="2", as_of=dt.date(2026, 9, 1))
    )
    assert [f.period for f in early.facts if f.label == "appraisal reduction amount"] == [
        "August 2026"
    ]
    none = in_session(lambda db: loan_facts(db, "pat", deal=FIXTURE_DEAL, loan="1"))
    assert [f for f in none.facts if f.label == "appraisal reduction amount"] == []


runner = CliRunner()


@needs_db
def test_the_cli_and_api_give_rosters_and_histories(panel):
    text = runner.invoke(cli, ["ask", "loans", FIXTURE_DEAL, "--user", "pat"])
    assert text.exit_code == 0, text.output
    assert text.output.startswith("Panel current through September 2026 (run 1, ")
    data = json.loads(
        runner.invoke(
            cli,
            ["ask", "loans", FIXTURE_DEAL, "--user", "pat", "--json", "--special-servicing",
             "--as-of", "2026-10-06"],
        ).output
    )  # fmt: skip
    assert [row["loan"] for row in data["loans"]] == ["2"]
    assert data["loans"][0]["reads"].startswith("in special servicing since 2025-03-06")
    assert data["loans"][0]["state"] == "in special servicing"
    assert data["text"].startswith("Panel current through")
    refused = runner.invoke(cli, ["ask", "loans", FIXTURE_DEAL, "--user", "quinn"])
    assert (refused.exit_code, refused.output) == (1, "quant may not ask questions\n")

    months = runner.invoke(cli, ["ask", "history", FIXTURE_DEAL, "2", "--user", "pat"])
    assert months.exit_code == 0, months.output
    assert months.output.splitlines()[2].startswith("- September 2026: ending balance")
    as_json = json.loads(
        runner.invoke(cli, ["ask", "history", FIXTURE_DEAL, "2", "--user", "pat", "--json"]).output
    )
    assert [m["month"] for m in as_json["months"]] == ["2026-08", "2026-09"]
    assert as_json["months"][1]["reads"].startswith("ending balance $30,000,000.00")
    missing = runner.invoke(cli, ["ask", "history", FIXTURE_DEAL, "99", "--user", "pat"])
    assert missing.exit_code == 1

    client = TestClient(create_app())
    pm = {"X-CMBS-User": "pat"}
    roster = client.get("/ask/loans", params={"deal": FIXTURE_DEAL}, headers=pm)
    assert roster.status_code == 200, roster.text
    assert roster.json()["loans"][0]["loan"] == "2"
    special = client.get(
        "/ask/loans", params={"deal": FIXTURE_DEAL, "special_servicing": True}, headers=pm
    )
    assert [row["loan"] for row in special.json()["loans"]] == ["2"]
    dated = client.get(
        "/ask/history",
        params={"deal": FIXTURE_DEAL, "loan": "2", "as_of": "2026-09-01"},
        headers=pm,
    )
    assert [m["period"] for m in dated.json()["months"]] == ["August 2026"]
