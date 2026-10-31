"""The loan history the loan page charts: its months, gaps and payment status changes (pure)."""

import datetime as dt
from decimal import Decimal

from cmbs.dashboard import service
from cmbs.dashboard.service import (
    FilingSource,
    HistoryMonth,
    MonthRef,
    PaymentStatus,
    StatusChange,
)
from cmbs.domain import Period
from cmbs.filings.tables import Filing
from cmbs.tape.tables import LoanPeriod

JULY, AUGUST, SEPTEMBER, OCTOBER = (Period(2026, month) for month in (7, 8, 9, 10))


def source(period: Period) -> FilingSource:
    number = f"0009900001-26-{400 + 2 * period.month + 1:06d}"
    return FilingSource(
        form="ABS-EE",
        accession=number,
        filing_date=dt.date(period.year, period.month, 29),
        url=f"https://example.test/{number}",
    )


def row(period: Period, **values) -> LoanPeriod:
    fields = {
        "loan": "2",
        "accession": source(period).accession,
        "ending_balance": Decimal("30400000.00"),
        "dscr": Decimal("1.12"),
        "payment_status": "0",
    }
    return LoanPeriod(**{**fields, **values})


def month(period: Period, status: str | None = None, *, on_tape: bool = True) -> HistoryMonth:
    """A history month whose only value of interest is its payment status code."""
    return HistoryMonth(
        month=service.month_ref(period),
        day=period.first_day,
        on_tape=on_tape,
        ending_balance=None,
        dscr=None,
        payment_status=None if status is None else PaymentStatus(status, "name"),
        source=source(period),
    )


# ---------------------------------------------------------------------------- one month


def test_a_filing_names_its_form_accession_date_and_edgar_page():
    filing = Filing(
        accession="0009900001-26-000417",
        cik=9900001,
        form="ABS-EE",
        filing_date=dt.date(2026, 8, 29),
        primary_document="ex102.xml",
    )
    assert service.filing_source(filing) == FilingSource(
        form="ABS-EE",
        accession="0009900001-26-000417",
        filing_date=dt.date(2026, 8, 29),
        url="https://www.sec.gov/Archives/edgar/data/9900001/000990000126000417/ex102.xml",
    )


def test_a_payment_status_keeps_its_code_as_reported_and_its_name():
    assert service.payment_status("A") == PaymentStatus("A", "in grace period")
    assert service.payment_status(" 01 ") == PaymentStatus("01", "30 days")
    assert service.payment_status("42") == PaymentStatus("42", "code 42")
    assert service.payment_status(None) is None


def test_a_month_on_the_tape_carries_its_values_as_text_and_its_filing():
    found = service.history_month(SEPTEMBER, source(SEPTEMBER), row(SEPTEMBER))
    assert found == HistoryMonth(
        month=MonthRef("2026-09", "September 2026"),
        day=dt.date(2026, 9, 1),
        on_tape=True,
        ending_balance="30400000.00",
        dscr="1.12",
        payment_status=PaymentStatus("0", "current"),
        source=source(SEPTEMBER),
    )


def test_a_ratio_keeps_every_digit_reported():
    found = service.history_month(JULY, source(JULY), row(JULY, dscr=Decimal("0.9650")))
    assert found.dscr == "0.9650"


def test_a_value_not_reported_is_none_never_zero():
    found = service.history_month(
        AUGUST, source(AUGUST), row(AUGUST, ending_balance=None, dscr=None, payment_status=None)
    )
    assert (found.on_tape, found.ending_balance, found.dscr, found.payment_status) == (
        True,
        None,
        None,
        None,
    )


def test_a_month_whose_tape_leaves_the_loan_out_reports_nothing():
    found = service.history_month(AUGUST, source(AUGUST), None)
    assert found == HistoryMonth(
        month=MonthRef("2026-08", "August 2026"),
        day=dt.date(2026, 8, 1),
        on_tape=False,
        ending_balance=None,
        dscr=None,
        payment_status=None,
        source=source(AUGUST),
    )


# ---------------------------------------------------------------------------- the months


def test_the_months_run_from_the_first_tape_reporting_the_loan_to_the_last():
    tapes = [(period, source(period)) for period in (JULY, AUGUST, SEPTEMBER, OCTOBER)]
    rows = {source(p).accession: row(p) for p in (AUGUST, OCTOBER)}
    months = service.history_months(tapes, rows)
    assert [(m.month.key, m.on_tape) for m in months] == [
        ("2026-08", True),
        ("2026-09", False),
        ("2026-10", True),
    ]
    assert [m.source for m in months] == [source(AUGUST), source(SEPTEMBER), source(OCTOBER)]


def test_one_month_reported_is_a_history_of_one_month():
    tapes = [(period, source(period)) for period in (JULY, AUGUST, SEPTEMBER)]
    months = service.history_months(tapes, {source(JULY).accession: row(JULY)})
    assert [m.month.key for m in months] == ["2026-07"]


def test_a_loan_no_tape_reports_has_no_history():
    tapes = [(period, source(period)) for period in (JULY, AUGUST)]
    assert service.history_months(tapes, {}) == ()
    assert service.history_months([], {}) == ()


# ---------------------------------------------------------------------------- status changes


def test_each_change_of_payment_status_is_marked_with_its_delinquency_bucket():
    months = [month(JULY, "0"), month(AUGUST, "A"), month(SEPTEMBER, "1")]
    assert service.status_changes("2", months) == (
        StatusChange(service.month_ref(AUGUST), dt.date(2026, 8, 1), "A", "grace or late"),
        StatusChange(service.month_ref(SEPTEMBER), dt.date(2026, 9, 1), "1", "30 days"),
    )


def test_the_first_status_reported_is_not_a_change():
    assert service.status_changes("2", [month(JULY, "2")]) == ()
    assert service.status_changes("2", []) == ()


def test_the_same_code_written_differently_is_not_a_change():
    months = [month(JULY, "1"), month(AUGUST, "01"), month(SEPTEMBER, " 1")]
    assert service.status_changes("2", months) == ()


def test_a_change_within_a_bucket_is_still_a_change():
    months = [month(JULY, "A"), month(AUGUST, "B")]
    [change] = service.status_changes("2", months)
    assert (change.month.key, change.code, change.bucket) == ("2026-08", "B", "grace or late")


def test_a_month_without_a_status_changes_nothing_and_is_skipped():
    months = [
        month(JULY, "0"),
        month(AUGUST, None),
        month(SEPTEMBER, None, on_tape=False),
        month(OCTOBER, "0"),
    ]
    assert service.status_changes("2", months) == ()
    months[3] = month(OCTOBER, "2")
    [change] = service.status_changes("2", months)
    assert (change.month.key, change.bucket) == ("2026-10", "60 days")


def test_a_code_off_the_list_is_marked_as_such():
    [change] = service.status_changes("2", [month(JULY, "0"), month(AUGUST, "Z")])
    assert (change.code, change.bucket) == ("Z", "status not on the code list")
