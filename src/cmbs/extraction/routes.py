"""Extracted fields and review items over HTTP. Probabilities travel as strings, never floats."""

import datetime as dt
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from cmbs.api.deps import DB, User
from cmbs.domain import Period
from cmbs.extraction.service import (
    CommentView,
    FieldView,
    ReviewView,
    comment_view,
    decide_review,
    extract_comments,
    model_client,
    open_review_items,
    review_item,
)

router = APIRouter(prefix="/extraction", tags=["extraction"])


class RunIn(BaseModel):
    deal: str
    period: str
    loan: str | None = None
    live: bool = False


class DecisionIn(BaseModel):
    """A value and the sentence that supports it, or "not stated" with no sentence."""

    value: str
    sentence: int | None = None


def _text(value: object | None) -> str | None:
    return None if value is None else str(value)


def _period(text: str) -> Period:
    try:
        return Period.parse(text)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def field_json(item: FieldView) -> dict[str, Any]:
    return {
        "field": item.field,
        "label": item.label,
        "status": item.status,
        "value": item.value,
        "display": item.display,
        "sentence": item.sentence,
        "evidence": item.evidence,
        "model_value": item.model_value,
        "answer_probability": _text(item.answer_probability),
        "support_probability": _text(item.support_probability),
        "review_reason": item.review_reason,
        "decided_by": item.decided_by,
        "decided_at": item.decided_at.isoformat() if item.decided_at else None,
    }


def view_json(view: CommentView) -> dict[str, Any]:
    return {
        "deal": view.deal,
        "loan": view.loan,
        "period": view.period.label,
        "accession": view.accession,
        "filing_date": view.filing_date.isoformat(),
        "comment": view.comment,
        "sentences": view.sentences,
        "model": view.model,
        "prompt_version": view.prompt_version,
        "settings_version": view.settings_version,
        "fields": [field_json(item) for item in view.fields.values()],
    }


def review_json(item: ReviewView) -> dict[str, Any]:
    return {
        "id": item.id,
        "deal": item.deal,
        "loan": item.loan,
        "period": item.period.label,
        "field": item.field,
        "label": item.label,
        "reason": item.reason,
        "question": item.question,
        "probability": str(item.probability),
        "model_value": item.model_value,
        "comment": item.comment,
        "sentences": item.sentences,
        "created_at": item.created_at.isoformat(),
        "status": item.status,
        "decided_by": item.decided_by,
        "decided_at": item.decided_at.isoformat() if item.decided_at else None,
        "decision": item.decision,
        "decision_sentence": item.decision_sentence,
    }


@router.get("/comments")
def read_comment(
    user: User, db: DB, deal: str, loan: str, period: str, as_of: dt.date | None = None
) -> dict[str, Any]:
    """A loan's extracted comment for a month, as known on `as_of` (default today)."""
    view = comment_view(db, user, deal=deal, loan=loan, period=_period(period), as_of=as_of)
    if view is None:
        raise HTTPException(status_code=404, detail=f"loan {loan} has no extracted comment")
    return view_json(view)


@router.post("/runs")
def run_extraction(body: RunIn, user: User, db: DB) -> dict[str, Any]:
    """Extract a month's comments; replays recorded answers unless `live` is set."""
    month = _period(body.period)
    client = model_client(db, user, live=body.live)
    summary = extract_comments(
        db, user=user, deal=body.deal, period=month, loan=body.loan, client=client
    )
    return {
        "deal": summary.deal,
        "period": summary.period.label,
        "comments": [
            {
                "loan": item.loan,
                "accession": item.accession,
                "replayed": item.replayed,
                "outcomes": item.outcomes,
            }
            for item in summary.comments
        ],
    }


@router.get("/review-items")
def list_review_items(user: User, db: DB) -> list[dict[str, Any]]:
    """Open review items, oldest first."""
    return [review_json(item) for item in open_review_items(db, user)]


@router.get("/review-items/{item_id}")
def read_review_item(item_id: int, user: User, db: DB) -> dict[str, Any]:
    return review_json(review_item(db, user, item_id))


@router.post("/review-items/{item_id}/decision")
def decide_review_item(item_id: int, body: DecisionIn, user: User, db: DB) -> dict[str, Any]:
    return field_json(decide_review(db, user, item_id, value=body.value, sentence=body.sentence))
