"""Field overrides and review declines

An analyst's override of an extracted value, kept beside the field it replaces (the model's
answer and any review decision stay in `silver.extracted_field`), and the reason a review item
was declined as not determinable (Q45, Q47).

Revision ID: 0010
Revises: 0008
Create Date: 2026-10-28 23:56:13.360656
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "field_override",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("sentence", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("overridden_by", sa.String(length=64), nullable=False),
        sa.Column("overridden_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("before_status", sa.String(length=20), nullable=False),
        sa.Column("before_value", sa.Text(), nullable=True),
        sa.Column("before_sentence", sa.Integer(), nullable=True),
        sa.CheckConstraint("btrim(reason) <> ''", name=op.f("ck_field_override_reason_given")),
        sa.CheckConstraint("btrim(value) <> ''", name=op.f("ck_field_override_value_given")),
        sa.CheckConstraint("sentence >= 1", name=op.f("ck_field_override_sentence_is_numbered")),
        sa.ForeignKeyConstraint(
            ["field_id"],
            ["silver.extracted_field.id"],
            name=op.f("fk_field_override_field_id_extracted_field"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["overridden_by"], ["app.user.name"], name=op.f("fk_field_override_overridden_by_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_field_override")),
        schema="silver",
    )
    op.create_index(
        "ix_field_override_field_at",
        "field_override",
        ["field_id", "overridden_at"],
        unique=False,
        schema="silver",
    )
    op.create_table(
        "review_decline",
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.CheckConstraint("btrim(reason) <> ''", name=op.f("ck_review_decline_reason_given")),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["app.review_item.id"],
            name=op.f("fk_review_decline_item_id_review_item"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("item_id", name=op.f("pk_review_decline")),
        schema="app",
    )


def downgrade() -> None:
    op.drop_table("review_decline", schema="app")
    op.drop_index("ix_field_override_field_at", table_name="field_override", schema="silver")
    op.drop_table("field_override", schema="silver")
