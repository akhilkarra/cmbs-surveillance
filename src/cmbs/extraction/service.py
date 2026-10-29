"""Extract special-servicing comments into the panel, and decide what waits for review.

`extract_comments` reads a deal's stored 10-D comments for a month (public text), asks the
model through the replay cache, runs the decision table on every field and stores the result:
auto-accepted values go straight to the panel, the rest wait in review items or are marked not
stated. Each comment is read once per model and prompt version. `extract_text` reads any text,
public or restricted, without storing a panel value (there is no store yet for restricted
sources such as an investor portal).

`decide_review` is a reviewer's decision: it fills the panel value, attributed and audited.
An analyst's override (`cmbs.review`) belongs to a loan's field for a month, not to one
extraction, and is laid over the field when it is read: `comment_view` shows the latest override
made by the day asked about, while the field's own row keeps the model's answer and any review
decision. It stays on top when the same comment is read again (a new model or prompt). When the
comment itself changed (an amended 10-D), the override is not applied: storing the new
extraction opens a review item for the field ("an earlier override no longer matches the
comment"), opened then rather than on read so that reading never writes. Storing a newer
extraction of a loan's month also marks the open review items of older ones superseded.

The checks that can refuse run before anything is written: who is acting, the settings in force
(an exact model version, a prompt version this code asks), the deal and its comments, then
every model call. A refusal is audited on its own connection and nothing of the run is kept.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import NoReturn

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.access.roles import Action
from cmbs.access.service import Actor, authorize
from cmbs.audit.service import LOAN, loan_key, record, refuse
from cmbs.audit.service import REVIEW_ITEM as AUDITED_REVIEW_ITEM
from cmbs.deals import service as deals
from cmbs.deals.tables import Deal
from cmbs.distribution.service import canonical_loan
from cmbs.distribution.tables import ReportComment
from cmbs.domain import Period
from cmbs.errors import Refused
from cmbs.extraction import fields as typed_fields
from cmbs.extraction.decision import UNSETTLED_ANSWER, Outcome
from cmbs.extraction.judgments import FieldResult, Judgment, decide_fields, read
from cmbs.extraction.questions import check_prompt_version, plan
from cmbs.extraction.sentences import Sentence, split
from cmbs.extraction.tables import (
    AUTO_ACCEPTED,
    DECIDED,
    HUMAN_REVIEWED,
    NEEDS_REVIEW,
    NOT_STATED,
    OPEN,
    SUPERSEDED,
    ExtractedField,
    Extraction,
    ReviewItem,
)
from cmbs.filings.tables import Filing
from cmbs.model.client import Answered, Ask, ModelClient
from cmbs.model.policy import Classification, check_pinned, key_for
from cmbs.model.providers import provider_for
from cmbs.model.recordings import (
    DbRecordings,
    Exported,
    export_recordings,
    import_recordings,
)
from cmbs.model.types import ModelRequest
from cmbs.review.tables import FieldOverride
from cmbs.settings.service import InForce, in_force

EXTRACT = "extract comment"
DECIDE = "decide review item"
IMPORT_RECORDINGS = "import model recordings"
NOT_STATED_DECISION = "not stated"
# How the panel marks a field an analyst overrode, and one whose review item was declined
# because the comment cannot settle it (Q45, Q47). Neither is stored as the field's status: the
# field's row keeps what the model or reviewer decided, and these are laid over it when read.
OVERRIDDEN = "overridden"
NOT_DETERMINABLE = "not determinable"
OVERRIDE_CONFLICT = "an earlier override no longer matches the comment"
DEAL = "deal"
REVIEW_ITEM = AUDITED_REVIEW_ITEM  # the loan's audit history finds refusals by it
STATUS_OF = {
    Outcome.AUTO_ACCEPT: AUTO_ACCEPTED,
    Outcome.REVIEW: NEEDS_REVIEW,
    Outcome.NOT_STATED: NOT_STATED,
}


class UnknownDeal(Refused):
    pass


class NoComment(Refused):
    """No stored comment to extract."""


class EmptyComment(Refused):
    pass


class ChangedComment(Refused):
    """A comment already read under this model and prompt version now says something else."""


class UnknownItem(Refused):
    pass


class AlreadyDecided(Refused):
    pass


class NoSuchSentence(Refused):
    pass


class BadDecision(Refused):
    """A value without a sentence, or "not stated" with one."""


@dataclass(frozen=True)
class SourceText:
    """Text to read, its data classification, and how messages name it."""

    text: str
    classification: Classification
    subject: str


@dataclass(frozen=True)
class Reading:
    """One text read by the model and decided field by field; nothing stored but the call."""

    sentences: list[Sentence]
    answered: Answered
    results: dict[str, FieldResult]
    settings_version: int


@dataclass(frozen=True)
class Extracted:
    """One stored comment's extraction. `replayed` is True when no request was sent."""

    loan: str
    accession: str
    extraction_id: int
    model_call: str
    replayed: bool
    outcomes: dict[str, str]


