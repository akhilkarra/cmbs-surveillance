"""Overrides against Postgres: the value replaced and the original kept, point in time, refusals
audited, history, the CLI view, and label candidates."""

import datetime as dt

import pytest
from sqlalchemy import select

from cmbs import clock
from cmbs.access.service import PermissionRefused
from cmbs.audit.tables import AuditEntry
from cmbs.extraction.fields import InvalidValue
from cmbs.extraction.service import (
    NoSuchSentence,
    UnknownDeal,
    comment_view,
    decide_review,
    open_review_items,
    render_view,
)
from cmbs.extraction.tables import ExtractedField
from cmbs.review.service import (
    NoExtraction,
    NoSentence,
    ReasonRequired,
    Unchanged,
    UnknownField,
    WaitingForReview,
    decline,
    label_candidates,
    override,
    override_history,
)
from cmbs.review.tables import FieldOverride
from cmbs.testing.world import FIXTURE_DEAL
from tests.conftest import needs_db
from tests.test_review_world import SEPTEMBER, loan_two

pytestmark = needs_db
OVERRIDDEN_AT = dt.datetime(2026, 10, 6, 10, tzinfo=dt.UTC)
REASON = "the request is for payment relief, not more time"
EVIDENCE = "The borrower has requested a 12-month maturity extension."


@pytest.fixture
def accepted(seeded):
    """Loan 2's borrower request auto-accepted as "maturity extension", sentence 3."""
    loan_two(seeded, answer="maturity extension", answer_p=0.93)
    seeded.commit()
    clock.set_now(OVERRIDDEN_AT)
    return seeded


def _override(db, user="alex", **options):
    values = {
        "deal": FIXTURE_DEAL,
        "loan": "2",
        "period": SEPTEMBER,
        "field": "borrower request",
        "value": "forbearance",
        "sentence": 3,
        "reason": REASON,
    }
    return override(db, user, **(values | options))


def _request(db, as_of=None):
    view = comment_view(db, "pat", deal=FIXTURE_DEAL, loan="2", period=SEPTEMBER, as_of=as_of)
    assert view is not None
    return view.fields["borrower_request"]


def test_an_override_replaces_the_value_and_keeps_the_original(accepted):
    made = _override(accepted)
    accepted.commit()
    assert (made.status, made.value, made.display, made.sentence, made.evidence) == (
        "overridden",
        "forbearance",
        "forbearance",
        3,
        EVIDENCE,
    )
    assert (made.overridden_by, made.overridden_at, made.override_reason, made.marked) == (
        "alex",
        OVERRIDDEN_AT,
        REASON,
        "overridden by alex",
    )
    assert _request(accepted) == made
    assert made.model_value == "maturity extension"
    row = accepted.scalars(
        select(ExtractedField).where(ExtractedField.field == "borrower_request")
    ).one()
    assert (row.status, row.value, row.sentence, row.decided_by) == (
        "auto-accepted",
        "maturity extension",
        3,
        None,
    )
    assert row.model_evidence == EVIDENCE
    [stored] = accepted.scalars(select(FieldOverride)).all()
    assert (
        stored.value,
        stored.sentence,
        stored.reason,
        stored.overridden_by,
        stored.before_status,
        stored.before_value,
        stored.before_sentence,
    ) == ("forbearance", 3, REASON, "alex", "auto-accepted", "maturity extension", 3)
    [entry] = accepted.scalars(select(AuditEntry).where(AuditEntry.action == "override value"))
    assert (
        entry.user_name,
        entry.role,
        entry.subject,
        entry.subject_key,
        entry.field,
        entry.before,
        entry.after,
        entry.reason,
        entry.at,
    ) == (
        "alex",
        "analyst",
        "loan",
        f"{FIXTURE_DEAL}/2",
        "borrower request",
        "maturity extension",
        "forbearance",
        REASON,
        OVERRIDDEN_AT,
    )
    assert entry.detail == {
        "override": stored.id,
        "sentence": 3,
        "before_status": "auto-accepted",
        "before_sentence": 3,
        "model_value": "maturity extension",
        "accession": "0009900001-26-000009",
        "period": "2026-09-01",
    }


