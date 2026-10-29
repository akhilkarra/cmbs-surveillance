"""The review queue, declined items, overrides of extracted values, and label candidates.

**The queue** is every review item open at the end of a day, the book's largest exposure first,
then the longest waiting (Q47). Exposure is the face the book holds, on that day, in the loan's
deal; a loan linked as a whole loan (Q48) counts the face held in every deal holding a note of
it. An item waiting more than five business days is overdue. Business days are Monday to
Friday: holidays are not taken out, so an item waiting over a holiday turns overdue a day early
rather than a day late. There is no holiday calendar among the settings, and a surveillance
queue errs toward flagging.

**Deciding** an item is `cmbs.extraction.service.decide_review`. **Declining** closes an item
the comment cannot settle: the field is left without a value, marked "not determinable", and
the analyst's reason is kept. Either way the item leaves the queue for everyone.

**An override** replaces a value an analyst knows to be wrong, whether it was auto-accepted,
reviewed or not stated, with a reason and a sentence of the stored comment as evidence (Q45).
The field's row is not changed, so the model's answer, its evidence and any review decision stay
stored; `comment_view` lays the override over the field from the moment it was made. A field
still waiting for review is decided through its item instead. Every override and decline is
audited with before and after.

**Label candidates** are the values people chose, reviewed or overridden, which the extraction
quality report proposes for the golden set.
"""

import datetime as dt
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import NoReturn

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.access.roles import Action
from cmbs.access.service import Actor, authorize
from cmbs.audit.service import LOAN, loan_key, record, refuse
from cmbs.book.service import Book, book_as_of, holding, whole_loan_notes, whole_loans_of
from cmbs.deals import service as deals
from cmbs.deals.tables import Deal
from cmbs.distribution.service import canonical_loan
from cmbs.domain import Period, format_money
from cmbs.errors import Refused
from cmbs.extraction import fields as typed_fields
from cmbs.extraction.service import (
    NOT_DETERMINABLE,
    NOT_STATED_DECISION,
    OVERRIDDEN,
    REVIEW_ITEM,
    FieldView,
    NoSuchSentence,
    ReviewView,
    UnknownDeal,
    UnknownItem,
    applicable,
    closed_problem,
    declined_fields,
    field_view,
    latest_extraction,
    latest_overrides,
    review_views,
)
from cmbs.extraction.tables import (
    DECIDED,
    NEEDS_REVIEW,
    NOT_STATED,
    OPEN,
    ExtractedField,
    Extraction,
    ReviewItem,
)
from cmbs.review.tables import FieldOverride, ReviewDecline

OVERDUE_AFTER = 5  # business days
DECLINE = "decline review item"
OVERRIDE = "override value"
REASON_REQUIRED = "a reason is required"
REVIEW = "review"


class ReasonRequired(Refused):
    """A decline or override without a reason."""


class UnknownField(Refused):
    pass


class NoExtraction(Refused):
    """Nothing extracted to override."""


class NoSentence(Refused):
    """An override that cites no sentence."""


class WaitingForReview(Refused):
    """A field still waiting for review is decided through its item, not overridden."""


class Unchanged(Refused):
    """An override that would leave the panel as it is."""


# Business days and the queue's order. Pure.


def business_days(start: dt.date, end: dt.date) -> int:
    """Weekdays after `start`, up to and including `end`: Thursday to the next Tuesday is 3.

    An item that arrived on a weekend starts counting on Monday. Zero when `end` is not after
    `start`.
    """
    days = (end - start).days
    return sum(1 for offset in range(1, days + 1) if (start + dt.timedelta(offset)).weekday() < 5)


def is_overdue(waited: int) -> bool:
    return waited > OVERDUE_AFTER


def waiting_text(waited: int) -> str:
    """'waiting 3 business days', 'overdue, waiting 7 business days'."""
    if waited == 0:
        shown = "waiting less than a business day"
    else:
        shown = f"waiting {waited} business day{'' if waited == 1 else 's'}"
    return f"overdue, {shown}" if is_overdue(waited) else shown


