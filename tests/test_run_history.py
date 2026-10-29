"""Runs: progress, finishing, abandoning, the history and how it reads (cmbs.runs.service)."""

import datetime as dt

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import DBAPIError

from cmbs import clock
from cmbs.access.service import PermissionRefused
from cmbs.domain import Period
from cmbs.runs import service as runs
from cmbs.runs.service import RunAlreadyFinished, RunView, UnknownRun
from cmbs.runs.tables import Run
from tests.conftest import needs_db

UTC = dt.UTC


def view(**changes) -> RunView:
    values = {
        "number": 43,
        "kind": "refresh book",
        "started_by": "olly",
        "started_at": dt.datetime(2026, 10, 27, 9, 0, tzinfo=UTC),
        "ended_at": dt.datetime(2026, 10, 27, 9, 4, tzinfo=UTC),
        "status": "succeeded",
        "summary": {
            "deals_refreshed": 2,
            "deals_skipped": 1,
            "deals_failed": 0,
            "loan_rows": 14,
            "alerts_raised": 3,
            "stopped": None,
            "deals": [],
        },
    }
    return RunView(**{**values, **changes})


# ---------------------------------------------------------------------------- how a run reads


def test_the_headline():
    assert view().headline() == (
        "run 43: olly, 2026-10-27 09:00 to 09:04, 2 deals refreshed, 1 skipped, "
        "14 loan rows loaded, 3 alerts raised"
    )


def test_the_headline_says_when_alerts_were_not_built_and_counts_failures():
    summary = {**view().summary, "alerts_raised": None, "deals_failed": 1}
    assert view(summary=summary).headline() == (
        "run 43: olly, 2026-10-27 09:00 to 09:04, 2 deals refreshed, 1 failed, 1 skipped, "
        "14 loan rows loaded, alerts not built"
    )
    assert view(summary={}).headline() == (
        "run 43: olly, 2026-10-27 09:00 to 09:04, 0 deals refreshed, 0 skipped, "
        "0 loan rows loaded, alerts not built"
    )


def test_the_period_shows_the_end_date_only_when_it_differs():
    late = view(ended_at=dt.datetime(2026, 10, 28, 0, 30, tzinfo=UTC))
    assert late.period() == "2026-10-27 09:00 to 2026-10-28 00:30"
    assert view(ended_at=None).period() == "2026-10-27 09:00, still running"
    # Shown in UTC whatever zone the times carry.
    eastern = dt.timezone(dt.timedelta(hours=-4))
    shifted = view(
        started_at=dt.datetime(2026, 10, 27, 5, 0, tzinfo=eastern),
        ended_at=dt.datetime(2026, 10, 27, 5, 4, tzinfo=eastern),
    )
    assert shifted.period() == "2026-10-27 09:00 to 09:04"


def test_the_text_lists_where_it_stopped_and_every_deal():
    summary = {
        **view().summary,
        "stopped": {
            "deal": "Fixture Trust 2026-F2",
            "period": "October 2026",
            "error": "10-D failed to parse",
            "detail": "not a report",
        },
        "deals": [
            {
                "deal": "Fixture Trust 2026-F2",
                "outcome": "failed",
                "reason": None,
                "months": [
                    {
                        "period": "September 2026",
                        "outcome": "loaded",
                        "loan_rows": 7,
                        "quarantined": 1,
                        "flags": 2,
                    },
                    {
                        "period": "October 2026",
                        "outcome": "not loaded",
                        "reason": "10-D failed to parse",
                        "detail": "not a report",
                    },
                    {
                        "period": "November 2026",
                        "outcome": "not loaded",
                        "reason": "no ABS-EE filed for November 2026",
                        "detail": None,
                    },
                ],
            },
            {"deal": "HYT 2024-RGCY", "outcome": "skipped", "reason": "not publicly reported"},
        ],
    }
    assert view(status="failed", summary=summary).text().splitlines()[1:] == [
        "refresh book, failed",
        "stopped at Fixture Trust 2026-F2, October 2026: 10-D failed to parse (not a report)",
        "Fixture Trust 2026-F2: failed",
        "  September 2026: loaded, 7 loan rows, 1 loan quarantined, 2 reconciliation flags",
        "  October 2026: not loaded, 10-D failed to parse (not a report)",
        "  November 2026: not loaded, no ABS-EE filed for November 2026",
        "HYT 2024-RGCY: skipped, not publicly reported",
    ]


def test_the_text_of_a_run_that_stopped_before_any_deal():
    summary = {"stopped": {"deal": None, "period": None, "error": "RuntimeError: boom"}}
    assert view(status="failed", summary=summary).text().splitlines()[1:] == [
        "refresh book, failed",
        "stopped: RuntimeError: boom",
    ]
    only_deal = {"stopped": {"deal": "F1", "period": None, "error": "x", "detail": None}}
    assert view(summary=only_deal).text().splitlines()[2] == "stopped at F1: x"


