"""The checks service on a real database: point-in-time reads, flags with both sides, audit."""

import datetime as dt
from decimal import Decimal
from html import escape
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.access.service import PermissionRefused
from cmbs.audit.tables import AuditEntry
from cmbs.checks.tables import CheckFlag
from cmbs.crosscheck import rollforward as rf
from cmbs.crosscheck import rules
from cmbs.crosscheck import service as crosscheck_service
from cmbs.crosscheck.service import (
    NoTape,
    UnknownDeal,
    check_rollforward,
    check_text_vs_tape,
    flags,
    rollforward,
    run_checks,
    text_vs_tape,
)
from cmbs.distribution.service import load_report
from cmbs.domain import Period, money
from cmbs.extraction.questions import sentence_name, support_name, value_name
from cmbs.extraction.sentences import split
from cmbs.extraction.service import decide_review, extract_comments, open_review_items
from cmbs.model.client import ModelClient
from cmbs.model.recordings import DbRecordings
from cmbs.model.scripted import ScriptedProvider, choice_answer, respond
from cmbs.model.types import Choice, ModelRequest, NoulAnswer
from cmbs.panel.service import deal_named
from cmbs.review.service import decline, override
from cmbs.tape.service import load_tape
from tests.conftest import needs_db
from tests.fixtures.tape.tapes import asset, tape

pytestmark = needs_db

DEAL = "Checks Trust 2026-C1"
CIK = 9_900_101
SEPTEMBER = Period(2026, 9)
AUGUST = Period(2026, 8)
TEMPLATE = Path(__file__).parent / "fixtures" / "alerts" / "fixture_10d_template.htm"
COMMENT = (
    "The loan transferred to Special Servicing effective 11/15/2024 due to monetary default. "
    "As of 6/30/2026, DSCR NOI was 0.64x."
)


def at(day: dt.date, hour: int = 12) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(hour), dt.UTC)


def accession(period: Period, form: int) -> str:
    return f"{CIK:010d}-{period.year % 100:02d}-{period.month:04d}{form:02d}"


def filed(period: Period) -> dt.date:
    return dt.date(period.year, period.month, 29)


def load_month(db: Session, period: Period, *assets: str) -> None:
    clock.set_now(at(filed(period), 18))
    load_tape(
        db,
        deal=DEAL,
        content=tape(*assets),
        file_name=f"checks_{period.first_day:%Y-%m}.xml",
        accession=accession(period, 1),
        filing_date=filed(period),
        cik=CIK,
    )
    db.commit()


def end(period: Period) -> str:
    return f"{period.month:02d}-11-{period.year}"


def loan(number: str, period: Period, *, prop: dict[str, str] | None = None, **tags: str) -> str:
    return asset(
        number,
        prop if prop is not None else {"propertyName": f"Property {number}"},
        reportingPeriodEndDate=end(period),
        **tags,
    )


def load_comment(db: Session, period: Period, text: str, *, answer_p: float = 0.95) -> None:
    """A 10-D with loan 2's comment, extracted with the model answering the comment's figures."""
    rows = f"    <tr><td>2</td><td>300000002</td><td>{escape(text)}</td></tr>"
    content = TEMPLATE.read_text().format(
        deal=escape(DEAL),
        distribution_date=f"{period.month:02d}/17/{period.year % 100:02d}",
        determination_date=f"{period.month:02d}/11/{period.year % 100:02d}",
        rows=rows,
    )
    load_report(
        db,
        user="quinn",
        deal=DEAL,
        accession=accession(period, 2),
        cik=CIK,
        filing_date=filed(period),
        file_name=f"checks_{period.first_day:%Y-%m}.htm",
        content=content.encode(),
        period=period,
    )
    figures = {"transfer_date": "11/15/2024", "dscr": "0.64x", "dscr_as_of": "6/30/2026"}

    def answer(request: ModelRequest):
        sentences = split(text)
        answers = {}
        for name, printed in figures.items():
            value = request.questions[value_name(name)]
            cited = request.questions[sentence_name(name)]
            assert isinstance(value, Choice) and isinstance(cited, Choice)
            sentence = next(s.text for s in sentences if printed in s.text)
            # Only the transfer date's answer is unsettled when `answer_p` is.
            p = answer_p if name == "transfer_date" else 0.95
            answers[value_name(name)] = choice_answer(value, printed, p)
            answers[support_name(name)] = NoulAnswer(noul=0.95)
            answers[sentence_name(name)] = choice_answer(cited, sentence, 0.95)
        return respond(request, answers)

    clock.set_now(at(filed(period) + dt.timedelta(days=1), 9))
    client = ModelClient(
        DbRecordings(db), provider=ScriptedProvider(answer), replay_only=False, allow_synthetic=True
    )
    extract_comments(db, user="quinn", deal=DEAL, period=period, client=client)
    db.commit()


