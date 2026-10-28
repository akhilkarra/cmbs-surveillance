"""Positions, the filing discovery log, vendor sources, deal aliases and depositor links

The book's positions files and their lines (Q38), a log of every filing discovery so the
coverage report can tell "looked and found nothing" from "never looked", and the vendor
loan-tape sources configured per deal (Q40), all application state in `app`. In `silver`:
other names of a deal ("COMM 2014-CCRE18" for "COMM 2014-CR18"), and the many-to-many link
from a depositor's filing (an ABS-15G) to each private deal it names.

Revision ID: 0004
Revises: 0006
Create Date: 2026-10-28 09:28:38.773760
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "filing_discovery",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("deal_id", sa.Integer(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_by", sa.String(length=64), nullable=False),
        sa.Column("cik", sa.Integer(), nullable=True),
        sa.Column("edgar_name", sa.String(length=256), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("filings", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["checked_by"], ["app.user.name"], name=op.f("fk_filing_discovery_checked_by_user")
        ),
        sa.ForeignKeyConstraint(
            ["deal_id"], ["silver.deal.id"], name=op.f("fk_filing_discovery_deal_id_deal")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_filing_discovery")),
        schema="app",
    )
    op.create_index(
        op.f("ix_app_filing_discovery_deal_id"),
        "filing_discovery",
        ["deal_id"],
        unique=False,
        schema="app",
    )
    op.create_table(
        "positions_file",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("loaded_by", sa.String(length=64), nullable=False),
        sa.Column("loaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_name", sa.String(length=256), nullable=True),
        sa.ForeignKeyConstraint(
            ["loaded_by"], ["app.user.name"], name=op.f("fk_positions_file_loaded_by_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_positions_file")),
        schema="app",
    )
    op.create_index(
        op.f("ix_app_positions_file_as_of"), "positions_file", ["as_of"], unique=False, schema="app"
    )
    op.create_table(
        "vendor_source",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("deal_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("configured_by", sa.String(length=64), nullable=False),
        sa.Column("configured_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["configured_by"], ["app.user.name"], name=op.f("fk_vendor_source_configured_by_user")
        ),
        sa.ForeignKeyConstraint(
            ["deal_id"], ["silver.deal.id"], name=op.f("fk_vendor_source_deal_id_deal")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vendor_source")),
        sa.UniqueConstraint("deal_id", "name", name=op.f("uq_vendor_source_deal_id")),
        schema="app",
    )
    op.create_index(
        op.f("ix_app_vendor_source_deal_id"),
        "vendor_source",
        ["deal_id"],
        unique=False,
        schema="app",
    )
    op.create_table(
        "position",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("file_id", sa.Integer(), nullable=False),
        sa.Column("deal_id", sa.Integer(), nullable=False),
        sa.Column("tranche", sa.String(length=16), nullable=False),
        sa.Column("face_amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.CheckConstraint("face_amount > 0", name=op.f("ck_position_positive_face")),
        sa.ForeignKeyConstraint(
            ["deal_id"], ["silver.deal.id"], name=op.f("fk_position_deal_id_deal")
        ),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["app.positions_file.id"],
            name=op.f("fk_position_file_id_positions_file"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_position")),
        sa.UniqueConstraint("file_id", "deal_id", "tranche", name=op.f("uq_position_file_id")),
        schema="app",
    )
    op.create_index(
        op.f("ix_app_position_deal_id"), "position", ["deal_id"], unique=False, schema="app"
    )
    op.create_index(
        op.f("ix_app_position_file_id"), "position", ["file_id"], unique=False, schema="app"
    )
    op.create_table(
        "deal_alias",
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("deal_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["deal_id"], ["silver.deal.id"], name=op.f("fk_deal_alias_deal_id_deal")
        ),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_deal_alias")),
        schema="silver",
    )
    op.create_index(
        op.f("ix_silver_deal_alias_deal_id"),
        "deal_alias",
        ["deal_id"],
        unique=False,
        schema="silver",
    )
    op.create_table(
        "deal_filing",
        sa.Column("deal_id", sa.Integer(), nullable=False),
        sa.Column("accession", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(
            ["accession"],
            ["raw.filing.accession"],
            name=op.f("fk_deal_filing_accession_filing"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["deal_id"], ["silver.deal.id"], name=op.f("fk_deal_filing_deal_id_deal")
        ),
        sa.PrimaryKeyConstraint("deal_id", "accession", name=op.f("pk_deal_filing")),
        schema="silver",
    )
    op.create_index(
        op.f("ix_silver_deal_filing_accession"),
        "deal_filing",
        ["accession"],
        unique=False,
        schema="silver",
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_silver_deal_filing_accession"), table_name="deal_filing", schema="silver"
    )
    op.drop_table("deal_filing", schema="silver")
    op.drop_index(op.f("ix_silver_deal_alias_deal_id"), table_name="deal_alias", schema="silver")
    op.drop_table("deal_alias", schema="silver")
    op.drop_index(op.f("ix_app_position_file_id"), table_name="position", schema="app")
    op.drop_index(op.f("ix_app_position_deal_id"), table_name="position", schema="app")
    op.drop_table("position", schema="app")
    op.drop_index(op.f("ix_app_vendor_source_deal_id"), table_name="vendor_source", schema="app")
    op.drop_table("vendor_source", schema="app")
    op.drop_index(op.f("ix_app_positions_file_as_of"), table_name="positions_file", schema="app")
    op.drop_table("positions_file", schema="app")
    op.drop_index(
        op.f("ix_app_filing_discovery_deal_id"), table_name="filing_discovery", schema="app"
    )
    op.drop_table("filing_discovery", schema="app")