def test_month_bounds():
    assert runs.month_bounds(Period(2025, 10)) == (dt.date(2025, 10, 1), dt.date(2025, 10, 31))
    assert runs.month_bounds(Period(2026, 2)) == (dt.date(2026, 2, 1), dt.date(2026, 2, 28))
    assert runs.month_bounds(Period(2026, 12)) == (dt.date(2026, 12, 1), dt.date(2026, 12, 31))


# ---------------------------------------------------------------------------- the database


def started(db, at: dt.datetime, user: str = "olly", kind: str = "refresh book") -> Run:
    clock.set_now(at)
    run = runs.start_run(db, kind=kind, started_by=user)
    db.commit()
    return run


@needs_db
def test_progress_then_finish(seeded):
    run = started(seeded, dt.datetime(2026, 10, 27, 9, 0, tzinfo=UTC))
    runs.record_progress(seeded, run, {"loan_rows": 7})
    assert (run.status, run.summary, run.ended_at) == ("running", {"loan_rows": 7}, None)
    clock.set_now(dt.datetime(2026, 10, 27, 9, 4, tzinfo=UTC))
    runs.finish_run(seeded, run, status="succeeded", summary={"loan_rows": 14})
    assert (run.status, run.summary) == ("succeeded", {"loan_rows": 14})
    assert run.ended_at == dt.datetime(2026, 10, 27, 9, 4, tzinfo=UTC)
    with pytest.raises(RunAlreadyFinished, match=f"^run {run.id} already finished as 'succeeded'$"):
        runs.record_progress(seeded, run, {})
    with pytest.raises(ValueError, match="a run cannot finish as 'running'"):
        runs.finish_run(seeded, run, status="running", summary={})
    with pytest.raises(ValueError, match="a run cannot finish as 'done'"):
        runs.finish_run(seeded, run, status="done", summary={})


@needs_db
def test_abandon_keeps_what_the_run_recorded(seeded):
    run = started(seeded, dt.datetime(2026, 10, 27, 9, 0, tzinfo=UTC))
    at = {"deal": "Fixture Trust 2026-F2", "period": "October 2026"}
    runs.record_progress(seeded, run, {"loan_rows": 7, "stopped": None, "at": at})
    runs.abandon(seeded, run, "stopped without finishing")
    assert run.status == "failed"
    assert run.summary == {
        "loan_rows": 7,
        "at": at,
        "stopped": {
            "deal": "Fixture Trust 2026-F2",
            "period": "October 2026",
            "error": "stopped without finishing",
            "detail": None,
        },
    }
    assert RunView.of(run).text().splitlines()[2] == (
        "stopped at Fixture Trust 2026-F2, October 2026: stopped without finishing"
    )
    other = started(seeded, dt.datetime(2026, 10, 27, 9, 0, tzinfo=UTC))
    runs.abandon(seeded, other, "gone")  # it never recorded where it was
    assert other.summary == {
        "stopped": {"deal": None, "period": None, "error": "gone", "detail": None}
    }
    with pytest.raises(RunAlreadyFinished):
        runs.abandon(seeded, other, "again")


@needs_db
def test_the_history_by_day_newest_first(seeded):
    first = started(seeded, dt.datetime(2025, 10, 1, 9, 0, tzinfo=UTC))
    second = started(seeded, dt.datetime(2025, 10, 31, 23, 59, tzinfo=UTC), kind="backfill")
    third = started(seeded, dt.datetime(2025, 11, 1, 0, 0, tzinfo=UTC))
    start, end = runs.month_bounds(Period(2025, 10))
    october = runs.history(seeded, "olly", start=start, end=end)
    assert [v.number for v in october] == [second.id, first.id]
    assert october[0] == RunView.of(second)
    assert [v.number for v in runs.history(seeded, "rae")] == [third.id, second.id, first.id]
    assert [v.number for v in runs.history(seeded, "olly", start=dt.date(2025, 11, 1))] == [
        third.id
    ]
    assert [v.number for v in runs.history(seeded, "olly", end=dt.date(2025, 10, 1))] == [first.id]


@needs_db
def test_reading_runs_needs_the_right(seeded):
    run = started(seeded, dt.datetime(2026, 10, 27, 9, 0, tzinfo=UTC))
    assert runs.read_run(seeded, "olly", run.id).number == run.id
    with pytest.raises(PermissionRefused, match="^pm may not see run history$"):
        runs.read_run(seeded, "pat", run.id)
    with pytest.raises(PermissionRefused, match="^quant may not see run history$"):
        runs.history(seeded, "quinn")
    with pytest.raises(UnknownRun, match="^there is no run 999$"):
        runs.read_run(seeded, "rae", 999)


@needs_db
def test_runs_are_never_deleted(seeded):
    started(seeded, dt.datetime(2025, 10, 1, 9, 0, tzinfo=UTC))
    with pytest.raises(DBAPIError, match="runs are kept: the run history is never deleted"):
        seeded.execute(delete(Run))
    seeded.rollback()
    with pytest.raises(DBAPIError, match="runs are kept"):
        seeded.execute(text("TRUNCATE app.run CASCADE"))
    seeded.rollback()
    assert len(runs.history(seeded, "olly")) == 1