def deal_id(db: Session) -> int:
    return deal_named(db, DEAL).id


def two_months(db: Session) -> None:
    """August and September: loan 1 rolls, loan 7 breaks by $10,000.00, loan 9 reports only an
    actual ending balance, loan 10 reports a realized loss; loan 1's beginning matches August's
    scheduled ending, loan 7's does not."""
    load_month(
        db,
        AUGUST,
        loan("1", AUGUST, reportPeriodEndScheduledLoanBalanceAmount="50100000.00"),
        loan(
            "7",
            AUGUST,
            reportPeriodEndActualBalanceAmount="9050000.00",
            reportPeriodEndScheduledLoanBalanceAmount="9060000.00",
        ),
    )
    load_month(
        db,
        SEPTEMBER,
        loan(
            "1",
            SEPTEMBER,
            reportPeriodBeginningScheduleLoanBalanceAmount="50100000.00",
            scheduledPrincipalAmount="100000.00",
            reportPeriodEndActualBalanceAmount="50100000.00",
            reportPeriodEndScheduledLoanBalanceAmount="50000000.00",
        ),
        loan(
            "7",
            SEPTEMBER,
            reportPeriodBeginningScheduleLoanBalanceAmount="9050000.00",
            scheduledPrincipalAmount="40000.00",
            unscheduledPrincipalCollectedAmount="0.00",
            otherPrincipalAdjustmentAmount="0.00",
            realizedLossToTrustAmount="0.00",
            reportPeriodEndActualBalanceAmount="9010000.00",
            reportPeriodEndScheduledLoanBalanceAmount="9000000.00",
        ),
        loan(
            "9",
            SEPTEMBER,
            reportPeriodBeginningScheduleLoanBalanceAmount="1000.00",
            reportPeriodEndActualBalanceAmount="1000.00",
        ),
        loan(
            "10",
            SEPTEMBER,
            reportPeriodBeginningScheduleLoanBalanceAmount="5000000.00",
            scheduledPrincipalAmount="0.00",
            unscheduledPrincipalCollectedAmount="3000000.00",
            otherPrincipalAdjustmentAmount="0.00",
            realizedLossToTrustAmount="2150000.00",
            reportPeriodEndScheduledLoanBalanceAmount="0.00",
        ),
    )