@dataclass(frozen=True)
class ExtractionSummary:
    deal: str
    period: Period
    comments: list[Extracted]


# Comments from 10-D filings on EDGAR are public; any other source says what it is.
TEN_D_CLASSIFICATION = Classification.PUBLIC


def call_key(text: str, *, model: str, prompt_version: int) -> str:
    """The recording key the extraction of `text` asks under, for checking a recording exists."""
    asked = plan(text, split(text))
    request = ModelRequest(model=model, state=asked.state, questions=asked.questions)
    return key_for(request, prompt_version=prompt_version, input_text=text)


def check_settings(settings: InForce) -> None:
    check_pinned(settings.values.model)
    check_prompt_version(settings.values.prompt_version)


def read_text(
    source: SourceText, client: ModelClient, settings: InForce, *, now: dt.datetime
) -> Reading:
    """Split, ask (through the replay cache) and decide. Pure apart from the client."""
    check_settings(settings)
    sentences = split(source.text)
    if not sentences:
        raise EmptyComment(f"{source.subject} is empty")
    asked = plan(source.text, sentences)
    answered = client.ask(
        Ask(
            questions=asked.questions,
            state=asked.state,
            input_text=source.text,
            classification=source.classification,
            subject=source.subject,
        ),
        settings,
        now=now,
    )
    judgments = read(asked, sentences, answered.recording.response)
    band = settings.values
    results = decide_fields(judgments, low=band.abstention_low, high=band.abstention_high)
    return Reading(
        sentences=sentences,
        answered=answered,
        results=results,
        settings_version=settings.version,
    )


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


def extract_text(db: Session, *, user: str, source: SourceText, client: ModelClient) -> Reading:
    """Read one text, public or restricted. The call is recorded; no panel value is stored."""
    actor = authorize(db, user, Action.RUN_REFRESH)
    try:
        return read_text(source, client, in_force(db), now=clock.now())
    except Refused as error:
        _refuse(db, error, actor, EXTRACT, "text", source.subject)


def _stored_comments(
    db: Session, deal_id: int, period: Period, loan: str | None
) -> Sequence[ReportComment]:
    """The period's comments, from filings public by today, in loan order."""
    query = (
        select(ReportComment)
        .join(Filing, Filing.accession == ReportComment.accession)
        .where(
            ReportComment.deal_id == deal_id,
            ReportComment.period == period.first_day,
            Filing.filing_date <= clock.today(),
        )
        .order_by(ReportComment.loan_id, ReportComment.accession, ReportComment.source_table)
    )
    if loan is not None:
        query = query.where(ReportComment.loan_id == canonical_loan(loan))
    return db.scalars(query).all()


def _review_question(judgment: Judgment, reason: str) -> tuple[str, Decimal]:
    """The question, and its probability, that sent the field to review: the answer when it
    is unsettled, otherwise whether a sentence supports it."""
    if reason == UNSETTLED_ANSWER:
        return judgment.value_question, judgment.answer_probability
    return judgment.support_question, judgment.support_probability


def review_reason_for(result: FieldResult, *, conflict: bool) -> tuple[str, str, Decimal] | None:
    """(reason, question, probability) of the review item a field's result opens; None when it
    opens none. A field whose earlier override no longer matches the comment always goes to
    review, asking the field's own question; the conflict comes first in the reason. Pure."""
    judgment = result.judgment
    if judgment is None:
        return None
    if result.decision.outcome is Outcome.REVIEW:
        reason = result.decision.reason or ""
        question, probability = _review_question(judgment, reason)
        return (f"{OVERRIDE_CONFLICT}; {reason}" if conflict else reason), question, probability
    if conflict:
        return OVERRIDE_CONFLICT, judgment.value_question, judgment.answer_probability
    return None


