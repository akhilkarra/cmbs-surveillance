"""The refresh routine's report, from stored run summaries. No database."""

import datetime as dt
from typing import Any

from cmbs.domain import Period
from cmbs.refresh.plan import (
    DealOutcome,
    DealResult,
    FilingLink,
    FilingRef,
    MonthFilings,
    MonthOutcome,
    MonthResult,
    deal_outcome,
    links,
    run_status,
    summary,
)
from cmbs.refresh.report import (
    cite,
    counted,
    deal_head,
    month_report,
    problem_counts,
    routine_report,
    stopped_line,
)
from cmbs.runs.service import RunView

SEP, AUG = Period(2026, 9), Period(2026, 8)
TAPE = FilingLink("ABS-EE", "T9", "https://t9/")
REPORT = FilingLink("10-D", "R9", "https://r9/")
AT = dt.datetime(2026, 10, 1, 9, 0, tzinfo=dt.UTC)


def loaded(period: Period = SEP, **extra: Any) -> MonthResult:
    values: dict[str, Any] = {
        "loan_rows": 7,
        "comments": 1,
        "review_items": 2,
        "alerts": 3,
        "rollforward_breaks": 0,
    }
    values.update(extra)
    return MonthResult(period, MonthOutcome.LOADED, filings=(TAPE, REPORT), **values)


def run(deals: list[DealResult], status: str | None = None) -> RunView:
    return RunView(
        number=12,
        kind="refresh deal",
        started_by="quinn",
        started_at=AT,
        ended_at=AT,
        status=status or run_status(deals),
        summary=summary(deals),
    )


def test_counted():
    assert counted(1, "loan") == "1 loan"
    assert counted(0, "loan") == "0 loans"
    assert counted(2, "loan") == "2 loans"
    assert counted(2, "box", "boxes") == "2 boxes"


def test_links_name_each_filing_with_its_edgar_folder():
    tape = FilingRef("0009900001-26-000419", 9900001, "ABS-EE", dt.date(2026, 9, 29), SEP)
    report = FilingRef("0009900001-26-000418", 9900001, "10-D", dt.date(2026, 9, 29), SEP)
    assert links(MonthFilings(SEP, tape, report)) == (
        FilingLink(
            "ABS-EE",
            "0009900001-26-000419",
            "https://www.sec.gov/Archives/edgar/data/9900001/000990000126000419/",
        ),
        FilingLink(
            "10-D",
            "0009900001-26-000418",
            "https://www.sec.gov/Archives/edgar/data/9900001/000990000126000418/",
        ),
    )
    assert [f.form for f in links(MonthFilings(SEP, None, report))] == ["10-D"]
    assert links(MonthFilings(SEP)) == ()
    assert TAPE.as_json() == {"form": "ABS-EE", "accession": "T9", "url": "https://t9/"}


def test_cite_picks_the_forms_in_order():
    filings = [TAPE.as_json(), REPORT.as_json()]
    assert cite(filings, ("10-D",)) == "10-D R9 https://r9/"
    assert cite(filings, ("10-D", "ABS-EE")) == "10-D R9 https://r9/; ABS-EE T9 https://t9/"
    assert cite([], ("10-D",)) == "no filing"


def test_a_loaded_month_counts_each_thing_with_its_filings():
    assert month_report(loaded().as_json()) == [
        "September 2026: loaded",
        "  7 loans loaded: ABS-EE T9 https://t9/",
        "  1 comment extracted: 10-D R9 https://r9/",
        "  2 items sent to review: 10-D R9 https://r9/",
        "  3 alerts raised: ABS-EE T9 https://t9/; 10-D R9 https://r9/",
        "  roll-forward passed: ABS-EE T9 https://t9/",
        "  reconciliation passed: ABS-EE T9 https://t9/; 10-D R9 https://r9/",
    ]


