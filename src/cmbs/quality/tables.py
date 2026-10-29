"""The golden set, its labels, and quality reports (application state, schema `app`).

app.golden_item    one labelled comment: imported from a labels file, or proposed from a value
                   a person decided on the panel; a proposed comment joins the golden set only
                   when a second analyst confirms it
app.golden_label   one labeller's answer for one field of one golden comment ("not stated" is
                   a null value)
app.quality_report one measurement of extraction against the golden set, kept for good, with
                   the model, prompt version and settings version it measured
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cmbs.db.models import APP_SCHEMA, Base

GOLDEN = "golden"
PROPOSED = "proposed"
REJECTED = "rejected"
ITEM_STATUSES = (GOLDEN, PROPOSED, REJECTED)
IMPORTED = "imported"
PANEL = "panel"
ITEM_SOURCES = (IMPORTED, PANEL)


def _known(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


class GoldenItem(Base):
    __tablename__ = "golden_item"
    __table_args__ = (
        CheckConstraint(_known("status", ITEM_STATUSES), name="known_status"),
        CheckConstraint(_known("source", ITEM_SOURCES), name="known_source"),
        CheckConstraint(
            f"(status = '{GOLDEN}') = (joined_at IS NOT NULL)", name="joined_when_golden"
        ),
        CheckConstraint(
            f"status <> '{REJECTED}' OR (decided_by IS NOT NULL AND reason IS NOT NULL)",
            name="rejection_attributed",
        ),
        CheckConstraint(
            f"source <> '{PANEL}' OR (deal_id IS NOT NULL AND accession IS NOT NULL)",
            name="panel_item_traced",
        ),
        {"schema": APP_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(160), unique=True)
    comment: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), index=True)
    deal_id: Mapped[int | None] = mapped_column(ForeignKey("silver.deal.id"))
    loan: Mapped[str | None] = mapped_column(String(32))
    period: Mapped[date | None] = mapped_column(Date)
    accession: Mapped[str | None] = mapped_column(ForeignKey("raw.filing.accession"))
    sentences: Mapped[list[str]] = mapped_column(JSONB)
    added_by: Mapped[str] = mapped_column(ForeignKey(f"{APP_SCHEMA}.user.name"))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(ForeignKey(f"{APP_SCHEMA}.user.name"))
    reason: Mapped[str | None] = mapped_column(Text)


class GoldenLabel(Base):
    __tablename__ = "golden_label"
    __table_args__ = (
        UniqueConstraint("item_id", "field", "labeller"),
        {"schema": APP_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(ForeignKey(f"{APP_SCHEMA}.golden_item.id"), index=True)
    field: Mapped[str] = mapped_column(String(32))
    labeller: Mapped[str] = mapped_column(ForeignKey(f"{APP_SCHEMA}.user.name"))
    value: Mapped[str | None] = mapped_column(Text)
    sentence: Mapped[int | None] = mapped_column(Integer)
    labelled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class QualityReport(Base):
    __tablename__ = "quality_report"
    __table_args__ = (
        CheckConstraint("items > 0", name="has_items"),
        {"schema": APP_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    built_by: Mapped[str] = mapped_column(ForeignKey(f"{APP_SCHEMA}.user.name"))
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[int] = mapped_column(Integer)
    settings_version: Mapped[int] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.settings_version.version")
    )
    items: Mapped[int] = mapped_column(Integer)
    # Per field: counts, rates and intervals; plus labeller agreement and the band.
    results: Mapped[dict[str, Any]] = mapped_column(JSONB)
    flags: Mapped[list[str]] = mapped_column(JSONB)
    previous_id: Mapped[int | None] = mapped_column(ForeignKey(f"{APP_SCHEMA}.quality_report.id"))
    # The predictions measured, so the report can be rebuilt and checked.
    predictions: Mapped[dict[str, Any]] = mapped_column(JSONB)
