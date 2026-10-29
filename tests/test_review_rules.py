"""The review package's pure rules: business days, overdue marks, order, summary, override checks
and label candidates. No database."""

import datetime as dt
from decimal import Decimal

import pytest

from cmbs.book.service import Book, Held
from cmbs.domain import Period
from cmbs.extraction import fields
from cmbs.extraction.service import NoSuchSentence, ReviewView
from cmbs.extraction.tables import ExtractedField
from cmbs.review.service import (
    NoSentence,
    QueueEntry,
    QueueSummary,
    ReasonRequired,
    Shown,
    Unchanged,
    UnknownField,
    WaitingForReview,
    business_days,
    candidate,
    clean_reason,
    end_of_day,
    exposure_to,
    field_spec,
    is_overdue,
    override_problem,
    queue_order,
    render_queue,
    render_queue_summary,
    shown,
    summarize,
    waiting_text,
)
from cmbs.review.tables import FieldOverride

D = dt.date
REQUEST = fields.spec("borrower_request")


@pytest.mark.parametrize(
    ("start", "end", "days"),
    [
        (D(2026, 10, 1), D(2026, 10, 6), 3),  # Thursday to Tuesday: Fri, Mon, Tue
        (D(2026, 10, 5), D(2026, 10, 6), 1),
        (D(2026, 9, 26), D(2026, 10, 6), 7),  # from a Saturday: Monday counts first
        (D(2026, 10, 6), D(2026, 10, 6), 0),
        (D(2026, 10, 6), D(2026, 10, 1), 0),  # never negative
        (D(2026, 10, 2), D(2026, 10, 4), 0),  # Friday to Sunday
        (D(2026, 10, 2), D(2026, 10, 5), 1),  # Friday to Monday
        (D(2026, 10, 3), D(2026, 10, 10), 5),  # Saturday to Saturday
        (D(2026, 9, 1), D(2026, 9, 30), 21),
    ],
)
def test_business_days_count_weekdays_after_the_start(start, end, days):
    assert business_days(start, end) == days


def test_overdue_is_more_than_five_business_days():
    assert [is_overdue(days) for days in (0, 5, 6, 30)] == [False, False, True, True]


@pytest.mark.parametrize(
    ("waited", "text"),
    [
        (0, "waiting less than a business day"),
        (1, "waiting 1 business day"),
        (5, "waiting 5 business days"),
        (6, "overdue, waiting 6 business days"),
        (7, "overdue, waiting 7 business days"),
    ],
)
def test_waiting_text(waited, text):
    assert waiting_text(waited) == text


def _book(*held: tuple[int, str, str]) -> Book:
    return Book(
        file_id=1,
        as_of=D(2026, 9, 30),
        positions=tuple(
            Held(deal, f"deal {deal}", tranche, Decimal(face)) for deal, tranche, face in held
        ),
    )


def test_exposure_is_the_face_held_in_the_deals_counted_once():
    book = _book((1, "E", "20000000.00"), (1, "F", "5000000.00"), (2, "D", "40000000.00"))
    assert exposure_to(book, [1]) == Decimal("25000000.00")
    assert exposure_to(book, [1, 2, 1]) == Decimal("65000000.00")
    assert exposure_to(book, [3]) == Decimal("0.00")
    assert exposure_to(None, [1]) == Decimal("0.00")


def test_end_of_day_is_the_next_midnight_utc():
    assert end_of_day(D(2026, 9, 30)) == dt.datetime(2026, 10, 1, tzinfo=dt.UTC)


@pytest.mark.parametrize(
    ("reason", "cleaned"),
    [("  too  vague\n", "too vague"), ("x", "x")],
)
def test_a_reason_is_kept_with_whitespace_collapsed(reason, cleaned):
    assert clean_reason(reason) == cleaned


@pytest.mark.parametrize("reason", [None, "", "  \n "])
def test_a_missing_reason_is_refused(reason):
    with pytest.raises(ReasonRequired, match="^a reason is required$"):
        clean_reason(reason)