def exposure_to(book: Book | None, deal_ids: Iterable[int]) -> Decimal:
    """The face the book holds in these deals together; zero without a book."""
    return sum((holding(book, deal_id).face for deal_id in set(deal_ids)), Decimal("0.00"))


def end_of_day(day: dt.date) -> dt.datetime:
    """Midnight UTC after `day`: what was done by the end of `day` happened before it."""
    return dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), dt.UTC)


def clean_reason(reason: str | None) -> str:
    """The reason with whitespace collapsed; refused when there is none."""
    cleaned = " ".join((reason or "").split())
    if not cleaned:
        raise ReasonRequired(REASON_REQUIRED)
    return cleaned


def field_spec(name: str) -> typed_fields.FieldSpec:
    """A field by its name ('borrower_request') or label ('borrower request')."""
    found = typed_fields.BY_NAME.get(name) or typed_fields.BY_LABEL.get(" ".join(name.split()))
    if found is None:
        raise UnknownField(f"there is no field named {name!r}")
    return found


@dataclass(frozen=True)
class QueueEntry:
    item: ReviewView
    exposure: Decimal
    waiting_since: dt.date
    waited: int

    @property
    def overdue(self) -> bool:
        return is_overdue(self.waited)

    @property
    def mark(self) -> str:
        return waiting_text(self.waited)


def queue_order(entry: QueueEntry) -> tuple[Decimal, dt.datetime, int]:
    """The largest exposure first, then the longest waiting, then the item number."""
    return (-entry.exposure, entry.item.created_at, entry.item.id)


@dataclass(frozen=True)
class QueueSummary:
    on: dt.date
    open: int
    overdue: int
    oldest_since: dt.date | None
    oldest_waited: int | None
    by_field: dict[str, int]


def summarize(entries: Sequence[QueueEntry], on: dt.date) -> QueueSummary:
    """Counts for risk: open items, overdue items, the oldest, and open items per field label
    (in the fields' own order)."""
    oldest = min(entries, key=lambda entry: (entry.item.created_at, entry.item.id), default=None)
    counted = Counter(entry.item.field for entry in entries)
    return QueueSummary(
        on=on,
        open=len(entries),
        overdue=sum(1 for entry in entries if entry.overdue),
        oldest_since=oldest.waiting_since if oldest else None,
        oldest_waited=oldest.waited if oldest else None,
        by_field={
            spec.label: counted[spec.name] for spec in typed_fields.FIELDS if counted[spec.name]
        },
    )


# The queue.


def _linked_deals(db: Session, deal_id: int, loan: str) -> list[int]:
    """The deals holding a note of the loan's whole loan; just its own deal when not linked."""
    whole_loan = whole_loans_of(db, deal_id).get(loan)
    if whole_loan is None:
        return [deal_id]
    return [deal for deal, _ in whole_loan_notes(db, whole_loan)]


def _open_on(db: Session, day: dt.date) -> Sequence[ReviewItem]:
    """Items created by the end of `day` and not decided, declined or superseded by then."""
    end = end_of_day(day)
    return db.scalars(
        select(ReviewItem).where(
            ReviewItem.created_at < end,
            or_(
                ReviewItem.status == OPEN,
                ReviewItem.decided_at >= end,
                ReviewItem.superseded_at >= end,
            ),
        )
    ).all()


def as_it_stood_open(view: ReviewView) -> ReviewView:
    """An item as it stood while still open: no decision and not superseded. Pure."""
    return replace(
        view,
        status=OPEN,
        decided_by=None,
        decided_at=None,
        decision=None,
        decision_sentence=None,
        superseded_at=None,
        superseded_by=None,
    )


