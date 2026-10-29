"""The dashboard's pure pieces: months, links, what verified a value, and the rows it shows."""

import datetime as dt
from decimal import Decimal

import pytest

from cmbs.alerts.lifecycle import State
from cmbs.alerts.rules import Severity
from cmbs.alerts.service import AlertView, CoveredLoan
from cmbs.crosscheck import rollforward as rf
from cmbs.dashboard import service
from cmbs.dashboard.service import (
    CONTRACT_PASSED,
    EVIDENCE_CHECKED,
    NOT_IN_AUDIT_TRAIL,
    ROLLFORWARD_NOT_CHECKED,
    BadMonth,
    BookRow,
    Check,
    LoanRef,
    MonthRef,
    TapeValue,
)
from cmbs.domain import Period
from cmbs.errors import Refused
from cmbs.extraction.service import FieldView
from cmbs.tape.tables import LoanPeriod

F1, F2 = "Fixture Trust 2026-F1", "Fixture Trust 2026-F2"
AT = dt.datetime(2026, 10, 28, 9, 0, tzinfo=dt.UTC)


# ---------------------------------------------------------------------------- months


def test_a_month_is_linked_by_its_key_and_shown_by_its_label():
    assert service.month_ref(Period(2026, 9)) == MonthRef("2026-09", "September 2026")
    assert service.month_ref(Period(987, 1)) == MonthRef("0987-01", "January 987")


@pytest.mark.parametrize(
    ("text", "period"),
    [
        ("2026-09", Period(2026, 9)),
        ("2026-01", Period(2026, 1)),
        ("2026-12", Period(2026, 12)),
        (" 2026-10 ", Period(2026, 10)),
    ],
)
def test_parse_month_reads_year_and_month(text, period):
    assert service.parse_month(text) == period


@pytest.mark.parametrize(
    "text", ["2026-00", "2026-13", "2026-9", "26-09", "September 2026", "2026-09-01", "x2026-09"]
)
def test_parse_month_refuses_anything_else(text):
    with pytest.raises(BadMonth) as refused:
        service.parse_month(text)
    assert str(refused.value) == f"not a month like 2026-09: {text!r}"
    assert isinstance(refused.value, Refused)


def test_the_default_month_is_the_latest_built_or_else_last_month():
    today = dt.date(2026, 10, 28)
    assert service.default_month(Period(2026, 8), today) == Period(2026, 8)
    assert service.default_month(None, today) == Period(2026, 9)
    assert service.default_month(None, dt.date(2026, 1, 3)) == Period(2025, 12)


# ---------------------------------------------------------------------------- the book


def covered(deal: str, loan: str, exposure: str, balance: str | None = "9000000.00") -> CoveredLoan:
    return CoveredLoan(
        deal=deal,
        loan=loan,
        accession="0009900001-26-000419",
        balance=None if balance is None else Decimal(balance),
        exposure=Decimal(exposure),
        classes=("E", "F") if deal == F1 else ("D",),
    )


def alert(*loans: CoveredLoan, state=State.NEW, state_text="new", note=None) -> AlertView:
    return AlertView(
        id=12,
        period=Period(2026, 9),
        severity=Severity.HIGH,
        title="transferred to special servicing",
        message="transferred to special servicing on 2026-09-02",
        added=(),
        settings_version=7,
        as_of=dt.date(2026, 9, 29),
        raised_at=AT,
        state=state,
        state_text=state_text,
        note=note,
        assigned_to=None,
        loans=loans,
    )


def test_a_book_row_carries_the_alert_as_the_alerts_service_reads_it():
    row = service.book_row(
        alert(
            covered(F1, "7", "25000000.00"),
            state=State.ASSIGNED,
            state_text="assigned to alex",
            note="call",
        )
    )
    assert row == BookRow(
        alert_id=12,
        severity="high",
        title="transferred to special servicing",
        message="transferred to special servicing on 2026-09-02",
        loans=(LoanRef(F1, "7"),),
        exposure=Decimal("25000000.00"),
        balance=Decimal("9000000.00"),
        classes=("E", "F"),
        state="assigned",
        state_text="assigned to alex",
        note="call",
    )


def test_a_whole_loan_row_lists_each_deal_and_sums_exposure_and_balance():
    row = service.book_row(
        alert(covered(F1, "7", "25000000.00"), covered(F2, "3", "40000000.00", "8000000.00"))
    )
    assert row.loans == (LoanRef(F1, "7"), LoanRef(F2, "3"))
    assert row.exposure == Decimal("65000000.00")
    assert row.balance == Decimal("17000000.00")
    assert row.classes == ("E", "F", "D")


