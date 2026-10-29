"""Typing a reported asset into a loan-period row: "not reported" is None, never zero."""

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from cmbs.absee.schedule_al import parse_assets
from cmbs.tape.convert import (
    LOAN_SOURCES,
    PROPERTY_SOURCES,
    ROW_FIELDS,
    LoanPeriodRow,
    Unreadable,
    read_code,
    read_date,
    read_decimal,
    read_money,
    read_rate,
    read_rate_value,
    to_row,
)
from tests.fixtures.tape.tapes import asset, fixture_bytes, tape

EVERY_TAG = asset(
    "8",
    prop={
        "propertyName": " Cedar Medical Office ",
        "propertyTypeCode": "MU",
        "mostRecentFinancialsStartDate": "07-01-2025",
        "mostRecentFinancialsEndDate": "06-30-2026",
        "mostRecentNetOperatingIncomeAmount": "612000.5",
        "mostRecentNetCashFlowAmount": "580000.00",
        "mostRecentDebtServiceAmount": "571321.00",
        "mostRecentDebtServiceCoverageNetOperatingIncomePercentage": "1.0712",
        "mostRecentPhysicalOccupancyPercentage": "0.93",
        "DefeasedStatusCode": "N",
    },
    reportPeriodBeginningScheduleLoanBalanceAmount="7540000.25",
    scheduledPrincipalAmount="30000.00",
    unscheduledPrincipalCollectedAmount="10000",
    otherPrincipalAdjustmentAmount="-1.25",
    realizedLossToTrustAmount="0.00",
    reportPeriodEndActualBalanceAmount="7500000.25",
    paidThroughDate="08-01-2026",
    paymentStatusLoanCode="1",
    mostRecentSpecialServicerTransferDate="02-14-2025",
    mostRecentMasterServicerReturnDate="07-20-2026",
    workoutStrategyCode="8",
    modificationCode="4",
    reportPeriodInterestRatePercentage=".04815000",
)


def only(content: bytes):
    [parsed] = parse_assets(content)
    return parsed


def test_reads_every_field_from_its_tag():
    assert to_row(only(tape(EVERY_TAG))) == LoanPeriodRow(
        loan="8",
        reporting_period_end=dt.date(2026, 9, 11),
        beginning_balance=Decimal("7540000.25"),
        scheduled_principal=Decimal("30000.00"),
        unscheduled_principal=Decimal("10000.00"),
        other_principal_adjustments=Decimal("-1.25"),
        realized_loss=Decimal("0.00"),
        ending_balance=Decimal("7500000.25"),
        paid_through_date=dt.date(2026, 8, 1),
        payment_status="1",
        special_servicer_transfer_date=dt.date(2025, 2, 14),
        master_servicer_return_date=dt.date(2026, 7, 20),
        workout_strategy="8",
        modification_code="4",
        interest_rate=Decimal("0.04815000"),
        property_count=1,
        property_type="MU",
        property_name="Cedar Medical Office",
        defeased_status="N",
        financials_start=dt.date(2025, 7, 1),
        financials_end=dt.date(2026, 6, 30),
        financials_end_newest=dt.date(2026, 6, 30),
        noi=Decimal("612000.50"),
        ncf=Decimal("580000.00"),
        dscr=Decimal("1.0712"),
        occupancy=Decimal("0.93"),
        property_note=None,
        interest_rate_note=None,
    )


def test_money_is_kept_to_the_cent_and_ratios_keep_every_digit():
    row = to_row(only(tape(EVERY_TAG)))
    assert isinstance(row, LoanPeriodRow)
    assert str(row.unscheduled_principal) == "10000.00"
    assert str(row.noi) == "612000.50"
    assert str(row.dscr) == "1.0712"
    assert str(row.interest_rate) == "0.04815000"


def test_a_loan_reporting_nothing_has_only_its_number_and_period_end():
    fields: dict[str, Any] = dict.fromkeys(ROW_FIELDS)
    expected = fields | {
        "loan": "9",
        "reporting_period_end": dt.date(2026, 9, 11),
        "property_count": 0,
    }
    assert to_row(only(tape(asset("9")))) == LoanPeriodRow(**expected)


def test_zero_is_kept_as_zero_not_as_missing():
    row = to_row(only(tape(asset("6", reportPeriodEndActualBalanceAmount="0"))))
    assert isinstance(row, LoanPeriodRow)
    assert row.ending_balance == Decimal("0.00")
    assert row.ending_balance is not None


def test_the_fixture_loan_1():
    loans = {a.asset_number: a for a in parse_assets(fixture_bytes())}
    row = to_row(loans["1"])
    assert isinstance(row, LoanPeriodRow)
    assert (row.ending_balance, row.paid_through_date, row.dscr, row.noi) == (
        Decimal("50000000.00"),
        dt.date(2026, 9, 1),
        Decimal("1.45"),
        Decimal("2100000.00"),
    )
    assert row.occupancy == Decimal("0.88")