def supersede_items(
    db: Session, *, deal_id: int, loan: str, period: Period, by: int, at: dt.datetime
) -> list[int]:
    """Mark the open review items of a loan's month that belong to extractions other than `by`
    superseded by it; returns their ids. They leave the queue and are kept."""
    older = (
        select(ExtractedField.id)
        .join(Extraction, Extraction.id == ExtractedField.extraction_id)
        .where(
            Extraction.deal_id == deal_id,
            Extraction.loan_id == loan,
            Extraction.period == period.first_day,
            Extraction.id != by,
        )
    )
    # An UPDATE re-checks `status` after waiting on a row another session holds, so an item a
    # reviewer decided meanwhile stays decided.
    found = db.scalars(
        update(ReviewItem)
        .where(ReviewItem.status == OPEN, ReviewItem.field_id.in_(older))
        .values(status=SUPERSEDED, superseded_at=at, superseded_by=by)
        .returning(ReviewItem.id)
    ).all()
    return sorted(found)


def _field_row(extraction_id: int, result: FieldResult) -> ExtractedField:
    judgment = result.judgment
    accepted = result.decision.outcome is Outcome.AUTO_ACCEPT and judgment is not None
    return ExtractedField(
        extraction_id=extraction_id,
        field=result.field,
        model_value=judgment.value if judgment else None,
        answer_probability=judgment.answer_probability if judgment else None,
        support_probability=judgment.support_probability if judgment else None,
        model_evidence=judgment.evidence if judgment else None,
        model_sentence=judgment.sentence if judgment else None,
        outcome=result.decision.outcome.value,
        review_reason=result.decision.reason,
        status=STATUS_OF[result.decision.outcome],
        value=judgment.value if accepted and judgment else None,
        sentence=judgment.sentence if accepted and judgment else None,
    )


def _store(
    db: Session,
    *,
    actor: Actor,
    deal: str,
    comment: ReportComment,
    reading: Reading,
    settings: InForce,
    run_id: int | None,
) -> Extracted:
    now = clock.now()
    extraction_id = db.scalar(
        insert(Extraction)
        .values(
            deal_id=comment.deal_id,
            loan_id=comment.loan_id,
            period=comment.period,
            accession=comment.accession,
            source_table=comment.source_table,
            comment=comment.comment,
            sentences=[sentence.text for sentence in reading.sentences],
            model=settings.values.model,
            prompt_version=settings.values.prompt_version,
            settings_version=settings.version,
            model_call=reading.answered.recording.call_id,
            run_id=run_id,
            extracted_by=actor.user,
            extracted_at=now,
        )
        .on_conflict_do_nothing(
            index_elements=[
                Extraction.accession,
                Extraction.loan_id,
                Extraction.source_table,
                Extraction.model,
                Extraction.prompt_version,
            ]
        )
        .returning(Extraction.id)
    )
    if extraction_id is None:
        # Another run stored this reading first; it is the same recorded answer.
        existing = _existing(db, comment, settings)
        if existing is None:  # pragma: no cover - the conflict guarantees the row
            raise RuntimeError("extraction vanished after a conflicting insert")
        return _extracted(db, existing, replayed=True)
    period = Period.of(comment.period)
    overridden = latest_overrides(db, deal_id=comment.deal_id, loan=comment.loan_id, period=period)
    conflicts = sorted(name for name, (_, cited) in overridden.items() if cited != comment.comment)
    superseded = supersede_items(
        db,
        deal_id=comment.deal_id,
        loan=comment.loan_id,
        period=period,
        by=extraction_id,
        at=now,
    )
    for result in reading.results.values():
        row = _field_row(extraction_id, result)
        db.add(row)
        db.flush()
        item = review_reason_for(result, conflict=result.field in conflicts)
        if item is not None and result.judgment is not None:
            reason, question, probability = item
            db.add(
                ReviewItem(
                    field_id=row.id,
                    deal_id=comment.deal_id,
                    loan_id=comment.loan_id,
                    field=result.field,
                    reason=reason,
                    question=question,
                    probability=probability,
                    created_at=now,
                    status=OPEN,
                )
            )
    db.flush()
    outcomes = {name: result.decision.outcome.value for name, result in reading.results.items()}
    record(
        db,
        user=actor.user,
        role=actor.role.value,
        run_id=run_id,
        action=EXTRACT,
        subject=LOAN,
        subject_key=loan_key(deal, comment.loan_id),
        detail={
            "accession": comment.accession,
            "period": comment.period.isoformat(),
            "model": settings.values.model,
            "prompt_version": settings.values.prompt_version,
            "settings_version": settings.version,
            "model_call": reading.answered.recording.call_id,
            "replayed": reading.answered.replayed,
            "outcomes": outcomes,
            "superseded_review_items": superseded,
            "override_conflicts": conflicts,
        },
    )
    return Extracted(
        loan=comment.loan_id,
        accession=comment.accession,
        extraction_id=extraction_id,
        model_call=reading.answered.recording.call_id,
        replayed=reading.answered.replayed,
        outcomes=outcomes,
    )


