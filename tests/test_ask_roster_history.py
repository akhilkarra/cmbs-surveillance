"""The deal roster (Q96), a loan's history and the loss-sizing facts (Q98) in `cmbs ask`."""

import datetime as dt
import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
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
    loss_by,
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
from cmbs.deals.service import by_name
from cmbs.domain import Period
from cmbs.filings.tables import FilingFile
from cmbs.refresh.tables import RunMonth
from cmbs.runs.service import Freshness, finish_run, start_run
from cmbs.tape.service import AMENDMENT, load_tape
from cmbs.tape.tables import LoanPeriod, LoanTape, TapeAsset
from cmbs.testing.bdd import changed_tables, data_snapshot, in_session
from cmbs.testing.world import FIXTURE_DEAL
from features.steps.published import Publication, only_loans, set_tags
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
        "property Lakeside Hotel Portfolio; ending balance $30,000,000.00; payment status 90+ "
        "days (code 3); DSCR not reported; occupancy not reported; financials as of 2024-12-31"
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
    assert months.output.splitlines()[2].startswith(
        "- September 2026: property Lakeside Hotel Portfolio; ending"
    )
    as_json = json.loads(
        runner.invoke(cli, ["ask", "history", FIXTURE_DEAL, "2", "--user", "pat", "--json"]).output
    )
    assert [m["month"] for m in as_json["months"]] == ["2026-08", "2026-09"]
    assert as_json["months"][1]["reads"].startswith("property Lakeside Hotel Portfolio; ending")
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


# ---------------------------------------------------------------------------- liquidated, off tape


def test_a_loan_that_left_the_pool_with_a_loss_is_liquidated():
    assert state_of(None, None, AUG, Decimal("13049503.19")) == (State.LIQUIDATED, "2026-08")
    assert state_of(None, None, AUG, Decimal("0.00")) == (State.PAID_OFF, "2026-08")
    assert state_of(None, None, None, Decimal("5")) == (State.NEVER_TRANSFERRED, None)


def month_row(period: Period, loss: str | None) -> LoanPeriod:
    return LoanPeriod(
        period=period.first_day, realized_loss=None if loss is None else Decimal(loss)
    )


def test_the_loss_is_the_first_zero_month_s_or_the_latest_before_it():
    months = [month_row(JUL, "5.00"), month_row(AUG, None), month_row(SEP, "7.00")]
    assert loss_by(months, AUG) is months[0]
    assert loss_by(months, SEP) is months[2]
    assert loss_by(months, JUL) is months[0]
    assert loss_by([month_row(JUL, "0.00"), month_row(AUG, "0.00")], AUG) is None
    assert loss_by([month_row(SEP, "7.00")], AUG) is None


def test_rows_off_the_tape_say_why_and_their_last_known_state():
    liquidated = RosterLoan(
        "17", "Regal", "not reported", State.LIQUIDATED, "2026-01", None, cite(),
        realized_loss="$13,049,503.19", loss_citation=cite(AUGUST_TAPE),
    )  # fmt: skip
    assert liquidated.reading() == "liquidated, realized loss $13,049,503.19, since 2026-01"
    assert liquidated.text().endswith(f"; realized loss in ABS-EE {AUGUST_TAPE})")
    held = RosterLoan(
        "20", None, "not on the tape", None, None, None, cite(), (),
        note="quarantined: loan is reported more than once",
    )  # fmt: skip
    assert held.reading() == "quarantined: loan is reported more than once"
    assert held.text() == (
        "loan 20, property name not reported, not on the tape: quarantined: loan is reported "
        f"more than once (ABS-EE {TAPE})"
    )
    rows = [held, roster_loan("3", State.NEVER_TRANSFERRED, None), liquidated]
    assert [row.loan for row in ordered(rows)] == ["17", "3", "20"]


def test_a_month_off_the_tape_says_so():
    month = Month("December 2022", "2022-12", [Value("property", "Ditson", "p")], cite(), "q: x")
    assert month.reading() == "q: x; property Ditson"
    assert Month("x", "y", [], cite(), "not on the tape").reading() == "not on the tape"


