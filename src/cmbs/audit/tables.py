from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cmbs.db.models import APP_SCHEMA, Base


class AuditEntry(Base):
    """One recorded event. Rows are never updated or deleted (a trigger enforces it)."""

    __tablename__ = "audit_entry"
    __table_args__ = (
        CheckConstraint("outcome IN ('done', 'refused')", name="known_outcome"),
        {"schema": APP_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    user_name: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str | None] = mapped_column(String(16))
    run_id: Mapped[int | None] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str | None] = mapped_column(String(32))
    subject_key: Mapped[str | None] = mapped_column(String(256), index=True)
    field: Mapped[str | None] = mapped_column(String(64))
    before: Mapped[str | None] = mapped_column(Text)
    after: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String(8), default="done")
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
