"""The record of panel exports (reproducibility and export).

app.panel_export holds one row per export: who took it, when, as of which date, its format,
file name, row count and SHA-256, and the settings version, model and prompt version it names.
Like the audit log it is append-only: a trigger refuses updating or deleting its rows.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-29 00:26:10.171819
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY_FUNCTION = """
CREATE FUNCTION app.refuse_panel_export_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'exports are recorded once: the export log is append-only';
END;
$$
"""
APPEND_ONLY_TRIGGER = """
CREATE TRIGGER panel_export_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON app.panel_export
FOR EACH STATEMENT EXECUTE FUNCTION app.refuse_panel_export_change()
"""


def upgrade() -> None:
    op.create_table(
        "panel_export",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("exported_by", sa.String(length=64), nullable=False),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("deal_id", sa.Integer(), nullable=True),
        sa.Column("format", sa.String(length=16), nullable=False),
        sa.Column("file_name", sa.String(length=256), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("settings_version", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "format IN ('csv', 'parquet')", name=op.f("ck_panel_export_known_format")
        ),
        sa.CheckConstraint("char_length(sha256) = 64", name=op.f("ck_panel_export_sha256_length")),
        sa.CheckConstraint("row_count >= 0", name=op.f("ck_panel_export_row_count_not_negative")),
        sa.ForeignKeyConstraint(
            ["deal_id"], ["silver.deal.id"], name=op.f("fk_panel_export_deal_id_deal")
        ),
        sa.ForeignKeyConstraint(
            ["exported_by"], ["app.user.name"], name=op.f("fk_panel_export_exported_by_user")
        ),
        sa.ForeignKeyConstraint(
            ["settings_version"],
            ["app.settings_version.version"],
            name=op.f("fk_panel_export_settings_version_settings_version"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_panel_export")),
        schema="app",
    )
    op.create_index(
        op.f("ix_app_panel_export_exported_at"),
        "panel_export",
        ["exported_at"],
        unique=False,
        schema="app",
    )
    op.execute(APPEND_ONLY_FUNCTION)
    op.execute(APPEND_ONLY_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER panel_export_append_only ON app.panel_export")
    op.execute("DROP FUNCTION app.refuse_panel_export_change()")
    op.drop_index(op.f("ix_app_panel_export_exported_at"), table_name="panel_export", schema="app")
    op.drop_table("panel_export", schema="app")
