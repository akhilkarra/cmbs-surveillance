"""Coverage classes, overdue filings, shares and the rendered report, without a database."""

import datetime as dt
import re
from decimal import Decimal

import pytest

from cmbs.coverage.rules import (
    ASSET_LEVEL_COMPLIANCE,
    ORDER,
    Classified,
    Coverage,
    FilingFact,
    classify,
    expected_by,
    exposure_by_class,
    issued_on,
    overdue_since,
    shares,
    with_vendor,
)
from cmbs.coverage.service import ClassExposure, CoverageReport, DealCoverage, render
from cmbs.domain import Period


def fact(form: str, day: str) -> FilingFact:
    return FilingFact(form, dt.date.fromisoformat(day))


CONDUIT_2018 = [
    fact("424B2", "2018-08-02"),
    fact("10-D", "2026-08-27"),
    fact("ABS-EE", "2026-08-27"),
    fact("10-D", "2026-09-29"),
    fact("ABS-EE", "2026-09-29"),
]
CONDUIT_2014 = [fact("424B5", "2014-12-12"), fact("10-D", "2026-08-31"), fact("10-D", "2026-09-30")]


# Classes


def test_loan_tapes_and_distribution_reports_are_full():
    assert classify(CONDUIT_2018, has_cik=True) == Classified(
        Coverage.FULL, "loan tape and distribution reports monthly"
    )


def test_a_deal_issued_before_the_asset_level_rule_has_distribution_reports_only():
    assert classify(CONDUIT_2014, has_cik=True) == Classified(
        Coverage.DISTRIBUTION_ONLY, "issued before November 2016, no loan tape"
    )


def test_the_asset_level_rule_starts_on_its_compliance_date():
    on_the_day = [FilingFact("424B2", ASSET_LEVEL_COMPLIANCE), fact("10-D", "2017-01-30")]
    day_before = [
        FilingFact("424B2", ASSET_LEVEL_COMPLIANCE - dt.timedelta(days=1)),
        fact("10-D", "2017-01-30"),
    ]
    assert classify(on_the_day, has_cik=True) == Classified(
        Coverage.DISTRIBUTION_ONLY, "no loan tape filed"
    )
    assert classify(day_before, has_cik=True).detail == "issued before November 2016, no loan tape"


def test_without_a_prospectus_the_first_filing_dates_the_deal():
    assert issued_on([fact("10-D", "2015-02-27"), fact("8-K", "2014-12-12")]) == dt.date(
        2014, 12, 12
    )
    assert issued_on([fact("8-K", "2014-12-12"), fact("424B5", "2014-12-15")]) == dt.date(
        2014, 12, 15
    )
    assert issued_on([]) is None


def test_a_form_15_suspends_reporting_and_names_the_last_10_d_month():
    filings = [*CONDUIT_2014, fact("10-D", "2024-09-27"), fact("15-15D", "2024-10-09")]
    filings = [f for f in filings if f.filing_date.year < 2026]
    assert classify(filings, has_cik=True) == Classified(
        Coverage.SUSPENDED, "Form 15-15D, last 10-D September 2024"
    )


def test_suspension_names_the_first_form_15_filed():
    filings = [
        fact("10-D", "2020-01-30"),
        fact("15-12G", "2021-03-01"),
        fact("15-15D", "2020-02-03"),
    ]
    assert classify(filings, has_cik=True).detail == "Form 15-15D, last 10-D January 2020"


def test_a_suspension_before_any_10_d_says_so():
    assert classify([fact("15-12B", "2019-01-02")], has_cik=True) == Classified(
        Coverage.SUSPENDED, "Form 15-12B, no 10-D filed"
    )


def test_amendments_do_not_make_a_deal_full():
    filings = [*CONDUIT_2014, fact("ABS-EE/A", "2026-09-30"), fact("10-D/A", "2026-09-30")]
    assert classify(filings, has_cik=True).coverage is Coverage.DISTRIBUTION_ONLY


