import datetime as dt

from cmbs.absee.schedule_al import LoanRecord, iter_loans, parse_date

# Every tag the parser reads, each with its own value, so a misread tag cannot go unnoticed.
# Loan 9 has no <property> and no balance, which real files also contain.
EVERY_TAG_XML = """<?xml version="1.0" encoding="UTF-8"?>
<assetData xmlns="http://www.sec.gov/edgar/document/absee/cmbs/assetdata">
  <assets>
    <assetNumber>8</assetNumber>
    <reportingPeriodEndDate>09-11-2026</reportingPeriodEndDate>
    <reportPeriodEndActualBalanceAmount>7500000.25</reportPeriodEndActualBalanceAmount>
    <paidThroughDate>08-01-2026</paidThroughDate>
    <paymentStatusLoanCode>1</paymentStatusLoanCode>
    <mostRecentSpecialServicerTransferDate>02-14-2025</mostRecentSpecialServicerTransferDate>
    <mostRecentMasterServicerReturnDate>07-20-2026</mostRecentMasterServicerReturnDate>
    <workoutStrategyCode>8</workoutStrategyCode>
    <modificationCode>4</modificationCode>
    <property>
      <propertyName> Cedar Medical Office </propertyName>
      <propertyTypeCode>MU</propertyTypeCode>
      <mostRecentFinancialsStartDate>07-01-2025</mostRecentFinancialsStartDate>
      <mostRecentFinancialsEndDate>06-30-2026</mostRecentFinancialsEndDate>
      <mostRecentNetOperatingIncomeAmount>612000.00</mostRecentNetOperatingIncomeAmount>
      <mostRecentDebtServiceCoverageNetOperatingIncomePercentage>1.07</mostRecentDebtServiceCoverageNetOperatingIncomePercentage>
      <mostRecentPhysicalOccupancyPercentage>0.93</mostRecentPhysicalOccupancyPercentage>
    </property>
  </assets>
  <assets>
    <assetNumber>9</assetNumber>
    <reportingPeriodEndDate>09-11-2026</reportingPeriodEndDate>
  </assets>
</assetData>
"""


def test_parse_date_reads_mm_dd_yyyy():
    assert parse_date("09-11-2026") == dt.date(2026, 9, 11)


def test_parse_date_treats_empty_as_not_reported():
    assert parse_date("") is None


def test_skips_portfolio_sub_assets_and_pari_passu_companions(schedule_al_path):
    numbers = [loan.asset_number for loan in iter_loans(schedule_al_path)]
    assert numbers == ["1", "2", "3", "4", "5", "6", "7"]


def test_reads_every_field_of_the_fixture_loans(schedule_al_path):
    loans = {loan.asset_number: loan for loan in iter_loans(schedule_al_path)}
    assert loans["1"] == LoanRecord(
        asset_number="1",
        reporting_period_end=dt.date(2026, 9, 11),
        ending_balance=50_000_000.0,
        paid_through="09-01-2026",
        payment_status="0",
        special_servicer_transfer="",
        master_servicer_return="",
        workout_strategy="",
        modification_code="",
        property_type="OF",
        property_name="Harbor Office Tower",
        financials_start=dt.date(2026, 1, 1),
        financials_end=dt.date(2026, 6, 30),
        noi="2100000.00",
        dscr_noi="1.45",
        occupancy="0.88",
    )
    assert loans["2"] == LoanRecord(
        asset_number="2",
        reporting_period_end=dt.date(2026, 9, 11),
        ending_balance=30_000_000.0,
        paid_through="",
        payment_status="3",
        special_servicer_transfer="03-06-2025",
        master_servicer_return="",
        workout_strategy="11",
        modification_code="",
        property_type="LO",
        property_name="Lakeside Hotel Portfolio",
        financials_start=dt.date(2024, 1, 1),
        financials_end=dt.date(2024, 12, 31),
        noi="",
        dscr_noi="",
        occupancy="",
    )


def test_reads_every_tag_and_strips_whitespace(tmp_path):
    path = tmp_path / "ex102.xml"
    path.write_text(EVERY_TAG_XML)
    loan = next(iter_loans(path))
    assert loan == LoanRecord(
        asset_number="8",
        reporting_period_end=dt.date(2026, 9, 11),
        ending_balance=7_500_000.25,
        paid_through="08-01-2026",
        payment_status="1",
        special_servicer_transfer="02-14-2025",
        master_servicer_return="07-20-2026",
        workout_strategy="8",
        modification_code="4",
        property_type="MU",
        property_name="Cedar Medical Office",
        financials_start=dt.date(2025, 7, 1),
        financials_end=dt.date(2026, 6, 30),
        noi="612000.00",
        dscr_noi="1.07",
        occupancy="0.93",
    )


def test_a_loan_without_property_or_balance_comes_back_empty(tmp_path):
    path = tmp_path / "ex102.xml"
    path.write_text(EVERY_TAG_XML)
    loans = {loan.asset_number: loan for loan in iter_loans(path)}
    assert loans["9"] == LoanRecord(
        asset_number="9",
        reporting_period_end=dt.date(2026, 9, 11),
        ending_balance=0.0,
        paid_through="",
        payment_status="",
        special_servicer_transfer="",
        master_servicer_return="",
        workout_strategy="",
        modification_code="",
        property_type="",
        property_name="",
        financials_start=None,
        financials_end=None,
        noi="",
        dscr_noi="",
        occupancy="",
    )


def test_missing_tags_come_back_empty(schedule_al_path):
    loans = {loan.asset_number: loan for loan in iter_loans(schedule_al_path)}
    assert loans["5"].financials_end is None
    assert loans["5"].payment_status == ""