def test_rollforward_reads_each_loan_point_in_time(seeded):
    two_months(seeded)
    results = rollforward(seeded, deal_id=deal_id(seeded), period=SEPTEMBER, as_of=filed(SEPTEMBER))
    by_loan = {result.loan: result for result in results}
    assert list(by_loan) == ["1", "7", "9", "10"]

    one = by_loan["1"]
    # Delinquent: the actual balance did not fall, the scheduled one did. The scheduled is used.
    assert one.balances.ending == rf.Ending(money("50,000,000.00"))
    assert one.roll == rf.Roll(
        rf.Status.PASSED,
        expected_ending=money("50,000,000.00"),
        gap=money("0.00"),
        not_reported=("unscheduled principal", "other principal adjustments"),
    )
    assert one.continuity == rf.Continuity(
        rf.Status.PASSED, previous_ending=money("50,100,000.00"), gap=money("0.00")
    )
    assert (one.accession, one.filed, one.previous_accession) == (
        accession(SEPTEMBER, 1),
        filed(SEPTEMBER),
        accession(AUGUST, 1),
    )
    assert one.label == "roll-forward passed"

    seven = by_loan["7"]
    # The actual balances (9,050,000 then 9,010,000) would roll; the scheduled ones do not.
    assert seven.balances.ending == rf.Ending(money("9,000,000.00"))
    assert (seven.roll.status, seven.roll.gap) == (rf.Status.BREAK, money("10,000.00"))
    assert (seven.continuity.status, seven.continuity.gap) == (rf.Status.BREAK, money("10,000.00"))
    assert seven.continuity.previous_ending == money("9,060,000.00")

    nine = by_loan["9"]
    # Only the actual ending balance: no fallback to it.
    assert nine.roll == rf.Roll(
        rf.Status.NOT_CHECKED, reason="scheduled ending balance not reported"
    )
    assert nine.continuity.reason == "no August 2026 tape row for the loan"
    assert nine.status is rf.Status.NOT_CHECKED

    ten = by_loan["10"]
    assert ten.roll == rf.Roll(rf.Status.NOT_CHECKED, reason=rf.REALIZED_LOSS_MONTH)


def test_continuity_needs_last_months_scheduled_ending(seeded):
    load_month(seeded, AUGUST, loan("7", AUGUST, reportPeriodEndActualBalanceAmount="9050000.00"))
    load_month(
        seeded,
        SEPTEMBER,
        loan(
            "7",
            SEPTEMBER,
            reportPeriodBeginningScheduleLoanBalanceAmount="9050000.00",
            reportPeriodEndScheduledLoanBalanceAmount="9050000.00",
        ),
    )
    (seven,) = rollforward(
        seeded, deal_id=deal_id(seeded), period=SEPTEMBER, as_of=filed(SEPTEMBER)
    )
    assert seven.continuity == rf.Continuity(
        rf.Status.NOT_CHECKED, reason="August 2026 scheduled ending balance not reported"
    )


def test_rollforward_before_the_tape_was_filed_is_empty(seeded):
    two_months(seeded)
    day_before = filed(SEPTEMBER) - dt.timedelta(days=1)
    assert rollforward(seeded, deal_id=deal_id(seeded), period=SEPTEMBER, as_of=day_before) == []


def test_rollforward_defaults_to_today(seeded):
    two_months(seeded)
    clock.set_now(at(filed(SEPTEMBER) - dt.timedelta(days=1)))
    assert rollforward(seeded, deal_id=deal_id(seeded), period=SEPTEMBER) == []
    clock.set_now(at(filed(SEPTEMBER)))
    assert len(rollforward(seeded, deal_id=deal_id(seeded), period=SEPTEMBER)) == 4


def test_check_rollforward_flags_breaks_with_both_sides_and_audits_them(seeded):
    two_months(seeded)
    clock.set_now(at(dt.date(2026, 10, 1)))
    summary = check_rollforward(seeded, user="quinn", deal=DEAL, period=SEPTEMBER, run_id=None)
    seeded.commit()
    assert (summary.rollforward_breaks, summary.rollforward_passed) == (1, 1)
    assert [(f.loan, f.check, f.message, f.new) for f in summary.flags] == [
        ("7", rf.ROLLFORWARD, rf.ROLL_BREAK, True),
        ("7", rf.CONTINUITY, rf.CONTINUITY_BREAK, True),
    ]
    stored = seeded.scalars(select(CheckFlag).order_by(CheckFlag.id)).all()
    assert [(f.loan, f.period, f.check) for f in stored] == [
        ("7", SEPTEMBER.first_day, rf.ROLLFORWARD),
        ("7", SEPTEMBER.first_day, rf.CONTINUITY),
    ]
    assert stored[0].sides["gap"] == "$10,000.00"
    assert stored[0].sides["reported scheduled ending balance"] == "$9,000,000.00"
    assert stored[0].sides["explained ending balance"] == "$9,010,000.00"
    assert stored[1].sides["August 2026 scheduled ending balance"] == "$9,060,000.00"
    entries = seeded.scalars(
        select(AuditEntry).where(AuditEntry.action == "raise check flag").order_by(AuditEntry.id)
    ).all()
    assert [(e.user_name, e.role, e.subject_key, e.field, e.after) for e in entries] == [
        ("quinn", "quant", f"{DEAL}/7", rf.ROLLFORWARD, rf.ROLL_BREAK),
        ("quinn", "quant", f"{DEAL}/7", rf.CONTINUITY, rf.CONTINUITY_BREAK),
    ]
    assert entries[0].detail == {"period": "2026-09-01", "sides": stored[0].sides}


