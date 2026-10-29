"""Overrides and review items when a loan's comment is extracted again.

An override belongs to the loan's field for the month: re-reading the same comment under a new
model keeps it on top; an amended comment does not, and opens a review item instead. Open items
of an extraction a newer one replaced are marked superseded and leave the queue.
"""

import datetime as dt
import re
from decimal import Decimal

import pytest
from sqlalchemy import select

from cmbs import clock
from cmbs.audit.tables import AuditEntry
from cmbs.extraction.questions import WORDING
from cmbs.extraction.service import (
    OVERRIDE_CONFLICT,
    AlreadyDecided,
    comment_view,
    decide_review,
    extract_comments,
    open_review_items,
    review_item,
)
from cmbs.extraction.tables import Extraction, ReviewItem
from cmbs.model.client import ModelClient
from cmbs.model.recordings import DbRecordings
from cmbs.review.service import WaitingForReview, decline, override, queue
from cmbs.settings.service import in_force, store_version
from cmbs.testing.world import FIXTURE_CIK, FIXTURE_DEAL
from tests.conftest import needs_db
from tests.test_review_world import (
    SEPTEMBER,
    Report,
    loan_two,
    scripted,
    store_comments,
)

pytestmark = needs_db
OVERRIDDEN_AT = dt.datetime(2026, 10, 6, 10, tzinfo=dt.UTC)
AGAIN_AT = dt.datetime(2026, 10, 8, 10, tzinfo=dt.UTC)
REASON = "the request is for payment relief, not more time"
AMENDED = (
    "The loan transferred to Special Servicing effective 3/6/2025 due to imminent monetary "
    "default. The borrower has requested a loan modification. The special servicer is "
    "evaluating the request."
)
AMENDMENT = Report(FIXTURE_DEAL, FIXTURE_CIK, "0009900001-26-000777", dt.date(2026, 10, 7))


def _override(db):
    return override(
        db,
        "alex",
        deal=FIXTURE_DEAL,
        loan="2",
        period=SEPTEMBER,
        field="borrower_request",
        value="forbearance",
        sentence=3,
        reason=REASON,
    )


def _request(db, as_of=None):
    view = comment_view(db, "pat", deal=FIXTURE_DEAL, loan="2", period=SEPTEMBER, as_of=as_of)
    assert view is not None
    return view, view.fields["borrower_request"]


def _extract(db, comment: str, answer: str, answer_p: float, sentence: int):
    provider = scripted("borrower_request", answer, answer_p, 0.9, sentence, comment)
    client = ModelClient(DbRecordings(db), provider=provider, replay_only=False)
    return extract_comments(
        db, user="quinn", deal=FIXTURE_DEAL, period=SEPTEMBER, loan="2", client=client
    )


def _new_model(db) -> None:
    """Settings version 8: the same prompt, another pinned model."""
    store_version(
        db,
        version=8,
        values=in_force(db).values.model_copy(update={"model": "clef-0.1.0"}),
        proposed_by="quinn",
        approved_by="rae",
        approved_at=AGAIN_AT - dt.timedelta(hours=1),
    )


@pytest.fixture
def overridden(seeded):
    """Loan 2's borrower request auto-accepted as "maturity extension", then overridden to
    "forbearance" by alex on 2026-10-06."""
    loan_two(seeded, answer="maturity extension", answer_p=0.93)
    seeded.commit()
    clock.set_now(OVERRIDDEN_AT)
    _override(seeded)
    seeded.commit()
    clock.set_now(AGAIN_AT)
    return seeded


def test_an_override_stays_on_top_when_the_same_comment_is_read_again(overridden):
    view, _ = _request(overridden)
    comment = view.comment
    _new_model(overridden)
    _extract(overridden, comment, "maturity extension", 0.93, 3)
    overridden.commit()
    again, request = _request(overridden)
    assert again.model == "clef-0.1.0"
    assert (request.status, request.value, request.overridden_by, request.model_value) == (
        "overridden",
        "forbearance",
        "alex",
        "maturity extension",
    )
    assert open_review_items(overridden, "pat") == []
    # As of the override's day, the first reading is the one shown, overridden as well.
    first, earlier = _request(overridden, as_of=OVERRIDDEN_AT.date())
    assert (first.model, earlier.status, earlier.value) == (
        "jev-1.14.0",
        "overridden",
        "forbearance",
    )