def test_fields_are_named_by_name_or_label():
    assert field_spec("borrower_request") is REQUEST
    assert field_spec("borrower  request") is REQUEST
    assert field_spec("DSCR").name == "dscr"
    with pytest.raises(UnknownField, match="^there is no field named 'tenant'$"):
        field_spec("tenant")


def _view(item_id: int, created: dt.datetime, field: str = "borrower_request") -> ReviewView:
    spec = fields.spec(field)
    return ReviewView(
        id=item_id,
        deal="Fixture Trust 2026-F1",
        loan=str(item_id),
        period=Period(2026, 9),
        field=field,
        label=spec.label,
        reason="unsettled answer",
        question="q",
        probability=Decimal("0.5"),
        model_value=None,
        comment="c",
        sentences=["c"],
        created_at=created,
        status="open",
        decided_by=None,
        decided_at=None,
        decision=None,
        decision_sentence=None,
    )


def _entry(item_id, exposure, created, waited=0, field="borrower_request") -> QueueEntry:
    return QueueEntry(
        item=_view(item_id, created, field),
        exposure=Decimal(exposure),
        waiting_since=created.date(),
        waited=waited,
    )


T = dt.datetime(2026, 10, 1, 10, tzinfo=dt.UTC)


def test_the_queue_orders_by_exposure_then_age_then_number():
    entries = [
        _entry(1, "25000000.00", T),
        _entry(2, "40000000.00", T + dt.timedelta(days=4)),
        _entry(3, "25000000.00", T - dt.timedelta(days=5)),
        _entry(4, "25000000.00", T),
        _entry(5, "0.00", T - dt.timedelta(days=30)),
    ]
    assert [e.item.id for e in sorted(entries, key=queue_order)] == [2, 3, 1, 4, 5]


def test_an_entry_is_marked_from_its_business_days():
    late = _entry(1, "1", T, waited=6)
    assert (late.overdue, late.mark) == (True, "overdue, waiting 6 business days")
    fresh = _entry(2, "1", T, waited=5)
    assert (fresh.overdue, fresh.mark) == (False, "waiting 5 business days")


def test_the_summary_counts_open_overdue_oldest_and_fields_in_field_order():
    entries = [
        _entry(1, "1", T, waited=3),
        _entry(2, "1", T - dt.timedelta(days=10), waited=8, field="transfer_reason"),
        _entry(3, "1", T - dt.timedelta(days=10), waited=8),
        _entry(4, "1", T + dt.timedelta(days=4), waited=6),
    ]
    summary = summarize(entries, D(2026, 10, 6))
    assert summary == QueueSummary(
        on=D(2026, 10, 6),
        open=4,
        overdue=3,
        oldest_since=D(2026, 9, 21),
        oldest_waited=8,
        by_field={"transfer reason": 1, "borrower request": 3},
    )
    assert list(summary.by_field) == ["transfer reason", "borrower request"]
    assert render_queue_summary(summary) == (
        "4 open items on 2026-10-06, 3 overdue\n"
        "oldest waiting since 2026-09-21 (overdue, waiting 8 business days)\n"
        "transfer reason: 1\nborrower request: 3\n"
    )


def test_an_empty_queue_summary():
    summary = summarize([], D(2026, 10, 6))
    assert (summary.open, summary.overdue, summary.oldest_since, summary.oldest_waited) == (
        0,
        0,
        None,
        None,
    )
    assert summary.by_field == {}
    assert render_queue_summary(summary) == "0 open items on 2026-10-06, 0 overdue\n"
    one = summarize([_entry(1, "1", T, waited=0)], D(2026, 10, 1))
    assert render_queue_summary(one) == (
        "1 open item on 2026-10-01, 0 overdue\n"
        "oldest waiting since 2026-10-01 (waiting less than a business day)\n"
        "borrower request: 1\n"
    )


def test_render_queue():
    assert render_queue([]) == "the review queue is empty\n"
    assert render_queue([_entry(7, "25000000", T, waited=7)]) == (
        "item 7: loan 7 of Fixture Trust 2026-F1, September 2026, borrower request "
        "(unsettled answer); exposure $25,000,000.00; since 2026-10-01, "
        "overdue, waiting 7 business days\n"
    )