def test_a_month_with_problems_lists_them():
    month = loaded(
        loan_rows=1, comments=2, review_items=1, alerts=1, quarantined=1, flags=2,
        rollforward_breaks=1, notes=("an amendment",),
    )  # fmt: skip
    assert month_report(month.as_json()) == [
        "September 2026: loaded",
        "  1 loan loaded: ABS-EE T9 https://t9/",
        "  2 comments extracted: 10-D R9 https://r9/",
        "  1 item sent to review: 10-D R9 https://r9/",
        "  1 alert raised: ABS-EE T9 https://t9/; 10-D R9 https://r9/",
        "  1 loan quarantined: ABS-EE T9 https://t9/",
        "  1 roll-forward break: ABS-EE T9 https://t9/",
        "  2 reconciliation flags: ABS-EE T9 https://t9/; 10-D R9 https://r9/",
        "  an amendment",
    ]


def test_a_month_without_a_model_or_the_roll_forward_says_so():
    month = loaded(comments=None, rollforward_breaks=None)
    lines = month_report(month.as_json())
    assert lines[2] == "  comments not extracted (the run had no model to ask): 10-D R9 https://r9/"
    assert "sent to review" not in "\n".join(lines)
    assert "  roll-forward not checked: ABS-EE T9 https://t9/" in lines


def test_a_month_not_loaded_says_why_and_what_was_tried():
    failed = MonthResult(
        SEP, MonthOutcome.NOT_LOADED, "10-D failed to parse", "bad", failed=True, filings=(TAPE,)
    )
    assert month_report(failed.as_json()) == [
        "September 2026: not loaded, 10-D failed to parse (bad)",
        "  tried ABS-EE T9 https://t9/",
    ]
    missing = MonthResult(SEP, MonthOutcome.NOT_LOADED, "no 10-D filed for September 2026")
    assert month_report(missing.as_json()) == [
        "September 2026: not loaded, no 10-D filed for September 2026"
    ]


def test_deal_heads():
    up = deal_outcome("F1", [loaded()], loaded_before=[AUG])
    assert deal_head(up.as_json()) == "F1: up to date through September 2026"
    nothing_new = deal_outcome("F1", [], loaded_before=[AUG, SEP])
    assert deal_head(nothing_new.as_json()) == "F1: up to date through September 2026"
    assert deal_head(deal_outcome("F1", []).as_json()) == "F1: nothing loaded yet"
    gap = MonthResult(Period(2026, 10), MonthOutcome.NOT_LOADED, "no 10-D filed for October 2026")
    behind = deal_outcome("F1", [loaded(), gap], loaded_before=[AUG])
    assert deal_head(behind.as_json()) == "F1: loaded through September 2026, 1 month not loaded"
    never = deal_outcome("F1", [gap])
    assert deal_head(never.as_json()) == "F1: nothing loaded yet, 1 month not loaded"
    skipped = DealResult("HYT", DealOutcome.SKIPPED, "not publicly reported")
    assert deal_head(skipped.as_json()) == "HYT: skipped, not publicly reported"
    failed = DealResult("X", DealOutcome.FAILED, "filing discovery failed: boom")
    assert deal_head(failed.as_json()) == "X: failed, filing discovery failed: boom"


def test_loaded_through_counts_months_loaded_before_and_by_the_run():
    failed = MonthResult(SEP, MonthOutcome.NOT_LOADED, "x", failed=True)
    assert deal_outcome("F1", [loaded(AUG), failed]).loaded_through == AUG
    assert deal_outcome("F1", [failed], loaded_before=[Period(2026, 7)]).loaded_through == (
        Period(2026, 7)
    )
    assert deal_outcome("F1", [failed]).loaded_through is None
    assert deal_outcome("F1", [loaded(AUG)], loaded_before=[SEP]).loaded_through == SEP