def test_every_unreadable_value_is_a_reason_in_field_order():
    content = tape(
        asset(
            "8",
            prop={
                "mostRecentFinancialsEndDate": "13-01-2026",
                "mostRecentNetOperatingIncomeAmount": "n/a",
            },
            reportPeriodEndActualBalanceAmount="1,000.00",
            reportPeriodBeginningScheduleLoanBalanceAmount="12.345",
            paidThroughDate="2026-09-01",
        )
    )
    assert to_row(only(content)) == [
        "beginning balance has more than two decimal places: '12.345'",
        "ending balance is not a number: '1,000.00'",
        "paid-through date is not a date: '2026-09-01'",
        "most recent financials end is not a date: '13-01-2026'",
        "most recent NOI is not a number: 'n/a'",
    ]


def test_a_rate_that_cannot_be_used_is_noted_and_the_loan_still_loads():
    unreadable = to_row(only(tape(asset("8", reportPeriodInterestRatePercentage=" 4.5% "))))
    assert isinstance(unreadable, LoanPeriodRow)
    assert (unreadable.interest_rate, unreadable.interest_rate_note) == (
        None,
        "interest rate is not a number: '4.5%'",
    )
    percent = to_row(only(tape(asset("8", reportPeriodInterestRatePercentage="4.5"))))
    assert isinstance(percent, LoanPeriodRow)
    assert (percent.interest_rate, percent.interest_rate_note) == (
        Decimal("4.5"),
        "interest rate '4.5' is outside 0 to 0.25",
    )


@pytest.mark.parametrize(
    ("text", "rate", "note"),
    [
        ("", None, None),
        ("0.25", Decimal("0.25"), None),
        ("0", Decimal("0"), None),
        (".07747000", Decimal("0.07747000"), None),
        ("0.2500001", Decimal("0.2500001"), "interest rate '0.2500001' is outside 0 to 0.25"),
        ("-0.01", Decimal("-0.01"), "interest rate '-0.01' is outside 0 to 0.25"),
        ("n/a", None, "interest rate is not a number: 'n/a'"),
    ],
)
def test_read_rate(text, rate, note):
    assert read_rate(text) == (rate, note)
    assert read_rate_value(text, "interest rate") == rate


def test_a_zero_rate_is_zero():
    zero = to_row(only(tape(asset("8", reportPeriodInterestRatePercentage="0.00"))))
    assert isinstance(zero, LoanPeriodRow)
    assert zero.interest_rate == Decimal("0.00")
    assert zero.interest_rate is not None


def test_a_loan_without_a_number_cannot_be_typed():
    assert to_row(
        only(tape("<assets><paymentStatusLoanCode>0</paymentStatusLoanCode></assets>"))
    ) == ["loan number is not reported"]


def test_loan_sources_cover_the_loan_level_fields_in_row_order():
    assert tuple(LOAN_SOURCES) == ROW_FIELDS[1 : ROW_FIELDS.index("property_count")]


def test_property_sources_read_every_property_value():
    assert tuple(PROPERTY_SOURCES) == (
        "property_type",
        "property_name",
        "defeased_status",
        "financials_start",
        "financials_end",
        "noi",
        "ncf",
        "debt_service",
        "dscr",
        "occupancy",
    )


PORTFOLIO = asset(
    "1",
    reportPeriodEndActualBalanceAmount="40000000.00",
)


def portfolio(*properties: dict[str, str]):
    body = "".join(
        "<property>"
        + "".join(f"<{tag}>{value}</{tag}>" for tag, value in p.items())
        + "</property>"
        for p in properties
    )
    return only(tape(PORTFOLIO.replace("</assets>", body + "</assets>")))


def test_a_loan_with_several_properties_combines_them():
    """NOI 1M and 2M for one statement period: the loan's NOI is 3M and DSCR is recomputed,
    never taken from the first property."""
    h1 = {
        "mostRecentFinancialsStartDate": "01-01-2026",
        "mostRecentFinancialsEndDate": "06-30-2026",
    }
    row = to_row(
        portfolio(
            {
                "propertyName": "First",
                "propertyTypeCode": "IN",
                **h1,
                "mostRecentNetOperatingIncomeAmount": "1000000.00",
                "mostRecentNetCashFlowAmount": "950000.00",
                "mostRecentDebtServiceAmount": "800000.00",
                "mostRecentDebtServiceCoverageNetOperatingIncomePercentage": "1.25",
                "mostRecentPhysicalOccupancyPercentage": "0.90",
            },
            {
                "propertyName": "Second",
                "propertyTypeCode": "RT",
                **h1,
                "mostRecentNetOperatingIncomeAmount": "2000000.00",
                "mostRecentNetCashFlowAmount": "1900000.00",
                "mostRecentDebtServiceAmount": "1600000.00",
                "mostRecentDebtServiceCoverageNetOperatingIncomePercentage": "1.25",
                "mostRecentPhysicalOccupancyPercentage": "0.80",
            },
        )
    )
    assert isinstance(row, LoanPeriodRow)
    assert (row.property_count, row.property_name, row.property_type) == (
        2,
        "Portfolio (2 properties)",
        "MU",
    )
    assert (row.noi, row.ncf, row.dscr, row.occupancy) == (
        Decimal("3000000.00"),
        Decimal("2850000.00"),
        Decimal("1.2500"),
        None,
    )
    assert (row.financials_start, row.financials_end) == (dt.date(2026, 1, 1), dt.date(2026, 6, 30))
    assert row.property_note is None


