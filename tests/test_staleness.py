import datetime as dt

import pytest

from cmbs.absee.schedule_al import iter_loans
from cmbs.analysis.staleness import (
    deal_staleness,
    file_staleness,
    is_active,
    nearest_rank_percentile,
)

FILING_DATE = dt.date(2026, 9, 29)


def test_active_excludes_paid_off_defeased_and_coops(schedule_al_path):
    active = [loan.asset_number for loan in iter_loans(schedule_al_path) if is_active(loan)]
    assert active == ["1", "2", "5", "7"]


def test_staleness_in_days_from_period_end_and_filing(schedule_al_path):
    result = file_staleness(schedule_al_path, FILING_DATE)
    assert result.active_loans == 4
    assert result.loans_with_financials == 3
    assert result.days_vs_period_end == [73, 619, 164]
    assert result.days_vs_filing == [91, 637, 182]
    assert result.over_six_months == 1


def test_no_loans_gives_empty_result():
    result = deal_staleness([], FILING_DATE)
    assert result.active_loans == 0
    assert result.days_vs_period_end == []


@pytest.mark.parametrize(
    ("values", "q", "expected"),
    [([], 0.5, None), ([5], 0.9, 5), ([73, 619, 164], 0.5, 164), ([73, 619, 164], 0.75, 619)],
)
def test_nearest_rank_percentile(values, q, expected):
    assert nearest_rank_percentile(values, q) == expected
