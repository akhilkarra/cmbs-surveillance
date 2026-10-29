"""Overrides of extracted values, and the reasons review items were declined.

`silver.field_override` is panel data: an analyst's value for one field of one loan's comment
for one month, replacing what the model or a reviewer gave, from the moment it was made. It
belongs to (deal, loan, period, field), not to one extraction, so it stays on top when the same
comment is read again under a new model or prompt; `field_id` is the extracted field it was made
against, whose extraction holds the comment its sentence cites. The field's own row is never
changed by an override, so the model's answer, its evidence and any review decision stay stored.
A later override of the same field supersedes an earlier one; each records what the panel
showed before it.

`app.review_decline` keeps the reason an analyst gave for declining a review item the comment
cannot settle. The item itself is closed in `app.review_item` (decision "not determinable").

Both tables are append-only: a trigger refuses UPDATE, DELETE and TRUNCATE.
"""

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from cmbs.db.models import APP_SCHEMA, Base


class FieldOverride(Base):
    __tablename__ = "field_override"
    __table_args__ = (
        CheckConstraint("sentence >= 1", name="sentence_is_numbered"),
        CheckConstraint("btrim(reason) <> ''", name="reason_given"),
        CheckConstraint("btrim(value) <> ''", name="value_given"),
        CheckConstraint("extract(day FROM period) = 1", name="period_is_first_of_month"),
        Index("ix_field_override_key_at", "deal_id", "loan_id", "period", "field", "overridden_at"),
        {"schema": "silver"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("silver.deal.id"))
    loan_id: Mapped[str] = mapped_column(String(32))
    period: Mapped[date] = mapped_column(Date)
    field: Mapped[str] = mapped_column(String(32))
    field_id: Mapped[int] = mapped_column(ForeignKey("silver.extracted_field.id"))
    # The canonical value ('forbearance', '2025-03-06', '0.71') and the sentence that backs it.
    value: Mapped[str] = mapped_column(Text)
    sentence: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    overridden_by: Mapped[str] = mapped_column(ForeignKey(f"{APP_SCHEMA}.user.name"))
    overridden_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # What the panel showed just before: its status, value and sentence.
    before_status: Mapped[str] = mapped_column(String(20))
    before_value: Mapped[str | None] = mapped_column(Text)
    before_sentence: Mapped[int | None] = mapped_column(Integer)


class ReviewDecline(Base):
    __tablename__ = "review_decline"
    __table_args__ = (
        CheckConstraint("btrim(reason) <> ''", name="reason_given"),
        {"schema": APP_SCHEMA},
    )

    item_id: Mapped[int] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.review_item.id"), primary_key=True
    )
    reason: Mapped[str] = mapped_column(Text)
