"""The settings ledger: read the settings in force, propose a change, approve or reject it.

A quant proposes with a reason; a risk officer approves or rejects; nobody approves their own
proposal; a model or prompt change must cite a quality report (grill log Q46). An approval
creates the next version, in force from the moment it is approved, so every result can record
the version that produced it.
"""

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import NoReturn

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.access.roles import Action
from cmbs.access.service import Actor, PermissionRefused, authorize
from cmbs.audit.service import record, refuse
from cmbs.errors import Refused
from cmbs.settings.tables import (
    APPROVED,
    AWAITING,
    PROPOSAL_STATUSES,
    REJECTED,
    SettingsProposal,
    SettingsVersion,
)
from cmbs.settings.values import (
    LABELS,
    Approved,
    SettingLine,
    Settings,
    apply_changes,
    as_json,
    changed_fields,
    describe_changes,
    format_field,
    needs_quality_report,
    setting_lines,
    stale_fields,
)

# An arbitrary constant naming the settings-ledger lock among Postgres advisory locks.
SETTINGS_LOCK_KEY = 0x5E771265

PROPOSAL = "settings proposal"
SETTING = "setting"
PROPOSE = "propose setting"
APPROVE = "approve setting"
REJECT = "reject setting"


class NoSettings(Refused):
    """No version was approved by the moment asked about."""


@dataclass(frozen=True)
class InForce:
    version: int
    values: Settings


class OutOfOrderVersion(Refused):
    """Versions are numbered in approval order; a new one must come after the latest."""


class MissingReason(Refused):
    """A proposal, and a rejection, must say why."""


class MissingQualityReport(Refused):
    """A model or prompt change must cite a quality report."""


class SelfApproval(PermissionRefused):
    """Nobody approves their own proposal."""


class UnknownProposal(Refused):
    pass


class AlreadyDecided(Refused):
    """A proposal is decided once."""


class StaleProposal(Refused):
    """Another approval changed a field this proposal changes, after it was proposed."""


def in_force(db: Session, at: dt.datetime | None = None) -> InForce:
    """The latest version approved at or before `at` (default: now).

    `store_version` keeps version numbers in approval order, so the highest number approved by
    `at` is the one in force.
    """
    moment = at or clock.now()
    row = db.scalars(
        select(SettingsVersion)
        .where(SettingsVersion.approved_at <= moment)
        .order_by(SettingsVersion.version.desc())
        .limit(1)
    ).one_or_none()
    if row is None:
        raise NoSettings(f"no settings approved by {moment.isoformat()}")
    return InForce(version=row.version, values=Settings.model_validate(row.values))


def _lock_ledger(db: Session) -> None:
    """Serialize ledger writers until the transaction ends. Taking it twice is harmless."""
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SETTINGS_LOCK_KEY})


def _latest(db: Session) -> SettingsVersion | None:
    return db.scalars(
        select(SettingsVersion).order_by(SettingsVersion.version.desc()).limit(1)
    ).one_or_none()


def store_version(
    db: Session,
    *,
    version: int,
    values: Settings,
    proposed_by: str,
    approved_by: str,
    approved_at: dt.datetime,
) -> SettingsVersion:
    # Serialize writers: the check below and the insert must see every committed version, even
    # when the ledger is empty, so concurrent approvals queue on a transaction-scoped lock.
    _lock_ledger(db)
    latest = _latest(db)
    if latest is not None and (version <= latest.version or approved_at < latest.approved_at):
        raise OutOfOrderVersion(
            f"version {version} approved {approved_at.isoformat()} does not follow version "
            f"{latest.version} approved {latest.approved_at.isoformat()}"
        )
    row = SettingsVersion(
        version=version,
        values=values.model_dump(mode="json"),
        proposed_by=proposed_by,
        approved_by=approved_by,
        approved_at=approved_at,
    )
    db.add(row)
    db.flush()
    return row


@dataclass(frozen=True)
class SettingsView:
    """The settings in force at a moment, each line with the versions that set it."""

    version: int
    lines: list[SettingLine]

    def line(self, label: str) -> SettingLine:
        for line in self.lines:
            if line.label == label:
                return line
        raise KeyError(label)


def read_settings(db: Session, reader: str, at: dt.datetime | None = None) -> SettingsView:
    """The settings in force at `at` (default: now) with their history. Part of the panel."""
    authorize(db, reader, Action.READ_PANEL)
    current = in_force(db, at)
    rows = db.scalars(
        select(SettingsVersion)
        .where(SettingsVersion.version <= current.version)
        .order_by(SettingsVersion.version)
    ).all()
    versions = [
        Approved(
            version=row.version,
            values=Settings.model_validate(row.values),
            proposed_by=row.proposed_by,
            approved_by=row.approved_by,
            approved_at=row.approved_at,
        )
        for row in rows
    ]
    return SettingsView(version=current.version, lines=setting_lines(versions))


def _refuse_proposal(
    db: Session, error: Refused, actor: Actor, action: str, key: str | None
) -> NoReturn:
    refuse(
        db,
        error,
        user=actor.user,
        role=actor.role.value,
        action=action,
        subject=PROPOSAL,
        subject_key=key,
    )