def queue(db: Session, reader: str, *, on: dt.date | None = None) -> list[QueueEntry]:
    """The items open at the end of `on` (today), in queue order, with exposure from the book
    on that day and business days waited by then."""
    authorize(db, reader, Action.READ_PANEL)
    day = on or clock.today()
    book = book_as_of(db, day)
    items = _open_on(db, day)
    entries = []
    for item, view in zip(items, review_views(db, items), strict=True):
        since = view.created_at.astimezone(dt.UTC).date()
        entries.append(
            QueueEntry(
                # The queue lists only items open at the end of `day`; one decided since is
                # shown as it stood then.
                item=as_it_stood_open(view),
                exposure=exposure_to(book, _linked_deals(db, item.deal_id, item.loan_id)),
                waiting_since=since,
                waited=business_days(since, day),
            )
        )
    return sorted(entries, key=queue_order)


def queue_summary(db: Session, reader: str, *, on: dt.date | None = None) -> QueueSummary:
    day = on or clock.today()
    return summarize(queue(db, reader, on=day), day)


# Declining.


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


def _parts(db: Session, row: ExtractedField) -> tuple[Extraction, Deal]:
    extraction = db.get(Extraction, row.extraction_id)
    deal = db.get(Deal, extraction.deal_id) if extraction is not None else None
    if extraction is None or deal is None:  # pragma: no cover - foreign keys
        raise RuntimeError(f"extracted field {row.id} lost its extraction")
    return extraction, deal


def decline(db: Session, user: str, item_id: int, *, reason: str | None) -> FieldView:
    """Close an item the comment cannot settle: the field keeps no value and is marked "not
    determinable", with the analyst's reason. Needs the right to decide review items."""
    actor = authorize(db, user, Action.DECIDE_REVIEW)
    key = str(item_id)
    try:
        cleaned = clean_reason(reason)
    except Refused as error:
        _refuse(db, error, actor, DECLINE, REVIEW_ITEM, key)
    # Locked and re-read, in the order deciding locks them, so a decline and a decision of one
    # item take turns and the second finds it closed.
    item = db.get(ReviewItem, item_id, with_for_update=True, populate_existing=True)
    if item is None:
        _refuse(
            db, UnknownItem(f"there is no review item {item_id}"), actor, DECLINE, REVIEW_ITEM, key
        )
    closed = closed_problem(item)
    if closed is not None:
        _refuse(db, closed, actor, DECLINE, REVIEW_ITEM, key)
    row = db.get(ExtractedField, item.field_id, with_for_update=True, populate_existing=True)
    if row is None:  # pragma: no cover - foreign key
        raise RuntimeError(f"review item {item_id} lost its field")
    extraction, deal = _parts(db, row)
    spec = typed_fields.spec(item.field)
    now = clock.now()
    row.status = NOT_STATED
    row.value = None
    row.sentence = None
    row.decided_by = actor.user
    row.decided_at = now
    item.status = DECIDED
    item.decided_by = actor.user
    item.decided_at = now
    item.decision = NOT_DETERMINABLE
    item.decision_sentence = None
    db.add(ReviewDecline(item_id=item.id, reason=cleaned))
    db.flush()
    record(
        db,
        user=actor.user,
        role=actor.role.value,
        action=DECLINE,
        subject=LOAN,
        subject_key=loan_key(deal.name, item.loan_id),
        field=spec.label,
        before="",
        after=NOT_DETERMINABLE,
        reason=cleaned,
        detail={
            "review_item": item.id,
            "accession": extraction.accession,
            "period": extraction.period.isoformat(),
            "review_reason": item.reason,
        },
    )
    return field_view(row, list(extraction.sentences), None, declined=True)


def declined_reason(db: Session, reader: str, item_id: int) -> str | None:
    """Why an item was declined; None when it was not."""
    authorize(db, reader, Action.READ_PANEL)
    found = db.get(ReviewDecline, item_id)
    return None if found is None else found.reason


# Overrides.


