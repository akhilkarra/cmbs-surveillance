"""Settings proposals

Proposed changes to the settings ledger and the decision on each: who proposed what and why,
under which role, and who approved or rejected it, when and why. The check constraints back up
the service: a decision is complete or absent, only an approval names a version, and nobody
approves their own proposal (grill log Q46).

Revision ID: 0006
Revises: 0002
Create Date: 2026-10-28 09:26:10.813730
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "settings_proposal",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("proposed_by", sa.String(length=64), nullable=False),
        sa.Column("proposer_role", sa.String(length=16), nullable=False),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("proposed_from", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("quality_report", sa.String(length=256), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("decided_by", sa.String(length=64), nullable=True),
        sa.Column("decider_role", sa.String(length=16), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "(status = 'approved') = (version IS NOT NULL)",
            name=op.f("ck_settings_proposal_approval_version"),
        ),
        sa.CheckConstraint(
            "(status = 'awaiting approval') = "
            "(decided_by IS NULL AND decider_role IS NULL AND decided_at IS NULL)",
            name=op.f("ck_settings_proposal_decided_unless_awaiting"),
        ),
        sa.CheckConstraint(
            "status <> 'approved' OR decided_by <> proposed_by",
            name=op.f("ck_settings_proposal_not_self_approved"),
        ),
        sa.CheckConstraint(
            "status <> 'rejected' OR decision_reason IS NOT NULL",
            name=op.f("ck_settings_proposal_rejection_has_reason"),
        ),
        sa.CheckConstraint(
            "status IN ('awaiting approval', 'approved', 'rejected')",
            name=op.f("ck_settings_proposal_known_status"),
        ),
        sa.CheckConstraint(
            "decided_at IS NULL OR decided_at >= proposed_at",
            name=op.f("ck_settings_proposal_decided_later"),
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["app.user.name"], name=op.f("fk_settings_proposal_decided_by_user")
        ),
        sa.ForeignKeyConstraint(
            ["proposed_by"], ["app.user.name"], name=op.f("fk_settings_proposal_proposed_by_user")
        ),
        sa.ForeignKeyConstraint(
            ["version"],
            ["app.settings_version.version"],
            name=op.f("fk_settings_proposal_version_settings_version"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_settings_proposal")),
        sa.UniqueConstraint("version", name=op.f("uq_settings_proposal_version")),
        schema="app",
    )
    op.create_index(
        op.f("ix_app_settings_proposal_status"),
        "settings_proposal",
        ["status"],
        unique=False,
        schema="app",
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_app_settings_proposal_status"), table_name="settings_proposal", schema="app"
    )
    op.drop_table("settings_proposal", schema="app")