def test_a_loan_whose_balance_is_not_reported_has_no_balance():
    assert service.book_row(alert(covered(F1, "7", "25000000.00", None))).balance is None


def test_an_empty_book_says_whether_the_month_was_built():
    october = Period(2026, 10)
    assert service.book_note(october, built=True, empty=True) == (
        "no open alerts on the book for October 2026"
    )
    assert service.book_note(october, built=False, empty=True) == (
        "the October 2026 alerts have not been built"
    )
    assert service.book_note(october, built=True, empty=False) is None
    assert service.book_note(october, built=False, empty=False) is None


# ---------------------------------------------------------------------------- links and loads


def test_an_edgar_link_opens_the_primary_document_or_else_the_filing_index():
    assert service.edgar_url(9900001, "0009900001-26-000418", "f1-10d.htm") == (
        "https://www.sec.gov/Archives/edgar/data/9900001/000990000126000418/f1-10d.htm"
    )
    assert service.edgar_url(9900001, "0009900001-26-000418", None) == (
        "https://www.sec.gov/Archives/edgar/data/9900001/000990000126000418/"
        "0009900001-26-000418-index.htm"
    )
    assert service.edgar_url(9900001, "0009900001-26-000418", "") == (
        "https://www.sec.gov/Archives/edgar/data/9900001/000990000126000418/"
        "0009900001-26-000418-index.htm"
    )


def test_what_loaded_a_value_names_the_run_or_the_user():
    assert service.loaded_text(43, "olly", AT, "loaded") == "run 43"
    assert service.loaded_text(None, "quinn", AT, "extracted") == "extracted by quinn outside a run"
    assert service.loaded_text(None, None, None, "loaded") == NOT_IN_AUDIT_TRAIL
    assert service.loaded_text(43, "olly", None, "loaded") == "load not in the audit trail"
    made = service.loaded(None, "quinn", AT)
    assert (made.run, made.by, made.at, made.text) == (
        None,
        "quinn",
        AT,
        "loaded by quinn outside a run",
    )


# ---------------------------------------------------------------------------- what verified a value


def test_the_checks_have_fixed_words_and_outcomes():
    assert Check("contract passed", "passed") == CONTRACT_PASSED
    assert (
        Check("scheduled balance roll-forward not checked", "not checked")
        == ROLLFORWARD_NOT_CHECKED
    )
    assert Check("evidence checked", "passed") == EVIDENCE_CHECKED
    assert (service.PASSED, service.FAILED, service.NOT_CHECKED, service.DECIDED) == (
        "passed",
        "failed",
        "not checked",
        "decided",
    )


SENTENCES = ("One.", "Two.", "Three.")


def test_an_accepted_value_is_verified_by_its_status_and_its_sentence():
    assert service.field_checks("maturity extension", "auto-accepted", 3, SENTENCES) == (
        Check("auto-accepted", "passed"),
        Check("evidence checked", "passed"),
    )


def test_a_value_a_person_settled_is_a_decision_not_a_passed_check():
    assert service.field_checks("pending", "human-reviewed", 1, SENTENCES) == (
        Check("human-reviewed", "decided"),
        Check("evidence checked", "passed"),
    )
    assert service.field_checks("modification", "overridden", 3, SENTENCES) == (
        Check("overridden", "decided"),
        Check("evidence checked", "passed"),
    )


@pytest.mark.parametrize("sentence", [None, 0, 4, -1])
def test_evidence_is_checked_only_when_the_sentence_is_in_the_comment(sentence):
    assert service.field_checks("x", "auto-accepted", sentence, SENTENCES) == (
        Check("auto-accepted", "passed"),
    )


def test_a_field_without_a_value_has_nothing_verified():
    assert service.field_checks(None, "needs review", 2, SENTENCES) == ()
    assert service.field_checks(None, "not stated", None, SENTENCES) == ()
    assert service.field_checks(None, "not determinable", None, SENTENCES) == ()


ON = dt.datetime(2026, 10, 2, 9, 30, tzinfo=dt.UTC)


def field(**changes) -> FieldView:
    values = {
        "field": "workout_strategy",
        "label": "workout strategy",
        "status": "auto-accepted",
        "value": "modification",
        "display": "modification",
        "sentence": 3,
        "evidence": "Three.",
        "model_value": "foreclosure",
        "answer_probability": Decimal("0.55"),
        "support_probability": Decimal("0.92"),
        "review_reason": None,
        "decided_by": None,
        "decided_at": None,
    }
    return FieldView(**{**values, **changes})