@dataclass(frozen=True)
class Shown:
    """What the panel shows for a field: its status, value and sentence."""

    status: str
    value: str | None
    sentence: int | None


def shown(row: ExtractedField, override: FieldOverride | None, *, declined: bool) -> Shown:
    """The field as the panel shows it now, override included."""
    if override is not None:
        return Shown(OVERRIDDEN, override.value, override.sentence)
    status = NOT_DETERMINABLE if declined else row.status
    return Shown(status, row.value, row.sentence)


def override_problem(
    spec: typed_fields.FieldSpec,
    *,
    loan: str,
    value: str,
    sentence: int | None,
    sentences: int,
    current: Shown,
    waiting: bool = False,
) -> Refused | None:
    """Why `value` citing `sentence` cannot override the field showing `current`, given a
    comment of `sentences` sentences and whether an open review item waits on the field; None
    if it can. Pure; `value` is already canonical."""
    if sentence is None:
        return NoSentence(f"an override cites the sentence that supports the {spec.label}")
    if not 1 <= sentence <= sentences:
        return NoSuchSentence(f"sentence {sentence} does not exist in this comment")
    if waiting or current.status == NEEDS_REVIEW:
        return WaitingForReview(
            f"loan {loan}'s {spec.label} is waiting for review; decide its review item instead"
        )
    if (current.value, current.sentence) == (value, sentence):
        shown_value = typed_fields.display(spec, value)
        return Unchanged(
            f"loan {loan}'s {spec.label} is already {shown_value}, citing sentence {sentence}"
        )
    return None


def _display(spec: typed_fields.FieldSpec, value: str | None) -> str:
    return "" if value is None else typed_fields.display(spec, value)