# A second fixture deal published month by month (features/steps/published.py), so its tapes can
# say what each test needs: July to September 2026, filed on the 29th, loaded on 2026-10-01.
F2 = "Fixture Trust 2026-F2"
LOADED = dt.datetime(2026, 10, 1, 9, 0, tzinfo=dt.UTC)
ASKED_F2 = dt.datetime(2026, 10, 6, 12, 0, tzinfo=dt.UTC)


def no_appraisal_reduction(content: bytes) -> bytes:
    """The 10-D without loan 2's appraisal reduction (an amount of 0.00 means none)."""
    changed = content.replace(
        b"<td>08/11/2026</td><td>6,500,000.00</td>", b"<td></td><td>0.00</td>"
    )
    assert changed != content
    return changed


@pytest.fixture
def f2(seeded):
    """Loan 4 quarantined in July, its first month; loan 6 liquidated in August with a realized
    loss; loan 3 paid off in September; loan 7
    in special servicing in August and then off the September tape; loan 5 quarantined in
    September; loan 2's appraisal reduction gone from the September 10-D."""
    published = Publication(F2)
    for period in (JUL, AUG, SEP):
        published.month(period)
    published.month(JUL).tape += [
        set_tags("6", {"reportPeriodEndActualBalanceAmount": "1.00"}),
        set_tags("4", {"reportPeriodEndActualBalanceAmount": "xyz"}),
    ]
    published.month(AUG).tape += [
        set_tags("6", {"realizedLossToTrustAmount": "2350000.00"}),
        set_tags("7", {"mostRecentSpecialServicerTransferDate": "08-03-2026"}),
    ]
    published.month(SEP).tape += [
        set_tags("3", {"reportPeriodEndActualBalanceAmount": "0.00"}),
        set_tags("5", {"reportPeriodEndActualBalanceAmount": "abc"}),
        only_loans({"1", "2", "3", "4", "5", "6"}),
    ]
    published.month(SEP).report.append(no_appraisal_reduction)
    published.load(LOADED)
    clock.set_now(ASKED_F2)
    return published


@needs_db
def test_the_roster_lists_liquidated_quarantined_and_off_tape_loans(f2):
    roster = in_session(lambda db: loans(db, "pat", deal=F2))
    rows = {row.loan: row for row in roster.loans}
    assert rows["6"].reading() == "liquidated, realized loss $2,350,000.00, since 2026-08"
    assert rows["6"].loss_citation is not None
    assert rows["3"].reading() == "paid off since 2026-09"
    assert rows["5"].reading() == (
        "quarantined: ending balance is not a number: 'abc'; last known: never transferred"
    )
    assert rows["5"].citation.accession == roster.accession
    assert rows["5"].property_name == "Cedar Logistics Park"
    assert rows["7"].reading() == (
        "not on the September 2026 tape; last reported August 2026, balance $9,000,000.00; "
        "last known: in special servicing since 2026-08-03"
    )
    august = in_session(lambda db: history(db, "pat", deal=F2, loan="7")).months[1]
    assert rows["7"].citation.accession == august.citation.accession
    assert [row.loan for row in roster.loans] == ["2", "7", "6", "3", "1", "4", "5"]
    special = in_session(lambda db: loans(db, "pat", deal=F2, special_servicing=True))
    assert [row.loan for row in special.loans] == ["2", "7"]


@needs_db
def test_a_past_day_s_roster_does_not_see_a_later_month(f2):
    early = in_session(lambda db: loans(db, "pat", deal=F2, as_of=dt.date(2026, 9, 15)))
    rows = {row.loan: row for row in early.loans}
    assert early.period == "August 2026"
    assert rows["3"].state is State.NEVER_TRANSFERRED  # zero only from September
    assert rows["7"].note is None
    assert "5" in rows and rows["5"].note is None