def test_an_override_names_who_when_why_and_what_the_model_read():
    view = field(
        status="overridden",
        overridden_by="alex",
        overridden_at=ON,
        override_reason="the special servicer's October letter",
    )
    shown = service.comment_value(view, SENTENCES)
    assert shown.decision == (
        "overridden by alex on 2026-10-02: the special servicer's October letter; "
        'the model read "foreclosure"'
    )
    assert (shown.overridden_by, shown.overridden_at, shown.override_reason) == (
        "alex",
        ON,
        "the special servicer's October letter",
    )
    assert (shown.model_value, shown.value) == ("foreclosure", "modification")
    assert shown.checks == (Check("overridden", "decided"), Check("evidence checked", "passed"))
    no_answer = service.decision_text(
        field(model_value=None, overridden_by="alex", overridden_at=None, override_reason="x"),
        None,
    )
    assert no_answer == "overridden by alex on an unknown day: x; the model gave no value"


def test_a_review_decision_names_who_and_when():
    view = field(status="human-reviewed", decided_by="alex", decided_at=ON)
    shown = service.comment_value(view, SENTENCES, "ignored: not declined")
    assert shown.decision == "human-reviewed by alex on 2026-10-02"
    assert (shown.decided_by, shown.decided_at, shown.declined_reason) == ("alex", ON, None)


def test_a_declined_field_says_not_determinable_with_who_and_why():
    view = field(
        status="not determinable",
        value=None,
        display=None,
        sentence=None,
        evidence=None,
        decided_by="alex",
        decided_at=ON,
    )
    shown = service.comment_value(view, SENTENCES, "the comment names no strategy yet")
    assert shown.decision == (
        "declined as not determinable by alex on 2026-10-02: the comment names no strategy yet"
    )
    assert (shown.value, shown.checks, shown.declined_reason) == (
        None,
        (),
        "the comment names no strategy yet",
    )
    assert service.decision_text(view, None) == (
        "declined as not determinable by alex on 2026-10-02"
    )


def test_a_value_nobody_settled_by_hand_has_no_decision():
    shown = service.comment_value(field(), SENTENCES)
    assert (shown.decision, shown.decided_by, shown.overridden_by) == (None, None, None)
    assert (
        service.comment_value(
            field(status="needs review", value=None, display=None), SENTENCES
        ).decision
        is None
    )


def loan_row(**values) -> LoanPeriod:
    fields = {
        "loan": "2",
        "reporting_period_end": dt.date(2026, 9, 11),
        "filing_date": dt.date(2026, 9, 29),
        "ending_balance": Decimal("30000000.00"),
        "beginning_balance": None,
        "payment_status": "3",
        "paid_through_date": None,
        "special_servicer_transfer_date": dt.date(2025, 3, 6),
        "workout_strategy": "11",
        "dscr": Decimal("0.7100"),
        "property_name": "Lakeside Hotel Portfolio",
        "property_type": "LO",
        "defeased_status": None,
        "financials_start": dt.date(2024, 1, 1),
        "financials_end": dt.date(2024, 12, 31),
    }
    return LoanPeriod(**{**fields, **values})


def test_tape_values_are_sent_as_text_with_their_kind_and_checks():
    rolled = Check("roll-forward passed", "passed")
    values = service.tape_values(loan_row(), rolled)
    contract, balance = (CONTRACT_PASSED,), (CONTRACT_PASSED, rolled)
    assert values == (
        TapeValue("ending_balance", "ending balance", "money", "30000000.00", balance),
        TapeValue("beginning_balance", "beginning scheduled balance", "money", None, balance),
        TapeValue("payment_status", "payment status", "text", "90+ days", contract),
        TapeValue("paid_through_date", "paid-through date", "date", None, contract),
        TapeValue(
            "special_servicer_transfer_date",
            "special-servicer transfer date",
            "date",
            "2025-03-06",
            contract,
        ),
        TapeValue("workout_strategy", "workout strategy", "text", "full payoff", contract),
        TapeValue("dscr", "DSCR", "ratio", "0.7100", contract),
    )


def test_tape_values_not_reported_are_none_never_zero():
    row = loan_row(
        ending_balance=None,
        beginning_balance=Decimal("0"),
        payment_status=None,
        paid_through_date=dt.date(2026, 7, 1),
        special_servicer_transfer_date=None,
        workout_strategy=None,
        dscr=None,
    )
    shown = {
        value.field: value.value for value in service.tape_values(row, ROLLFORWARD_NOT_CHECKED)
    }
    assert shown == {
        "ending_balance": None,
        "beginning_balance": "0.00",
        "payment_status": None,
        "paid_through_date": "2026-07-01",
        "special_servicer_transfer_date": None,
        "workout_strategy": None,
        "dscr": None,
    }