def test_a_new_deal_with_only_its_offering_tape_is_full_before_its_first_report():
    filings = [fact("424H", "2026-06-22"), fact("ABS-EE", "2026-06-22")]
    assert classify(filings, has_cik=True) == Classified(
        Coverage.FULL, "loan tape filed, no distribution report yet"
    )


def test_an_entity_without_periodic_reports_is_not_publicly_reported():
    assert classify([fact("ABS-15G", "2017-11-08")], has_cik=True) == Classified(
        Coverage.NOT_PUBLIC, "no 10-D or ABS-EE on EDGAR"
    )


def test_a_deal_without_an_entity_lists_the_depositor_filings():
    assert classify([fact("ABS-15G", "2024-08-06")], has_cik=False) == Classified(
        Coverage.NOT_PUBLIC, "only the depositor's ABS-15G of 2024-08-06"
    )
    two = [fact("ABS-15G", "2024-09-01"), fact("ABS-15G", "2024-08-06")]
    assert classify(two, has_cik=False).detail == (
        "only the depositor's ABS-15G filings of 2024-08-06 and 2024-09-01"
    )
    three = [*two, fact("ABS-15G", "2024-10-01"), fact("8-K", "2024-10-02")]
    assert classify(three, has_cik=False).detail == (
        "only the depositor's ABS-15G filings of 2024-08-06, 2024-09-01 and 2024-10-01"
    )


def test_a_deal_found_nowhere_says_so():
    assert classify([], has_cik=False) == Classified(
        Coverage.NOT_PUBLIC, "no filing found on EDGAR"
    )


def test_without_an_entity_a_form_15_is_not_read_as_a_suspension():
    filings = [fact("ABS-15G", "2024-08-06"), fact("15-15D", "2024-10-09")]
    assert classify(filings, has_cik=False).coverage is Coverage.NOT_PUBLIC


# Vendor sources


def test_a_vendor_source_makes_a_private_deal_full_via_vendor():
    public = Classified(Coverage.NOT_PUBLIC, "only the depositor's ABS-15G of 2024-08-06")
    assert with_vendor(public, ["tape feed"]) == Classified(
        Coverage.VENDOR, "loan tape from tape feed; not publicly reported on EDGAR"
    )
    assert with_vendor(public, ["a", "b"]).detail == (
        "loan tape from a and b; not publicly reported on EDGAR"
    )


def test_a_vendor_source_does_not_change_a_deal_already_full():
    public = Classified(Coverage.FULL, "loan tape and distribution reports monthly")
    assert with_vendor(public, ["tape feed"]) is public


def test_no_vendor_source_leaves_the_public_class():
    public = Classified(Coverage.SUSPENDED, "Form 15-15D, last 10-D September 2024")
    assert with_vendor(public, []) is public


def test_a_vendor_source_on_an_unchecked_deal_says_discovery_has_not_run():
    public = Classified(Coverage.NOT_CHECKED, "filing discovery has not run for this deal")
    assert (
        with_vendor(public, ["feed"]).detail == "loan tape from feed; filing discovery has not run"
    )


# Overdue filings


def test_the_next_filing_is_expected_30_days_after_the_latest():
    assert expected_by(CONDUIT_2018) == dt.date(2026, 10, 29)
    assert expected_by([fact("ABS-EE", "2026-10-02"), fact("10-D", "2026-09-29")]) == dt.date(
        2026, 11, 1
    )
    assert expected_by([fact("8-K", "2026-10-02"), fact("10-D/A", "2026-10-05")]) is None


def test_a_deal_is_overdue_only_after_the_expected_date():
    assert overdue_since(Coverage.FULL, CONDUIT_2018, dt.date(2026, 10, 29)) is None
    assert overdue_since(Coverage.FULL, CONDUIT_2018, dt.date(2026, 10, 30)) == dt.date(
        2026, 10, 29
    )
    assert overdue_since(Coverage.DISTRIBUTION_ONLY, CONDUIT_2014, dt.date(2026, 11, 5)) == (
        dt.date(2026, 10, 30)
    )


