"""The dashboard's pure pieces: months, links, what verified a value, and the rows it shows."""

import datetime as dt
from decimal import Decimal

import pytest

from cmbs.alerts.lifecycle import State
from cmbs.alerts.rules import Severity
from cmbs.alerts.service import AlertView, CoveredLoan
from cmbs.dashboard import service
from cmbs.dashboard.service import (
    CONTRACT_PASSED,
    EVIDENCE_CHECKED,
    NOT_IN_AUDIT_TRAIL,
    ROLLFORWARD_NOT_CHECKED,
    BadMonth,
    BookRow,
    Check,
    LoanAlert,
    LoanRef,
    MonthRef,
    TapeValue,
)
from cmbs.domain import Period
from cmbs.errors import Refused
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


def test_an_empty_book_says_so():
    assert (
        service.book_note(Period(2026, 10), True) == "no open alerts on the book for October 2026"
    )
    assert service.book_note(Period(2026, 10), False) is None


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
    assert Check("roll-forward not checked", "not checked") == ROLLFORWARD_NOT_CHECKED
    assert Check("evidence checked", "passed") == EVIDENCE_CHECKED
    assert (service.PASSED, service.FAILED, service.NOT_CHECKED) == (
        "passed",
        "failed",
        "not checked",
    )


SENTENCES = ("One.", "Two.", "Three.")


def test_an_accepted_value_is_verified_by_its_status_and_its_sentence():
    assert service.field_checks("maturity extension", "auto-accepted", 3, SENTENCES) == (
        Check("auto-accepted", "passed"),
        Check("evidence checked", "passed"),
    )
    assert service.field_checks("pending", "human-reviewed", 1, SENTENCES) == (
        Check("human-reviewed", "passed"),
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
        TapeValue("beginning_balance", "beginning balance", "money", None, balance),
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


def test_the_roll_forward_is_not_checked_until_the_crosscheck_lands():
    assert service.rollforward_check(None, deal_id=1, period=Period(2026, 9), loan="2") == (  # pyright: ignore[reportArgumentType]
        ROLLFORWARD_NOT_CHECKED
    )


def test_financials_show_their_end_and_age():
    found = service.financials(loan_row())
    assert (found.start, found.end) == (dt.date(2024, 1, 1), dt.date(2024, 12, 31))
    assert (found.days_at_period_end, found.days_at_filing) == (619, 637)
    assert found.status == "619 days old at period end, 637 days old at filing"


def test_financials_of_an_excluded_or_unreported_loan_have_no_age():
    coop = service.financials(loan_row(property_type="CH"))
    assert (coop.days_at_period_end, coop.status) == (None, "excluded: co-op")
    missing = service.financials(loan_row(financials_start=None, financials_end=None))
    assert (missing.end, missing.days_at_period_end, missing.status) == (
        None,
        None,
        "no updated financials",
    )


def test_a_loans_alerts_are_those_covering_it_in_that_deal():
    views = [
        alert(covered(F1, "7", "25000000.00")),
        alert(covered(F2, "7", "40000000.00")),
        alert(covered(F2, "3", "40000000.00"), covered(F1, "7", "25000000.00")),
        alert(covered(F1, "2", "25000000.00")),
    ]
    assert service.covers(views[0], F1, "7")
    assert not service.covers(views[1], F1, "7")
    assert service.loan_alerts(views, F1, "7") == (
        LoanAlert(12, "high", "transferred to special servicing on 2026-09-02", "new"),
        LoanAlert(12, "high", "transferred to special servicing on 2026-09-02", "new"),
    )
    assert service.loan_alerts(views, F2, "2") == ()


def test_a_missing_comment_says_which_month():
    assert (
        service.comment_note(Period(2026, 9)) == "no servicer comment extracted for September 2026"
    )
