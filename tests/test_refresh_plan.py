"""Which months a run loads, and how its outcome reads (cmbs.refresh.plan). No database."""

import datetime as dt

import pytest

from cmbs.domain import Period
from cmbs.refresh.plan import (
    DealOutcome,
    DealResult,
    FilingRef,
    MonthFilings,
    MonthOutcome,
    MonthResult,
    alerts_raised,
    backfill_months,
    deal_lines,
    deal_outcome,
    month_lines,
    monthly_filings,
    months_between,
    refresh_months,
    run_status,
    stopped,
    summary,
)

JUL, AUG, SEP, OCT, NOV = (Period(2026, m) for m in (7, 8, 9, 10, 11))


def ref(form: str, period: Period, accession: str = "A", day: int = 29) -> FilingRef:
    return FilingRef(accession, 9_900_001, form, dt.date(period.year, period.month, day), period)


def both(period: Period) -> MonthFilings:
    return MonthFilings(period, ref("ABS-EE", period, "T"), ref("10-D", period, "R"))


def loaded(period: Period, rows: int = 7, **extra) -> MonthResult:
    return MonthResult(period, MonthOutcome.LOADED, loan_rows=rows, **extra)


def failed(period: Period) -> MonthResult:
    return MonthResult(
        period, MonthOutcome.NOT_LOADED, "10-D failed to parse", "not a report", failed=True
    )


def missing(period: Period) -> MonthResult:
    return MonthResult(period, MonthOutcome.NOT_LOADED, f"no ABS-EE filed for {period}")


# ---------------------------------------------------------------------------- the filings


def test_a_month_is_complete_with_both_filings():
    assert both(SEP).complete
    assert both(SEP).missing() is None
    assert not MonthFilings(SEP, tape=ref("ABS-EE", SEP)).complete
    assert not MonthFilings(SEP, report=ref("10-D", SEP)).complete


def test_a_month_says_which_filing_it_lacks():
    assert MonthFilings(SEP, report=ref("10-D", SEP)).missing() == (
        "no ABS-EE filed for September 2026"
    )
    assert MonthFilings(SEP, tape=ref("ABS-EE", SEP)).missing() == (
        "no 10-D filed for September 2026"
    )
    assert MonthFilings(SEP).missing() == "no ABS-EE or 10-D filed for September 2026"


def test_a_month_says_which_amendments_runs_do_not_load():
    amended = MonthFilings(
        SEP,
        amendments=(ref("ABS-EE/A", SEP, "TA1"), ref("10-D/A", SEP, "RA"), ref("ABS-EE/A", SEP)),
    )
    assert amended.notes() == (
        "September 2026 has a 10-D/A that runs do not load yet",
        "September 2026 has an ABS-EE/A that runs do not load yet",
    )
    assert both(SEP).notes() == ()


def test_monthly_filings_pairs_tapes_and_reports_and_keeps_amendments():
    filings = [
        ref("10-D", OCT, "R10"),
        ref("ABS-EE", SEP, "T9"),
        ref("10-D", SEP, "R9"),
        ref("ABS-EE/A", SEP, "TA9b", day=30),
        ref("ABS-EE/A", SEP, "TA9a", day=30),
        ref("10-D/A", NOV, "RA11"),
        ref("424B2", AUG, "P"),
    ]
    months = monthly_filings(filings)
    assert list(months) == [SEP, OCT, NOV]
    assert months[SEP] == MonthFilings(
        SEP,
        ref("ABS-EE", SEP, "T9"),
        ref("10-D", SEP, "R9"),
        (ref("ABS-EE/A", SEP, "TA9a", day=30), ref("ABS-EE/A", SEP, "TA9b", day=30)),
    )
    assert months[OCT] == MonthFilings(OCT, None, ref("10-D", OCT, "R10"))
    assert months[NOV] == MonthFilings(NOV, amendments=(ref("10-D/A", NOV, "RA11"),))


def test_of_two_originals_for_a_month_the_later_filed_counts():
    early, late = ref("ABS-EE", SEP, "T2", day=20), ref("ABS-EE", SEP, "T1", day=29)
    assert monthly_filings([late, early])[SEP].tape == late
    assert monthly_filings([early, late])[SEP].tape == late
    # Filed the same day: the higher accession.
    one, two = ref("10-D", SEP, "R1"), ref("10-D", SEP, "R2")
    assert monthly_filings([two, one])[SEP].report == two
    assert monthly_filings([one, two])[SEP].report == two


# ---------------------------------------------------------------------------- the plan


def test_months_between():
    assert months_between(Period(2025, 11), Period(2026, 2)) == [
        Period(2025, 11),
        Period(2025, 12),
        Period(2026, 1),
        Period(2026, 2),
    ]
    assert months_between(SEP, SEP) == [SEP]
    assert months_between(OCT, SEP) == []


