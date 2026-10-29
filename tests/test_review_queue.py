"""The review queue against Postgres: order by exposure and age, overdue marks, declines, the
summary, whole loans and point in time."""

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select

from cmbs import clock
from cmbs.access.service import PermissionRefused
from cmbs.audit.tables import AuditEntry
from cmbs.book.tables import WholeLoan, WholeLoanNote
from cmbs.extraction.service import (
    AlreadyDecided,
    UnknownItem,
    comment_view,
    decide_review,
    review_item,
)
from cmbs.extraction.tables import ExtractedField, ReviewItem
from cmbs.review.service import (
    ReasonRequired,
    decline,
    declined_reason,
    queue,
    queue_summary,
)
from cmbs.review.tables import ReviewDecline
from cmbs.testing.world import FIXTURE_DEAL
from tests.conftest import needs_db
from tests.test_review_world import QUEUE, SECOND_DEAL, SEPTEMBER, open_queue

pytestmark = needs_db
ON = dt.date(2026, 10, 6)
NOW = dt.datetime(2026, 10, 6, 9, tzinfo=dt.UTC)
REASON = "the comment does not say what was requested"


@pytest.fixture
def items(seeded) -> dict[str, int]:
    ids = open_queue(seeded)
    seeded.commit()
    clock.set_now(NOW)
    return ids


def _letters(items, entries) -> list[str]:
    by_id = {item_id: letter for letter, item_id in items.items()}
    return [by_id[entry.item.id] for entry in entries]


def test_the_queue_is_ordered_by_exposure_then_age_with_marks(seeded, items):
    entries = queue(seeded, "alex")
    assert _letters(items, entries) == ["B", "C", "A"]
    assert [(e.item.loan, e.item.deal, e.exposure) for e in entries] == [
        ("3", SECOND_DEAL, Decimal("40000000.00")),
        ("5", FIXTURE_DEAL, Decimal("25000000.00")),
        ("2", FIXTURE_DEAL, Decimal("25000000.00")),
    ]
    assert [(e.waiting_since, e.waited, e.overdue, e.mark) for e in entries] == [
        (dt.date(2026, 10, 5), 1, False, "waiting 1 business day"),
        (dt.date(2026, 9, 26), 7, True, "overdue, waiting 7 business days"),
        (dt.date(2026, 10, 1), 3, False, "waiting 3 business days"),
    ]
    assert queue(seeded, "pat", on=ON) == entries


def test_the_queue_is_read_point_in_time(seeded, items):
    # On 2026-09-30 only C had arrived, and the book (dated 2026-09-30) was already loaded.
    [only] = queue(seeded, "alex", on=dt.date(2026, 9, 30))
    assert (only.item.id, only.waited, only.exposure) == (items["C"], 3, Decimal("25000000.00"))
    # Before the book, exposure is zero.
    assert queue(seeded, "alex", on=dt.date(2026, 9, 26))[0].exposure == Decimal("0.00")
    # An item decided on 2026-10-06 was still open at the end of 2026-10-05.
    planned = QUEUE["B"]
    decide_review(seeded, "alex", items["B"], value=planned.answer, sentence=planned.sentence)
    seeded.commit()
    assert _letters(items, queue(seeded, "alex", on=dt.date(2026, 10, 5))) == ["B", "C", "A"]
    assert _letters(items, queue(seeded, "alex")) == ["C", "A"]


def test_a_whole_loan_counts_every_deal_holding_a_note(seeded, items):
    # The link service needs both loans on a loaded tape, which this world has not; the link is
    # stored directly, as the service would store it after its checks.
    whole = WholeLoan(linked_by="quinn", linked_at=NOW)
    seeded.add(whole)
    seeded.flush()
    for letter in ("A", "B"):
        item = seeded.get_one(ReviewItem, items[letter])
        seeded.add(WholeLoanNote(deal_id=item.deal_id, loan=item.loan_id, whole_loan_id=whole.id))
    seeded.commit()
    entries = queue(seeded, "alex")
    # A and B now share 65m; A has waited longer.
    assert _letters(items, entries) == ["A", "B", "C"]
    assert [entry.exposure for entry in entries] == [
        Decimal("65000000.00"),
        Decimal("65000000.00"),
        Decimal("25000000.00"),
    ]


def test_reading_the_queue_needs_the_panel(seeded, items):
    with pytest.raises(PermissionRefused, match="^operator may not read the panel$"):
        queue(seeded, "olly")