def test_a_portfolio_with_different_statement_periods_reports_the_oldest_end():
    """The reviewer's two properties: NOI 1M to June 2026 and 2M to December 2024."""
    row = to_row(
        portfolio(
            {
                "mostRecentFinancialsStartDate": "01-01-2026",
                "mostRecentFinancialsEndDate": "06-30-2026",
                "mostRecentNetOperatingIncomeAmount": "1000000.00",
            },
            {
                "mostRecentFinancialsStartDate": "01-01-2024",
                "mostRecentFinancialsEndDate": "12-31-2024",
                "mostRecentNetOperatingIncomeAmount": "2000000.00",
            },
        )
    )
    assert isinstance(row, LoanPeriodRow)
    assert (row.financials_end, row.financials_end_newest) == (
        dt.date(2024, 12, 31),
        dt.date(2026, 6, 30),
    )
    assert (row.noi, row.dscr) == (None, None)
    assert row.property_note == (
        "properties report financials for different periods, "
        "so the loan's NOI, NCF and DSCR are not reported"
    )


def test_unreadable_values_name_the_property_they_are_in():
    row = to_row(
        portfolio(
            {"mostRecentNetOperatingIncomeAmount": "1.00"},
            {
                "mostRecentNetOperatingIncomeAmount": "n/a",
                "mostRecentFinancialsStartDate": "07-01-2026",
                "mostRecentFinancialsEndDate": "06-30-2026",
            },
        )
    )
    assert row == [
        "property 2 most recent NOI is not a number: 'n/a'",
        "property 2 most recent financials end before they start",
    ]


def test_one_property_out_of_order_is_left_to_the_contract():
    row = to_row(
        portfolio(
            {
                "mostRecentFinancialsStartDate": "07-01-2026",
                "mostRecentFinancialsEndDate": "06-30-2026",
            }
        )
    )
    assert isinstance(row, LoanPeriodRow)
    assert (row.financials_start, row.financials_end) == (dt.date(2026, 7, 1), dt.date(2026, 6, 30))


def test_fully_defeased_status_is_carried():
    row = to_row(portfolio({"DefeasedStatusCode": "F", "propertyName": "Defeased"}))
    assert isinstance(row, LoanPeriodRow)
    assert (row.defeased_status, row.property_name) == ("F", "Defeased")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", None),
        ("0", Decimal("0")),
        ("-3", Decimal("-3")),
        ("1.4500", Decimal("1.4500")),
        ("+1", Decimal("1")),
        ("5.", Decimal("5")),
        (".5", Decimal("0.5")),
        ("-.25", Decimal("-0.25")),
        (".00000000", Decimal("0")),
    ],
)
def test_read_decimal_reads_xml_schema_decimals(text, expected):
    assert read_decimal(text, "x") == expected


@pytest.mark.parametrize("text", ["abc", ".", "-", "1e3", "1 000", "--1", "+-1", "1.2.3", "NaN"])
def test_read_decimal_refuses_anything_but_a_decimal(text):
    with pytest.raises(Unreadable) as error:
        read_decimal(text, "most recent DSCR")
    assert str(error.value) == f"most recent DSCR is not a number: {text!r}"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", None),
        ("5", "5.00"),
        ("5.1", "5.10"),
        ("5.10", "5.10"),
        ("5.100", "5.10"),
        ("-0.01", "-0.01"),
        (".00000000", "0.00"),
        ("93100000.00000000", "93100000.00"),
    ],
)
def test_read_money(text, expected):
    value = read_money(text, "x")
    assert (str(value) if value is not None else None) == expected


def test_read_money_never_rounds_away_a_third_decimal():
    with pytest.raises(Unreadable) as error:
        read_money("5.105", "ending balance")
    assert str(error.value) == "ending balance has more than two decimal places: '5.105'"


def test_read_date():
    assert read_date("02-29-2024", "x") == dt.date(2024, 2, 29)
    assert read_date("", "x") is None
    with pytest.raises(Unreadable) as error:
        read_date("02-29-2026", "paid-through date")
    assert str(error.value) == "paid-through date is not a date: '02-29-2026'"


def test_read_code():
    assert read_code("", "x") is None
    assert read_code("B", "x") == "B"