@pytest.mark.parametrize(
    "coverage", [Coverage.SUSPENDED, Coverage.NOT_PUBLIC, Coverage.VENDOR, Coverage.NOT_CHECKED]
)
def test_deals_that_do_not_report_monthly_are_never_overdue(coverage):
    assert overdue_since(coverage, CONDUIT_2018, dt.date(2027, 1, 1)) is None


def test_a_reporting_deal_without_filings_is_not_overdue():
    assert overdue_since(Coverage.FULL, [], dt.date(2027, 1, 1)) is None


# Shares


def test_exposure_by_class_keeps_report_order_and_drops_empty_classes():
    exposures = {
        Coverage.NOT_PUBLIC: Decimal("30"),
        Coverage.FULL: Decimal("12"),
        Coverage.SUSPENDED: Decimal("0"),
    }
    assert exposure_by_class(exposures) == [
        (Coverage.FULL, Decimal("12")),
        (Coverage.NOT_PUBLIC, Decimal("30")),
    ]
    assert ORDER[0] is Coverage.FULL and ORDER[-1] is Coverage.NOT_CHECKED


# The rendered report


def row(deal, coverage, detail, amount, *, overdue=None, vendors=(), classes=("D",), checked=None):
    checked_as_of = dt.date(2026, 11, 5) if checked is None else checked
    return DealCoverage(
        deal, classes, Decimal(amount), coverage, detail, overdue, vendors, checked_as_of
    )


def test_render_lists_deals_classes_and_calls_out_blind_spots():
    report = CoverageReport(
        on=dt.date(2026, 11, 5),
        positions_as_of=dt.date(2026, 9, 30),
        deals=(
            row("HYT 2024-RGCY", Coverage.NOT_PUBLIC, "only the depositor's", "30000000.00"),
            row(
                "BANK 2018-BNK13",
                Coverage.FULL,
                "monthly",
                "12000000.00",
                overdue=dt.date(2026, 10, 29),
                classes=("D", "E"),
            ),
            row("X 2026-V1", Coverage.VENDOR, "via feed", "5000000.00", vendors=("feed",)),
            row(
                "Y 2026-N1",
                Coverage.FULL,
                "monthly",
                "3000000.00",
                checked=dt.date(2026, 11, 4),
            ),
        ),
        by_class=(
            ClassExposure(Coverage.FULL, Decimal("12000000.00"), 24),
            ClassExposure(Coverage.VENDOR, Decimal("5000000.00"), 10),
            ClassExposure(Coverage.NOT_PUBLIC, Decimal("30000000.00"), 60),
            ClassExposure(Coverage.NOT_CHECKED, Decimal("3000000.00"), 6),
        ),
        total=Decimal("50000000.00"),
    )
    text = render(report)
    lines = text.splitlines()
    assert lines[0] == "Coverage report as of 2026-11-05, positions as of 2026-09-30"
    assert lines[2].split() == ["Deal", "Class", "Exposure", "Coverage", "Detail"]
    table = [re.split(r"\s{2,}", line) for line in lines]
    assert ["BANK 2018-BNK13", "D, E", "12,000,000.00", "full", "monthly"] in table
    assert ["full", "12,000,000.00", "24%"] in table
    assert ["total", "50,000,000.00", "100%"] in table
    assert "60% of the book's exposure cannot be surveilled from public filings." in lines
    assert "10% of the book's exposure is surveilled only through vendor sources." in lines
    assert "6% of the book's exposure has not been checked against EDGAR yet." in lines
    assert "BANK 2018-BNK13: latest filing overdue, expected by about 2026-10-29" in lines
    assert "Y 2026-N1: last looked up as of 2026-11-04" in lines
    assert "BANK 2018-BNK13: last looked up" not in text
    assert lines[-1] == "X 2026-V1: feed"
    assert text.endswith("\n")
    assert report.share_of(Coverage.SUSPENDED) == 0
    assert report.deal("X 2026-V1").vendor_sources == ("feed",)