def _existing(db: Session, comment: ReportComment, settings: InForce) -> Extraction | None:
    return db.scalars(
        select(Extraction).where(
            Extraction.accession == comment.accession,
            Extraction.loan_id == comment.loan_id,
            Extraction.source_table == comment.source_table,
            Extraction.model == settings.values.model,
            Extraction.prompt_version == settings.values.prompt_version,
        )
    ).one_or_none()


def _extracted(db: Session, extraction: Extraction, *, replayed: bool) -> Extracted:
    rows = db.scalars(
        select(ExtractedField).where(ExtractedField.extraction_id == extraction.id)
    ).all()
    order = [spec.name for spec in typed_fields.FIELDS]
    return Extracted(
        loan=extraction.loan_id,
        accession=extraction.accession,
        extraction_id=extraction.id,
        model_call=extraction.model_call,
        replayed=replayed,
        outcomes={
            row.field: row.outcome for row in sorted(rows, key=lambda r: order.index(r.field))
        },
    )


def model_client(db: Session, user: str, *, live: bool) -> ModelClient:
    """The client a run uses: replay only, or live with the provider for the model in force.

    The user is authorized first, so a model no provider serves is an audited refusal.
    """
    actor = authorize(db, user, Action.RUN_REFRESH)
    provider = None
    if live:
        try:
            model = in_force(db).values.model
            provider = provider_for(model)
        except Refused as error:
            _refuse(db, error, actor, EXTRACT, "model provider", None)
    return ModelClient(DbRecordings(db), provider=provider, replay_only=not live)


def extract_comments(
    db: Session,
    *,
    user: str,
    deal: str,
    period: Period,
    client: ModelClient,
    loan: str | None = None,
    run_id: int | None = None,
) -> ExtractionSummary:
    """Extract every stored comment of `deal` for `period` (or one loan's) into the panel.

    A comment already read under the model and prompt version in force is not read again.
    """
    actor = authorize(db, user, Action.RUN_REFRESH)
    try:
        settings = in_force(db)
        check_settings(settings)
        deal_row = deals.by_name(db, deal)
        if deal_row is None:
            raise UnknownDeal(f"no deal named {deal!r}")
        comments = _stored_comments(db, deal_row.id, period, loan)
        if loan is not None and not comments:
            raise NoComment(
                f"no special-servicing comment for loan {loan} of {deal_row.name} in {period}"
            )
        extracted: list[Extracted] = []
        for comment in comments:
            existing = _existing(db, comment, settings)
            if existing is not None:
                if existing.comment != comment.comment:
                    raise ChangedComment(
                        f"loan {comment.loan_id}'s comment in {comment.accession} changed since "
                        f"it was extracted"
                    )
                extracted.append(_extracted(db, existing, replayed=True))
                continue
            source = SourceText(
                text=comment.comment,
                classification=TEN_D_CLASSIFICATION,
                subject=f"loan {comment.loan_id} of {deal_row.name}, "
                f"{Period.of(comment.period)} comment ({comment.accession})",
            )
            reading = read_text(source, client, settings, now=clock.now())
            extracted.append(
                _store(
                    db,
                    actor=actor,
                    deal=deal_row.name,
                    comment=comment,
                    reading=reading,
                    settings=settings,
                    run_id=run_id,
                )
            )
    except Refused as error:
        _refuse(db, error, actor, EXTRACT, DEAL, deal)
    return ExtractionSummary(deal=deal_row.name, period=period, comments=extracted)