def test_a_code_not_on_the_schedule_al_list_is_named_as_a_code():
    values = service.tape_values(
        loan_row(payment_status="Z", workout_strategy="42"), CONTRACT_PASSED
    )
    shown = {value.field: value.value for value in values}
    assert (shown["payment_status"], shown["workout_strategy"]) == ("code Z", "code 42")


def rolled(roll: rf.Roll, continuity: rf.Continuity) -> rf.LoanRollForward:
    return rf.LoanRollForward(
        loan="2",
        period=Period(2026, 9),
        accession="0009900001-26-000419",
        filed=dt.date(2026, 9, 29),
        balances=rf.Balances(
            beginning=Decimal("30000000.00"),
            scheduled_principal=Decimal("0.00"),
            unscheduled_principal=Decimal("0.00"),
            other_principal_adjustments=Decimal("0.00"),
            realized_loss=None,
            ending=rf.Ending(Decimal("30000000.00")),
        ),
        roll=roll,
        continuity=continuity,
    )


PASS = rf.Status.PASSED
UNCHECKED = rf.Status.NOT_CHECKED


def test_a_roll_forward_that_passes_is_marked_passed():
    found = rolled(rf.Roll(PASS), rf.Continuity(PASS))
    assert service.rollforward_verdict(found) == Check(
        "scheduled balance roll-forward passed", "passed"
    )
    # The month's own roll passing is a pass even when last month cannot be compared.
    first = rolled(rf.Roll(PASS), rf.Continuity(UNCHECKED, reason="no August 2026 tape row"))
    assert service.rollforward_verdict(first).outcome == "passed"


@pytest.mark.parametrize(
    ("roll", "continuity"),
    [
        (rf.Roll(rf.Status.BREAK, gap=Decimal("1.00")), rf.Continuity(PASS)),
        (rf.Roll(PASS), rf.Continuity(rf.Status.BREAK, gap=Decimal("5.00"))),
    ],
)
def test_a_break_in_the_roll_or_its_tie_to_last_month_is_marked_broken(roll, continuity):
    assert service.rollforward_verdict(rolled(roll, continuity)) == Check(
        "scheduled balance roll-forward broken", "failed"
    )


def test_a_roll_forward_that_could_not_run_says_why():
    loss = rolled(rf.Roll(UNCHECKED, reason=rf.REALIZED_LOSS_MONTH), rf.Continuity(PASS))
    assert service.rollforward_verdict(loss) == Check(
        "scheduled balance roll-forward not checked: "
        "a realized-loss month: the loss includes more than principal",
        "not checked",
    )
    assert service.rollforward_verdict(None) == ROLLFORWARD_NOT_CHECKED


def test_financials_show_their_end_and_age():
    found = service.financials(loan_row())
    assert (found.start, found.end) == (dt.date(2024, 1, 1), dt.date(2024, 12, 31))
    assert (found.days_at_period_end, found.days_at_filing) == (619, 637)
    assert found.status == "619 days old at period end, 637 days old at filing"


def test_financials_ending_after_the_period_end_are_a_data_problem_not_a_negative_age():
    late = service.financials(loan_row(financials_end=dt.date(2026, 12, 31)))
    assert (late.end, late.days_at_period_end, late.days_at_filing) == (
        dt.date(2026, 12, 31),
        None,
        None,
    )
    assert late.status == "financials end 2026-12-31, after the period end"
    same_day = service.financials(loan_row(financials_end=dt.date(2026, 9, 11)))
    assert (same_day.days_at_period_end, same_day.status) == (
        0,
        "0 days old at period end, 18 days old at filing",
    )


def test_financials_of_an_excluded_or_unreported_loan_have_no_age():
    coop = service.financials(loan_row(property_type="CH"))
    assert (coop.days_at_period_end, coop.status) == (None, "excluded: co-op")
    missing = service.financials(loan_row(financials_start=None, financials_end=None))
    assert (missing.end, missing.days_at_period_end, missing.status) == (
        None,
        None,
        "no updated financials",
    )


def test_an_alerts_state_on_a_day_is_its_last_change_by_then():
    assert service.state_on([]) == "new"
    assert service.state_on(["acknowledged by pat on 2026-10-01"]) == (
        "acknowledged by pat on 2026-10-01"
    )
    assert service.state_on(["acknowledged by pat on 2026-10-01", "closed by alex"]) == (
        "closed by alex"
    )


def test_a_missing_comment_says_which_month():
    assert (
        service.comment_note(Period(2026, 9)) == "no servicer comment extracted for September 2026"
    )