def test_running_the_checks_again_raises_nothing_new(seeded):
    two_months(seeded)
    clock.set_now(at(dt.date(2026, 10, 1)))
    check_rollforward(seeded, user="quinn", deal=DEAL, period=SEPTEMBER)
    seeded.commit()
    again = check_rollforward(seeded, user="olly", deal=DEAL, period=SEPTEMBER)
    seeded.commit()
    assert [f.new for f in again.flags] == [False, False]
    assert len(seeded.scalars(select(CheckFlag)).all()) == 2


def test_an_automated_check_names_its_run(seeded):
    from cmbs.runs.service import start_run

    two_months(seeded)
    clock.set_now(at(dt.date(2026, 10, 1)))
    run = start_run(seeded, kind="refresh", started_by="quinn")
    check_rollforward(seeded, user="quinn", deal=DEAL, period=SEPTEMBER, run_id=run.id)
    seeded.commit()
    runs = seeded.scalars(
        select(AuditEntry.run_id).where(AuditEntry.action == "raise check flag")
    ).all()
    assert runs == [run.id, run.id]


def test_a_pm_may_not_run_checks(seeded):
    two_months(seeded)
    with pytest.raises(PermissionRefused, match="^pm may not run refreshes and backfills$"):
        check_rollforward(seeded, user="pat", deal=DEAL, period=SEPTEMBER)


def test_an_unknown_deal_is_refused_and_audited(seeded):
    with pytest.raises(UnknownDeal, match="^no deal named 'Nope'$"):
        run_checks(seeded, user="quinn", deal="Nope", period=SEPTEMBER)
    seeded.rollback()
    refusal = seeded.scalars(select(AuditEntry).where(AuditEntry.outcome == "refused")).one()
    assert (refusal.action, refusal.subject, refusal.subject_key) == ("run checks", "deal", "Nope")


def test_a_month_with_no_tape_yet_is_refused(seeded):
    two_months(seeded)
    clock.set_now(at(dt.date(2026, 9, 28)))
    with pytest.raises(
        NoTape,
        match="^no September 2026 loan tape of Checks Trust 2026-C1 had been filed by 2026-09-28$",
    ):
        check_text_vs_tape(seeded, user="quinn", deal=DEAL, period=SEPTEMBER)


def test_text_vs_tape_compares_accepted_values_against_the_tape(seeded):
    load_month(
        seeded,
        SEPTEMBER,
        loan(
            "2",
            SEPTEMBER,
            prop={"propertyName": "Hotel", "mostRecentFinancialsEndDate": "12-31-2024"},
            mostRecentSpecialServicerTransferDate="03-06-2025",
        ),
    )
    load_comment(seeded, SEPTEMBER, COMMENT)
    clock.set_now(at(dt.date(2026, 10, 1)))
    summary = run_checks(seeded, user="quinn", deal=DEAL, period=SEPTEMBER)
    seeded.commit()
    (result,) = summary.text_vs_tape
    assert [(f.check, f.status, f.message) for f in result.findings] == [
        (rules.TRANSFER_DATE, rules.Status.FLAG, "transfer dates differ by more than two periods"),
        (rules.NEWER_FINANCIALS, rules.Status.NOTE, rules.NEWER_THAN_TAPE),
    ]
    transfer = result.findings[0].sides
    assert transfer["text"] == {
        "source": "10-D comment",
        "transfer_date": "2024-11-15",
        "sentence": 1,
        "accession": accession(SEPTEMBER, 2),
        "filed": "2026-09-29",
        "as_of": "2026-09-17",
    }
    assert transfer["tape"]["transfer_date"] == "2025-03-06"
    views = flags(seeded, reader="pat", deal=DEAL, period=SEPTEMBER, loan="2")
    assert [(v.check, v.message, v.is_note) for v in views] == [
        (rules.TRANSFER_DATE, "transfer dates differ by more than two periods", False),
        (rules.NEWER_FINANCIALS, rules.NEWER_THAN_TAPE, True),
    ]
    assert views[0].sides == transfer
    assert (views[0].deal, views[0].period) == (DEAL, SEPTEMBER)