def propose(
    db: Session,
    user: str,
    *,
    changes: Mapping[str, object],
    reason: str,
    quality_report: str | None = None,
) -> SettingsProposal:
    """Propose new values for some settings. Nothing changes until a risk officer approves."""
    actor = authorize(db, user, Action.PROPOSE_SETTING)
    latest = _latest(db)
    if latest is None:
        raise NoSettings("no settings have been approved yet")
    current = Settings.model_validate(latest.values)
    try:
        proposed = apply_changes(current, changes)
    except Refused as invalid:
        _refuse_proposal(db, invalid, actor, PROPOSE, None)
    fields = changed_fields(current, proposed)
    report = (quality_report or "").strip() or None
    if needs_quality_report(fields) and report is None:
        error = MissingQualityReport("a model or prompt change needs a quality report")
        _refuse_proposal(db, error, actor, PROPOSE, None)
    if not reason.strip():
        _refuse_proposal(db, MissingReason("a proposal needs a reason"), actor, PROPOSE, None)
    proposal = SettingsProposal(
        proposed_by=actor.user,
        proposer_role=actor.role.value,
        proposed_at=clock.now(),
        changes=as_json(proposed, fields),
        proposed_from=as_json(current, fields),
        reason=reason.strip(),
        quality_report=report,
        status=AWAITING,
    )
    db.add(proposal)
    db.flush()
    record(
        db,
        user=actor.user,
        role=actor.role.value,
        action=PROPOSE,
        subject=PROPOSAL,
        subject_key=str(proposal.id),
        after=describe_changes(proposal.proposed_from, proposal.changes),
        reason=proposal.reason,
        detail={"changes": proposal.changes, "quality_report": report},
    )
    return proposal


def _locked_awaiting(db: Session, proposal_id: int, actor: Actor, action: str) -> SettingsProposal:
    """The proposal, locked and re-read so two reviewers cannot both decide it."""
    key = str(proposal_id)
    proposal = db.get(SettingsProposal, proposal_id, with_for_update=True, populate_existing=True)
    if proposal is None:
        _refuse_proposal(db, UnknownProposal(f"there is no proposal {key}"), actor, action, key)
    if proposal.status != AWAITING:
        error = AlreadyDecided(f"proposal {key} was already {proposal.status}")
        _refuse_proposal(db, error, actor, action, key)
    return proposal


def approve(db: Session, user: str, proposal_id: int) -> SettingsVersion:
    """Approve a proposal: the next version, the change applied on top of the latest one, in
    force from now."""
    actor = authorize(db, user, Action.DECIDE_SETTING)
    key = str(proposal_id)
    proposal = _locked_awaiting(db, proposal_id, actor, APPROVE)
    if proposal.proposed_by == actor.user:
        error = SelfApproval("a proposal cannot be approved by its proposer")
        _refuse_proposal(db, error, actor, APPROVE, key)
    _lock_ledger(db)
    latest = _latest(db)
    if latest is None:  # pragma: no cover - a proposal is always made on top of a version
        raise NoSettings("no settings have been approved yet")
    current = Settings.model_validate(latest.values)
    stale = stale_fields(proposal.proposed_from, current)
    if stale:
        field, then, now = stale[0]
        error = StaleProposal(
            f"the {LABELS[field]} has changed from {format_field(field, then)} to "
            f"{format_field(field, now)} since proposal {key}; propose the change again"
        )
        _refuse_proposal(db, error, actor, APPROVE, key)
    try:
        values = apply_changes(current, proposal.changes)
    except Refused as invalid:
        _refuse_proposal(db, invalid, actor, APPROVE, key)
    approved_at = clock.now()
    version = store_version(
        db,
        version=latest.version + 1,
        values=values,
        proposed_by=proposal.proposed_by,
        approved_by=actor.user,
        approved_at=approved_at,
    )
    proposal.status = APPROVED
    proposal.decided_by = actor.user
    proposal.decider_role = actor.role.value
    proposal.decided_at = approved_at
    proposal.version = version.version
    db.flush()
    for field in changed_fields(current, values):
        record(
            db,
            user=actor.user,
            role=actor.role.value,
            action=APPROVE,
            subject=SETTING,
            subject_key=field,
            field=LABELS[field],
            before=format_field(field, getattr(current, field)),
            after=format_field(field, getattr(values, field)),
            reason=proposal.reason,
            detail={
                "proposal": proposal.id,
                "proposed_by": proposal.proposed_by,
                "version": version.version,
            },
        )
    return version


def reject(db: Session, user: str, proposal_id: int, *, reason: str) -> SettingsProposal:
    """Reject a proposal with a reason. The settings in force do not change."""
    actor = authorize(db, user, Action.DECIDE_SETTING)
    key = str(proposal_id)
    proposal = _locked_awaiting(db, proposal_id, actor, REJECT)
    if not reason.strip():
        _refuse_proposal(db, MissingReason("a rejection needs a reason"), actor, REJECT, key)
    proposal.status = REJECTED
    proposal.decided_by = actor.user
    proposal.decider_role = actor.role.value
    proposal.decided_at = clock.now()
    proposal.decision_reason = reason.strip()
    db.flush()
    record(
        db,
        user=actor.user,
        role=actor.role.value,
        action=REJECT,
        subject=PROPOSAL,
        subject_key=key,
        reason=proposal.decision_reason,
        detail={"proposed_by": proposal.proposed_by, "changes": proposal.changes},
    )
    return proposal


def proposals(db: Session, reader: str, status: str | None = None) -> Sequence[SettingsProposal]:
    """Proposals, newest first, optionally only those with one status. Part of the panel."""
    authorize(db, reader, Action.READ_PANEL)
    if status is not None and status not in PROPOSAL_STATUSES:
        raise ValueError(f"no proposal status {status!r}")
    query = select(SettingsProposal).order_by(SettingsProposal.id.desc())
    if status is not None:
        query = query.where(SettingsProposal.status == status)
    return db.scalars(query).all()
