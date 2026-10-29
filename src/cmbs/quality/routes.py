"""Extraction quality over HTTP: the golden set, quality reports and label candidates.

The bodies are the same JSON as the files the command line reads (`cmbs.quality.files`).
"""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from cmbs.access.roles import Action
from cmbs.access.service import authorize
from cmbs.api.deps import DB, User
from cmbs.quality import service
from cmbs.quality.files import read_golden, read_predictions
from cmbs.quality.service import CandidateView, ReportView

router = APIRouter(prefix="/quality", tags=["quality"])


def _report(view: ReportView) -> dict[str, Any]:
    return {
        "id": view.id,
        "built_by": view.built_by,
        "built_at": view.built_at.isoformat(),
        "model": view.model,
        "prompt_version": view.prompt_version,
        "settings_version": view.settings_version,
        "items": view.items,
        "previous_id": view.previous_id,
        "flags": view.flags,
        **view.results,
    }


def _candidate(view: CandidateView) -> dict[str, Any]:
    return {
        "id": view.id,
        "key": view.key,
        "deal": view.deal,
        "loan": view.loan,
        "period": view.period.label,
        "accession": view.accession,
        "status": view.status,
        "labels": view.labels,
        "sentences": view.sentences,
        "decided_by": view.decided_by,
        "reason": view.reason,
    }


class GoldenIn(BaseModel):
    source_name: str
    items: list[dict[str, Any]]


@router.post("/golden-set")
def load_golden(body: GoldenIn, user: User, db: DB) -> dict[str, int]:
    authorize(db, user, Action.PROPOSE_SETTING)  # before the labels are read
    comments = read_golden({"items": body.items})
    new = service.import_golden_set(db, user=user, comments=comments, source_name=body.source_name)
    return {"items": len(comments), "new": new}


@router.post("/reports")
def build(body: dict[str, Any], user: User, db: DB) -> dict[str, Any]:
    authorize(db, user, Action.PROPOSE_SETTING)  # before the predictions are read
    return _report(service.build_report(db, user=user, predictions=read_predictions(body)))


@router.get("/reports/latest")
def latest(user: User, db: DB) -> dict[str, Any]:
    return _report(service.report(db, reader=user))


@router.get("/reports/{report_id}")
def one(report_id: int, user: User, db: DB) -> dict[str, Any]:
    return _report(service.report(db, reader=user, report_id=report_id))


@router.post("/candidates")
def extend(user: User, db: DB) -> list[dict[str, Any]]:
    return [_candidate(view) for view in service.extend_golden_set(db, user=user)]


@router.get("/candidates")
def waiting(user: User, db: DB) -> list[dict[str, Any]]:
    return [_candidate(view) for view in service.candidates(db, reader=user)]


@router.post("/candidates/{item_id}/confirm")
def confirm(item_id: int, user: User, db: DB) -> dict[str, Any]:
    return _candidate(service.confirm_candidate(db, user=user, item_id=item_id))


class RejectIn(BaseModel):
    reason: str


@router.post("/candidates/{item_id}/reject")
def reject(item_id: int, body: RejectIn, user: User, db: DB) -> dict[str, Any]:
    return _candidate(service.reject_candidate(db, user=user, item_id=item_id, reason=body.reason))
