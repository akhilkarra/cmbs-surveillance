"""The golden set and extraction quality reports.

- `import_golden_set` loads a labels file (a quant's job); every labeller must be an analyst.
  Importing the same file again changes nothing; a different label for a stored comment is
  refused, never overwritten.
- `build_report` measures a set of predictions (the pipeline's answers for every golden
  comment under one model and prompt version) against the golden set, compares it with the
  previous report and stores it for good, naming the model, prompt version and the settings
  version in force (whose abstention band decided what was auto-accepted). A model or prompt
  change cites the report's number when it is proposed (grill log Q46).
- `extend_golden_set` proposes as label candidates the panel values a person decided (review
  decisions and overrides, Q45): the person who decided is the first labeller. A candidate
  joins the golden set only when a second analyst confirms it (`confirm_candidate`); a second
  analyst who disagrees rejects it with a reason (`reject_candidate`).

Writes are authorized first, take an advisory lock, re-read what they change under it, and are
audited; refusals are audited with the actor.
"""

import datetime as dt
from dataclasses import dataclass
from typing import Any, NoReturn

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.access.roles import Action, acting_role
from cmbs.access.service import Actor, authorize, roles_of
from cmbs.audit.service import record, refuse
from cmbs.deals.tables import Deal
from cmbs.domain import Period
from cmbs.errors import Refused
from cmbs.extraction.fields import spec
from cmbs.extraction.tables import ExtractedField, Extraction
from cmbs.quality import measure
from cmbs.quality.files import LabelledComment, PredictionSet
from cmbs.quality.measure import GoldenComment
from cmbs.quality.tables import (
    GOLDEN,
    IMPORTED,
    PANEL,
    PROPOSED,
    REJECTED,
    GoldenItem,
    GoldenLabel,
    QualityReport,
)
from cmbs.settings.tables import SettingsVersion

IMPORT = "import golden set"
BUILD = "build quality report"
PROPOSE = "propose golden item"
CONFIRM = "confirm golden item"
REJECT = "reject golden item"
GOLDEN_SET = "golden set"
GOLDEN_ITEM = "golden item"
REPORT = "quality report"
# An arbitrary constant naming the golden-set lock among Postgres advisory locks.
GOLDEN_LOCK_KEY = 0x60DE5E7


class NotAnAnalyst(Refused):
    pass


class ChangedLabel(Refused):
    """A stored golden comment's labels are never overwritten by an import."""


class EmptyGoldenSet(Refused):
    pass


class PredictionsDoNotMatch(Refused):
    """Predictions must cover every golden comment, and only golden comments."""


class UnknownSettingsVersion(Refused):
    pass


class UnknownReport(Refused):
    pass


class UnknownCandidate(Refused):
    pass


class NotProposed(Refused):
    pass


class SameAnalyst(Refused):
    """The analyst who decided a value cannot also be its second labeller."""


class MissingReason(Refused):
    pass


def _lock(db: Session) -> None:
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": GOLDEN_LOCK_KEY})


def _refuse(
    db: Session, error: Refused, actor: Actor, action: str, subject: str, key: str | None
) -> NoReturn:
    refuse(
        db,
        error,
        user=actor.user,
        role=actor.role.value,
        action=action,
        subject=subject,
        subject_key=key,
    )


def _labels_of(db: Session, item_ids: list[int]) -> dict[int, dict[str, dict[str, str | None]]]:
    labels: dict[int, dict[str, dict[str, str | None]]] = {item: {} for item in item_ids}
    if not item_ids:
        return labels
    rows = db.scalars(
        select(GoldenLabel)
        .where(GoldenLabel.item_id.in_(item_ids))
        .order_by(GoldenLabel.item_id, GoldenLabel.labeller, GoldenLabel.field)
    ).all()
    for row in rows:
        labels[row.item_id].setdefault(row.labeller, {})[row.field] = row.value
    return labels