# Reading the panel.


@dataclass(frozen=True)
class FieldView:
    field: str
    label: str
    status: str
    value: str | None
    display: str | None
    sentence: int | None
    evidence: str | None
    model_value: str | None
    answer_probability: Decimal | None
    support_probability: Decimal | None
    review_reason: str | None
    decided_by: str | None
    decided_at: dt.datetime | None
    overridden_by: str | None = None
    overridden_at: dt.datetime | None = None
    override_reason: str | None = None

    @property
    def typed(self) -> str | dt.date | Decimal | None:
        if self.value is None:
            return None
        return typed_fields.typed(typed_fields.spec(self.field), self.value)

    @property
    def marked(self) -> str:
        """'auto-accepted', 'human-reviewed by alex', 'overridden by alex', 'needs review'."""
        if self.overridden_by is not None:
            return f"{OVERRIDDEN} by {self.overridden_by}"
        if self.decided_by is not None:
            return f"{self.status} by {self.decided_by}"
        return self.status


@dataclass(frozen=True)
class CommentView:
    deal: str
    loan: str
    period: Period
    accession: str
    filing_date: dt.date
    comment: str
    sentences: list[str]
    model: str
    prompt_version: int
    settings_version: int
    model_call: str
    fields: dict[str, FieldView]


def field_view(
    row: ExtractedField,
    sentences: list[str],
    as_of: dt.date | None,
    *,
    override: FieldOverride | None = None,
    declined: bool = False,
) -> FieldView:
    """A field as the panel shows it. A decision taken after `as_of` is not yet visible.

    `override` is the latest override the reader may see (the caller filters by date); it
    replaces the value and sentence and marks the field overridden. `declined` marks a field
    whose review item was declined as not determinable.
    """
    spec = typed_fields.spec(row.field)
    hidden = (
        as_of is not None
        and row.decided_at is not None
        and row.decided_at.astimezone(dt.UTC).date() > as_of
    )
    status, value, sentence = (
        (NEEDS_REVIEW, None, None) if hidden else (row.status, row.value, row.sentence)
    )
    if declined and not hidden:
        status = NOT_DETERMINABLE
    if override is not None:
        status, value, sentence = OVERRIDDEN, override.value, override.sentence
    return FieldView(
        field=row.field,
        label=spec.label,
        status=status,
        value=value,
        display=typed_fields.display(spec, value) if value is not None else None,
        sentence=sentence,
        evidence=sentences[sentence - 1] if sentence is not None else None,
        model_value=row.model_value,
        answer_probability=row.answer_probability,
        support_probability=row.support_probability,
        review_reason=row.review_reason,
        decided_by=None if hidden else row.decided_by,
        decided_at=None if hidden else row.decided_at,
        overridden_by=override.overridden_by if override is not None else None,
        overridden_at=override.overridden_at if override is not None else None,
        override_reason=override.reason if override is not None else None,
    )


def latest_extraction(
    db: Session, *, deal_id: int, loan: str, period: Period, day: dt.date
) -> tuple[Extraction, dt.date] | None:
    """The latest extraction of a loan's comment for a period known on `day`, with the filing
    date: from a filing public by that day, extracted by the end of it (UTC)."""
    found = db.execute(
        select(Extraction, Filing.filing_date)
        .join(Filing, Filing.accession == Extraction.accession)
        .where(
            Extraction.deal_id == deal_id,
            Extraction.loan_id == canonical_loan(loan),
            Extraction.period == period.first_day,
            Filing.filing_date <= day,
            Extraction.extracted_at < _end_of(day),
        )
        .order_by(Extraction.id.desc())
        .limit(1)
    ).one_or_none()
    return None if found is None else (found[0], found[1])


