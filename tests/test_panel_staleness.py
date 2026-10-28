"""Staleness over panel loans: exclusions, "no updated financials" and the deal summary."""

import datetime as dt
from decimal import Decimal

import pytest

from cmbs.analysis.staleness import (
    AT_SECURITIZATION_ONLY,
    NO_UPDATED_FINANCIALS,
    Exclusion,
    LoanStaleness,
    StalenessSummary,
    TapeLoan,
    exclusion,
    is_active,
    loan_staleness,
    measure,
    nearest_rank_percentile,
    summarize,
)

PERIOD_END = dt.date(2026, 9, 11)
FILED = dt.date(2026, 9, 29)


def loan(number: str = "1", **values) -> TapeLoan:
    defaults = {
        "loan": number,
        "ending_balance": Decimal("1000000.00"),
        "property_name": "Harbor Office Tower",
        "property_type": "OF",
        "defeased_status": "N",
        "reporting_period_end": PERIOD_END,
        "financials_end": dt.date(2026, 6, 30),
        "filing_date": FILED,
    }
    return TapeLoan(**defaults | values)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({}, None),
        ({"property_name": "Defeased"}, Exclusion.DEFEASED),
        ({"property_name": "Defeased", "ending_balance": Decimal(0)}, Exclusion.DEFEASED),
        ({"property_name": "defeased"}, None),
        ({"property_type": "CH"}, Exclusion.COOPERATIVE),
        ({"property_type": "CH", "ending_balance": None}, Exclusion.COOPERATIVE),
        ({"ending_balance": None}, Exclusion.BALANCE_NOT_REPORTED),
        ({"ending_balance": Decimal("0.00")}, Exclusion.PAID_OFF),
        ({"ending_balance": Decimal("-1.00")}, Exclusion.PAID_OFF),
        ({"ending_balance": Decimal("0.01")}, None),
        ({"property_name": None, "property_type": None}, None),
        ({"defeased_status": "F"}, Exclusion.DEFEASED),
        ({"defeased_status": "F", "property_name": "Portfolio (2 properties)"}, Exclusion.DEFEASED),
        ({"defeased_status": "P"}, None),
        ({"defeased_status": "X"}, None),
        ({"defeased_status": None}, None),
    ],
)
def test_exclusion(values, expected):
    assert exclusion(loan(**values)) == expected
    assert is_active(loan(**values)) is (expected is None)


def test_staleness_is_measured_from_period_end_and_filing_date():
    assert loan_staleness(loan()) == LoanStaleness(
        loan="1",
        excluded=None,
        financials_end=dt.date(2026, 6, 30),
        days_vs_period_end=73,
        days_vs_filing=91,
    )


def test_an_amended_row_is_measured_from_its_own_filing_date():
    assert loan_staleness(loan(filing_date=dt.date(2026, 10, 5))).days_vs_filing == 97


def test_a_loan_without_financials_is_never_zero_days():
    measured = loan_staleness(loan(financials_end=None))
    assert measured == LoanStaleness("1", None, None, None, None)
    assert measured.status == NO_UPDATED_FINANCIALS


def test_an_excluded_loan_is_not_measured_even_with_financials():
    measured = loan_staleness(loan(ending_balance=Decimal(0)))
    assert measured == LoanStaleness("1", Exclusion.PAID_OFF, dt.date(2026, 6, 30), None, None)
    assert measured.status == "excluded: paid off"


def test_status_of_a_measured_loan():
    assert loan_staleness(loan()).status == "73 days old at period end, 91 days old at filing"


def fixture_loans() -> list[TapeLoan]:
    """The September 2026 fixture tape (features/README.md)."""
    return [
        loan("1", financials_end=dt.date(2026, 6, 30)),
        loan("2", financials_end=dt.date(2024, 12, 31), property_type="LO"),
        loan("3", property_name="Defeased", financials_end=None),
        loan("4", property_type="CH", financials_end=None),
        loan("5", financials_end=None),
        loan("6", ending_balance=Decimal("0.00"), financials_end=dt.date(2025, 12, 31)),
        loan("7", financials_end=dt.date(2026, 3, 31)),
    ]


def test_the_fixture_summary():
    report = measure(fixture_loans())
    assert report.summary == StalenessSummary(
        active_loans=4,
        loans_with_financials=3,
        loans_without_financials=1,
        median_days=164,
        p90_days=619,
        over_six_months=1,
        median_days_vs_filing=182,
    )
    assert report.summary.note is None
    assert report.summary.text() == (
        "4 active loans, 3 with updated financials, 1 without, median 164 days, "
        "90th percentile 619 days, 1 over six months, median 182 days at filing"
    )
    assert [row.status for row in report.loans] == [
        "73 days old at period end, 91 days old at filing",
        "619 days old at period end, 637 days old at filing",
        "excluded: defeased",
        "excluded: co-op",
        NO_UPDATED_FINANCIALS,
        "excluded: paid off",
        "164 days old at period end, 182 days old at filing",
    ]
    assert report.loan("5").days_vs_period_end is None


def test_a_new_issue_has_only_at_securitization_financials():
    summary = measure([loan(str(n), financials_end=None) for n in range(1, 34)]).summary
    assert (summary.active_loans, summary.loans_with_financials, summary.median_days) == (
        33,
        0,
        None,
    )
    assert summary.note == AT_SECURITIZATION_ONLY
    assert summary.text() == (
        "33 active loans, 0 with updated financials, 33 without, "
        "only at-securitization financials so far"
    )


def test_a_deal_with_no_active_loans_has_no_note():
    summary = summarize([LoanStaleness("6", Exclusion.PAID_OFF, None, None, None)])
    assert summary.active_loans == 0
    assert summary.note is None
    assert summary.text() == "0 active loans, 0 with updated financials, 0 without"


def test_six_months_means_more_than_183_days():
    loans = [
        loan("1", financials_end=PERIOD_END - dt.timedelta(days=183)),
        loan("2", financials_end=PERIOD_END - dt.timedelta(days=184)),
    ]
    assert measure(loans).summary.over_six_months == 1


def test_report_loan_lookup_fails_for_an_unknown_loan():
    report = measure([loan("1"), loan("2", financials_end=None)])
    assert report.loan("2").status == NO_UPDATED_FINANCIALS
    with pytest.raises(KeyError, match="no loan '99' in the report"):
        report.loan("99")


@pytest.mark.parametrize(
    ("values", "q", "expected"),
    [
        ([73, 164], 0.5, 73),  # the 1st of 2, not the 2nd that rounding q * (n - 1) gave
        ([73, 164], 0.9, 164),
        ([40, 10, 30, 20], 0.25, 10),
        ([40, 10, 30, 20], 0.5, 20),
        ([40, 10, 30, 20], 0.75, 30),
        ([40, 10, 30, 20], 0.9, 40),
        ([40, 10, 30, 20], 1, 40),
        ([73, 619, 164], 0.5, 164),
        ([73, 619, 164], 0.9, 619),
        (list(range(1, 11)), 0.9, 9),  # ceil(0.9 * 10) is 9, not the 10 a float would give
        (list(range(1, 11)), 0.3, 3),
        ([], 0.5, None),
    ],
)
def test_nearest_rank_is_the_ceil_q_n_th_smallest(values, q, expected):
    assert nearest_rank_percentile(values, q) == expected


@pytest.mark.parametrize("q", [0, -0.1, 1.01])
def test_a_percentile_outside_zero_to_one_is_a_bug(q):
    with pytest.raises(
        ValueError, match=f"a percentile is between 0 \\(excluded\\) and 1, not {q}"
    ):
        nearest_rank_percentile([1, 2], q)