def golden_set(db: Session) -> list[GoldenComment]:
    """Every comment in the golden set (confirmed, not proposed or rejected), by key."""
    items = db.scalars(
        select(GoldenItem).where(GoldenItem.status == GOLDEN).order_by(GoldenItem.key)
    ).all()
    labels = _labels_of(db, [item.id for item in items])
    # A comment confirmed from the panel was labelled by a second analyst who saw the first
    # label: a reference, but not an independent pair for labeller agreement.
    return [
        GoldenComment(item.key, labels[item.id], independent=item.source == IMPORTED)
        for item in items
    ]


# Importing labels.


def _check_labellers(db: Session, comments: list[LabelledComment]) -> None:
    for name in sorted({labeller for comment in comments for labeller in comment.labels}):
        if acting_role(roles_of(db, name), Action.DECIDE_REVIEW) is None:
            raise NotAnAnalyst(f"{name} is not an analyst, so cannot label the golden set")


def import_golden_set(
    db: Session, *, user: str, comments: list[LabelledComment], source_name: str
) -> int:
    """Store the labelled comments; returns how many were new."""
    actor = authorize(db, user, Action.PROPOSE_SETTING)
    now = clock.now()
    try:
        _check_labellers(db, comments)
        _lock(db)
        existing = {
            item.key: item
            for item in db.scalars(
                select(GoldenItem).where(GoldenItem.key.in_([c.key for c in comments]))
            )
        }
        stored = _labels_of(db, [item.id for item in existing.values()])
        for comment in comments:
            item = existing.get(comment.key)
            if item is not None and (
                item.comment != comment.comment or stored[item.id] != comment.labels
            ):
                raise ChangedLabel(
                    f"golden item {comment.key} is already stored with other labels; "
                    f"labels are never overwritten"
                )
    except Refused as error:
        _refuse(db, error, actor, IMPORT, GOLDEN_SET, source_name)
    new = [comment for comment in comments if comment.key not in existing]
    for comment in new:
        item = GoldenItem(
            key=comment.key,
            comment=comment.comment,
            source=IMPORTED,
            status=GOLDEN,
            sentences=[],
            added_by=actor.user,
            added_at=now,
            joined_at=now,
        )
        db.add(item)
        db.flush()
        for labeller, answers in comment.labels.items():
            for name, value in answers.items():
                db.add(
                    GoldenLabel(
                        item_id=item.id,
                        field=name,
                        labeller=labeller,
                        value=value,
                        labelled_at=now,
                    )
                )
    db.flush()
    record(
        db,
        user=actor.user,
        role=actor.role.value,
        action=IMPORT,
        subject=GOLDEN_SET,
        subject_key=source_name,
        detail={"items": len(comments), "new": len(new)},
    )
    return len(new)


# Reports.


@dataclass(frozen=True)
class ReportView:
    id: int
    built_by: str
    built_at: dt.datetime
    model: str
    prompt_version: int
    settings_version: int | None
    items: int
    results: dict[str, Any]
    flags: list[str]
    previous_id: int | None

    def field(self, name: str) -> dict[str, Any]:
        return self.results["fields"][name]

    @property
    def band(self) -> str:
        return self.results["band"]

    @property
    def settings_versions(self) -> list[int]:
        """The settings versions that produced the predictions measured."""
        return list(self.results["settings_versions"])


def _view(row: QualityReport) -> ReportView:
    return ReportView(
        id=row.id,
        built_by=row.built_by,
        built_at=row.built_at,
        model=row.model,
        prompt_version=row.prompt_version,
        settings_version=row.settings_version,
        items=row.items,
        results=dict(row.results),
        flags=list(row.flags),
        previous_id=row.previous_id,
    )


def overall_band(qualities: list[measure.FieldQuality]) -> str:
    """Holding when every field measured holds; not measured when none was auto-accepted."""
    bands = [quality.band for quality in qualities if quality.band != measure.NOT_MEASURED]
    if not bands:
        return measure.NOT_MEASURED
    return measure.HOLDING if all(b == measure.HOLDING for b in bands) else measure.NOT_HOLDING