@needs_db
def test_the_history_marks_months_off_the_tape(f2):
    quarantined = in_session(lambda db: history(db, "pat", deal=F2, loan="5"))
    assert [m.reading().split(";")[0] for m in quarantined.months] == [
        "property Cedar Logistics Park",
        "property Cedar Logistics Park",
        "quarantined: ending balance is not a number: 'abc'",
    ]
    assert quarantined.months[2].reading().endswith("; property Cedar Logistics Park")
    gone = in_session(lambda db: history(db, "pat", deal=F2, loan="7"))
    assert [m.reading() for m in gone.months][2] == "not on the tape"
    assert gone.months[2].citation.accession == quarantined.months[2].citation.accession


@needs_db
def test_an_appraisal_reduction_the_latest_10d_omits_is_not_stated(f2):
    def reductions(day: dt.date | None) -> list[str | None]:
        found = in_session(lambda db: loan_facts(db, "pat", deal=F2, loan="2", as_of=day))
        return [f.value for f in found.facts if f.label == "appraisal reduction amount"]

    assert reductions(None) == []
    assert reductions(dt.date(2026, 9, 15)) == ["$6,500,000.00"]


@needs_db
def test_freshness_names_the_run_that_loaded_the_current_tape(f2):
    first = in_session(lambda db: loans(db, "pat", deal=F2)).freshness
    assert first == "Panel current through September 2026 (run 1, 2026-10-01)"
    # A later run loads an older month, and another fails to load October.
    older = Publication(F2)
    older.month(Period(2026, 6))
    older.load(dt.datetime(2026, 10, 3, 9, 0, tzinfo=dt.UTC))

    def failed(db):
        run = start_run(db, kind="refresh deal", started_by="olly")
        deal = by_name(db, F2)
        assert deal is not None
        db.add(
            RunMonth(
                run_id=run.id,
                deal_id=deal.id,
                period=Period(2026, 10).first_day,
                outcome="not loaded",
                reason="10-D failed to parse",
                loan_rows=0,
                at=dt.datetime(2026, 10, 4, 9, 0, tzinfo=dt.UTC),
            )
        )
        finish_run(db, run, status="failed", summary={})

    in_session(failed)
    later = in_session(lambda db: loans(db, "pat", deal=F2))
    assert later.freshness == first
    before = in_session(lambda db: loans(db, "pat", deal=F2, as_of=dt.date(2026, 9, 30)))
    assert before.freshness == "Panel current through September 2026"


@needs_db
def test_a_loan_quarantined_in_its_first_month_has_no_last_known_state(f2):
    july = in_session(lambda db: loans(db, "pat", deal=F2, as_of=dt.date(2026, 8, 15)))
    rows = {row.loan: row for row in july.loans}
    assert july.period == "July 2026"
    assert rows["4"].reading() == "quarantined: ending balance is not a number: 'xyz'"
    assert (rows["4"].state, july.loans[-1].loan) == (None, "4")
    first = in_session(lambda db: history(db, "pat", deal=F2, loan="4")).months[0]
    assert first.reading().startswith("quarantined: ending balance is not a number: 'xyz'")


@needs_db
def test_a_loan_an_amendment_omits_is_off_the_tape(f2):
    def amend(db):
        original = db.scalars(
            select(LoanTape).where(
                LoanTape.period == SEP.first_day, LoanTape.file_name.like("%f2%")
            )
        ).one()
        content = db.get_one(FilingFile, (original.accession, original.file_name)).content
        load_tape(
            db,
            deal=F2,
            content=only_loans({"2", "3", "4", "5", "6"})(content),
            file_name="amended.xml",
            accession="0009900002-26-000900",
            filing_date=dt.date(2026, 10, 2),
            form=AMENDMENT,
            amends=original.accession,
            cik=9900002,
        )
        return original.accession

    original = in_session(amend)
    rows = {row.loan: row for row in in_session(lambda db: loans(db, "pat", deal=F2)).loans}
    assert rows["1"].reading() == (
        "not on the September 2026 tape; last reported September 2026, balance $50,000,000.00; "
        "last known: never transferred"
    )
    assert rows["1"].citation.accession == original
