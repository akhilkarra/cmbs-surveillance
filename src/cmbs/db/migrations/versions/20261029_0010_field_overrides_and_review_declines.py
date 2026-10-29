"""Field overrides, review declines and superseded review items

An analyst's override of an extracted value, kept per (deal, loan, period, field) beside the
fields it replaces (the model's answer and any review decision stay in `silver.extracted_field`),
and the reason a review item was declined as not determinable (Q45, Q47). Both tables are
append-only: a trigger refuses UPDATE, DELETE and TRUNCATE, as on `app.audit_entry`. A review item
whose extraction a newer one replaced is marked superseded, with when and by which extraction.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-29 00:21:58.497131
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

APPEND_ONLY_FUNCTION = """
CREATE FUNCTION app.refuse_append_only_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '%', TG_ARGV[0];
END;
$$
"""
APPEND_ONLY = {
    "silver.field_override": "overrides are append-only",
    "app.review_decline": "review declines are append-only",
}

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "field_override",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("deal_id", sa.Integer(), nullable=False),
        sa.Column("loan_id", sa.String(length=32), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("field", sa.String(length=32), nullable=False),
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
        sa.CheckConstraint(
            "extract(day FROM period) = 1", name=op.f("ck_field_override_period_is_first_of_month")
        ),
        sa.CheckConstraint("sentence >= 1", name=op.f("ck_field_override_sentence_is_numbered")),
        sa.ForeignKeyConstraint(
            ["deal_id"], ["silver.deal.id"], name=op.f("fk_field_override_deal_id_deal")
        ),
        sa.ForeignKeyConstraint(
            ["field_id"],
            ["silver.extracted_field.id"],
            name=op.f("fk_field_override_field_id_extracted_field"),
        ),
        sa.ForeignKeyConstraint(
            ["overridden_by"], ["app.user.name"], name=op.f("fk_field_override_overridden_by_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_field_override")),
        schema="silver",
    )
    op.create_index(
        "ix_field_override_key_at",
        "field_override",
        ["deal_id", "loan_id", "period", "field", "overridden_at"],
        unique=False,
        schema="silver",
    )
    op.create_table(
        "review_decline",
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.CheckConstraint("btrim(reason) <> ''", name=op.f("ck_review_decline_reason_given")),
        sa.ForeignKeyConstraint(
            ["item_id"], ["app.review_item.id"], name=op.f("fk_review_decline_item_id_review_item")
        ),
        sa.PrimaryKeyConstraint("item_id", name=op.f("pk_review_decline")),
        schema="app",
    )
    op.add_column(
        "review_item",
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        schema="app",
    )
    op.add_column(
        "review_item", sa.Column("superseded_by", sa.Integer(), nullable=True), schema="app"
    )
    op.create_foreign_key(
        op.f("fk_review_item_superseded_by_extraction"),
        "review_item",
        "extraction",
        ["superseded_by"],
        ["id"],
        source_schema="app",
        referent_schema="silver",
    )
    op.drop_constraint(op.f("ck_review_item_known_status"), "review_item", schema="app")
    op.create_check_constraint(
        op.f("ck_review_item_known_status"),
        "review_item",
        "status IN ('open', 'decided', 'superseded')",
        schema="app",
    )
    op.create_check_constraint(
        op.f("ck_review_item_superseded_is_complete"),
        "review_item",
        "(status = 'superseded') = (superseded_at IS NOT NULL AND superseded_by IS NOT NULL)",
        schema="app",
    )
    op.execute(APPEND_ONLY_FUNCTION)
    for table, message in APPEND_ONLY.items():
        name = table.split(".")[1]
        op.execute(
            f"CREATE TRIGGER {name}_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION app.refuse_append_only_change('{message}')"
        )


def downgrade() -> None:
    for table in APPEND_ONLY:
        op.execute(f"DROP TRIGGER {table.split('.')[1]}_append_only ON {table}")
    op.execute("DROP FUNCTION app.refuse_append_only_change()")
    op.drop_constraint(op.f("ck_review_item_superseded_is_complete"), "review_item", schema="app")
    op.drop_constraint(op.f("ck_review_item_known_status"), "review_item", schema="app")
    op.create_check_constraint(
        op.f("ck_review_item_known_status"),
        "review_item",
        "status IN ('open', 'decided')",
        schema="app",
    )
    op.drop_constraint(
        op.f("fk_review_item_superseded_by_extraction"),
        "review_item",
        schema="app",
        type_="foreignkey",
    )
    op.drop_column("review_item", "superseded_by", schema="app")
    op.drop_column("review_item", "superseded_at", schema="app")
    op.drop_table("review_decline", schema="app")
    op.drop_index("ix_field_override_key_at", table_name="field_override", schema="silver")
    op.drop_table("field_override", schema="silver")