def results_of(
    golden: list[GoldenComment], predictions: PredictionSet
) -> tuple[list[measure.FieldQuality], dict[str, Any]]:
    """Measure every predicted field and labeller agreement; the results as stored."""
    names = predictions.fields
    qualities = [measure.measure_field(golden, predictions.predictions, name) for name in names]
    # Agreement is about the labels, whatever was predicted: every field anyone labelled.
    agreement = measure.agreement(golden, measure.labelled_fields(golden))
    shown = agreement.overall.shown()
    results = {
        "fields": {quality.field: measure.as_json(quality) for quality in qualities},
        "bands": {quality.field: measure.band_text(quality) for quality in qualities},
        "band": overall_band(qualities),
        "agreement": {
            "labellers": list(agreement.labellers),
            "agreed": agreement.overall.successes,
            "double_labelled": agreement.overall.trials,
            "rate": shown[0] if shown else None,
            "low": shown[1] if shown else None,
            "high": shown[2] if shown else None,
            "kappa": {
                name: (str(measure.rounded(kappa)) if kappa is not None else None)
                for name, kappa in agreement.kappa.items()
            },
        },
    }
    return qualities, results


def _check_coverage(golden: list[GoldenComment], predictions: PredictionSet) -> None:
    if not golden:
        raise EmptyGoldenSet("the golden set is empty")
    if not predictions.fields:
        raise PredictionsDoNotMatch("the predictions name no field")
    keys = {item.key for item in golden}
    extra = sorted(key for key in predictions.predictions if key not in keys)
    if extra:
        raise PredictionsDoNotMatch(
            f"predictions for comments not in the golden set: {', '.join(extra[:5])}"
        )
    missing = measure.missing_predictions(golden, predictions.predictions, predictions.fields)
    if missing:
        more = f" and {len(missing) - 5} more" if len(missing) > 5 else ""
        raise PredictionsDoNotMatch(f"no prediction for {', '.join(missing[:5])}{more}")


def _check_versions(db: Session, predictions: PredictionSet) -> list[int]:
    """The settings versions that produced the predictions; each must say and must exist."""
    versions = predictions.settings_versions
    if None in versions:
        raise PredictionsDoNotMatch(
            "every prediction must name the settings version that produced it"
        )
    known = set(db.scalars(select(SettingsVersion.version)))
    named = [version for version in versions if version is not None]
    unknown = [str(version) for version in named if version not in known]
    if unknown:
        raise UnknownSettingsVersion(f"there is no settings version {', '.join(unknown)}")
    return named


def build_report(db: Session, *, user: str, predictions: PredictionSet) -> ReportView:
    """Measure the predictions against the golden set and store the report."""
    actor = authorize(db, user, Action.PROPOSE_SETTING)
    try:
        versions = _check_versions(db, predictions)
        _lock(db)
        golden = golden_set(db)
        _check_coverage(golden, predictions)
    except Refused as error:
        _refuse(
            db,
            error,
            actor,
            BUILD,
            REPORT,
            f"{predictions.model} prompt {predictions.prompt_version}",
        )
    qualities, results = results_of(golden, predictions)
    results["settings_versions"] = versions
    previous = db.scalars(
        select(QualityReport).order_by(QualityReport.id.desc()).limit(1)
    ).one_or_none()
    flags = measure.worse(previous.results["fields"], qualities) if previous is not None else []
    row = QualityReport(
        built_by=actor.user,
        built_at=clock.now(),
        model=predictions.model,
        prompt_version=predictions.prompt_version,
        settings_version=versions[0] if len(versions) == 1 else None,
        items=len(golden),
        results=results,
        flags=flags,
        previous_id=previous.id if previous is not None else None,
        predictions=predictions.as_json(),
    )
    db.add(row)
    db.flush()
    record(
        db,
        user=actor.user,
        role=actor.role.value,
        action=BUILD,
        subject=REPORT,
        subject_key=str(row.id),
        detail={
            "model": row.model,
            "prompt_version": row.prompt_version,
            "settings_versions": versions,
            "items": row.items,
            "flags": flags,
        },
    )
    return _view(row)