def override(
    db: Session,
    user: str,
    *,
    deal: str,
    loan: str,
    period: Period,
    field: str,
    value: str,
    sentence: int | None,
    reason: str | None,
) -> FieldView:
    """Replace a field's value in the panel from now on, keeping what was there.

    Overrides the latest extraction of the loan's comment for the period known today. Needs the
    right to override values (an analyst; a PM may not). Refusals are audited.
    """
    actor = authorize(db, user, Action.OVERRIDE_VALUE)
    loan_id = canonical_loan(loan)
    # Until the deal resolves, a refusal is keyed on the name as typed, whitespace collapsed:
    # there is no registered name to use, and the key still reads as what was asked.
    subject_key = loan_key(" ".join(deal.split()), loan_id)
    try:
        spec = field_spec(field)
        chosen = typed_fields.canonical(spec, value)
        cleaned = clean_reason(reason)
        deal_row = deals.by_name(db, deal)
        if deal_row is None:
            raise UnknownDeal(f"no deal named {deal!r}")
        subject_key = loan_key(deal_row.name, loan_id)
        found = latest_extraction(
            db, deal_id=deal_row.id, loan=loan, period=period, day=clock.today()
        )
        if found is None:
            raise NoExtraction(
                f"loan {loan_id} of {deal_row.name} has no extracted comment for {period}"
            )
        extraction, _ = found
        # Locked and re-read: overrides of one field take turns, and each sees the last.
        row = db.scalars(
            select(ExtractedField)
            .where(ExtractedField.extraction_id == extraction.id, ExtractedField.field == spec.name)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        if row is None:
            raise NoExtraction(
                f"loan {loan_id}'s {spec.label} was not extracted from {extraction.accession}"
            )
        declined = row.id in declined_fields(db, [row.id])
        earlier = latest_overrides(
            db, deal_id=deal_row.id, loan=loan_id, period=Period.of(extraction.period)
        ).get(spec.name)
        current = shown(row, applicable(earlier, extraction.comment), declined=declined)
        waiting = db.scalar(
            select(ReviewItem.id).where(ReviewItem.field_id == row.id, ReviewItem.status == OPEN)
        )
        problem = override_problem(
            spec,
            loan=extraction.loan_id,
            value=chosen,
            sentence=sentence,
            sentences=len(extraction.sentences),
            current=current,
            waiting=waiting is not None,
        )
        if problem is not None:
            raise problem
    except Refused as error:
        _refuse(db, error, actor, OVERRIDE, LOAN, subject_key)
    if sentence is None:  # pragma: no cover - override_problem refuses it
        raise RuntimeError("an override without a sentence passed the checks")
    made = FieldOverride(
        deal_id=extraction.deal_id,
        loan_id=extraction.loan_id,
        period=extraction.period,
        field=spec.name,
        field_id=row.id,
        value=chosen,
        sentence=sentence,
        reason=cleaned,
        overridden_by=actor.user,
        overridden_at=clock.now(),
        before_status=current.status,
        before_value=current.value,
        before_sentence=current.sentence,
    )
    db.add(made)
    db.flush()
    record(
        db,
        user=actor.user,
        role=actor.role.value,
        action=OVERRIDE,
        subject=LOAN,
        subject_key=subject_key,
        field=spec.label,
        before=_display(spec, current.value),
        after=typed_fields.display(spec, chosen),
        reason=cleaned,
        detail={
            "override": made.id,
            "sentence": sentence,
            "before_status": current.status,
            "before_sentence": current.sentence,
            "model_value": row.model_value,
            "accession": extraction.accession,
            "period": extraction.period.isoformat(),
        },
    )
    return field_view(row, list(extraction.sentences), None, override=made, declined=declined)


@dataclass(frozen=True)
class OverrideView:
    id: int
    field: str
    label: str
    value: str
    display: str
    sentence: int
    evidence: str
    accession: str
    reason: str
    by: str
    at: dt.datetime
    before_status: str
    before_display: str


def override_history(
    db: Session, reader: str, *, deal: str, loan: str, period: Period
) -> list[OverrideView]:
    """Every override of a loan's fields for a month, oldest first, each with the sentence it
    cites from the comment it was made against."""
    authorize(db, reader, Action.READ_PANEL)
    deal_row = deals.by_name(db, deal)
    if deal_row is None:
        raise UnknownDeal(f"no deal named {deal!r}")
    rows = db.execute(
        select(FieldOverride, Extraction)
        .join(ExtractedField, ExtractedField.id == FieldOverride.field_id)
        .join(Extraction, Extraction.id == ExtractedField.extraction_id)
        .where(
            FieldOverride.deal_id == deal_row.id,
            FieldOverride.loan_id == canonical_loan(loan),
            FieldOverride.period == period.first_day,
        )
        .order_by(FieldOverride.overridden_at, FieldOverride.id)
    ).all()
    views = []
    for made, cited in rows:
        spec = typed_fields.spec(made.field)
        views.append(
            OverrideView(
                id=made.id,
                field=made.field,
                label=spec.label,
                value=made.value,
                display=typed_fields.display(spec, made.value),
                sentence=made.sentence,
                evidence=cited.sentences[made.sentence - 1],
                accession=cited.accession,
                reason=made.reason,
                by=made.overridden_by,
                at=made.overridden_at,
                before_status=made.before_status,
                before_display=_display(spec, made.before_value),
            )
        )
    return views


# Label candidates.


@dataclass(frozen=True)
class LabelCandidate:
    """A value a person chose, proposed as a golden-set label for the comment it came from."""

    deal: str
    loan: str
    period: Period
    accession: str
    extraction_id: int
    field: str
    label: str
    value: str  # canonical, or "not stated"
    sentence: int | None
    source: str  # "override" or "review"
    by: str
    at: dt.datetime
    model_value: str | None


def review_choice(
    row: ExtractedField, *, declined: bool
) -> tuple[str, int | None, str, dt.datetime] | None:
    """(value, sentence, by, at) of a reviewer's decision; None for a field nobody decided or
    one declined as not determinable. Pure."""
    if declined or row.decided_by is None or row.decided_at is None:
        return None
    value = row.value if row.value is not None else NOT_STATED_DECISION
    return value, row.sentence, row.decided_by, row.decided_at


type CandidateKey = tuple[str, str, Period, str]


def latest_per_field(candidates: Iterable[LabelCandidate]) -> list[LabelCandidate]:
    """The latest candidate per deal, loan, month and field, oldest first; an override and a
    review at the same moment keep the override. Pure."""
    latest: dict[CandidateKey, LabelCandidate] = {}
    for found in candidates:
        key = (found.deal, found.loan, found.period, found.field)
        held = latest.get(key)
        rank = (found.at, found.source == "override")
        if held is None or rank > (held.at, held.source == "override"):
            latest[key] = found
    return sorted(latest.values(), key=lambda item: (item.at, item.extraction_id, item.field))


def label_candidates(db: Session, reader: str) -> list[LabelCandidate]:
    """The latest value a person chose for each field of each loan's month, reviewed or
    overridden, oldest first. Each names the comment (extraction) its sentence comes from."""
    authorize(db, reader, Action.READ_PANEL)
    found: list[LabelCandidate] = []
    reviewed = db.execute(
        select(ExtractedField, Extraction, Deal.name)
        .join(Extraction, Extraction.id == ExtractedField.extraction_id)
        .join(Deal, Deal.id == Extraction.deal_id)
        .where(ExtractedField.decided_by.is_not(None))
    ).all()
    declined = declined_fields(db, [row.id for row, _, _ in reviewed])
    for row, extraction, deal in reviewed:
        choice = review_choice(row, declined=row.id in declined)
        if choice is not None:
            found.append(_candidate(deal, extraction, row, REVIEW, *choice))
    overridden = db.execute(
        select(FieldOverride, ExtractedField, Extraction, Deal.name)
        .join(ExtractedField, ExtractedField.id == FieldOverride.field_id)
        .join(Extraction, Extraction.id == ExtractedField.extraction_id)
        .join(Deal, Deal.id == Extraction.deal_id)
    ).all()
    for made, row, extraction, deal in overridden:
        found.append(
            _candidate(
                deal,
                extraction,
                row,
                "override",
                made.value,
                made.sentence,
                made.overridden_by,
                made.overridden_at,
            )
        )
    return latest_per_field(found)


def _candidate(
    deal: str,
    extraction: Extraction,
    row: ExtractedField,
    source: str,
    value: str,
    sentence: int | None,
    by: str,
    at: dt.datetime,
) -> LabelCandidate:
    return LabelCandidate(
        deal=deal,
        loan=extraction.loan_id,
        period=Period.of(extraction.period),
        accession=extraction.accession,
        extraction_id=extraction.id,
        field=row.field,
        label=typed_fields.spec(row.field).label,
        value=value,
        sentence=sentence,
        source=source,
        by=by,
        at=at,
        model_value=row.model_value,
    )


# Plain text for the command line.


def render_queue(entries: Sequence[QueueEntry]) -> str:
    if not entries:
        return "the review queue is empty\n"
    lines = [
        f"item {entry.item.id}: loan {entry.item.loan} of {entry.item.deal}, {entry.item.period}, "
        f"{entry.item.label} ({entry.item.reason}); exposure {format_money(entry.exposure)}; "
        f"since {entry.waiting_since}, {entry.mark}"
        for entry in entries
    ]
    return "\n".join(lines) + "\n"


def render_queue_summary(summary: QueueSummary) -> str:
    noun = "item" if summary.open == 1 else "items"
    lines = [f"{summary.open} open {noun} on {summary.on}, {summary.overdue} overdue"]
    if summary.oldest_since is not None:
        lines.append(
            f"oldest waiting since {summary.oldest_since} "
            f"({waiting_text(summary.oldest_waited or 0)})"
        )
    lines += [f"{label}: {count}" for label, count in summary.by_field.items()]
    return "\n".join(lines) + "\n"
