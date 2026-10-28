"""App schema and the tables every feature shares

Creates the `app` schema for application state, then users and roles, the append-only audit
trail, settings versions and runs there; the deal registry and check flags in `silver`; and
the filings registry with stored files in `raw` (grill log Q63). A trigger makes audit
entries impossible to update or delete.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-28 01:05:24.657076
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


APPEND_ONLY_FUNCTION = """
CREATE FUNCTION app.refuse_audit_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'the audit log is append-only';
END;
$$
"""
APPEND_ONLY_TRIGGER = """
CREATE TRIGGER audit_entry_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON app.audit_entry
FOR EACH STATEMENT EXECUTE FUNCTION app.refuse_audit_change()
"""


def upgrade() -> None:
    op.execute("CREATE SCHEMA app")
    op.create_table(
        "audit_entry",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_name", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=True),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.String(length=32), nullable=True),
        sa.Column("subject_key", sa.String(length=256), nullable=True),
        sa.Column("field", sa.String(length=64), nullable=True),
        sa.Column("before", sa.Text(), nullable=True),
        sa.Column("after", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("outcome", sa.String(length=8), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint(
            "outcome IN ('done', 'refused')", name=op.f("ck_audit_entry_known_outcome")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_entry")),
        schema="app",
    )
    op.create_index(
        op.f("ix_app_audit_entry_at"), "audit_entry", ["at"], unique=False, schema="app"
    )
    op.create_index(
        op.f("ix_app_audit_entry_run_id"), "audit_entry", ["run_id"], unique=False, schema="app"
    )
    op.create_index(
        op.f("ix_app_audit_entry_subject_key"),
        "audit_entry",
        ["subject_key"],
        unique=False,
        schema="app",
    )
    op.create_index(
        op.f("ix_app_audit_entry_user_name"),
        "audit_entry",
        ["user_name"],
        unique=False,
        schema="app",
    )
    op.create_table(
        "run",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("started_by", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'completed with problems', 'failed')",
            name=op.f("ck_run_known_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run")),
        schema="app",
    )
    op.create_table(
        "settings_version",
        sa.Column("version", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("proposed_by", sa.String(length=64), nullable=False),
        sa.Column("approved_by", sa.String(length=64), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("version", name=op.f("pk_settings_version")),
        schema="app",
    )
    op.create_index(
        op.f("ix_app_settings_version_approved_at"),
        "settings_version",
        ["approved_at"],
        unique=False,
        schema="app",
    )
    op.create_table(
        "user",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_user")),
        schema="app",
    )
    op.create_table(
        "deal",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("cik", sa.Integer(), nullable=True),
        sa.Column("edgar_name", sa.String(length=256), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deal")),
        sa.UniqueConstraint("name", name=op.f("uq_deal_name")),
        schema="silver",
    )
    op.create_index(op.f("ix_silver_deal_cik"), "deal", ["cik"], unique=False, schema="silver")
    op.create_table(
        "user_role",
        sa.Column("user_name", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "role IN ('pm', 'analyst', 'quant', 'risk', 'operator')",
            name=op.f("ck_user_role_known_role"),
        ),
        sa.ForeignKeyConstraint(
            ["user_name"],
            ["app.user.name"],
            name=op.f("fk_user_role_user_name_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_name", "role", name=op.f("pk_user_role")),
        schema="app",
    )
    op.create_table(
        "check_flag",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("deal_id", sa.Integer(), nullable=False),
        sa.Column("loan", sa.String(length=32), nullable=False),
        sa.Column("period", sa.Date(), nullable=True),
        sa.Column("check", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("sides", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("raised_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["deal_id"], ["silver.deal.id"], name=op.f("fk_check_flag_deal_id_deal")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_check_flag")),
        schema="silver",
    )
    op.create_index(
        op.f("ix_silver_check_flag_deal_id"),
        "check_flag",
        ["deal_id"],
        unique=False,
        schema="silver",
    )
    op.create_table(
        "filing",
        sa.Column("accession", sa.String(length=20), nullable=False),
        sa.Column("cik", sa.Integer(), nullable=False),
        sa.Column("deal_id", sa.Integer(), nullable=True),
        sa.Column("form", sa.String(length=16), nullable=False),
        sa.Column("filing_date", sa.Date(), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=True),
        sa.Column("period", sa.Date(), nullable=True),
        sa.Column("amends", sa.String(length=20), nullable=True),
        sa.Column("primary_document", sa.String(length=256), nullable=True),
        sa.ForeignKeyConstraint(
            ["deal_id"], ["silver.deal.id"], name=op.f("fk_filing_deal_id_deal")
        ),
        sa.PrimaryKeyConstraint("accession", name=op.f("pk_filing")),
        schema="raw",
    )
    op.create_index(op.f("ix_raw_filing_cik"), "filing", ["cik"], unique=False, schema="raw")
    op.create_index(
        op.f("ix_raw_filing_deal_id"), "filing", ["deal_id"], unique=False, schema="raw"
    )
    op.create_index(
        op.f("ix_raw_filing_filing_date"), "filing", ["filing_date"], unique=False, schema="raw"
    )
    op.create_index(op.f("ix_raw_filing_period"), "filing", ["period"], unique=False, schema="raw")
    op.create_table(
        "filing_file",
        sa.Column("accession", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("char_length(sha256) = 64", name=op.f("ck_filing_file_sha256_length")),
        sa.ForeignKeyConstraint(
            ["accession"],
            ["raw.filing.accession"],
            name=op.f("fk_filing_file_accession_filing"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("accession", "name", name=op.f("pk_filing_file")),
        schema="raw",
    )
    op.execute(APPEND_ONLY_FUNCTION)
    op.execute(APPEND_ONLY_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_entry_append_only ON app.audit_entry")
    op.execute("DROP FUNCTION app.refuse_audit_change()")
    op.drop_table("filing_file", schema="raw")
    op.drop_index(op.f("ix_raw_filing_period"), table_name="filing", schema="raw")
    op.drop_index(op.f("ix_raw_filing_filing_date"), table_name="filing", schema="raw")
    op.drop_index(op.f("ix_raw_filing_deal_id"), table_name="filing", schema="raw")
    op.drop_index(op.f("ix_raw_filing_cik"), table_name="filing", schema="raw")
    op.drop_table("filing", schema="raw")
    op.drop_index(op.f("ix_silver_check_flag_deal_id"), table_name="check_flag", schema="silver")
    op.drop_table("check_flag", schema="silver")
    op.drop_table("user_role", schema="app")
    op.drop_index(op.f("ix_silver_deal_cik"), table_name="deal", schema="silver")
    op.drop_table("deal", schema="silver")
    op.drop_table("user", schema="app")
    op.drop_index(
        op.f("ix_app_settings_version_approved_at"), table_name="settings_version", schema="app"
    )
    op.drop_table("settings_version", schema="app")
    op.drop_table("run", schema="app")
    op.drop_index(op.f("ix_app_audit_entry_user_name"), table_name="audit_entry", schema="app")
    op.drop_index(op.f("ix_app_audit_entry_subject_key"), table_name="audit_entry", schema="app")
    op.drop_index(op.f("ix_app_audit_entry_run_id"), table_name="audit_entry", schema="app")
    op.drop_index(op.f("ix_app_audit_entry_at"), table_name="audit_entry", schema="app")
    op.drop_table("audit_entry", schema="app")
    op.execute("DROP SCHEMA app")
