from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cmbs.db.models import APP_SCHEMA, Base


class SettingsVersion(Base):
    """One approved set of settings. A version takes effect at `approved_at` and stays in force
    until the next version's `approved_at`."""

    __tablename__ = "settings_version"
    __table_args__ = {"schema": APP_SCHEMA}

    version: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    values: Mapped[dict[str, Any]] = mapped_column(JSONB)
    proposed_by: Mapped[str] = mapped_column(String(64))
    approved_by: Mapped[str] = mapped_column(String(64))
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
