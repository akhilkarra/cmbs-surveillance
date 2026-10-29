"""Recorded model calls (the replay cache), in `app`: one row per distinct request.

The key covers the model, the prompt version, the questions, the state and the exact input text
(`cmbs.model.policy.recording_key`), so a row is only ever replayed for the identical request.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cmbs.db.models import APP_SCHEMA, Base


class ModelCall(Base):
    __tablename__ = "model_call"
    __table_args__ = (
        CheckConstraint("classification IN ('public', 'restricted')", name="known_classification"),
        CheckConstraint("answered_by = model", name="answered_by_model_asked"),
        {"schema": APP_SCHEMA},
    )

    call_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    model: Mapped[str] = mapped_column(String(64))
    answered_by: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[int] = mapped_column(Integer)
    questions_sha256: Mapped[str] = mapped_column(String(64))
    state_sha256: Mapped[str] = mapped_column(String(64))
    input_sha256: Mapped[str] = mapped_column(String(64), index=True)
    classification: Mapped[str] = mapped_column(String(16))
    # The settings version in force when the call was made (grill log Q46). Not a foreign key:
    # a recording imported from a file may name a version this database never had.
    settings_version: Mapped[int] = mapped_column(Integer)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
