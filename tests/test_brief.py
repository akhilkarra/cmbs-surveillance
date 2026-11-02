"""The committee brief (grill log Q98): composed only from what asking gives, every value cited."""

import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cmbs import clock
from cmbs.access.service import PermissionRefused
from cmbs.api.app import create_app
from cmbs.ask.answer import (
    NEEDS_REVIEW,
    Answer,
    Citation,
    Fact,
    Kind,
    is_write_request,
    outside_in,
    topics_in,
)
from cmbs.ask.history import Month, Value
from cmbs.ask.roster import RosterLoan, State
from cmbs.ask.service import ASSET_TAGS, TAPE_FIELDS, UnknownDeal
from cmbs.brief.service import (
    NOT_IN_PANEL,
    QUESTIONS,
    SHOWN_MONTHS,
    TAPE_TAGS,
    Brief,
    Held,
    Item,
    Section,
    answer_item,
    brief,
    condensed,
    month_line,
    not_reported,
    other_line,
)
from cmbs.cli import app as cli
from cmbs.db.models import SCHEMAS
from cmbs.domain import Period
from cmbs.testing.bdd import changed_tables, data_snapshot, in_session
from cmbs.testing.world import FIXTURE_DEAL
from tests.conftest import needs_db
from tests.fixtures.refresh.offline import offline_fixture  # noqa: F401
from tests.test_routine_world import LOAD_DAY, backfill

AUG, SEP = Period(2026, 8), Period(2026, 9)
FILED = dt.date(2026, 9, 29)
TAPE = "0009900001-26-000419"
AUGUST_TAPE = "0009900001-26-000417"
REPORT = "0009900001-26-000418"
ASKED = dt.datetime(2026, 10, 6, 12, 0, tzinfo=dt.UTC)


def cite(accession: str = TAPE, **more) -> Citation:
    return Citation("ABS-EE", accession, FILED, "https://t/", **more)


def row(loan: str, state: State, since: str | None, **more) -> RosterLoan:
    values = {
        "property_name": "Harbor",
        "ending_balance": "$1.00",
        "workout_strategy": None,
        "citation": cite(),
    } | more
    return RosterLoan(loan=loan, state=state, since=since, **values)


# ---------------------------------------------------------------------------- the questions


def test_each_question_names_exactly_its_topics_to_the_composer():
    for question in QUESTIONS:
        assert sorted(topics_in(question.asked)) == sorted(question.topics), question
        assert outside_in(question.asked) == [], question
        assert not is_write_request(question.asked), question


def test_what_the_panel_never_holds_is_what_ask_calls_not_in_the_data():
    assert NOT_IN_PANEL[:2] == ("ASER", "appraised value")
    for name in NOT_IN_PANEL:
        assert outside_in(name), name
        assert topics_in(name) == [], name


def test_every_tape_tag_is_known_by_its_topic():
    tags = {tag for found in TAPE_TAGS.values() for tag in found}
    assert tags == {spec.tag for spec in (*TAPE_FIELDS, *ASSET_TAGS)}
    assert TAPE_TAGS["liquidation"] == ("liquidationPrepaymentDate", "liquidationPrepaymentCode")


def test_a_topic_with_nothing_says_where_the_tape_was_empty():
    assert not_reported("realized loss", cite(), "September 2026") == (
        "realized loss: not reported on the September 2026 tape, so the panel holds none "
        f"(ABS-EE {TAPE}: realizedLossToTrustAmount)"
    )
    assert not_reported("borrower request", cite(), "September 2026") == (
        "borrower request: not in the data; the panel holds none for this loan"
    )


# ---------------------------------------------------------------------------- the lines


def fact(name: str, label: str, topic: str, value: str | None, status: str = "reported", **more):
    citation = more.pop("citation", cite(tape_field="workoutStrategyCode"))
    return Fact(name, label, topic, "September 2026", value, status, citation, **more)


SENTENCE = "The special servicer is evaluating the request."
WAITING = fact(
    "workout_strategy",
    "workout strategy",
    "workout strategy",
    None,
    NEEDS_REVIEW,
    citation=Citation("10-D", REPORT, FILED, "https://t/", sentence=4, quote=SENTENCE),
    support="unsettled",
)
TAPE_WORKOUT = fact(
    "tape.workout_strategy", "workout strategy on the tape", "workout strategy", "foreclosure"
)