def report(db: Session, *, reader: str, report_id: int | None = None) -> ReportView:
    """A stored report; the latest when no number is given."""
    authorize(db, reader, Action.READ_PANEL)
    query = select(QualityReport)
    query = (
        query.where(QualityReport.id == report_id)
        if report_id is not None
        else query.order_by(QualityReport.id.desc()).limit(1)
    )
    row = db.scalars(query).one_or_none()
    if row is None:
        which = f"quality report {report_id}" if report_id is not None else "quality report"
        raise UnknownReport(f"there is no {which}")
    return _view(row)


# Label candidates from the panel.


@dataclass(frozen=True)
class CandidateView:
    id: int
    key: str
    deal: str
    loan: str
    period: Period
    accession: str
    status: str
    labels: dict[str, dict[str, str | None]]
    sentences: dict[str, int | None]
    comment: str
    decided_by: str | None
    reason: str | None


def candidate_key(deal: str, loan: str, period: Period, field: str) -> str:
    """One candidate per loan, month and field: 'Fixture Trust 2026-F1/2/2026-09/borrower_request'.

    A comment extracted again (another model or prompt version) and decided again is the same
    candidate, so the latest decision stands for it."""
    return f"{deal}/{loan}/{period.first_day:%Y-%m}/{field}"


def _candidate(db: Session, item: GoldenItem) -> CandidateView:
    deal = db.get(Deal, item.deal_id) if item.deal_id is not None else None
    if deal is None or item.loan is None or item.period is None or item.accession is None:
        raise UnknownCandidate(f"golden item {item.id} did not come from the panel")
    labels = db.scalars(
        select(GoldenLabel).where(GoldenLabel.item_id == item.id).order_by(GoldenLabel.id)
    ).all()
    by_labeller: dict[str, dict[str, str | None]] = {}
    sentences: dict[str, int | None] = {}
    for label in labels:
        by_labeller.setdefault(label.labeller, {})[label.field] = label.value
        sentences.setdefault(label.field, label.sentence)
    return CandidateView(
        id=item.id,
        key=item.key,
        deal=deal.name,
        loan=item.loan,
        period=Period.of(item.period),
        accession=item.accession,
        status=item.status,
        labels=by_labeller,
        sentences=sentences,
        comment=item.comment,
        decided_by=item.decided_by,
        reason=item.reason,
    )


def _decided_fields(db: Session) -> list[tuple[Extraction, ExtractedField, str]]:
    """Panel fields a person decided, with the extraction they belong to and its deal, oldest
    decision first."""
    rows = db.execute(
        select(Extraction, ExtractedField, Deal.name)
        .join(ExtractedField, ExtractedField.extraction_id == Extraction.id)
        .join(Deal, Deal.id == Extraction.deal_id)
        .where(ExtractedField.decided_by.is_not(None))
        .order_by(ExtractedField.decided_at, ExtractedField.id)
    ).all()
    return [(extraction, row, deal) for extraction, row, deal in rows]


@dataclass(frozen=True)
class Extended:
    """The candidates proposed, and how many decisions proposed nothing: already a golden item
    or candidate (proposed, confirmed or rejected), or superseded by a later decision on the
    same loan, month and field."""

    proposed: list[CandidateView]
    skipped: int


def extend_golden_set(db: Session, *, user: str) -> Extended:
    """Propose the latest decision on each loan, month and field that is not yet a golden item.

    The person who decided is the candidate's first labeller; a labeller is never given a
    second label on the same candidate.
    """
    actor = authorize(db, user, Action.PROPOSE_SETTING)
    _lock(db)
    now = clock.now()
    known = set(db.scalars(select(GoldenItem.key)))
    decided = _decided_fields(db)
    latest: dict[str, tuple[Extraction, ExtractedField]] = {}
    for extraction, row, deal in decided:
        key = candidate_key(deal, extraction.loan_id, Period.of(extraction.period), row.field)
        latest[key] = (extraction, row)  # oldest first: the last one seen is the latest
    proposed: list[GoldenItem] = []
    for key, (extraction, row) in latest.items():
        if key in known or row.decided_by is None:
            continue
        item = GoldenItem(
            key=key,
            comment=extraction.comment,
            source=PANEL,
            status=PROPOSED,
            deal_id=extraction.deal_id,
            loan=extraction.loan_id,
            period=extraction.period,
            accession=extraction.accession,
            sentences=list(extraction.sentences),
            added_by=actor.user,
            added_at=now,
        )
        db.add(item)
        db.flush()
        db.add(
            GoldenLabel(
                item_id=item.id,
                field=row.field,
                labeller=row.decided_by,
                value=row.value,
                sentence=row.sentence,
                labelled_at=row.decided_at or now,
            )
        )
        proposed.append(item)
    db.flush()
    views = [_candidate(db, item) for item in proposed]
    for view in views:
        record(
            db,
            user=actor.user,
            role=actor.role.value,
            action=PROPOSE,
            subject=GOLDEN_ITEM,
            subject_key=view.key,
            detail={"labels": view.labels},
        )
    return Extended(proposed=views, skipped=len(decided) - len(views))