def test_a_value_awaiting_review_is_not_compared_until_decided(seeded):
    load_month(
        seeded,
        SEPTEMBER,
        loan("2", SEPTEMBER, mostRecentSpecialServicerTransferDate="03-06-2025"),
    )
    load_comment(seeded, SEPTEMBER, COMMENT, answer_p=0.55)
    day = dt.date(2026, 10, 1)
    clock.set_now(at(day))
    (result,) = text_vs_tape(
        seeded, reader="pat", deal_id=deal_id(seeded), period=SEPTEMBER, as_of=day
    )
    assert [f.check for f in result.findings] == [rules.NEWER_FINANCIALS]

    (item,) = [i for i in open_review_items(seeded, "alex") if i.field == "transfer_date"]
    clock.set_now(at(dt.date(2026, 10, 2)))
    decide_review(seeded, "alex", item.id, value="11/15/2024", sentence=1)
    seeded.commit()
    # Point in time: the decision is visible from the day it was made.
    (before,) = text_vs_tape(
        seeded, reader="pat", deal_id=deal_id(seeded), period=SEPTEMBER, as_of=day
    )
    assert [f.check for f in before.findings] == [rules.NEWER_FINANCIALS]
    (after,) = text_vs_tape(
        seeded, reader="pat", deal_id=deal_id(seeded), period=SEPTEMBER, as_of=dt.date(2026, 10, 2)
    )
    assert [(f.check, f.status) for f in after.findings] == [
        (rules.TRANSFER_DATE, rules.Status.FLAG),
        (rules.NEWER_FINANCIALS, rules.Status.NOTE),
    ]


def test_a_comment_for_a_loan_not_on_the_tape_is_skipped(seeded):
    load_month(seeded, SEPTEMBER, loan("1", SEPTEMBER))
    load_comment(seeded, SEPTEMBER, COMMENT)
    day = dt.date(2026, 10, 1)
    (result,) = text_vs_tape(
        seeded, reader="pat", deal_id=deal_id(seeded), period=SEPTEMBER, as_of=day
    )
    assert result.findings == []
    assert result.skipped == "loan 2 is not on the September 2026 tape as of 2026-10-01"


def test_comments_are_not_read_before_their_10d_was_filed(seeded):
    load_month(seeded, SEPTEMBER, loan("2", SEPTEMBER))
    load_comment(seeded, SEPTEMBER, COMMENT)
    on_filing = filed(SEPTEMBER)
    # Extracted the day after filing: on the filing day itself nothing was extracted yet.
    assert (
        text_vs_tape(
            seeded, reader="pat", deal_id=deal_id(seeded), period=SEPTEMBER, as_of=on_filing
        )
        == []
    )


def test_flags_need_a_known_deal_and_the_right_to_read(seeded):
    with pytest.raises(UnknownDeal):
        flags(seeded, reader="pat", deal="Nope")
    with pytest.raises(PermissionRefused, match="^operator may not read the panel$"):
        flags(seeded, reader="olly", deal="Nope")


def test_flags_filter_by_month_and_loan(seeded):
    two_months(seeded)
    clock.set_now(at(dt.date(2026, 10, 1)))
    check_rollforward(seeded, user="quinn", deal=DEAL, period=SEPTEMBER)
    seeded.commit()
    assert len(flags(seeded, reader="rae", deal=DEAL)) == 2
    assert flags(seeded, reader="rae", deal=DEAL, period=AUGUST) == []
    assert flags(seeded, reader="rae", deal=DEAL, loan="1") == []
    assert [v.loan for v in flags(seeded, reader="rae", deal=DEAL, loan="7")] == ["7", "7"]