def test_a_question_follows_q97_for_a_field_awaiting_review():
    plan = QUESTIONS[3]
    item = answer_item(
        plan, deal="F1", row=row("2", State.SPECIAL_SERVICING, "2025-03-06"),
        period="September 2026", facts=[WAITING, TAPE_WORKOUT],
    )  # fmt: skip
    assert item.stated == [TAPE_WORKOUT]
    assert item.lines() == [
        "The servicer's stated plan:",
        f"- {TAPE_WORKOUT.text()}",
        "- the workout strategy is awaiting human review; the comment's sentence on it, "
        f'unreviewed: not a panel fact: "{SENTENCE}" (10-D {REPORT}, sentence 4)',
        "- modification: not reported on the September 2026 tape, so the panel holds none "
        f"(ABS-EE {TAPE}: modificationCode)",
    ]


def test_a_question_the_panel_holds_nothing_on_says_so_for_each_topic():
    item = answer_item(
        QUESTIONS[5], deal="F1", row=row("2", State.SPECIAL_SERVICING, "2025-03-06"),
        period="September 2026", facts=[TAPE_WORKOUT],
    )  # fmt: skip
    assert item.answer.kind is Kind.NOT_IN_DATA
    assert item.stated == []
    assert item.lines()[0] == "Advances outstanding:"
    assert item.lines()[1].startswith("- advances: not reported on the September 2026 tape")
    assert len(item.lines()) == 2


def test_months_and_other_loans_read_with_their_citations():
    month = Month(
        "September 2026", "2026-09",
        [Value("ending balance", "$1.00", "reportPeriodEndActualBalanceAmount"),
         Value("DSCR", None, "mostRecentDebtServiceCoverageNetOperatingIncomePercentage")],
        cite(),
    )  # fmt: skip
    assert month_line(month) == (
        f"September 2026: ending balance $1.00; DSCR not reported (ABS-EE {TAPE}: "
        "reportPeriodEndActualBalanceAmount, "
        "mostRecentDebtServiceCoverageNetOperatingIncomePercentage)"
    )
    back = row("4", State.RETURNED, "2023-09-15", workout_strategy="resolved (code 8)")
    assert other_line(back) == (
        "loan 4, Harbor: returned to the master servicer on 2023-09-15; tape workout strategy: "
        f"resolved (code 8) (ABS-EE {TAPE}: mostRecentMasterServicerReturnDate)"
    )
    paid = row("6", State.PAID_OFF, "2026-08", paid_off_citation=cite(AUGUST_TAPE))
    assert other_line(paid) == (
        "loan 6, Harbor: paid off, zero balance since 2026-08 "
        f"(ABS-EE {AUGUST_TAPE}: reportPeriodEndActualBalanceAmount)"
    )
    never = row("1", State.NEVER_TRANSFERRED, None, property_name=None)
    assert other_line(never) == (
        "loan 1, property name not reported: never transferred to the special servicer "
        f"(ABS-EE {TAPE}: mostRecentSpecialServicerTransferDate, "
        "mostRecentMasterServicerReturnDate)"
    )


def months_of(count: int) -> list[Month]:
    found, period = [], Period(2025, 1)
    for number in range(count):
        found.append(
            Month(period.label, f"{period.year}-{period.month:02d}", [], cite(f"A-{number}"))
        )
        period = period.shift(1)
    return found


def test_the_history_is_condensed_to_the_months_held_and_the_last_six():
    assert condensed([]) == (None, [])
    one = months_of(1)
    held, shown = condensed(one)
    assert held is not None and held == Held(1, one[0], one[0]) and shown == one
    assert held.text() == "1 month held, January 2025 (ABS-EE A-0)"
    many = months_of(21)
    held, shown = condensed(many)
    assert held is not None and shown == many[-SHOWN_MONTHS:]
    assert [month.period for month in shown][0] == "April 2026"
    assert held.text() == (
        "21 months held, January 2025 to September 2026 (ABS-EE A-0 to ABS-EE A-20)"
    )


def test_a_brief_opens_with_freshness_and_ends_each_section_with_the_gaps():
    fresh = "Panel current through September 2026 (run 1, 2026-10-01)"
    day = dt.date(2026, 10, 6)
    assert Brief("F1", day, "Panel holds no loan tape", None).text() == "Panel holds no loan tape"
    quiet = Brief("F1", day, fresh, "September 2026", [], [row("1", State.NEVER_TRANSFERRED, None)])
    assert quiet.text().splitlines()[:2] == [
        fresh,
        "Committee brief on F1, as known on 2026-10-06: no loan is in special servicing on the "
        "September 2026 tape.",
    ]
    assert quiet.text().splitlines()[-2:] == [
        "The other loans:",
        f"- {other_line(row('1', State.NEVER_TRANSFERRED, None))}",
    ]
    special = row("2", State.SPECIAL_SERVICING, "2025-03-06")
    item = Item("Payment status", Answer("payment status", "F1", "2", Kind.NOT_IN_DATA))
    section = Section(special, [item], None, [])
    assert section.lines() == [
        special.text(),
        "Payment status:",
        "History the panel holds (up to the latest six months as filed):",
        f"Not in the panel: {', '.join(NOT_IN_PANEL)}.",
    ]
    lines = Brief("F1", day, fresh, "September 2026", [section]).text().splitlines()
    assert lines[1].endswith("the loans in special servicing on the September 2026 tape, every "
                             "value cited.")  # fmt: skip
    assert lines[2:] == ["", *section.lines()]