def candidates(db: Session, *, reader: str) -> list[CandidateView]:
    """Golden items proposed from the panel and waiting for a second analyst."""
    authorize(db, reader, Action.READ_PANEL)
    items = db.scalars(
        select(GoldenItem).where(GoldenItem.status == PROPOSED).order_by(GoldenItem.id)
    ).all()
    return [_candidate(db, item) for item in items]


def _proposed(db: Session, actor: Actor, item_id: int, action: str) -> GoldenItem:
    """The candidate, locked and re-read; refused unless it is still proposed and the actor
    did not decide any of its labels."""
    _lock(db)
    item = db.get(GoldenItem, item_id, with_for_update=True, populate_existing=True)
    try:
        if item is None or item.source != PANEL:
            raise UnknownCandidate(f"there is no golden item candidate {item_id}")
        if item.status != PROPOSED:
            raise NotProposed(f"golden item {item_id} is already {item.status}")
        labellers = set(
            db.scalars(select(GoldenLabel.labeller).where(GoldenLabel.item_id == item.id))
        )
        if actor.user in labellers:
            raise SameAnalyst(f"{actor.user} decided this label; a second analyst must confirm it")
    except Refused as error:
        _refuse(db, error, actor, action, GOLDEN_ITEM, str(item_id))
    return item


def confirm_candidate(db: Session, *, user: str, item_id: int) -> CandidateView:
    """A second analyst agrees with every label: the comment joins the golden set, labelled
    by both."""
    actor = authorize(db, user, Action.DECIDE_REVIEW)
    item = _proposed(db, actor, item_id, CONFIRM)
    now = clock.now()
    first = db.scalars(select(GoldenLabel).where(GoldenLabel.item_id == item.id)).all()
    for label in first:
        db.add(
            GoldenLabel(
                item_id=item.id,
                field=label.field,
                labeller=actor.user,
                value=label.value,
                sentence=label.sentence,
                labelled_at=now,
            )
        )
    item.status = GOLDEN
    item.joined_at = now
    item.decided_by = actor.user
    db.flush()
    view = _candidate(db, item)
    record(
        db,
        user=actor.user,
        role=actor.role.value,
        action=CONFIRM,
        subject=GOLDEN_ITEM,
        subject_key=item.key,
        before=PROPOSED,
        after=GOLDEN,
        detail={"labels": view.labels},
    )
    return view


def reject_candidate(db: Session, *, user: str, item_id: int, reason: str) -> CandidateView:
    """A second analyst disagrees: the comment stays out of the golden set, with the reason."""
    actor = authorize(db, user, Action.DECIDE_REVIEW)
    item = _proposed(db, actor, item_id, REJECT)
    why = " ".join(reason.split())
    if not why:
        _refuse(
            db,
            MissingReason("a rejection needs a reason"),
            actor,
            REJECT,
            GOLDEN_ITEM,
            str(item_id),
        )
    item.status = REJECTED
    item.decided_by = actor.user
    item.reason = why
    db.flush()
    record(
        db,
        user=actor.user,
        role=actor.role.value,
        action=REJECT,
        subject=GOLDEN_ITEM,
        subject_key=item.key,
        before=PROPOSED,
        after=REJECTED,
        reason=why,
    )
    return _candidate(db, item)


def label_text(name: str, value: str | None) -> str:
    return f"{spec(name).label} {value if value is not None else 'not stated'}"