def test_decimal_gap_is_exact_to_the_cent(seeded):
    load_month(
        seeded,
        SEPTEMBER,
        loan(
            "3",
            SEPTEMBER,
            reportPeriodBeginningScheduleLoanBalanceAmount="100.00",
            scheduledPrincipalAmount="0.01",
            reportPeriodEndScheduledLoanBalanceAmount="100.00",
        ),
    )
    (result,) = rollforward(
        seeded, deal_id=deal_id(seeded), period=SEPTEMBER, as_of=filed(SEPTEMBER)
    )
    assert (result.roll.status, result.roll.gap) == (rf.Status.BREAK, Decimal("-0.01"))


def _transfer_checks(db: Session, day: dt.date) -> list[tuple[str, rules.Status]]:
    (result,) = text_vs_tape(db, reader="pat", deal_id=deal_id(db), period=SEPTEMBER, as_of=day)
    return [(f.check, f.status) for f in result.findings]


def test_an_overridden_transfer_date_is_the_one_compared(seeded):
    load_month(
        seeded,
        SEPTEMBER,
        loan("2", SEPTEMBER, mostRecentSpecialServicerTransferDate="03-06-2025"),
    )
    load_comment(seeded, SEPTEMBER, COMMENT)
    day = dt.date(2026, 10, 1)
    # Auto-accepted 2024-11-15 against the tape's 2025-03-06: four months apart.
    assert _transfer_checks(seeded, day)[0] == (rules.TRANSFER_DATE, rules.Status.FLAG)
    clock.set_now(at(dt.date(2026, 10, 2)))
    override(
        seeded,
        "alex",
        deal=DEAL,
        loan="2",
        period=SEPTEMBER,
        field="transfer_date",
        value="2025-03-06",
        sentence=1,
        reason="the servicer's own transfer notice gives 3/6/2025",
    )
    seeded.commit()
    # Point in time: before the override the extracted date stands; from it, the override.
    assert _transfer_checks(seeded, day)[0] == (rules.TRANSFER_DATE, rules.Status.FLAG)
    (result,) = text_vs_tape(
        seeded, reader="pat", deal_id=deal_id(seeded), period=SEPTEMBER, as_of=dt.date(2026, 10, 2)
    )
    transfer = result.findings[0]
    assert (transfer.check, transfer.status) == (rules.TRANSFER_DATE, rules.Status.AGREE)
    assert transfer.sides["text"]["transfer_date"] == "2025-03-06"


def test_a_declined_field_is_not_compared(seeded):
    load_month(
        seeded,
        SEPTEMBER,
        loan("2", SEPTEMBER, mostRecentSpecialServicerTransferDate="03-06-2025"),
    )
    load_comment(seeded, SEPTEMBER, COMMENT, answer_p=0.55)
    (item,) = [i for i in open_review_items(seeded, "alex") if i.field == "transfer_date"]
    clock.set_now(at(dt.date(2026, 10, 2)))
    shown = decline(seeded, "alex", item.id, reason="the comment gives two transfer dates")
    seeded.commit()
    assert (shown.status, shown.value) == ("not determinable", None)
    assert _transfer_checks(seeded, dt.date(2026, 10, 2)) == [
        (rules.NEWER_FINANCIALS, rules.Status.NOTE)
    ]


def test_compared_statuses():
    assert frozenset({"auto-accepted", "human-reviewed", "overridden"}) == (
        crosscheck_service.COMPARED
    )


def test_reading_text_versus_tape_needs_the_right_to_read_the_panel(seeded):
    two_months(seeded)
    with pytest.raises(PermissionRefused, match="^operator may not read the panel$"):
        text_vs_tape(seeded, reader="olly", deal_id=deal_id(seeded), period=SEPTEMBER)
