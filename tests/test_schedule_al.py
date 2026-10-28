import datetime as dt

from cmbs.absee.schedule_al import iter_loans, parse_date


def test_parse_date_reads_mm_dd_yyyy():
    assert parse_date("09-11-2026") == dt.date(2026, 9, 11)


def test_parse_date_treats_empty_as_not_reported():
    assert parse_date("") is None


def test_skips_portfolio_sub_assets_and_pari_passu_companions(schedule_al_path):
    numbers = [loan.asset_number for loan in iter_loans(schedule_al_path)]
    assert numbers == ["1", "2", "3", "4", "5", "6", "7"]


def test_reads_loan_and_property_fields(schedule_al_path):
    loan = next(iter_loans(schedule_al_path))
    assert loan.reporting_period_end == dt.date(2026, 9, 11)
    assert loan.ending_balance == 50_000_000.0
    assert loan.property_type == "OF"
    assert loan.financials_end == dt.date(2026, 6, 30)
    assert loan.dscr_noi == "1.45"


def test_missing_tags_come_back_empty(schedule_al_path):
    loans = {loan.asset_number: loan for loan in iter_loans(schedule_al_path)}
    assert loans["5"].financials_end is None
    assert loans["5"].payment_status == ""