def test_an_override_is_visible_from_when_it_was_made(accepted):
    _override(accepted)
    accepted.commit()
    before = _request(accepted, as_of=dt.date(2026, 10, 5))
    assert (before.status, before.value, before.overridden_by) == (
        "auto-accepted",
        "maturity extension",
        None,
    )
    on_the_day = _request(accepted, as_of=dt.date(2026, 10, 6))
    assert (on_the_day.status, on_the_day.value) == ("overridden", "forbearance")


def test_a_later_override_supersedes_and_records_what_it_replaced(accepted):
    _override(accepted)
    accepted.commit()
    clock.set_now(OVERRIDDEN_AT + dt.timedelta(days=1))
    again = _override(accepted, user="morgan", value="loan modification", reason="modification")
    accepted.commit()
    assert (again.value, again.overridden_by, again.marked) == (
        "loan modification",
        "morgan",
        "overridden by morgan",
    )
    history = override_history(accepted, "pat", deal=FIXTURE_DEAL, loan="2", period=SEPTEMBER)
    assert [(h.by, h.before_status, h.before_display, h.display, h.reason) for h in history] == [
        ("alex", "auto-accepted", "maturity extension", "forbearance", REASON),
        ("morgan", "overridden", "forbearance", "loan modification", "modification"),
    ]
    assert [(h.field, h.label, h.sentence, h.evidence) for h in history] == [
        ("borrower_request", "borrower request", 3, EVIDENCE)
    ] * 2
    assert _request(accepted, as_of=dt.date(2026, 10, 6)).value == "forbearance"
    assert _request(accepted).value == "loan modification"
    # The latest override wins even when two share a moment: the later one stored.
    third = _override(accepted, value="forbearance", reason="back again")
    assert third.value == "forbearance"


def test_the_cli_view_shows_who_overrode_and_why(accepted):
    _override(accepted)
    view = comment_view(accepted, "pat", deal=FIXTURE_DEAL, loan="2", period=SEPTEMBER)
    assert view is not None
    assert f"borrower request: forbearance (overridden, sentence 3 by alex: {REASON})" in (
        render_view(view)
    )


def _refusals(db) -> list[tuple[str, str, str | None, str | None]]:
    rows = db.scalars(select(AuditEntry).where(AuditEntry.outcome == "refused"))
    return [(e.user_name, e.action, e.subject_key, e.reason) for e in rows]


@pytest.mark.parametrize(
    ("options", "error", "message"),
    [
        ({"reason": None}, ReasonRequired, "a reason is required"),
        ({"reason": "  "}, ReasonRequired, "a reason is required"),
        ({"sentence": 9}, NoSuchSentence, "sentence 9 does not exist in this comment"),
        (
            {"sentence": None},
            NoSentence,
            "an override cites the sentence that supports the borrower request",
        ),
        ({"field": "tenant"}, UnknownField, "there is no field named 'tenant'"),
        (
            {"value": "payment holiday"},
            InvalidValue,
            "'payment holiday' is not a borrower request; choose one of: maturity extension, "
            "forbearance, loan modification, discounted payoff, loan assumption, collateral "
            "release, other",
        ),
        (
            {"value": "maturity extension"},
            Unchanged,
            "loan 2's borrower request is already maturity extension, citing sentence 3",
        ),
    ],
)
def test_bad_overrides_are_refused_audited_and_change_nothing(accepted, options, error, message):
    with pytest.raises(error) as refused:
        _override(accepted, **options)
    assert str(refused.value) == message
    accepted.rollback()
    assert accepted.scalars(select(FieldOverride)).all() == []
    assert _refusals(accepted) == [("alex", "override value", f"{FIXTURE_DEAL}/2", message)]
    assert _request(accepted).value == "maturity extension"


def test_a_pm_cannot_override(accepted):
    with pytest.raises(PermissionRefused, match="^pm may not override values$"):
        _override(accepted, user="pat")
    accepted.rollback()
    assert _refusals(accepted) == [("pat", "override values", None, "pm may not override values")]


