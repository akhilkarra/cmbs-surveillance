"""Golden set, golden labels and quality reports

The labelled comments extraction is measured against, each labeller's answer per field ("not
stated" is a null value), and every quality report kept for good with the model, prompt
version and settings version it measured (Q39, Q45, Q46). A comment proposed from a value a
person decided on the panel joins the golden set only once a second analyst confirms it. All
application state in `app`; the text-versus-tape and roll-forward checks add no table (they
raise `silver.check_flag` rows).

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-29 00:09:25.158293
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quality_report",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("built_by", sa.String(length=64), nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=False),
        sa.Column("settings_version", sa.Integer(), nullable=True),
        sa.Column("items", sa.Integer(), nullable=False),
        sa.Column("results", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("flags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("previous_id", sa.Integer(), nullable=True),
        sa.Column("predictions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint("items > 0", name=op.f("ck_quality_report_has_items")),
        sa.ForeignKeyConstraint(
            ["built_by"], ["app.user.name"], name=op.f("fk_quality_report_built_by_user")
        ),
        sa.ForeignKeyConstraint(
            ["previous_id"],
            ["app.quality_report.id"],
            name=op.f("fk_quality_report_previous_id_quality_report"),
        ),
        sa.ForeignKeyConstraint(
            ["settings_version"],
            ["app.settings_version.version"],
            name=op.f("fk_quality_report_settings_version_settings_version"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quality_report")),
        schema="app",
    )
    op.create_index(
        op.f("ix_app_quality_report_built_at"),
        "quality_report",
        ["built_at"],
        unique=False,
        schema="app",
    )
    op.create_table(
        "golden_item",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(length=160), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("deal_id", sa.Integer(), nullable=True),
        sa.Column("loan", sa.String(length=32), nullable=True),
        sa.Column("period", sa.Date(), nullable=True),
        sa.Column("accession", sa.String(length=20), nullable=True),
        sa.Column("sentences", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("added_by", sa.String(length=64), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "(status = 'golden') = (joined_at IS NOT NULL)",
            name=op.f("ck_golden_item_joined_when_golden"),
        ),
        sa.CheckConstraint(
            "source <> 'panel' OR (deal_id IS NOT NULL AND accession IS NOT NULL)",
            name=op.f("ck_golden_item_panel_item_traced"),
        ),
        sa.CheckConstraint(
            "source IN ('imported', 'panel')", name=op.f("ck_golden_item_known_source")
        ),
        sa.CheckConstraint(
            "status <> 'rejected' OR (decided_by IS NOT NULL AND reason IS NOT NULL)",
            name=op.f("ck_golden_item_rejection_attributed"),
        ),
        sa.CheckConstraint(
            "status IN ('golden', 'proposed', 'rejected')", name=op.f("ck_golden_item_known_status")
        ),
        sa.ForeignKeyConstraint(
            ["accession"], ["raw.filing.accession"], name=op.f("fk_golden_item_accession_filing")
        ),
        sa.ForeignKeyConstraint(
            ["added_by"], ["app.user.name"], name=op.f("fk_golden_item_added_by_user")
        ),
        sa.ForeignKeyConstraint(
            ["deal_id"], ["silver.deal.id"], name=op.f("fk_golden_item_deal_id_deal")
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["app.user.name"], name=op.f("fk_golden_item_decided_by_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_golden_item")),
        sa.UniqueConstraint("key", name=op.f("uq_golden_item_key")),
        schema="app",
    )
    op.create_index(
        op.f("ix_app_golden_item_status"), "golden_item", ["status"], unique=False, schema="app"
    )
    op.create_table(
        "golden_label",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("field", sa.String(length=32), nullable=False),
        sa.Column("labeller", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("sentence", sa.Integer(), nullable=True),
        sa.Column("labelled_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"], ["app.golden_item.id"], name=op.f("fk_golden_label_item_id_golden_item")
        ),
        sa.ForeignKeyConstraint(
            ["labeller"], ["app.user.name"], name=op.f("fk_golden_label_labeller_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_golden_label")),
        sa.UniqueConstraint("item_id", "field", "labeller", name=op.f("uq_golden_label_item_id")),
        schema="app",
    )
    op.create_index(
        op.f("ix_app_golden_label_item_id"), "golden_label", ["item_id"], unique=False, schema="app"
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_app_golden_label_item_id"), table_name="golden_label", schema="app")
    op.drop_table("golden_label", schema="app")
    op.drop_index(op.f("ix_app_golden_item_status"), table_name="golden_item", schema="app")
    op.drop_table("golden_item", schema="app")
    op.drop_index(op.f("ix_app_quality_report_built_at"), table_name="quality_report", schema="app")
    op.drop_table("quality_report", schema="app")