def latest_overrides(
    db: Session,
    *,
    deal_id: int,
    loan: str,
    period: Period,
    before: dt.datetime | None = None,
) -> dict[str, tuple[FieldOverride, str]]:
    """Each field's latest override of a loan's comment for a month (of those made before
    `before`, when given), by field name, with the comment text its sentence cites."""
    query = (
        select(FieldOverride, Extraction.comment)
        .join(ExtractedField, ExtractedField.id == FieldOverride.field_id)
        .join(Extraction, Extraction.id == ExtractedField.extraction_id)
        .where(
            FieldOverride.deal_id == deal_id,
            FieldOverride.loan_id == canonical_loan(loan),
            FieldOverride.period == period.first_day,
        )
    )
    if before is not None:
        query = query.where(FieldOverride.overridden_at < before)
    rows = db.execute(query.order_by(FieldOverride.overridden_at, FieldOverride.id)).all()
    return {made.field: (made, cited) for made, cited in rows}


def applicable(found: tuple[FieldOverride, str] | None, comment: str) -> FieldOverride | None:
    """The override, when the comment its sentence cites is `comment`; None otherwise."""
    if found is None or found[1] != comment:
        return None
    return found[0]


def declined_fields(db: Session, field_ids: Sequence[int]) -> set[int]:
    """The fields whose review item was declined as not determinable."""
    if not field_ids:
        return set()
    return set(
        db.scalars(
            select(ReviewItem.field_id).where(
                ReviewItem.field_id.in_(field_ids), ReviewItem.decision == NOT_DETERMINABLE
            )
        )
    )


def comment_view(
    db: Session,
    reader: str,
    *,
    deal: str,
    loan: str,
    period: Period,
    as_of: dt.date | None = None,
) -> CommentView | None:
    """The latest extraction of a loan's comment for a period, as known on `as_of` (today):
    from a filing public by that day, extracted by the end of it (UTC). Needs the right to read
    the panel."""
    authorize(db, reader, Action.READ_PANEL)
    return panel_fields(db, deal=deal, loan=loan, period=period, as_of=as_of)


def panel_fields(
    db: Session, *, deal: str, loan: str, period: Period, as_of: dt.date | None = None
) -> CommentView | None:
    """What `comment_view` shows, overrides and declines laid on, point in time, without
    authorizing a reader.

    Internal: for system work that already holds its own authority (the checks a refresh runs
    compare comments under "run refreshes and backfills"). A user's read goes through
    `comment_view`.
    """
    deal_row = deals.by_name(db, deal)
    if deal_row is None:
        raise UnknownDeal(f"no deal named {deal!r}")
    day = as_of or clock.today()
    found = latest_extraction(db, deal_id=deal_row.id, loan=loan, period=period, day=day)
    if found is None:
        return None
    extraction, filed = found
    rows = db.scalars(
        select(ExtractedField).where(ExtractedField.extraction_id == extraction.id)
    ).all()
    by_field = {row.field: row for row in rows}
    ids = [row.id for row in rows]
    overrides = latest_overrides(
        db,
        deal_id=deal_row.id,
        loan=extraction.loan_id,
        period=Period.of(extraction.period),
        before=_end_of(day),
    )
    declined = declined_fields(db, ids)
    return CommentView(
        deal=deal_row.name,
        loan=extraction.loan_id,
        period=Period.of(extraction.period),
        accession=extraction.accession,
        filing_date=filed,
        comment=extraction.comment,
        sentences=list(extraction.sentences),
        model=extraction.model,
        prompt_version=extraction.prompt_version,
        settings_version=extraction.settings_version,
        model_call=extraction.model_call,
        fields={
            spec.name: field_view(
                by_field[spec.name],
                extraction.sentences,
                as_of,
                override=applicable(overrides.get(spec.name), extraction.comment),
                declined=by_field[spec.name].id in declined,
            )
            for spec in typed_fields.FIELDS
            if spec.name in by_field
        },
    )


# Review items.


@dataclass(frozen=True)
class ReviewView:
    id: int
    deal: str
    loan: str
    period: Period
    field: str
    label: str
    reason: str
    question: str
    probability: Decimal
    model_value: str | None
    comment: str
    sentences: list[str]
    created_at: dt.datetime
    status: str
    decided_by: str | None
    decided_at: dt.datetime | None
    decision: str | None
    decision_sentence: int | None
    superseded_at: dt.datetime | None = None
    superseded_by: int | None = None


