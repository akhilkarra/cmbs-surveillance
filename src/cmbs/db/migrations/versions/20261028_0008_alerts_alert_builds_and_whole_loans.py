"""Alerts, alert builds and whole loans

Monthly alerts with their shared lifecycle state, the loans each alert covers, and a record of
every deal-month built (Q25, Q44). Whole loans link the pari passu notes of one loan held
through several deals, so a linked loan raises one alert (Q48). All application state in `app`.

Revision ID: 0008
Revises: 0003
Create Date: 2026-10-28 19:42:01.033827
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
        sa.ForeignKeyConstraint(["run_id"], ["app.run.id"], name=op.f("fk_alert_run_id_run")),
        sa.ForeignKeyConstraint(
            ["whole_loan_id"], ["app.whole_loan.id"], name=op.f("fk_alert_whole_loan_id_whole_loan")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert")),
        sa.UniqueConstraint("subject", "period", "rule", name="uq_alert_subject_period_rule"),
        schema="app",
    )
    op.create_index(
        op.f("ix_app_alert_assigned_to"), "alert", ["assigned_to"], unique=False, schema="app"
    )
    op.create_index(
        "ix_app_alert_period_state", "alert", ["period", "state"], unique=False, schema="app"
    )
    op.create_table(
        "alert_build",
        sa.Column("deal_id", sa.Integer(), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("accession", sa.String(length=20), nullable=False),
        sa.Column("compared_with", sa.String(length=20), nullable=True),
        sa.Column("report_accession", sa.String(length=20), nullable=True),
        sa.Column("settings_version", sa.Integer(), nullable=False),
        sa.Column("first_built_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "extract(day FROM period) = 1", name=op.f("ck_alert_build_period_is_first_of_month")
        ),
        sa.ForeignKeyConstraint(
            ["accession"], ["raw.filing.accession"], name=op.f("fk_alert_build_accession_filing")
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
        sa.PrimaryKeyConstraint("deal_id", "period", name=op.f("pk_alert_build")),
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
        "alert_loan",
        sa.Column("alert_id", sa.Integer(), nullable=False),
        sa.Column("deal_id", sa.Integer(), nullable=False),
        sa.Column("loan", sa.Text(), nullable=False),
        sa.Column("accession", sa.String(length=20), nullable=False),
        sa.Column("balance", sa.Numeric(precision=18, scale=2), nullable=True),
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
        sa.PrimaryKeyConstraint("alert_id", "deal_id", name=op.f("pk_alert_loan")),
        schema="app",
    )
    op.create_index(
        "ix_app_alert_loan_deal_loan", "alert_loan", ["deal_id", "loan"], unique=False, schema="app"
    )


def downgrade() -> None:
    op.drop_index("ix_app_alert_loan_deal_loan", table_name="alert_loan", schema="app")
    op.drop_table("alert_loan", schema="app")
    op.drop_table("whole_loan_note", schema="app")
    op.drop_table("alert_build", schema="app")
    op.drop_index("ix_app_alert_period_state", table_name="alert", schema="app")
    op.drop_index(op.f("ix_app_alert_assigned_to"), table_name="alert", schema="app")
    op.drop_table("alert", schema="app")
    op.drop_table("whole_loan", schema="app")
