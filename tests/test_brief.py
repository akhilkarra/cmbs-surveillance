"""The committee brief (grill log Q98): composed only from what asking gives, every value cited."""

import dataclasses
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
from cmbs.ask.roster import ROSTER_FIELDS, Roster, RosterLoan, State
from cmbs.ask.service import ASSET_TAGS, PROPERTY_TAGS, TAPE_FIELDS, UnknownDeal
from cmbs.brief.service import (
    NOT_IN_PANEL,
    QUESTIONS,
    SHOWN_MONTHS,
    TAPE_TAGS,
    Brief,
    Held,
    Item,
    LastKnown,
    Section,
    _cites,
    _tape_of,
    answer_item,
    brief,
    condensed,
    month_line,
    not_reported,
    other_line,
    others_lines,
    renamed_notice,
    split_book,
    unseen_notice,
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


def row(loan: str, state: State | None, since: str | None, **more) -> RosterLoan:
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
    assert NOT_IN_PANEL[:2] == ("ASER", "LTV")
    for name in NOT_IN_PANEL:
        assert outside_in(name), name
        assert topics_in(name) == [], name
    # What the tape gives is not among them (Q100).
    for given in ("valuation", "appraised value", "maturity date", "interest rate", "tenant"):
        assert not any(given in name for name in NOT_IN_PANEL), given


def test_every_tape_tag_is_known_by_its_topic():
    tags = {tag for found in TAPE_TAGS.values() for tag in found}
    assert tags == {spec.tag for spec in (*TAPE_FIELDS, *ASSET_TAGS, *PROPERTY_TAGS)}
    assert TAPE_TAGS["maturity date"] == ("maturityDate",)
    assert TAPE_TAGS["interest rate"] == ("reportPeriodInterestRatePercentage",)
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


def test_loans_off_the_tape_quarantined_or_liquidated_read_as_the_roster_has_them():
    lost = row(
        "8", State.LIQUIDATED, "2026-01", realized_loss="$2.00",
        paid_off_citation=cite(AUGUST_TAPE), loss_citation=cite("L"),
    )  # fmt: skip
    assert other_line(lost) == (
        "loan 8, Harbor: liquidated, realized loss $2.00, zero balance since 2026-01 "
        f"(ABS-EE {AUGUST_TAPE}: reportPeriodEndActualBalanceAmount; ABS-EE L: "
        "realizedLossToTrustAmount)"
    )
    held = row(
        "9", State.SPECIAL_SERVICING, "2025-03-06", ending_balance=None,
        tape_fields=(), note="quarantined: balance unreadable",
    )  # fmt: skip
    assert other_line(held) == (
        "loan 9, Harbor: quarantined: balance unreadable; last known: in special servicing "
        f"since 2025-03-06 (ABS-EE {TAPE})"
    )
    gone = row("10", None, None, note="not on the September 2026 tape; last reported July 2026")
    assert other_line(gone).startswith(
        "loan 10, Harbor: not on the September 2026 tape; last reported July 2026 (ABS-EE "
    )
    omitted = Month("August 2026", "2026-08", [], cite(AUGUST_TAPE), "not on the tape")
    assert month_line(omitted) == f"August 2026: not on the tape (ABS-EE {AUGUST_TAPE})"


def test_only_loans_in_special_servicing_on_the_current_tape_get_a_section():
    special = row("2", State.SPECIAL_SERVICING, "2025-03-06")
    noted = row("9", State.SPECIAL_SERVICING, "2025-03-06", note="not on the September tape")
    back = row("4", State.RETURNED, "2023-09-15")
    returned = row("7", State.RETURNED, "2023-09-15", note="quarantined: unreadable")
    assert split_book([special, noted, back, returned]) == (
        [special],
        [noted],
        [back, returned],
    )


def test_the_other_loans_fit_a_page():
    late = cite(AUGUST_TAPE)
    off = "not on the October 2026 tape; last reported August 2026, balance $1.00"
    rows = [
        row("57", State.SPECIAL_SERVICING, "2025-11-24", ending_balance=None, note=off,
            citation=late),
        row("12", State.RETURNED, "2024-05-24"),
        row("17", State.LIQUIDATED, "2026-01", realized_loss="$2.00",
            paid_off_citation=cite("Z"), loss_citation=cite("Z")),
        row("3", State.PAID_OFF, "2024-08", paid_off_citation=cite("P")),
        row("60", State.PAID_OFF, "2026-04", ending_balance=None, note=off, citation=late),
        row("9", None, None, ending_balance=None, tape_fields=(), note="quarantined: unreadable"),
        row("1", State.NEVER_TRANSFERRED, None),
        row("4", State.NEVER_TRANSFERRED, None),
        row("20", State.NEVER_TRANSFERRED, None, ending_balance=None, note=off, citation=late),
        row("26", State.NEVER_TRANSFERRED, None, ending_balance=None, note=off, citation=late),
        row("30", State.NEVER_TRANSFERRED, None, ending_balance=None, note="not a roster note"),
        # The same month and state, but another last tape: a group of its own.
        row("29", State.NEVER_TRANSFERRED, None, ending_balance=None, note=off,
            citation=cite("JULY")),
        row("70", State.NEVER_TRANSFERRED, None, ending_balance=None, tape_fields=(),
            note="quarantined: balance unreadable"),
        row("71", State.PAID_OFF, "2025-01", ending_balance=None, tape_fields=(),
            note="quarantined: balance unreadable"),
        row("72", State.NEVER_TRANSFERRED, None, ending_balance=None, tape_fields=(),
            note="quarantined: balance unreadable"),
    ]  # fmt: skip
    lines = others_lines(rows, "October 2026")
    assert lines[0].startswith("loan 57, Harbor: not on the October 2026 tape; last reported ")
    assert "last known: in special servicing since 2025-11-24" in lines[0]
    assert [line.split(",")[0] for line in lines[1:5]] == [
        "loan 12", "loan 17", "loan 3", "loan 9",
    ]  # fmt: skip
    # One filing cited for two fields is named once.
    assert lines[2].endswith(
        "(ABS-EE Z: reportPeriodEndActualBalanceAmount, realizedLossToTrustAmount)"
    )
    # A note the brief cannot read stays a line of its own.
    assert lines[5].startswith("loan 30, Harbor: not a roster note")
    assert lines[6:] == [
        "never transferred: loans 1, 4 (2 loans) (ABS-EE "
        f"{TAPE}: mostRecentSpecialServicerTransferDate, mostRecentMasterServicerReturnDate)",
        "not on the October 2026 tape, last known paid off: loan 60 (1 loan; last reported "
        f"August 2026) (ABS-EE {AUGUST_TAPE}: reportPeriodEndActualBalanceAmount)",
        "not on the October 2026 tape, last known never transferred: loans 20, 26 (2 loans; "
        f"last reported August 2026) (ABS-EE {AUGUST_TAPE}: "
        "mostRecentSpecialServicerTransferDate, mostRecentMasterServicerReturnDate)",
        "not on the October 2026 tape, last known never transferred: loan 29 (1 loan; last "
        "reported August 2026) (ABS-EE JULY: mostRecentSpecialServicerTransferDate, "
        "mostRecentMasterServicerReturnDate)",
        "quarantined on the October 2026 tape (balance unreadable), last known never "
        f"transferred: loans 70, 72 (2 loans) (ABS-EE {TAPE})",
        "quarantined on the October 2026 tape (balance unreadable), last known paid off: "
        f"loan 71 (1 loan) (ABS-EE {TAPE})",
    ]


def test_a_filing_cited_twice_is_named_once_with_each_field_once():
    assert _cites((cite(), ("a", "b")), (cite("Y"), ()), (cite(), ("b", "c"))) == (
        f"ABS-EE {TAPE}: a, b, c; ABS-EE Y"
    )


def test_months_quarantined_or_omitted_are_not_held():
    months = months_of(4)
    noted = [
        months[0],
        dataclasses.replace(months[1], note="quarantined: unreadable"),
        dataclasses.replace(months[2], note="not on the tape"),
        months[3],
    ]
    held, shown = condensed(noted)
    assert held is not None and held == Held(2, months[0], months[3], 1, 1)
    assert held.text() == (
        "2 months held, January 2025 to April 2025, besides 1 month quarantined and 1 month not "
        "on the tape (ABS-EE A-0 to ABS-EE A-3)"
    )
    assert shown == noted
    nothing = [dataclasses.replace(months[0], note="quarantined: unreadable")]
    assert condensed(nothing) == (None, nothing)
    two = [dataclasses.replace(months[0], note="not on the tape")] * 2 + [months[3]]
    found, _ = condensed(two)
    assert found is not None and found.text() == (
        "1 month held, April 2025, besides 2 months not on the tape (ABS-EE A-3)"
    )


def test_a_loan_last_known_in_special_servicing_gets_a_short_section():
    august = Month(
        "August 2026", "2026-08",
        [Value("ending balance", "$9.00", "reportPeriodEndActualBalanceAmount")],
        cite(AUGUST_TAPE),
    )  # fmt: skip
    off = "not on the September 2026 tape; last reported August 2026, balance $9.00"
    gone = row("5", State.SPECIAL_SERVICING, "2024-02-01", ending_balance=None, note=off,
               citation=cite(AUGUST_TAPE))  # fmt: skip
    assert LastKnown(gone, cite(), august).lines() == [
        "loan 5, Harbor, last known on the August 2026 tape: in special servicing since "
        f"2024-02-01 (ABS-EE {AUGUST_TAPE}: {', '.join(ROSTER_FIELDS)})",
        f"- {off} (ABS-EE {TAPE}; ABS-EE {AUGUST_TAPE}: reportPeriodEndActualBalanceAmount)",
        f"- {month_line(august)}",
    ]
    held = dataclasses.replace(gone, note="quarantined: unreadable", citation=cite())
    assert LastKnown(held, cite(), august).lines()[1] == (
        f"- quarantined: unreadable (ABS-EE {TAPE})"
    )
    day = dt.date(2026, 10, 6)
    notice = "1 loan is not on the September 2026 tape and was last known in special servicing."
    both = Brief("F1", day, "fresh", "September 2026", [], [],
                 [LastKnown(gone, cite(), august)], [notice])  # fmt: skip
    assert both.text().splitlines()[1:4] == [
        "Committee brief on F1, as known on 2026-10-06: the loans in special servicing on the "
        "September 2026 tape, and those last known there, every value cited.",
        notice,
        "",
    ]
    assert "no loan is in special servicing" not in both.text()


def test_the_header_counts_the_loans_the_tape_does_not_show():
    off = "not on the September 2026 tape; last reported August 2026, balance $1.00"
    special = row("5", State.SPECIAL_SERVICING, "2024-02-01", note=off)
    quiet = row("6", State.NEVER_TRANSFERRED, None, note=off)
    period = "September 2026"
    assert unseen_notice([], period, quarantined=False) is None
    assert unseen_notice([special], period, quarantined=False) == (
        "1 loan is not on the September 2026 tape and was last known in special servicing."
    )
    assert unseen_notice([special, quiet], period, quarantined=False) == (
        "2 loans are not on the September 2026 tape, 1 of them last known in special servicing."
    )
    assert unseen_notice([quiet], period, quarantined=True) == (
        "1 loan on the September 2026 tape was quarantined, none last known in special servicing."
    )
    assert unseen_notice([special, special], period, quarantined=True) == (
        "2 loans on the September 2026 tape were quarantined and were last known in special "
        "servicing."
    )


def roster_of(period: str | None, names: dict[str, str | None]) -> Roster:
    rows = [row(loan, State.NEVER_TRANSFERRED, None, property_name=name)
            for loan, name in names.items()]  # fmt: skip
    return Roster("F1", dt.date(2021, 8, 31), "fresh", period, "A", False, rows)


def test_the_current_tape_is_the_one_its_rows_cite():
    off = "not on the September 2026 tape; last reported August 2026, balance $1.00"
    gone = row("5", State.NEVER_TRANSFERRED, None, note=off, citation=cite(AUGUST_TAPE))
    held = row("6", State.NEVER_TRANSFERRED, None, note="quarantined: x")
    assert _tape_of(roster_of("September 2026", {})) is None
    only_gone = dataclasses.replace(roster_of("September 2026", {}), loans=[gone])
    assert _tape_of(only_gone) is None
    assert _tape_of(dataclasses.replace(only_gone, loans=[gone, held])) == cite()


def test_a_tape_whose_names_mostly_differ_may_be_another_deal_s():
    july = roster_of("July 2021", {"1": "Mall", "2": "Tower", "3": "Inn", "4": None})
    august = roster_of("August 2021", {"1": "Depot", "2": "Plaza", "3": "inn ", "4": "X"})
    assert renamed_notice(august, july) == (
        "The property names of 2 of the 3 loans on both the August 2021 and July 2021 tapes "
        "differ: the August 2021 tape may be another deal's (docs/MESS-REGISTER.md M-029)."
    )
    # Half or fewer differing is not most; no earlier tape, or the same one, says nothing.
    same = roster_of("August 2021", {"1": "Depot", "2": "Tower", "3": "Inn", "4": "X"})
    assert renamed_notice(same, july) is None
    assert renamed_notice(august, roster_of(None, {})) is None
    assert renamed_notice(august, august) is None
    assert renamed_notice(august, roster_of("July 2021", {"9": "Z"})) is None


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
        "- never transferred: loan 1 (1 loan) (ABS-EE "
        f"{TAPE}: mostRecentSpecialServicerTransferDate, mostRecentMasterServicerReturnDate)",
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


@pytest.fixture
def months_apart(seeded):
    """August 2026 loaded and its comments extracted on 2026-09-16; September on 2026-10-01."""
    backfill(in_session, AUG, AUG, on=dt.datetime(2026, 9, 16, 9, 0, tzinfo=dt.UTC))
    backfill(in_session, SEP, SEP)
    clock.set_now(ASKED)
    return seeded


@needs_db
def test_a_brief_as_of_a_past_day_reads_that_day_s_month(months_apart):
    """Point in time: on 2026-09-20 only August 2026 was filed and extracted, so every fact of
    the section, the comment's, the appraisal reduction and the tape's (advances included),
    comes from the August filings, never September's."""
    found = in_session(lambda db: brief(db, "pat", deal=FIXTURE_DEAL, as_of=dt.date(2026, 9, 20)))
    [section] = found.sections
    august = {"0009900001-26-000416", AUGUST_TAPE}
    cited = [fact.citation for item in section.items for fact in item.stated if fact.citation]
    assert cited and {c.accession for c in cited} <= august, cited
    sources = {c.form for c in cited}
    assert sources == {"10-D", "ABS-EE"}, sources
    assert any(c.sentence is not None for c in cited)  # the comment's
    assert any(c.report_table is not None for c in cited)  # the appraisal reduction
    advances = next(i for i in section.items if i.heading == "Advances outstanding")
    assert all(AUGUST_TAPE in line for line in advances.not_reported), advances.not_reported
    assert all(item.answer.stated == [] or all(f.period == "August 2026" for f in item.stated)
               for item in section.items)  # fmt: skip
    text = "\n".join(section.lines())
    assert "0009900001-26-000418" not in text and TAPE not in text


@needs_db
def test_the_brief_reads_in_one_snapshot(panel):
    """The brief makes its session REPEATABLE READ before its first read, so the roster, facts
    and histories it composes agree even while a load commits."""

    def level(db):
        brief(db, "pat", deal=FIXTURE_DEAL)
        return db.connection().get_isolation_level()

    assert in_session(level) == "REPEATABLE READ"


@needs_db
def test_loans_the_tape_does_not_show_are_noted_and_last_known_ones_get_a_section(
    panel, monkeypatch
):
    """Loan 2 read as off the current tape (and a loan 99 the panel holds nothing of), and
    the previous tape's names all different: the brief says so and drops neither."""
    from cmbs.brief import service

    real = service.loans
    off = "not on the September 2026 tape; last reported August 2026, balance $30,000,000.00"

    def roster(db, reader, *, deal, as_of=None):
        found = real(db, reader, deal=deal, as_of=as_of)
        if found.period == "August 2026":
            renamed = [dataclasses.replace(r, property_name=f"Other {r.loan}") for r in found.loans]
            return dataclasses.replace(found, loans=renamed)
        rows = [dataclasses.replace(r, note=off) if r.loan == "2" else r for r in found.loans]
        ghost = dataclasses.replace(rows[0], loan="99", note=off)
        return dataclasses.replace(found, loans=[*rows, ghost])

    monkeypatch.setattr(service, "loans", roster)
    found = in_session(lambda db: brief(db, "pat", deal=FIXTURE_DEAL))
    assert found.sections == []
    [known] = found.last_known
    assert known.loan.loan == "2" and known.month.period == "September 2026"
    assert "99" in [row.loan for row in found.others]
    assert found.notices[0].startswith("The property names of ")
    assert found.notices[1] == (
        "2 loans are not on the September 2026 tape and were last known in special servicing."
    )
    assert "no loan is in special servicing" not in found.text()


@needs_db
def test_a_tape_that_shows_no_loan_drops_none(panel, monkeypatch):
    """Every loan read as off the current tape: no current tape to compare or cite, so the
    ones last known in special servicing stay among the others, counted in the header."""
    from cmbs.brief import service

    real = service.loans
    off = "not on the September 2026 tape; last reported August 2026, balance $1.00"

    def roster(db, reader, *, deal, as_of=None):
        found = real(db, reader, deal=deal, as_of=as_of)
        return dataclasses.replace(
            found, loans=[dataclasses.replace(r, note=off) for r in found.loans]
        )

    monkeypatch.setattr(service, "loans", roster)
    found = in_session(lambda db: brief(db, "pat", deal=FIXTURE_DEAL))
    assert (found.sections, found.last_known) == ([], [])
    assert "2" in [row.loan for row in found.others]
    assert found.notices == [
        "7 loans are not on the September 2026 tape, 1 of them last known in special servicing."
    ]
    assert "no loan is in special servicing" not in found.text()
    assert "and those last known there" in found.text().splitlines()[1]


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