def _review_view(db: Session, item: ReviewItem) -> ReviewView:
    row = db.get(ExtractedField, item.field_id)
    extraction = db.get(Extraction, row.extraction_id) if row is not None else None
    deal = db.get(Deal, item.deal_id)
    if row is None or extraction is None or deal is None:  # pragma: no cover - foreign keys
        raise RuntimeError(f"review item {item.id} lost its field")
    return ReviewView(
        id=item.id,
        deal=deal.name,
        loan=item.loan_id,
        period=Period.of(extraction.period),
        field=item.field,
        label=typed_fields.spec(item.field).label,
        reason=item.reason,
        question=item.question,
        probability=item.probability,
        model_value=row.model_value,
        comment=extraction.comment,
        sentences=list(extraction.sentences),
        created_at=item.created_at,
        status=item.status,
        decided_by=item.decided_by,
        decided_at=item.decided_at,
        decision=item.decision,
        decision_sentence=item.decision_sentence,
        superseded_at=item.superseded_at,
        superseded_by=item.superseded_by,
    )


def review_views(db: Session, items: Sequence[ReviewItem]) -> list[ReviewView]:
    """Review items as the queue and the API show them, in the order given."""
    return [_review_view(db, item) for item in items]


def review_item(db: Session, reader: str, item_id: int) -> ReviewView:
    authorize(db, reader, Action.READ_PANEL)
    item = db.get(ReviewItem, item_id)
    if item is None:
        raise UnknownItem(f"there is no review item {item_id}")
    return _review_view(db, item)


def open_review_items(db: Session, reader: str) -> list[ReviewView]:
    """Open items, oldest first. Ordering by exposure and overdue marks come with the queue."""
    authorize(db, reader, Action.READ_PANEL)
    items = db.scalars(
        select(ReviewItem)
        .where(ReviewItem.status == OPEN)
        .order_by(ReviewItem.created_at, ReviewItem.id)
    ).all()
    return [_review_view(db, item) for item in items]


def _refuse_item(db: Session, error: Refused, actor: Actor, item_id: int) -> NoReturn:
    refuse(
        db,
        error,
        user=actor.user,
        role=actor.role.value,
        action=DECIDE,
        subject=REVIEW_ITEM,
        subject_key=str(item_id),
    )


def _decision(
    spec: typed_fields.FieldSpec, value: str, sentence: int | None, sentences: list[str]
) -> str | None:
    """The canonical value decided, or None for "not stated"; refuses an impossible decision.

    The sentence is checked against the sentences stored with the extraction, the ones the
    model and the reviewer saw.
    """
    if " ".join(value.split()) == NOT_STATED_DECISION:
        if sentence is not None:
            raise BadDecision("a field decided as not stated cites no sentence")
        return None
    chosen = typed_fields.canonical(spec, value)
    if sentence is None:
        raise BadDecision(f"a {spec.label} needs the sentence that supports it")
    if not 1 <= sentence <= len(sentences):
        raise NoSuchSentence(f"sentence {sentence} does not exist in this comment")
    return chosen


def closed_problem(item: ReviewItem) -> Refused | None:
    """Why an item can no longer be decided or declined; None while it is open. Pure."""
    if item.status == SUPERSEDED:
        return AlreadyDecided(
            f"review item {item.id} was superseded by a newer extraction ({item.superseded_by})"
        )
    if item.status != OPEN:
        return AlreadyDecided(f"review item {item.id} was already decided by {item.decided_by}")
    return None


def _end_of(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), dt.UTC)