def test_declining_marks_the_field_not_determinable_and_closes_the_item(seeded, items):
    clock.set_now(dt.datetime(2026, 10, 6, 11, tzinfo=dt.UTC))
    declined = decline(seeded, "alex", items["A"], reason=f"  {REASON} ")
    seeded.commit()
    assert (declined.status, declined.value, declined.sentence, declined.marked) == (
        "not determinable",
        None,
        None,
        "not determinable by alex",
    )
    view = comment_view(seeded, "pat", deal=FIXTURE_DEAL, loan="2", period=SEPTEMBER)
    assert view is not None
    request = view.fields["borrower_request"]
    assert (request.status, request.value, request.decided_by, request.model_value) == (
        "not determinable",
        None,
        "alex",
        "loan modification",
    )
    item = review_item(seeded, "pat", items["A"])
    assert (item.status, item.decision, item.decided_by, item.decision_sentence) == (
        "decided",
        "not determinable",
        "alex",
        None,
    )
    assert declined_reason(seeded, "pat", items["A"]) == REASON
    assert declined_reason(seeded, "pat", items["B"]) is None
    assert _letters(items, queue(seeded, "morgan")) == ["B", "C"]
    [entry] = seeded.scalars(select(AuditEntry).where(AuditEntry.action == "decline review item"))
    assert (
        entry.user_name,
        entry.role,
        entry.subject,
        entry.subject_key,
        entry.field,
        entry.before,
        entry.after,
        entry.reason,
    ) == (
        "alex",
        "analyst",
        "loan",
        f"{FIXTURE_DEAL}/2",
        "borrower request",
        "",
        "not determinable",
        REASON,
    )
    assert entry.detail == {
        "review_item": items["A"],
        "accession": "0009900001-26-000301",
        "period": "2026-09-01",
        "review_reason": "unsettled answer",
    }
    # Before the decline, the field was waiting for review.
    before = comment_view(
        seeded, "pat", deal=FIXTURE_DEAL, loan="2", period=SEPTEMBER, as_of=dt.date(2026, 10, 5)
    )
    assert before is not None and before.fields["borrower_request"].status == "needs review"


def _refusals(db) -> list[tuple[str, str, str | None, str | None]]:
    rows = db.scalars(select(AuditEntry).where(AuditEntry.outcome == "refused"))
    return [(e.user_name, e.action, e.subject_key, e.reason) for e in rows]


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_a_decline_needs_a_reason(seeded, items, reason):
    with pytest.raises(ReasonRequired, match="^a reason is required$"):
        decline(seeded, "alex", items["A"], reason=reason)
    seeded.rollback()
    assert seeded.get(ReviewItem, items["A"]).status == "open"
    assert _refusals(seeded) == [
        ("alex", "decline review item", str(items["A"]), "a reason is required")
    ]


def test_a_pm_cannot_decline(seeded, items):
    with pytest.raises(PermissionRefused, match="^pm may not decide review items$"):
        decline(seeded, "pat", items["A"], reason=REASON)


def test_a_decided_or_unknown_item_cannot_be_declined(seeded, items):
    planned = QUEUE["B"]
    decide_review(seeded, "alex", items["B"], value=planned.answer, sentence=planned.sentence)
    seeded.commit()
    with pytest.raises(
        AlreadyDecided, match=f"^review item {items['B']} was already decided by alex$"
    ):
        decline(seeded, "morgan", items["B"], reason=REASON)
    seeded.rollback()
    with pytest.raises(UnknownItem, match="^there is no review item 999$"):
        decline(seeded, "alex", 999, reason=REASON)
    seeded.rollback()
    assert [r[2] for r in _refusals(seeded)] == [str(items["B"]), "999"]
    assert seeded.scalars(select(ReviewDecline)).all() == []
    field = seeded.scalars(
        select(ExtractedField).where(
            ExtractedField.id == seeded.get(ReviewItem, items["B"]).field_id
        )
    ).one()
    assert (field.status, field.value) == ("human-reviewed", "forbearance")


def test_the_summary_for_risk(seeded, items):
    summary = queue_summary(seeded, "rae", on=ON)
    assert (
        summary.on,
        summary.open,
        summary.overdue,
        summary.oldest_since,
        summary.oldest_waited,
    ) == (
        ON,
        3,
        1,
        dt.date(2026, 9, 26),
        7,
    )
    assert summary.by_field == {"transfer reason": 1, "borrower request": 2}
    decline(seeded, "alex", items["C"], reason="no reason given for the transfer")
    seeded.commit()
    after = queue_summary(seeded, "rae")
    assert (after.on, after.open, after.overdue, after.oldest_since) == (
        ON,
        2,
        0,
        dt.date(2026, 10, 1),
    )
