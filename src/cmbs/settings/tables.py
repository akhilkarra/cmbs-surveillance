from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cmbs.db.models import APP_SCHEMA, Base

AWAITING = "awaiting approval"
APPROVED = "approved"
REJECTED = "rejected"
PROPOSAL_STATUSES = (AWAITING, APPROVED, REJECTED)


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


class SettingsProposal(Base):
    """A proposed change to the settings, and the decision on it.

    `changes` holds the new value of each field it changes, `proposed_from` the value each of
    those fields had when it was proposed (so an approval can tell if another change got there
    first). An approval creates settings version `version`. The constraints repeat the service's
    rules as a backstop: a decision is complete or absent, only an approval has a version, and
    nobody approves their own proposal.
    """

    __tablename__ = "settings_proposal"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in PROPOSAL_STATUSES) + ")",
            name="known_status",
        ),
        CheckConstraint(
            f"(status = '{AWAITING}') = "
            "(decided_by IS NULL AND decider_role IS NULL AND decided_at IS NULL)",
            name="decided_unless_awaiting",
        ),
        CheckConstraint(
            f"(status = '{APPROVED}') = (version IS NOT NULL)", name="approval_version"
        ),
        CheckConstraint(
            f"status <> '{APPROVED}' OR decided_by <> proposed_by", name="not_self_approved"
        ),
        CheckConstraint(
            f"status <> '{REJECTED}' OR decision_reason IS NOT NULL", name="rejection_has_reason"
        ),
        CheckConstraint("decided_at IS NULL OR decided_at >= proposed_at", name="decided_later"),
        {"schema": APP_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    proposed_by: Mapped[str] = mapped_column(ForeignKey(f"{APP_SCHEMA}.user.name"))
    proposer_role: Mapped[str] = mapped_column(String(16))
    proposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    changes: Mapped[dict[str, Any]] = mapped_column(JSONB)
    proposed_from: Mapped[dict[str, Any]] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(Text)
    quality_report: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(20), default=AWAITING, index=True)
    decided_by: Mapped[str | None] = mapped_column(ForeignKey(f"{APP_SCHEMA}.user.name"))
    decider_role: Mapped[str | None] = mapped_column(String(16))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int | None] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.settings_version.version"), unique=True
    )