ACCEPTED = Shown("auto-accepted", "maturity extension", 3)


def _problem(value="forbearance", sentence=3, sentences=4, current=ACCEPTED):
    return override_problem(
        REQUEST, loan="2", value=value, sentence=sentence, sentences=sentences, current=current
    )


def test_an_override_with_a_new_value_or_sentence_has_no_problem():
    assert _problem() is None
    assert _problem(value="maturity extension", sentence=4) is None
    assert _problem(sentence=1) is None and _problem(sentence=4) is None
    assert _problem(current=Shown("not stated", None, None)) is None
    assert _problem(current=Shown("not determinable", None, None)) is None


@pytest.mark.parametrize(
    ("options", "error", "message"),
    [
        (
            {"sentence": None},
            NoSentence,
            "an override cites the sentence that supports the borrower request",
        ),
        ({"sentence": 0}, NoSuchSentence, "sentence 0 does not exist in this comment"),
        ({"sentence": 5}, NoSuchSentence, "sentence 5 does not exist in this comment"),
        (
            {"current": Shown("needs review", None, None)},
            WaitingForReview,
            "loan 2's borrower request is waiting for review; decide its review item instead",
        ),
        (
            {"value": "maturity extension"},
            Unchanged,
            "loan 2's borrower request is already maturity extension, citing sentence 3",
        ),
    ],
)
def test_override_problems(options, error, message):
    problem = _problem(**options)
    assert isinstance(problem, error)
    assert str(problem) == message


def test_an_unchanged_figure_is_named_as_the_panel_shows_it():
    dscr = fields.spec("dscr")
    problem = override_problem(
        dscr,
        loan="2",
        value="0.71",
        sentence=2,
        sentences=4,
        current=Shown("human-reviewed", "0.71", 2),
    )
    assert str(problem) == "loan 2's DSCR is already 0.71x, citing sentence 2"


AT = dt.datetime(2026, 10, 6, 10, tzinfo=dt.UTC)


def _row(**values):
    defaults = {
        "status": "auto-accepted",
        "value": "maturity extension",
        "sentence": 3,
        "decided_by": None,
        "decided_at": None,
    }
    return ExtractedField(field="borrower_request", **(defaults | values))


def _override(**values):
    defaults = {"value": "forbearance", "sentence": 3, "overridden_by": "alex", "overridden_at": AT}
    return FieldOverride(**(defaults | values))


def test_what_the_panel_shows_before_an_override():
    assert shown(_row(), None, declined=False) == ACCEPTED
    assert shown(_row(), _override(), declined=False) == Shown("overridden", "forbearance", 3)
    declined = _row(
        status="not stated", value=None, sentence=None, decided_by="alex", decided_at=AT
    )
    assert shown(declined, None, declined=True) == Shown("not determinable", None, None)


def test_label_candidates_come_from_people():
    reviewed = _row(status="human-reviewed", value="forbearance", decided_by="alex", decided_at=AT)
    not_stated = _row(
        status="not stated", value=None, sentence=None, decided_by="morgan", decided_at=AT
    )
    declined = _row(
        status="not stated", value=None, sentence=None, decided_by="alex", decided_at=AT
    )
    assert candidate(_row(), None, declined=False) is None
    assert candidate(reviewed, None, declined=False) == ("review", "forbearance", 3, "alex", AT)
    assert candidate(not_stated, None, declined=False) == (
        "review",
        "not stated",
        None,
        "morgan",
        AT,
    )
    assert candidate(declined, None, declined=True) is None
    later = _override(value="loan modification", sentence=2, overridden_by="morgan")
    assert candidate(declined, later, declined=True) == (
        "override",
        "loan modification",
        2,
        "morgan",
        AT,
    )
    assert candidate(_row(), _override(), declined=False) == (
        "override",
        "forbearance",
        3,
        "alex",
        AT,
    )