def test_an_amended_comment_does_not_carry_an_override_silently(overridden):
    store_comments(overridden, AMENDMENT, {"2": AMENDED})
    _extract(overridden, AMENDED, "loan modification", 0.93, 2)
    overridden.commit()
    view, request = _request(overridden)
    assert view.accession == AMENDMENT.accession
    # The field shows the new reading's outcome, and waits for a person to settle the conflict.
    assert (request.status, request.value, request.sentence, request.overridden_by) == (
        "auto-accepted",
        "loan modification",
        2,
        None,
    )
    [item] = open_review_items(overridden, "alex")
    assert (item.reason, item.question, item.probability, item.model_value) == (
        OVERRIDE_CONFLICT,
        WORDING["borrower_request"].value,
        Decimal("0.93000"),
        "loan modification",
    )
    assert [entry.item.id for entry in queue(overridden, "alex")] == [item.id]
    with pytest.raises(WaitingForReview):
        _override(overridden)
    overridden.rollback()
    [entry] = overridden.scalars(
        select(AuditEntry)
        .where(AuditEntry.action == "extract comment")
        .order_by(AuditEntry.id.desc())
        .limit(1)
    )
    assert (entry.detail["override_conflicts"], entry.detail["superseded_review_items"]) == (
        ["borrower_request"],
        [],
    )
    # The reviewer settles it; the override still shows for the original comment's day.
    decide_review(overridden, "alex", item.id, value="forbearance", sentence=2)
    overridden.commit()
    assert _request(overridden)[1].status == "human-reviewed"
    assert _request(overridden, as_of=OVERRIDDEN_AT.date())[1].value == "forbearance"


def test_an_amended_comment_unsettled_too_names_both_reasons(overridden):
    store_comments(overridden, AMENDMENT, {"2": AMENDED})
    _extract(overridden, AMENDED, "loan modification", 0.5, 2)
    overridden.commit()
    [item] = open_review_items(overridden, "alex")
    assert item.reason == f"{OVERRIDE_CONFLICT}; unsettled answer"


@pytest.fixture
def waiting(seeded):
    """Loan 2's borrower request waiting for review after the first reading."""
    loan_two(seeded, answer="forbearance", answer_p=0.55)
    seeded.commit()
    clock.set_now(AGAIN_AT)
    [item] = open_review_items(seeded, "alex")
    return item


def test_a_newer_reading_supersedes_the_open_items_of_older_ones(seeded, waiting):
    view, _ = _request(seeded)
    _new_model(seeded)
    _extract(seeded, view.comment, "forbearance", 0.5, 3)
    seeded.commit()
    newest = seeded.scalars(select(Extraction.id).order_by(Extraction.id.desc()).limit(1)).one()
    old = review_item(seeded, "pat", waiting.id)
    assert (old.status, old.superseded_by, old.superseded_at) == ("superseded", newest, AGAIN_AT)
    assert seeded.get(ReviewItem, waiting.id) is not None  # kept, not deleted
    [fresh] = open_review_items(seeded, "alex")
    assert fresh.id != waiting.id
    assert [entry.item.id for entry in queue(seeded, "alex")] == [fresh.id]
    # The day before, the old item was still open, and is shown as it stood.
    [before] = queue(seeded, "alex", on=dt.date(2026, 10, 7))
    assert (before.item.id, before.item.status, before.item.superseded_by) == (
        waiting.id,
        "open",
        None,
    )
    message = f"review item {waiting.id} was superseded by a newer extraction ({newest})"
    with pytest.raises(AlreadyDecided, match=f"^{re.escape(message)}$"):
        decide_review(seeded, "alex", waiting.id, value="forbearance", sentence=3)
    seeded.rollback()
    with pytest.raises(AlreadyDecided, match=f"^{re.escape(message)}$"):
        decline(seeded, "alex", waiting.id, reason="vague")
    seeded.rollback()
    [entry] = seeded.scalars(
        select(AuditEntry)
        .where(AuditEntry.action == "extract comment")
        .order_by(AuditEntry.id.desc())
        .limit(1)
    )
    assert entry.detail["superseded_review_items"] == [waiting.id]


def test_a_decided_item_is_not_superseded(seeded, waiting):
    decide_review(seeded, "alex", waiting.id, value="forbearance", sentence=3)
    seeded.commit()
    view, _ = _request(seeded)
    _new_model(seeded)
    _extract(seeded, view.comment, "forbearance", 0.93, 3)
    seeded.commit()
    assert review_item(seeded, "pat", waiting.id).status == "decided"