def test_a_refresh_of_an_empty_deal_loads_only_the_newest_complete_month():
    available = {AUG: both(AUG), SEP: both(SEP), OCT: MonthFilings(OCT, report=ref("10-D", OCT))}
    assert refresh_months(available, set()) == [SEP]


def test_a_refresh_tries_every_month_after_the_first_loaded_that_is_not_loaded():
    available = {
        AUG: both(AUG),
        SEP: MonthFilings(SEP, tape=ref("ABS-EE", SEP)),
        OCT: both(OCT),
        NOV: MonthFilings(NOV, tape=ref("ABS-EE", NOV)),
    }
    # September lacks its 10-D but lies before October: it is tried first, and stops the deal.
    assert refresh_months(available, {AUG}) == [SEP, OCT]
    # A gap after the first loaded month is tried again, even behind a later loaded month.
    assert refresh_months(available, {AUG, OCT}) == [SEP]
    assert refresh_months(available, {JUL}) == [AUG, SEP, OCT]


def test_a_refresh_tries_calendar_months_with_no_filing_at_all():
    available = {JUL: both(JUL), OCT: both(OCT)}
    assert refresh_months(available, {JUL}) == [AUG, SEP, OCT]


def test_a_refresh_stops_at_the_newest_complete_month():
    available = {AUG: both(AUG), SEP: both(SEP), OCT: both(OCT)}
    assert refresh_months(available, {Period(2026, 6)}) == [JUL, AUG, SEP, OCT]
    assert refresh_months(available, {AUG, SEP, OCT}) == []
    # A month loaded out of order (by hand) is not loaded again, nor anything after the newest.
    assert refresh_months(available, {AUG, OCT, Period(2026, 12)}) == [SEP]


def test_a_refresh_with_no_complete_month_loads_nothing():
    assert refresh_months({}, set()) == []
    assert refresh_months({SEP: MonthFilings(SEP, report=ref("10-D", SEP))}, {AUG}) == []


def test_a_backfill_tries_every_month_of_its_range_not_yet_loaded():
    assert backfill_months(AUG, OCT, set()) == [AUG, SEP, OCT]
    assert backfill_months(AUG, OCT, {SEP}) == [AUG, OCT]
    assert backfill_months(SEP, SEP, set()) == [SEP]


def test_a_backfill_that_ends_before_it_starts_is_an_error():
    with pytest.raises(ValueError, match="^a backfill from October 2026 to August 2026 ends"):
        backfill_months(OCT, AUG, set())


# ---------------------------------------------------------------------------- outcomes


def test_month_texts_count_rows_quarantines_and_flags():
    assert loaded(SEP).text() == (
        "September 2026: loaded, 7 loan rows, 0 loans quarantined, 0 reconciliation flags"
    )
    assert loaded(SEP, rows=1, quarantined=1, flags=1).text() == (
        "September 2026: loaded, 1 loan row, 1 loan quarantined, 1 reconciliation flag"
    )
    assert loaded(SEP, quarantined=2, flags=3, notes=("a note", "another")).text() == (
        "September 2026: loaded, 7 loan rows, 2 loans quarantined, 3 reconciliation flags\n"
        "  a note\n"
        "  another"
    )
    assert failed(SEP).text() == "September 2026: not loaded, 10-D failed to parse (not a report)"
    assert missing(SEP).text() == "September 2026: not loaded, no ABS-EE filed for September 2026"


def test_month_lines_read_older_summaries_without_counts():
    assert month_lines({"period": "May 2026", "outcome": "loaded"}) == [
        "May 2026: loaded, 0 loan rows, 0 loans quarantined, 0 reconciliation flags"
    ]


def test_month_as_json():
    assert failed(SEP).as_json() == {
        "period": "September 2026",
        "outcome": "not loaded",
        "reason": "10-D failed to parse",
        "detail": "not a report",
        "loan_rows": 0,
        "quarantined": 0,
        "flags": 0,
        "alerts": 0,
        "failed": True,
        "notes": [],
        "comments": None,
        "review_items": 0,
        "rollforward_breaks": None,
        "text_flags": None,
        "filings": [],
    }
    assert loaded(SEP, alerts=3, quarantined=1, flags=2, notes=("n",)).as_json() == {
        "period": "September 2026",
        "outcome": "loaded",
        "reason": None,
        "detail": None,
        "loan_rows": 7,
        "quarantined": 1,
        "flags": 2,
        "alerts": 3,
        "failed": False,
        "notes": ["n"],
        "comments": None,
        "review_items": 0,
        "rollforward_breaks": None,
        "text_flags": None,
        "filings": [],
    }


