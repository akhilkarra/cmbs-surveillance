"""The record of every panel export: who took which snapshot, as of when, and its hash.

`app.panel_export` is a log, like the audit trail: rows are added, never changed or removed
(a trigger refuses both). The file itself is not kept, only its SHA-256. Taking the same export
again gives the same file only while nothing published by its as-of date has been loaded since:
an export holds what had been published by its as-of date as the panel knew it when the export
was taken, and a later backfill can add filings published before that date. The record says
when each export was taken.
"""

from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from cmbs.db.models import APP_SCHEMA, Base

FORMATS = ("csv", "parquet")


class PanelExport(Base):
    __tablename__ = "panel_export"
    __table_args__ = (
        CheckConstraint(
            "format IN (" + ", ".join(f"'{name}'" for name in FORMATS) + ")", name="known_format"
        ),
        CheckConstraint("row_count >= 0", name="row_count_not_negative"),
        CheckConstraint("char_length(sha256) = 64", name="sha256_length"),
        {"schema": APP_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exported_by: Mapped[str] = mapped_column(ForeignKey(f"{APP_SCHEMA}.user.name"))
    exported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    as_of: Mapped[date] = mapped_column(Date)
    # The deal exported; None for the whole panel.
    deal_id: Mapped[int | None] = mapped_column(ForeignKey("silver.deal.id"))
    format: Mapped[str] = mapped_column(String(16))
    file_name: Mapped[str] = mapped_column(String(256))
    row_count: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    settings_version: Mapped[int] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.settings_version.version")
    )
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[int] = mapped_column(Integer)