def decide_review(
    db: Session, user: str, item_id: int, *, value: str, sentence: int | None
) -> FieldView:
    """A reviewer's decision: a value backed by a sentence of the comment, or "not stated"
    (which cites no sentence)."""
    actor = authorize(db, user, Action.DECIDE_REVIEW)
    # Locked and re-read, so two reviewers cannot both decide the item.
    item = db.get(ReviewItem, item_id, with_for_update=True, populate_existing=True)
    if item is None:
        _refuse_item(db, UnknownItem(f"there is no review item {item_id}"), actor, item_id)
    error = closed_problem(item)
    if error is not None:
        _refuse_item(db, error, actor, item_id)
    row = db.get(ExtractedField, item.field_id, with_for_update=True, populate_existing=True)
    extraction = db.get(Extraction, row.extraction_id) if row is not None else None
    deal = db.get(Deal, item.deal_id)
    if row is None or extraction is None or deal is None:  # pragma: no cover - foreign keys
        raise RuntimeError(f"review item {item_id} lost its field")
    spec = typed_fields.spec(item.field)
    try:
        chosen = _decision(spec, value, sentence, extraction.sentences)
    except Refused as invalid:
        _refuse_item(db, invalid, actor, item_id)
    now = clock.now()
    before = typed_fields.display(spec, row.value) if row.value is not None else ""
    row.status = NOT_STATED if chosen is None else HUMAN_REVIEWED
    row.value = chosen
    row.sentence = sentence
    row.decided_by = actor.user
    row.decided_at = now
    item.status = DECIDED
    item.decided_by = actor.user
    item.decided_at = now
    item.decision = NOT_STATED_DECISION if chosen is None else chosen
    item.decision_sentence = sentence
    db.flush()
    record(
        db,
        user=actor.user,
        role=actor.role.value,
        action=DECIDE,
        subject=LOAN,
        subject_key=loan_key(deal.name, item.loan_id),
        field=spec.label,
        before=before,
        after=NOT_STATED_DECISION if chosen is None else typed_fields.display(spec, chosen),
        reason="not stated in the comment" if sentence is None else f"sentence {sentence}",
        detail={
            "review_item": item.id,
            "accession": extraction.accession,
            "period": extraction.period.isoformat(),
            "review_reason": item.reason,
        },
    )
    return field_view(row, list(extraction.sentences), None)


# Recordings as files, and plain-text views for the command line.


def import_recording_files(db: Session, user: str, directory: Path) -> int:
    """Load recording files into the replay cache; a file that does not check out is refused."""
    actor = authorize(db, user, Action.RUN_REFRESH)
    try:
        return import_recordings(DbRecordings(db), directory)
    except Refused as error:
        _refuse(db, error, actor, IMPORT_RECORDINGS, "recordings", str(directory))


def export_recording_files(db: Session, user: str, directory: Path) -> Exported:
    """Write every recorded call of public text to `directory`, one file each, in the fixture
    format. Recordings of restricted text are skipped and counted."""
    authorize(db, user, Action.EXPORT)
    return export_recordings(db, directory)


def render_summary(summary: ExtractionSummary) -> str:
    """'Fixture Trust 2026-F1, September 2026: 1 comment, 0 sent to the model' and a line per
    comment with each field's outcome."""
    sent = sum(1 for item in summary.comments if not item.replayed)
    noun = "comment" if len(summary.comments) == 1 else "comments"
    lines = [
        f"{summary.deal}, {summary.period}: {len(summary.comments)} {noun}, "
        f"{sent} sent to the model"
    ]
    for item in summary.comments:
        outcomes = ", ".join(
            f"{typed_fields.spec(name).label} {outcome}" for name, outcome in item.outcomes.items()
        )
        lines.append(f"loan {item.loan} ({item.accession}): {outcomes}")
    return "\n".join(lines) + "\n"


def render_view(view: CommentView) -> str:
    """A loan's comment, numbered, then each field with its status and evidence."""
    lines = [
        f"{view.deal} loan {view.loan}, {view.period} ({view.accession}, filed "
        f"{view.filing_date}); {view.model}, prompt version {view.prompt_version}, settings "
        f"version {view.settings_version}"
    ]
    lines += [f"  [{number}] {text}" for number, text in enumerate(view.sentences, start=1)]
    for item in view.fields.values():
        shown = item.display if item.display is not None else "-"
        note = item.status
        if item.sentence is not None:
            note += f", sentence {item.sentence}"
        if item.review_reason is not None and item.status == NEEDS_REVIEW:
            note += f": {item.review_reason}"
        if item.overridden_by is not None:
            note += f" by {item.overridden_by}: {item.override_reason}"
        elif item.decided_by is not None:
            note += f" by {item.decided_by}"
        lines.append(f"{item.label}: {shown} ({note})")
    return "\n".join(lines) + "\n"
