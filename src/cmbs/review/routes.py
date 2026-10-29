"""The review queue, declines, overrides and label candidates over HTTP.

Deciding an item is `POST /extraction/review-items/{id}/decision`. Exposure travels as a string
of the exact amount, never a float.
"""

import datetime as dt
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from cmbs.api.deps import DB, User
from cmbs.domain import Period
from cmbs.extraction.routes import field_json, review_json
from cmbs.review.service import (
    LabelCandidate,
    OverrideView,
    QueueEntry,
    QueueSummary,
    decline,
    declined_reason,
    label_candidates,
    override,
    override_history,
    queue,
    queue_summary,
)

router = APIRouter(prefix="/review", tags=["review"])


class DeclineIn(BaseModel):
    reason: str


class OverrideIn(BaseModel):
    deal: str
    loan: str
    period: str
    field: str
    value: str
    sentence: int | None = None
    reason: str | None = None


def _period(text: str) -> Period:
    try:
        return Period.parse(text)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def entry_json(entry: QueueEntry) -> dict[str, Any]:
    return {
        **review_json(entry.item),
        "exposure": str(entry.exposure),
        "waiting_since": entry.waiting_since.isoformat(),
        "business_days_waiting": entry.waited,
        "overdue": entry.overdue,
        "mark": entry.mark,
    }


def summary_json(summary: QueueSummary) -> dict[str, Any]:
    return {
        "on": summary.on.isoformat(),
        "open": summary.open,
        "overdue": summary.overdue,
        "oldest_since": summary.oldest_since.isoformat() if summary.oldest_since else None,
        "oldest_business_days_waiting": summary.oldest_waited,
        "by_field": summary.by_field,
    }


def override_json(made: OverrideView) -> dict[str, Any]:
    return {
        "id": made.id,
        "field": made.field,
        "label": made.label,
        "value": made.value,
        "display": made.display,
        "sentence": made.sentence,
        "evidence": made.evidence,
        "accession": made.accession,
        "reason": made.reason,
        "by": made.by,
        "at": made.at.isoformat(),
        "before_status": made.before_status,
        "before": made.before_display,
    }


def candidate_json(found: LabelCandidate) -> dict[str, Any]:
    return {
        "deal": found.deal,
        "loan": found.loan,
        "period": found.period.label,
        "accession": found.accession,
        "field": found.field,
        "label": found.label,
        "value": found.value,
        "sentence": found.sentence,
        "source": found.source,
        "by": found.by,
        "at": found.at.isoformat(),
        "model_value": found.model_value,
    }


@router.get("/queue")
def read_queue(user: User, db: DB, on: dt.date | None = None) -> list[dict[str, Any]]:
    """Items open at the end of `on` (today): largest exposure first, then the oldest."""
    return [entry_json(entry) for entry in queue(db, user, on=on)]


@router.get("/queue/summary")
def read_summary(user: User, db: DB, on: dt.date | None = None) -> dict[str, Any]:
    return summary_json(queue_summary(db, user, on=on))


@router.post("/items/{item_id}/decline")
def decline_item(item_id: int, body: DeclineIn, user: User, db: DB) -> dict[str, Any]:
    """Close an item the comment cannot settle; the field is marked "not determinable"."""
    return field_json(decline(db, user, item_id, reason=body.reason))


@router.get("/items/{item_id}/decline")
def read_decline(item_id: int, user: User, db: DB) -> dict[str, Any]:
    reason = declined_reason(db, user, item_id)
    if reason is None:
        raise HTTPException(status_code=404, detail=f"review item {item_id} was not declined")
    return {"item": item_id, "reason": reason}


@router.post("/overrides")
def make_override(body: OverrideIn, user: User, db: DB) -> dict[str, Any]:
    """Replace a field's value from now on, with a reason and a cited sentence."""
    return field_json(
        override(
            db,
            user,
            deal=body.deal,
            loan=body.loan,
            period=_period(body.period),
            field=body.field,
            value=body.value,
            sentence=body.sentence,
            reason=body.reason,
        )
    )


@router.get("/overrides")
def read_overrides(user: User, db: DB, deal: str, loan: str, period: str) -> list[dict[str, Any]]:
    """Every override of a loan's latest extracted comment for a month, oldest first."""
    found = override_history(db, user, deal=deal, loan=loan, period=_period(period))
    return [override_json(made) for made in found]


@router.get("/label-candidates")
def read_label_candidates(user: User, db: DB) -> list[dict[str, Any]]:
    """Reviewed and overridden values, proposed as golden-set labels."""
    return [candidate_json(found) for found in label_candidates(db, user)]
