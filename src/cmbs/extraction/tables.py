"""Extractions, their fields, and review items.

`silver.extraction` is one comment read under one model and prompt version, tied to the 10-D it
came from (so it is visible from that filing's date) and to the recorded call that answered it.
`silver.extracted_field` keeps, per field, both the model's answer and the panel's value: they
differ while a field waits for review, and after a reviewer (or, later, an override) decides.
`app.review_item` is a field waiting for a person, with what the review queue will need.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cmbs.db.models import APP_SCHEMA, Base

AUTO_ACCEPTED = "auto-accepted"
NEEDS_REVIEW = "needs review"
NOT_STATED = "not stated"
HUMAN_REVIEWED = "human-reviewed"
FIELD_STATUSES = (AUTO_ACCEPTED, NEEDS_REVIEW, NOT_STATED, HUMAN_REVIEWED)

OPEN = "open"
DECIDED = "decided"
REVIEW_STATUSES = (OPEN, DECIDED)

PROBABILITY = Numeric(6, 5)


def _known(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


class Extraction(Base):
    __tablename__ = "extraction"
    __table_args__ = (
        UniqueConstraint("accession", "loan_id", "source_table", "model", "prompt_version"),
        {"schema": "silver"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("silver.deal.id"))
    loan_id: Mapped[str] = mapped_column(String(32))
    period: Mapped[date] = mapped_column(Date)
    accession: Mapped[str] = mapped_column(ForeignKey("raw.filing.accession"))
    source_table: Mapped[str] = mapped_column(String(128))
    comment: Mapped[str] = mapped_column(Text)
    sentences: Mapped[list[str]] = mapped_column(JSONB)
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[int] = mapped_column(Integer)
    settings_version: Mapped[int] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.settings_version.version")
    )
    model_call: Mapped[str] = mapped_column(ForeignKey(f"{APP_SCHEMA}.model_call.call_id"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey(f"{APP_SCHEMA}.run.id"))
    extracted_by: Mapped[str] = mapped_column(ForeignKey(f"{APP_SCHEMA}.user.name"))
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExtractedField(Base):
    __tablename__ = "extracted_field"
    __table_args__ = (
        UniqueConstraint("extraction_id", "field"),
        CheckConstraint(_known("status", FIELD_STATUSES), name="known_status"),
        CheckConstraint(
            "(status IN ('auto-accepted', 'human-reviewed')) = (value IS NOT NULL)",
            name="value_when_accepted",
        ),
        CheckConstraint(
            "(status = 'human-reviewed') = (decided_by IS NOT NULL AND decided_at IS NOT NULL)",
            name="reviewed_is_attributed",
        ),
        {"schema": "silver"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    extraction_id: Mapped[int] = mapped_column(
        ForeignKey("silver.extraction.id", ondelete="CASCADE"), index=True
    )
    field: Mapped[str] = mapped_column(String(32))
    # The model's answer, kept whatever the panel shows. Empty when the field was not asked.
    model_value: Mapped[str | None] = mapped_column(Text)
    answer_probability: Mapped[Decimal | None] = mapped_column(PROBABILITY)
    support_probability: Mapped[Decimal | None] = mapped_column(PROBABILITY)
    model_evidence: Mapped[str | None] = mapped_column(Text)
    model_sentence: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(16))
    review_reason: Mapped[str | None] = mapped_column(Text)
    # The panel's value and the sentence that backs it.
    status: Mapped[str] = mapped_column(String(20))
    value: Mapped[str | None] = mapped_column(Text)
    sentence: Mapped[int | None] = mapped_column(Integer)
    decided_by: Mapped[str | None] = mapped_column(ForeignKey(f"{APP_SCHEMA}.user.name"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewItem(Base):
    __tablename__ = "review_item"
    __table_args__ = (
        CheckConstraint(_known("status", REVIEW_STATUSES), name="known_status"),
        CheckConstraint(
            "(status = 'decided') = (decided_by IS NOT NULL AND decided_at IS NOT NULL "
            "AND decision IS NOT NULL)",
            name="decided_is_complete",
        ),
        {"schema": APP_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    field_id: Mapped[int] = mapped_column(
        ForeignKey("silver.extracted_field.id", ondelete="CASCADE"), unique=True
    )
    deal_id: Mapped[int] = mapped_column(ForeignKey("silver.deal.id"))
    loan_id: Mapped[str] = mapped_column(String(32))
    field: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    question: Mapped[str] = mapped_column(Text)
    probability: Mapped[Decimal] = mapped_column(PROBABILITY)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(16), default=OPEN, index=True)
    decided_by: Mapped[str | None] = mapped_column(ForeignKey(f"{APP_SCHEMA}.user.name"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision: Mapped[str | None] = mapped_column(Text)
    decision_sentence: Mapped[int | None] = mapped_column(Integer)
