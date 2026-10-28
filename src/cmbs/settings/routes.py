"""The settings in force with their history, and proposals to change them."""

import datetime as dt
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel

from cmbs.api.deps import DB, User
from cmbs.settings.service import approve, proposals, propose, read_settings, reject
from cmbs.settings.tables import SettingsProposal

router = APIRouter(prefix="/settings", tags=["settings"])

Status = Literal["awaiting approval", "approved", "rejected"]


class Proposal(BaseModel):
    changes: dict[str, str | int | bool]
    reason: str
    quality_report: str | None = None


class Rejection(BaseModel):
    reason: str


def proposal_view(proposal: SettingsProposal) -> dict[str, Any]:
    return {
        "id": proposal.id,
        "status": proposal.status,
        "proposed_by": proposal.proposed_by,
        "proposer_role": proposal.proposer_role,
        "proposed_at": proposal.proposed_at.isoformat(),
        "changes": proposal.changes,
        "proposed_from": proposal.proposed_from,
        "reason": proposal.reason,
        "quality_report": proposal.quality_report,
        "decided_by": proposal.decided_by,
        "decider_role": proposal.decider_role,
        "decided_at": proposal.decided_at.isoformat() if proposal.decided_at else None,
        "decision_reason": proposal.decision_reason,
        "version": proposal.version,
    }


@router.get("")
def settings(user: User, db: DB, at: dt.datetime | None = None) -> dict[str, Any]:
    """The settings in force at `at` (default now), each with the versions that changed it."""
    view = read_settings(db, user, at)
    return {
        "version": view.version,
        "settings": [
            {
                "label": line.label,
                "value": line.value,
                "history": [
                    {
                        "version": change.version,
                        "value": change.value,
                        "proposed_by": change.proposed_by,
                        "approved_by": change.approved_by,
                        "approved_at": change.approved_at.isoformat(),
                    }
                    for change in line.history
                ],
            }
            for line in view.lines
        ],
    }


@router.get("/proposals")
def list_proposals(user: User, db: DB, status: Status | None = None) -> list[dict[str, Any]]:
    """Proposals, newest first, optionally only those with one status."""
    return [proposal_view(proposal) for proposal in proposals(db, user, status)]


@router.post("/proposals", status_code=201)
def make_proposal(user: User, db: DB, body: Proposal) -> dict[str, Any]:
    made = propose(
        db, user, changes=body.changes, reason=body.reason, quality_report=body.quality_report
    )
    return proposal_view(made)


@router.post("/proposals/{proposal_id}/approve")
def approve_proposal(user: User, db: DB, proposal_id: int) -> dict[str, Any]:
    version = approve(db, user, proposal_id)
    return {"version": version.version, "approved_at": version.approved_at.isoformat()}


@router.post("/proposals/{proposal_id}/reject")
def reject_proposal(user: User, db: DB, proposal_id: int, body: Rejection) -> dict[str, Any]:
    return proposal_view(reject(db, user, proposal_id, reason=body.reason))