def test_problem_counts_in_the_order_to_act_on_them():
    gap = MonthResult(Period(2026, 10), MonthOutcome.NOT_LOADED, "no 10-D filed for October 2026")
    deals = [
        deal_outcome("F1", [loaded(quarantined=2, rollforward_breaks=1, flags=3), gap]),
        DealResult("F2", DealOutcome.SKIPPED, "run 4 is already refreshing F2", problem=True),
        DealResult("F3", DealOutcome.FAILED, "filing discovery failed: boom"),
        DealResult("HYT", DealOutcome.SKIPPED, "not publicly reported"),
    ]
    assert problem_counts(summary(deals)) == [
        "2 loans quarantined",
        "1 roll-forward break",
        "3 reconciliation flags",
        "1 month not loaded",
        "1 deal not refreshed",
        "1 deal held by another run",
    ]
    assert problem_counts(summary([deal_outcome("F1", [loaded()])])) == []
    # A deal that failed partway is counted by its month, not again as a deal.
    failed = MonthResult(SEP, MonthOutcome.NOT_LOADED, "x", failed=True)
    assert problem_counts(summary([deal_outcome("F1", [failed])])) == ["1 month not loaded"]


def test_stopped_line():
    assert stopped_line(None) is None
    assert stopped_line({}) is None
    where = {"deal": "F1", "period": "September 2026", "error": "boom", "detail": "why"}
    assert stopped_line(where) == "stopped at F1, September 2026: boom (why)"
    assert stopped_line({**where, "period": None, "detail": None}) == "stopped at F1: boom"
    assert stopped_line({"deal": None, "period": None, "error": "boom"}) == "stopped: boom"


def test_a_clean_routine_report():
    deals = [deal_outcome("F1", [loaded()], loaded_before=[AUG])]
    assert routine_report(run(deals)).splitlines()[:4] == [
        "run 12 (refresh deal, started by quinn): succeeded",
        "F1: up to date through September 2026",
        "  September 2026: loaded",
        "    7 loans loaded: ABS-EE T9 https://t9/",
    ]


def test_problems_open_the_report_and_it_is_not_called_a_success():
    month = loaded(quarantined=1, rollforward_breaks=1)
    deals = [deal_outcome("F1", [month], ("a deal note",), loaded_before=[AUG])]
    lines = routine_report(run(deals)).splitlines()
    assert lines[:3] == [
        "1 loan quarantined, 1 roll-forward break",
        "run 12 (refresh deal, started by quinn): completed with problems",
        "F1: up to date through September 2026",
    ]
    assert lines[-1] == "  a deal note"


def test_a_stopped_run_opens_with_where_it_stopped():
    failed = MonthResult(SEP, MonthOutcome.NOT_LOADED, "HTTP 403", "fetching u", failed=True)
    deals = [deal_outcome("F1", [failed], loaded_before=[AUG])]
    lines = routine_report(run(deals)).splitlines()
    assert lines[:4] == [
        "stopped at F1, September 2026: HTTP 403 (fetching u)",
        "1 month not loaded",
        "run 12 (refresh deal, started by quinn): failed",
        "F1: loaded through August 2026, 1 month not loaded",
    ]


def test_run_status_counts_flags_and_roll_forward_breaks_as_problems():
    assert run_status([deal_outcome("F1", [loaded()])]) == "succeeded"
    assert run_status([deal_outcome("F1", [loaded(flags=1)])]) == "completed with problems"
    broken = loaded(rollforward_breaks=1)
    assert run_status([deal_outcome("F1", [broken])]) == "completed with problems"
    assert run_status([deal_outcome("F1", [loaded(rollforward_breaks=None)])]) == "succeeded"
    assert loaded(quarantined=1).problems
    assert not loaded().problems


def test_the_summary_totals_comments_review_items_and_breaks():
    deals = [
        deal_outcome("F1", [loaded(AUG, comments=None, rollforward_breaks=None), loaded()]),
        deal_outcome("F2", [loaded(comments=4, review_items=1, rollforward_breaks=2)]),
    ]
    found = summary(deals)
    assert (found["comments"], found["review_items"], found["rollforward_breaks"]) == (5, 5, 2)