# ---------------------------------------------------------------------------- the panel


@pytest.fixture
def panel(seeded):
    backfill(in_session, AUG, SEP)
    clock.set_now(ASKED)
    return seeded


@needs_db
def test_the_brief_composes_the_roster_facts_and_history_and_writes_nothing(panel):
    before = data_snapshot(SCHEMAS)
    found = in_session(lambda db: brief(db, "pat", deal=FIXTURE_DEAL))
    assert changed_tables(before, data_snapshot(SCHEMAS)) == []
    assert found.freshness == (
        f"Panel current through September 2026 (run 1, {LOAD_DAY.date().isoformat()})"
    )
    assert [section.loan.loan for section in found.sections] == ["2"]
    assert [other.loan for other in found.others] == ["6", "1", "3", "4", "5", "7"]
    [section] = found.sections
    assert [month.period for month in section.history] == ["August 2026", "September 2026"]
    assert section.held is not None and section.held.text() == (
        f"2 months held, August 2026 to September 2026 (ABS-EE {AUGUST_TAPE} to ABS-EE {TAPE})"
    )
    assert [item.heading for item in section.items] == [q.heading for q in QUESTIONS]
    transfer = section.items[0]
    assert {f.name for f in transfer.stated} >= {"transfer_date", "transfer_reason"}
    text = found.text()
    assert text.splitlines()[0] == found.freshness
    assert "paid off, zero balance since 2026-08" in text
    # The appraisal reduction, cited to the 10-D's report table.
    reduction = [line for line in text.splitlines() if line.startswith("- appraisal reduction")]
    assert len(reduction) == 1 and "$6,500,000.00" in reduction[0], reduction
    assert f"10-D {REPORT}, " in reduction[0] and "dated 2026-08-11" in reduction[0], reduction
    early = in_session(lambda db: brief(db, "pat", deal=FIXTURE_DEAL, as_of=dt.date(2026, 9, 1)))
    assert early.period == "August 2026"
    assert [month.period for month in early.sections[0].history] == ["August 2026"]


@needs_db
def test_the_brief_refuses_like_any_ask(panel):
    with pytest.raises(PermissionRefused):
        in_session(lambda db: brief(db, "quinn", deal=FIXTURE_DEAL))
    with pytest.raises(UnknownDeal):
        in_session(lambda db: brief(db, "pat", deal="Nope"))
    nothing = in_session(lambda db: brief(db, "pat", deal=FIXTURE_DEAL, as_of=dt.date(2025, 1, 1)))
    assert nothing.period is None
    assert nothing.text() == nothing.freshness


runner = CliRunner()


@needs_db
def test_the_cli_and_api_give_the_brief(panel):
    text = runner.invoke(cli, ["brief", "deal", FIXTURE_DEAL, "--user", "pat"])
    assert text.exit_code == 0, text.output
    assert text.output.startswith("Panel current through September 2026 (run 1, ")
    data = json.loads(
        runner.invoke(
            cli, ["brief", "deal", FIXTURE_DEAL, "--user", "pat", "--json", "--as-of", "2026-10-06"]
        ).output
    )
    assert [section["loan"]["loan"] for section in data["sections"]] == ["2"]
    assert data["text"] == text.output.rstrip("\n")
    refused = runner.invoke(cli, ["brief", "deal", FIXTURE_DEAL, "--user", "quinn"])
    assert (refused.exit_code, refused.output) == (1, "quant may not ask questions\n")
    bad = runner.invoke(cli, ["brief", "deal", FIXTURE_DEAL, "--user", "pat", "--as-of", "soon"])
    assert bad.exit_code == 2

    client = TestClient(create_app())
    found = client.get("/brief/deal", params={"deal": FIXTURE_DEAL}, headers={"X-CMBS-User": "pat"})
    assert found.status_code == 200, found.text
    assert found.json()["text"] == data["text"]
    assert client.get("/brief/deal", params={"deal": FIXTURE_DEAL}).status_code == 401
