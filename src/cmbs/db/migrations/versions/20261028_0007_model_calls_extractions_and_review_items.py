"""Model calls, extractions and review items

Recorded model calls (`app.model_call`, the replay cache), comment extractions with a model
answer and a panel value per field (`silver.extraction`, `silver.extracted_field`), and the
fields waiting for a reviewer (`app.review_item`). The check constraints back up the service:
an accepted field has a value, a human-reviewed one names who decided and when, a decided
review item is complete, and a recording was answered by the model it asked.

Chained onto 0005, the head of main when this branch started (grill log Q71).

Revision ID: 0007
Revises: 0005
Create Date: 2026-10-28 19:45:45.047212
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_call",
        sa.Column("call_id", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("answered_by", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=False),
        sa.Column("questions_sha256", sa.String(length=64), nullable=False),
        sa.Column("state_sha256", sa.String(length=64), nullable=False),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("classification", sa.String(length=16), nullable=False),
        sa.Column("settings_version", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("synthetic", sa.Boolean(), nullable=False),
        sa.Column("request", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "classification IN ('public', 'restricted')",
            name=op.f("ck_model_call_known_classification"),
        ),
        sa.CheckConstraint(
            "answered_by = model", name=op.f("ck_model_call_answered_by_model_asked")
        ),
        sa.PrimaryKeyConstraint("call_id", name=op.f("pk_model_call")),
        schema="app",
    )
    op.create_index(
        op.f("ix_app_model_call_input_sha256"),
        "model_call",
        ["input_sha256"],
        unique=False,
        schema="app",
    )
    op.create_table(
        "extraction",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("deal_id", sa.Integer(), nullable=False),
        sa.Column("loan_id", sa.String(length=32), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("accession", sa.String(length=20), nullable=False),
        sa.Column("source_table", sa.String(length=128), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("sentences", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=False),
        sa.Column("settings_version", sa.Integer(), nullable=False),
        sa.Column("model_call", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("extracted_by", sa.String(length=64), nullable=False),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["accession"], ["raw.filing.accession"], name=op.f("fk_extraction_accession_filing")
        ),
        sa.ForeignKeyConstraint(
            ["deal_id"], ["silver.deal.id"], name=op.f("fk_extraction_deal_id_deal")
        ),
        sa.ForeignKeyConstraint(
            ["extracted_by"], ["app.user.name"], name=op.f("fk_extraction_extracted_by_user")
        ),
        sa.ForeignKeyConstraint(
            ["model_call"],
            ["app.model_call.call_id"],
            name=op.f("fk_extraction_model_call_model_call"),
        ),
        sa.ForeignKeyConstraint(["run_id"], ["app.run.id"], name=op.f("fk_extraction_run_id_run")),
        sa.ForeignKeyConstraint(
            ["settings_version"],
            ["app.settings_version.version"],
            name=op.f("fk_extraction_settings_version_settings_version"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extraction")),
        sa.UniqueConstraint(
            "accession",
            "loan_id",
            "source_table",
            "model",
            "prompt_version",
            name=op.f("uq_extraction_accession"),
        ),
        schema="silver",
    )
    op.create_table(
        "extracted_field",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("extraction_id", sa.Integer(), nullable=False),
        sa.Column("field", sa.String(length=32), nullable=False),
        sa.Column("model_value", sa.Text(), nullable=True),
        sa.Column("answer_probability", sa.Numeric(precision=6, scale=5), nullable=True),
        sa.Column("support_probability", sa.Numeric(precision=6, scale=5), nullable=True),
        sa.Column("model_evidence", sa.Text(), nullable=True),
        sa.Column("model_sentence", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("sentence", sa.Integer(), nullable=True),
        sa.Column("decided_by", sa.String(length=64), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'human-reviewed') = (decided_by IS NOT NULL AND decided_at IS NOT NULL)",
            name=op.f("ck_extracted_field_reviewed_is_attributed"),
        ),
        sa.CheckConstraint(
            "(status IN ('auto-accepted', 'human-reviewed')) = (value IS NOT NULL)",
            name=op.f("ck_extracted_field_value_when_accepted"),
        ),
        sa.CheckConstraint(
            "status IN ('auto-accepted', 'needs review', 'not stated', 'human-reviewed')",
            name=op.f("ck_extracted_field_known_status"),
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["app.user.name"], name=op.f("fk_extracted_field_decided_by_user")
        ),
        sa.ForeignKeyConstraint(
            ["extraction_id"],
            ["silver.extraction.id"],
            name=op.f("fk_extracted_field_extraction_id_extraction"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extracted_field")),
        sa.UniqueConstraint(
            "extraction_id", "field", name=op.f("uq_extracted_field_extraction_id")
        ),
        schema="silver",
    )
    op.create_index(
        op.f("ix_silver_extracted_field_extraction_id"),
        "extracted_field",
        ["extraction_id"],
        unique=False,
        schema="silver",
    )
    op.create_table(
        "review_item",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("deal_id", sa.Integer(), nullable=False),
        sa.Column("loan_id", sa.String(length=32), nullable=False),
        sa.Column("field", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("probability", sa.Numeric(precision=6, scale=5), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("decided_by", sa.String(length=64), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision", sa.Text(), nullable=True),
        sa.Column("decision_sentence", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "(status = 'decided') = (decided_by IS NOT NULL AND decided_at IS NOT NULL "
            "AND decision IS NOT NULL)",
            name=op.f("ck_review_item_decided_is_complete"),
        ),
        sa.CheckConstraint(
            "status IN ('open', 'decided')", name=op.f("ck_review_item_known_status")
        ),
        sa.ForeignKeyConstraint(
            ["deal_id"], ["silver.deal.id"], name=op.f("fk_review_item_deal_id_deal")
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["app.user.name"], name=op.f("fk_review_item_decided_by_user")
        ),
        sa.ForeignKeyConstraint(
            ["field_id"],
            ["silver.extracted_field.id"],
            name=op.f("fk_review_item_field_id_extracted_field"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_item")),
        sa.UniqueConstraint("field_id", name=op.f("uq_review_item_field_id")),
        schema="app",
    )
    op.create_index(
        op.f("ix_app_review_item_created_at"),
        "review_item",
        ["created_at"],
        unique=False,
        schema="app",
    )
    op.create_index(
        op.f("ix_app_review_item_status"), "review_item", ["status"], unique=False, schema="app"
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_app_review_item_status"), table_name="review_item", schema="app")
    op.drop_index(op.f("ix_app_review_item_created_at"), table_name="review_item", schema="app")
    op.drop_table("review_item", schema="app")
    op.drop_index(
        op.f("ix_silver_extracted_field_extraction_id"),
        table_name="extracted_field",
        schema="silver",
    )
    op.drop_table("extracted_field", schema="silver")
    op.drop_table("extraction", schema="silver")
    op.drop_index(op.f("ix_app_model_call_input_sha256"), table_name="model_call", schema="app")
    op.drop_table("model_call", schema="app")