def test_render_leaves_out_sections_with_nothing_to_say():
    report = CoverageReport(
        on=dt.date(2026, 10, 27),
        positions_as_of=dt.date(2026, 9, 30),
        deals=(row("BANK 2018-BNK13", Coverage.FULL, "monthly", "12000000.00"),),
        by_class=(ClassExposure(Coverage.FULL, Decimal("12000000.00"), 100),),
        total=Decimal("12000000.00"),
    )
    text = render(report)
    assert "0% of the book's exposure cannot be surveilled from public filings." in text
    absent_sections = ("Overdue", "Vendor sources", "vendor sources.", "checked against", "older")
    for absent in absent_sections:
        assert absent not in text


def test_render_without_a_book_or_positions_says_so():
    nothing = CoverageReport(dt.date(2026, 10, 27), None, (), (), Decimal("0.00"))
    assert render(nothing) == (
        "Coverage report as of 2026-10-27\nNo positions file is dated on or before 2026-10-27.\n"
    )
    empty = CoverageReport(dt.date(2026, 10, 27), dt.date(2026, 9, 30), (), (), Decimal("0.00"))
    assert render(empty) == (
        "Coverage report as of 2026-10-27\nThe positions file of 2026-09-30 holds no positions.\n"
    )


def test_a_lookup_is_stale_only_when_it_ran_as_of_an_earlier_date():
    on = dt.date(2026, 11, 5)
    assert row("A", Coverage.FULL, "", "1", checked=dt.date(2026, 11, 4)).stale(on)
    assert not row("A", Coverage.FULL, "", "1", checked=on).stale(on)
    unchecked = DealCoverage("A", ("D",), Decimal(1), Coverage.NOT_CHECKED, "", None, ())
    assert unchecked.checked_as_of is None and not unchecked.stale(on)


# Found in review


def test_a_deal_with_loan_tapes_that_files_a_form_15_is_suspended():
    filings = [
        fact("424B2", "2018-08-02"),
        fact("10-D", "2024-08-27"),
        fact("ABS-EE", "2024-08-27"),
        fact("10-D", "2024-09-27"),
        fact("ABS-EE", "2024-09-27"),
        fact("15-15D", "2024-10-09"),
    ]
    assert classify(filings, has_cik=True) == Classified(
        Coverage.SUSPENDED, "Form 15-15D, last 10-D September 2024"
    )


def test_three_equal_classes_share_exactly_100_percent():
    third = Decimal("10000000.00")
    assert shares([third, third, third]) == [34, 33, 33]
    assert sum(shares([third, third, third])) == 100


def test_shares_give_the_spare_points_to_the_largest_remainders():
    amounts = [Decimal("12"), Decimal("6"), Decimal("2"), Decimal("30")]
    assert shares(amounts) == [24, 12, 4, 60]
    # 1/7, 2/7, 4/7 = 14.29, 28.57, 57.14: the spare point goes to 28.57.
    assert shares([Decimal(1), Decimal(2), Decimal(4)]) == [14, 29, 57]
    assert shares([]) == []
    assert shares([Decimal(0)]) == [0]


def period_fact(form: str, filed: str, period: tuple[int, int]) -> FilingFact:
    return FilingFact(form, dt.date.fromisoformat(filed), Period(*period))


def test_a_late_filing_for_an_earlier_month_does_not_move_the_due_date():
    filings = [
        period_fact("10-D", "2026-07-29", (2026, 7)),
        period_fact("10-D", "2026-09-29", (2026, 9)),
        period_fact("10-D", "2026-10-10", (2026, 8)),  # August, filed late
    ]
    assert expected_by(filings) == dt.date(2026, 10, 29)
    assert overdue_since(Coverage.FULL, filings, dt.date(2026, 11, 5)) == dt.date(2026, 10, 29)


def test_the_due_date_runs_from_the_first_filing_for_the_latest_month():
    filings = [
        period_fact("ABS-EE", "2026-09-29", (2026, 9)),
        period_fact("10-D", "2026-10-02", (2026, 9)),
        period_fact("10-D", "2026-08-27", (2026, 8)),
    ]
    assert expected_by(filings) == dt.date(2026, 10, 29)
