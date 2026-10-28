"""Extract special-servicing comments into the panel, and decide what waits for review.

`extract_comments` reads a deal's stored 10-D comments for a month (public text), asks the
model through the replay cache, runs the decision table on every field and stores the result:
auto-accepted values go straight to the panel, the rest wait in review items or are marked not
stated. Each comment is read once per model and prompt version. `extract_text` reads any text,
public or restricted, without storing a panel value (there is no store yet for restricted
sources such as an investor portal).

`decide_review` is a reviewer's decision: it fills the panel value, attributed and audited.

The checks that can refuse run before anything is written: who is acting, the settings in force
(an exact model version, a prompt version this code asks), the deal and its comments, then
every model call. A refusal is audited on its own connection and nothing of the run is kept.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import NoReturn

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.access.roles import Action
from cmbs.access.service import Actor, authorize
from cmbs.audit.service import LOAN, loan_key, record, refuse
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
from cmbs.extraction.sentences import Sentence, numbered, split
from cmbs.extraction.tables import (
    AUTO_ACCEPTED,
    DECIDED,
    HUMAN_REVIEWED,
    NEEDS_REVIEW,
    NOT_STATED,
    OPEN,
    ExtractedField,
    Extraction,
    ReviewItem,
)
from cmbs.filings.tables import Filing
from cmbs.model.client import Answered, Ask, ModelClient
from cmbs.model.policy import Classification, check_pinned
from cmbs.settings.service import InForce, in_force

EXTRACT = "extract comment"
DECIDE = "decide review item"
DEAL = "deal"
REVIEW_ITEM = "review item"
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
    db: Session, error: Refused, actor: Actor, action: str, subject: str, key: str
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
            model_call=reading.answered.recording.key,
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
    for result in reading.results.values():
        row = _field_row(extraction_id, result)
        db.add(row)
        db.flush()
        if result.decision.outcome is Outcome.REVIEW and result.judgment is not None:
            reason = result.decision.reason or ""
            question, probability = _review_question(result.judgment, reason)
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
            "model_call": reading.answered.recording.key,
            "replayed": reading.answered.replayed,
            "outcomes": outcomes,
        },
    )
    return Extracted(
        loan=comment.loan_id,
        accession=comment.accession,
        extraction_id=extraction_id,
        model_call=reading.answered.recording.key,
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
                classification=Classification.PUBLIC,  # 10-Ds are public on EDGAR
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

    @property
    def typed(self) -> str | dt.date | Decimal | None:
        if self.value is None:
            return None
        return typed_fields.typed(typed_fields.spec(self.field), self.value)


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


def field_view(row: ExtractedField, sentences: list[str], as_of: dt.date | None) -> FieldView:
    """A field as the panel shows it. A decision taken after `as_of` is not yet visible."""
    spec = typed_fields.spec(row.field)
    hidden = as_of is not None and row.decided_at is not None and row.decided_at.date() > as_of
    status, value, sentence = (
        (NEEDS_REVIEW, None, None) if hidden else (row.status, row.value, row.sentence)
    )
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
    """The latest extraction of a loan's comment for a period, as known on `as_of` (today)."""
    authorize(db, reader, Action.READ_PANEL)
    deal_row = deals.by_name(db, deal)
    if deal_row is None:
        raise UnknownDeal(f"no deal named {deal!r}")
    day = as_of or clock.today()
    found = db.execute(
        select(Extraction, Filing.filing_date)
        .join(Filing, Filing.accession == Extraction.accession)
        .where(
            Extraction.deal_id == deal_row.id,
            Extraction.loan_id == canonical_loan(loan),
            Extraction.period == period.first_day,
            Filing.filing_date <= day,
        )
        .order_by(Extraction.id.desc())
        .limit(1)
    ).one_or_none()
    if found is None:
        return None
    extraction, filed = found
    rows = db.scalars(
        select(ExtractedField).where(ExtractedField.extraction_id == extraction.id)
    ).all()
    by_field = {row.field: row for row in rows}
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
            spec.name: field_view(by_field[spec.name], extraction.sentences, as_of)
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
    )


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


def decide_review(db: Session, user: str, item_id: int, *, value: str, sentence: int) -> FieldView:
    """A reviewer's value for the field, backed by a sentence of the comment."""
    actor = authorize(db, user, Action.DECIDE_REVIEW)
    # Locked and re-read, so two reviewers cannot both decide the item.
    item = db.get(ReviewItem, item_id, with_for_update=True, populate_existing=True)
    if item is None:
        _refuse_item(db, UnknownItem(f"there is no review item {item_id}"), actor, item_id)
    if item.status != OPEN:
        error = AlreadyDecided(f"review item {item_id} was already decided by {item.decided_by}")
        _refuse_item(db, error, actor, item_id)
    row = db.get(ExtractedField, item.field_id, with_for_update=True, populate_existing=True)
    extraction = db.get(Extraction, row.extraction_id) if row is not None else None
    deal = db.get(Deal, item.deal_id)
    if row is None or extraction is None or deal is None:  # pragma: no cover - foreign keys
        raise RuntimeError(f"review item {item_id} lost its field")
    spec = typed_fields.spec(item.field)
    try:
        chosen = typed_fields.canonical(spec, value)
    except Refused as invalid:
        _refuse_item(db, invalid, actor, item_id)
    if numbered(split(extraction.comment), sentence) is None:
        error = NoSuchSentence(f"sentence {sentence} does not exist in this comment")
        _refuse_item(db, error, actor, item_id)
    now = clock.now()
    before = typed_fields.display(spec, row.value) if row.value is not None else ""
    row.status = HUMAN_REVIEWED
    row.value = chosen
    row.sentence = sentence
    row.decided_by = actor.user
    row.decided_at = now
    item.status = DECIDED
    item.decided_by = actor.user
    item.decided_at = now
    item.decision = chosen
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
        after=typed_fields.display(spec, chosen),
        reason=f"sentence {sentence}",
        detail={
            "review_item": item.id,
            "accession": extraction.accession,
            "period": extraction.period.isoformat(),
            "review_reason": item.reason,
        },
    )
    return field_view(row, list(extraction.sentences), None)