def test_deal_texts():
    refreshed = DealResult("F1", DealOutcome.REFRESHED, None, (loaded(SEP),), ("old note",))
    assert refreshed.text() == (
        "F1: refreshed\n"
        "  September 2026: loaded, 7 loan rows, 0 loans quarantined, 0 reconciliation flags\n"
        "  old note"
    )
    assert DealResult("F1", DealOutcome.REFRESHED).text() == "F1: refreshed, nothing new to load"
    assert DealResult("HYT", DealOutcome.SKIPPED, "not publicly reported").text() == (
        "HYT: skipped, not publicly reported"
    )
    assert DealResult("F1", DealOutcome.FAILED, "filing discovery failed: x").text() == (
        "F1: failed, filing discovery failed: x"
    )
    assert DealResult("F1", DealOutcome.FAILED).text() == "F1: failed"
    assert deal_lines({"deal": "F2", "outcome": "skipped"}) == ["F2: skipped"]


def test_a_deal_fails_when_a_month_failed_to_load():
    assert deal_outcome("F1", [loaded(AUG), failed(SEP)]).outcome is DealOutcome.FAILED
    assert deal_outcome("F1", [loaded(AUG), failed(SEP)]).stopped_at == failed(SEP)
    assert deal_outcome("F1", [loaded(AUG), missing(SEP)]).outcome is DealOutcome.REFRESHED
    assert deal_outcome("F1", [loaded(AUG), missing(SEP)]).stopped_at is None
    assert deal_outcome("F1", []) == DealResult("F1", DealOutcome.REFRESHED, None, ())
    assert deal_outcome("F1", [], ["n"]).notes == ("n",)


def test_run_status():
    refreshed = DealResult("F1", DealOutcome.REFRESHED, None, (loaded(SEP),))
    skipped = DealResult("HYT", DealOutcome.SKIPPED, "not publicly reported")
    assert run_status([refreshed, skipped]) == "succeeded"
    assert run_status([]) == "succeeded"
    held = DealResult("F2", DealOutcome.SKIPPED, "run 42 is already refreshing F2", problem=True)
    assert run_status([refreshed, held]) == "completed with problems"
    gap = DealResult("F1", DealOutcome.REFRESHED, None, (loaded(AUG), missing(SEP)))
    assert run_status([gap, skipped]) == "completed with problems"
    quarantine = DealResult("F1", DealOutcome.REFRESHED, None, (loaded(SEP, quarantined=1),))
    assert run_status([quarantine]) == "completed with problems"
    broken = DealResult("F2", DealOutcome.FAILED, None, (failed(SEP),))
    assert run_status([gap, broken, held]) == "failed"
    assert run_status([DealResult("F2", DealOutcome.FAILED, "discovery")]) == "failed"


def test_alerts_raised_counts_the_loaded_months():
    assert alerts_raised([loaded(AUG, alerts=1), loaded(SEP, alerts=2), missing(OCT)]) == 3
    assert alerts_raised([loaded(AUG, alerts=0), loaded(SEP)]) == 0
    # A month not loaded rolled its alerts back with it.
    assert alerts_raised([MonthResult(OCT, MonthOutcome.NOT_LOADED, "r", alerts=4)]) == 0
    assert alerts_raised([]) == 0


def test_stopped_names_the_first_failure():
    first = DealResult("F1", DealOutcome.REFRESHED, None, (loaded(SEP),))
    month = DealResult("F2", DealOutcome.FAILED, None, (loaded(AUG), failed(SEP)))
    early = DealResult("F3", DealOutcome.FAILED, "filing discovery failed: no answer")
    assert stopped([first]) is None
    assert stopped([first, month, early]) == {
        "deal": "F2",
        "period": "September 2026",
        "error": "10-D failed to parse",
        "detail": "not a report",
    }
    assert stopped([first, early, month]) == {
        "deal": "F3",
        "period": None,
        "error": "filing discovery failed: no answer",
        "detail": None,
    }


def test_summary_counts_deals_months_rows_quarantines_and_flags():
    deals = [
        DealResult(
            "F1",
            DealOutcome.REFRESHED,
            None,
            (loaded(AUG, alerts=1, quarantined=1, flags=2), loaded(SEP, 6, alerts=0, flags=1)),
        ),
        DealResult("F2", DealOutcome.FAILED, None, (missing(AUG), failed(SEP))),
        DealResult("HYT", DealOutcome.SKIPPED, "not publicly reported"),
    ]
    found = summary(deals)
    assert {key: value for key, value in found.items() if key != "deals"} == {
        "deals_refreshed": 1,
        "deals_skipped": 1,
        "deals_failed": 1,
        "months_loaded": 2,
        "months_not_loaded": 2,
        "loan_rows": 13,
        "quarantined": 1,
        "flags": 3,
        "alerts_raised": 1,
        "comments": 0,
        "review_items": 0,
        "rollforward_breaks": 0,
        "text_flags": 0,
        "stopped": {
            "deal": "F2",
            "period": "September 2026",
            "error": "10-D failed to parse",
            "detail": "not a report",
        },
    }
    assert found["deals"] == [deal.as_json() for deal in deals]
    assert found["deals"][2] == {
        "deal": "HYT",
        "outcome": "skipped",
        "reason": "not publicly reported",
        "months": [],
        "notes": [],
        "problem": False,
        "loaded_through": None,
    }