def test_an_unknown_deal_or_a_month_without_an_extraction_is_refused(accepted):
    with pytest.raises(UnknownDeal, match="^no deal named 'No Such Trust'$"):
        override(
            accepted,
            "alex",
            deal="No Such Trust",
            loan="2",
            period=SEPTEMBER,
            field="borrower_request",
            value="forbearance",
            sentence=3,
            reason=REASON,
        )
    accepted.rollback()
    with pytest.raises(NoExtraction) as refused:
        _override(accepted, loan="7")
    assert str(refused.value) == (
        f"loan 7 of {FIXTURE_DEAL} has no extracted comment for September 2026"
    )
    accepted.rollback()
    assert [r[2] for r in _refusals(accepted)] == ["No Such Trust/2", f"{FIXTURE_DEAL}/7"]


def test_no_override_before_the_comment_is_public(accepted):
    clock.set_now(dt.datetime(2026, 9, 28, tzinfo=dt.UTC))  # the 10-D is filed on 2026-09-29
    with pytest.raises(NoExtraction):
        _override(accepted)


@pytest.fixture
def waiting(seeded):
    loan_two(seeded, answer="forbearance", answer_p=0.55)
    seeded.commit()
    clock.set_now(OVERRIDDEN_AT)
    [item] = open_review_items(seeded, "alex")
    return item


def test_a_field_waiting_for_review_is_decided_not_overridden(seeded, waiting):
    with pytest.raises(WaitingForReview) as refused:
        _override(seeded)
    assert str(refused.value) == (
        "loan 2's borrower request is waiting for review; decide its review item instead"
    )


def test_reviewed_and_declined_fields_can_be_overridden(seeded, waiting):
    decide_review(seeded, "alex", waiting.id, value="maturity extension", sentence=3)
    seeded.commit()
    made = _override(seeded, user="morgan")
    assert (made.status, made.value, made.decided_by, made.overridden_by) == (
        "overridden",
        "forbearance",
        "alex",
        "morgan",
    )
    [entry] = seeded.scalars(select(AuditEntry).where(AuditEntry.action == "override value"))
    assert (entry.before, entry.detail["before_status"]) == ("maturity extension", "human-reviewed")


def test_a_declined_field_overridden_keeps_its_decline(seeded, waiting):
    decline(seeded, "alex", waiting.id, reason="vague")
    seeded.commit()
    made = _override(seeded)
    assert (made.status, made.value) == ("overridden", "forbearance")
    [stored] = seeded.scalars(select(FieldOverride)).all()
    assert (stored.before_status, stored.before_value, stored.before_sentence) == (
        "not determinable",
        None,
        None,
    )
    [entry] = seeded.scalars(select(AuditEntry).where(AuditEntry.action == "override value"))
    assert entry.before == ""


def test_label_candidates_are_reviewed_and_overridden_values(seeded, waiting):
    assert label_candidates(seeded, "quinn") == []
    decide_review(seeded, "alex", waiting.id, value="maturity extension", sentence=3)
    seeded.commit()
    [reviewed] = label_candidates(seeded, "quinn")
    assert (reviewed.source, reviewed.value, reviewed.sentence, reviewed.by) == (
        "review",
        "maturity extension",
        3,
        "alex",
    )
    assert (reviewed.deal, reviewed.loan, reviewed.period, reviewed.accession) == (
        FIXTURE_DEAL,
        "2",
        SEPTEMBER,
        "0009900001-26-000009",
    )
    assert (reviewed.field, reviewed.label, reviewed.model_value) == (
        "borrower_request",
        "borrower request",
        "forbearance",
    )
    _override(seeded, user="morgan")
    seeded.commit()
    [overridden] = label_candidates(seeded, "quinn")
    assert (overridden.source, overridden.value, overridden.by, overridden.at) == (
        "override",
        "forbearance",
        "morgan",
        OVERRIDDEN_AT,
    )
    with pytest.raises(PermissionRefused):
        label_candidates(seeded, "olly")


def test_a_declined_field_is_no_label_candidate(seeded, waiting):
    decline(seeded, "alex", waiting.id, reason="vague")
    seeded.commit()
    assert label_candidates(seeded, "quinn") == []
