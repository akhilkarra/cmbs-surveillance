"""Alerts, alert builds and whole loans

Monthly alerts with their shared lifecycle state, the loans each alert covers, and every vintage
of a deal-month built, never overwritten; an alert a later vintage no longer supports is marked
superseded, so only one alert per subject, month and rule is not superseded (Q25, Q44). Whole
loans link the pari passu notes of one loan held through several deals, so a linked loan
raises one alert (Q48). All application state in `app`.

Revision ID: 0008
Revises: 0003
Create Date: 2026-10-28 21:00:17.311077
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "whole_loan",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("linked_by", sa.String(length=64), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["linked_by"], ["app.user.name"], name=op.f("fk_whole_loan_linked_by_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_whole_loan")),
        schema="app",
    )
    op.create_table(
        "alert_build",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("deal_id", sa.Integer(), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("accession", sa.String(length=20), nullable=False),
        sa.Column("report_accession", sa.String(length=20), nullable=True),
        sa.Column("compared_with", sa.String(length=20), nullable=True),
        sa.Column("compared_report", sa.String(length=20), nullable=True),
        sa.Column("settings_version", sa.Integer(), nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("built_by", sa.String(length=64), nullable=True),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "extract(day FROM period) = 1", name=op.f("ck_alert_build_period_is_first_of_month")
        ),
        sa.ForeignKeyConstraint(
            ["accession"], ["raw.filing.accession"], name=op.f("fk_alert_build_accession_filing")
        ),
        sa.ForeignKeyConstraint(
            ["built_by"], ["app.user.name"], name=op.f("fk_alert_build_built_by_user")
        ),
        sa.ForeignKeyConstraint(
            ["compared_report"],
            ["raw.filing.accession"],
            name=op.f("fk_alert_build_compared_report_filing"),
        ),
        sa.ForeignKeyConstraint(
            ["compared_with"],
            ["raw.filing.accession"],
            name=op.f("fk_alert_build_compared_with_filing"),
        ),
        sa.ForeignKeyConstraint(
            ["deal_id"], ["silver.deal.id"], name=op.f("fk_alert_build_deal_id_deal")
        ),
        sa.ForeignKeyConstraint(
            ["report_accession"],
            ["raw.filing.accession"],
            name=op.f("fk_alert_build_report_accession_filing"),
        ),
        sa.ForeignKeyConstraint(["run_id"], ["app.run.id"], name=op.f("fk_alert_build_run_id_run")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_build")),
        schema="app",
    )
    op.create_index(
        "ix_app_alert_build_deal_period",
        "alert_build",
        ["deal_id", "period", "id"],
        unique=False,
        schema="app",
    )
    op.create_table(
        "whole_loan_note",
        sa.Column("deal_id", sa.Integer(), nullable=False),
        sa.Column("loan", sa.Text(), nullable=False),
        sa.Column("whole_loan_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["deal_id"], ["silver.deal.id"], name=op.f("fk_whole_loan_note_deal_id_deal")
        ),
        sa.ForeignKeyConstraint(
            ["whole_loan_id"],
            ["app.whole_loan.id"],
            name=op.f("fk_whole_loan_note_whole_loan_id_whole_loan"),
        ),
        sa.PrimaryKeyConstraint("deal_id", "loan", name=op.f("pk_whole_loan_note")),
        sa.UniqueConstraint(
            "whole_loan_id", "deal_id", name=op.f("uq_whole_loan_note_whole_loan_id")
        ),
        schema="app",
    )
    op.create_table(
        "alert",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("subject", sa.String(length=64), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("rule", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("added", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("whole_loan_id", sa.Integer(), nullable=True),
        sa.Column("settings_version", sa.Integer(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("raised_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("raised_in", sa.Integer(), nullable=False),
        sa.Column("superseded_by", sa.Integer(), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_reason", sa.Text(), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("acknowledged_by", sa.String(length=64), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assigned_to", sa.String(length=64), nullable=True),
        sa.Column("assigned_by", sa.String(length=64), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assign_note", sa.Text(), nullable=True),
        sa.Column("closed_by", sa.String(length=64), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_note", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "severity IN ('high', 'medium', 'low')", name=op.f("ck_alert_known_severity")
        ),
        sa.CheckConstraint(
            "state <> 'acknowledged' OR acknowledged_by IS NOT NULL",
            name=op.f("ck_alert_acknowledged_by_someone"),
        ),
        sa.CheckConstraint(
            "state <> 'assigned' OR assigned_to IS NOT NULL",
            name=op.f("ck_alert_assigned_to_someone"),
        ),
        sa.CheckConstraint(
            "state <> 'closed' OR (closed_by IS NOT NULL AND close_note IS NOT NULL)",
            name=op.f("ck_alert_closed_with_a_note"),
        ),
        sa.CheckConstraint(
            "state IN ('new', 'acknowledged', 'assigned', 'closed')",
            name=op.f("ck_alert_known_state"),
        ),
        sa.CheckConstraint(
            "(superseded_by IS NULL) = (superseded_at IS NULL) "
            "AND (superseded_by IS NULL) = (superseded_reason IS NULL)",
            name=op.f("ck_alert_superseded_with_a_reason"),
        ),
        sa.CheckConstraint(
            "extract(day FROM period) = 1", name=op.f("ck_alert_period_is_first_of_month")
        ),
        sa.ForeignKeyConstraint(
            ["acknowledged_by"], ["app.user.name"], name=op.f("fk_alert_acknowledged_by_user")
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by"], ["app.user.name"], name=op.f("fk_alert_assigned_by_user")
        ),
        sa.ForeignKeyConstraint(
            ["assigned_to"], ["app.user.name"], name=op.f("fk_alert_assigned_to_user")
        ),
        sa.ForeignKeyConstraint(
            ["closed_by"], ["app.user.name"], name=op.f("fk_alert_closed_by_user")
        ),
        sa.ForeignKeyConstraint(
            ["raised_in"], ["app.alert_build.id"], name=op.f("fk_alert_raised_in_alert_build")
        ),
        sa.ForeignKeyConstraint(["run_id"], ["app.run.id"], name=op.f("fk_alert_run_id_run")),
        sa.ForeignKeyConstraint(
            ["superseded_by"],
            ["app.alert_build.id"],
            name=op.f("fk_alert_superseded_by_alert_build"),
        ),
        sa.ForeignKeyConstraint(
            ["whole_loan_id"], ["app.whole_loan.id"], name=op.f("fk_alert_whole_loan_id_whole_loan")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert")),
        schema="app",
    )
    op.create_index(
        op.f("ix_app_alert_assigned_to"), "alert", ["assigned_to"], unique=False, schema="app"
    )
    op.create_index(
        "ix_app_alert_period_state", "alert", ["period", "state"], unique=False, schema="app"
    )
    op.create_index(
        "uq_app_alert_open_subject_period_rule",
        "alert",
        ["subject", "period", "rule"],
        unique=True,
        schema="app",
        postgresql_where=sa.text("superseded_by IS NULL"),
    )
    op.create_table(
        "alert_loan",
        sa.Column("alert_id", sa.Integer(), nullable=False),
        sa.Column("deal_id", sa.Integer(), nullable=False),
        sa.Column("loan", sa.Text(), nullable=False),
        sa.Column("accession", sa.String(length=20), nullable=False),
        sa.Column("balance", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("superseded_by", sa.Integer(), nullable=True),
        sa.CheckConstraint("balance >= 0", name=op.f("ck_alert_loan_balance_not_negative")),
        sa.ForeignKeyConstraint(
            ["accession"], ["raw.filing.accession"], name=op.f("fk_alert_loan_accession_filing")
        ),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["app.alert.id"],
            name=op.f("fk_alert_loan_alert_id_alert"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["deal_id"], ["silver.deal.id"], name=op.f("fk_alert_loan_deal_id_deal")
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by"],
            ["app.alert_build.id"],
            name=op.f("fk_alert_loan_superseded_by_alert_build"),
        ),
        sa.PrimaryKeyConstraint("alert_id", "deal_id", name=op.f("pk_alert_loan")),
        schema="app",
    )
    op.create_index(
        "ix_app_alert_loan_deal_loan", "alert_loan", ["deal_id", "loan"], unique=False, schema="app"
    )


def downgrade() -> None:
    op.drop_index("ix_app_alert_loan_deal_loan", table_name="alert_loan", schema="app")
    op.drop_table("alert_loan", schema="app")
    op.drop_index(
        "uq_app_alert_open_subject_period_rule",
        table_name="alert",
        schema="app",
        postgresql_where=sa.text("superseded_by IS NULL"),
    )
    op.drop_index("ix_app_alert_period_state", table_name="alert", schema="app")
    op.drop_index(op.f("ix_app_alert_assigned_to"), table_name="alert", schema="app")
    op.drop_table("alert", schema="app")
    op.drop_table("whole_loan_note", schema="app")
    op.drop_index("ix_app_alert_build_deal_period", table_name="alert_build", schema="app")
    op.drop_table("alert_build", schema="app")
    op.drop_table("whole_loan", schema="app")
