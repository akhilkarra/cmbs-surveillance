"""Loan tapes in bronze and silver

One row per loaded EX-102 file with its amendment depth (bronze.loan_tape), every asset of the
file exactly as reported (bronze.loan_tape_asset), one typed row per top-level loan per filing
(silver.loan_period, keyed by accession so an amendment adds a version and never overwrites),
and loans quarantined by the loan_period contract with their reason (silver.loan_quarantine).
The CHECK constraints repeat the contract's rules.

Revision ID: 0003
Revises: 0006
Create Date: 2026-10-28 09:29:11.597044
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "loan_tape",
        sa.Column("accession", sa.String(length=20), nullable=False),
        sa.Column("file_name", sa.String(length=256), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("deal_id", sa.Integer(), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("filing_date", sa.Date(), nullable=False),
        sa.Column("amendment_depth", sa.Integer(), nullable=False),
        sa.CheckConstraint("amendment_depth >= 0", name=op.f("ck_loan_tape_depth_not_negative")),
        sa.CheckConstraint(
            "extract(day FROM period) = 1", name=op.f("ck_loan_tape_period_is_first_of_month")
        ),
        sa.ForeignKeyConstraint(
            ["accession", "file_name"],
            ["raw.filing_file.accession", "raw.filing_file.name"],
            name=op.f("fk_loan_tape_accession_filing_file"),
        ),
        sa.ForeignKeyConstraint(
            ["accession"], ["raw.filing.accession"], name=op.f("fk_loan_tape_accession_filing")
        ),
        sa.ForeignKeyConstraint(
            ["deal_id"], ["silver.deal.id"], name=op.f("fk_loan_tape_deal_id_deal")
        ),
        sa.PrimaryKeyConstraint("accession", name=op.f("pk_loan_tape")),
        schema="bronze",
    )
    op.create_index(
        "ix_bronze_loan_tape_deal_period",
        "loan_tape",
        ["deal_id", "period", "filing_date"],
        unique=False,
        schema="bronze",
    )
    op.create_table(
        "loan_tape_asset",
        sa.Column("accession", sa.String(length=20), nullable=False),
        sa.Column("file_name", sa.String(length=256), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("asset_number", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("properties", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "kind IN ('loan', 'companion note', 'portfolio property')",
            name=op.f("ck_loan_tape_asset_known_kind"),
        ),
        sa.CheckConstraint("position >= 1", name=op.f("ck_loan_tape_asset_position_from_one")),
        sa.ForeignKeyConstraint(
            ["accession", "file_name"],
            ["raw.filing_file.accession", "raw.filing_file.name"],
            name=op.f("fk_loan_tape_asset_accession_filing_file"),
        ),
        sa.ForeignKeyConstraint(
            ["accession"],
            ["bronze.loan_tape.accession"],
            name=op.f("fk_loan_tape_asset_accession_loan_tape"),
        ),
        sa.PrimaryKeyConstraint(
            "accession", "file_name", "position", name=op.f("pk_loan_tape_asset")
        ),
        schema="bronze",
    )
    op.create_table(
        "loan_period",
        sa.Column("deal_id", sa.Integer(), nullable=False),
        sa.Column("loan", sa.Text(), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("accession", sa.String(length=20), nullable=False),
        sa.Column("filing_date", sa.Date(), nullable=False),
        sa.Column("file_name", sa.String(length=256), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("asset_position", sa.Integer(), nullable=False),
        sa.Column("reporting_period_end", sa.Date(), nullable=False),
        sa.Column("beginning_balance", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("scheduled_principal", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("unscheduled_principal", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("other_principal_adjustments", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("realized_loss", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("ending_balance", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("paid_through_date", sa.Date(), nullable=True),
        sa.Column("payment_status", sa.Text(), nullable=True),
        sa.Column("special_servicer_transfer_date", sa.Date(), nullable=True),
        sa.Column("master_servicer_return_date", sa.Date(), nullable=True),
        sa.Column("workout_strategy", sa.Text(), nullable=True),
        sa.Column("modification_code", sa.Text(), nullable=True),
        sa.Column("property_count", sa.Integer(), nullable=False),
        sa.Column("property_type", sa.Text(), nullable=True),
        sa.Column("property_name", sa.Text(), nullable=True),
        sa.Column("defeased_status", sa.Text(), nullable=True),
        sa.Column("financials_start", sa.Date(), nullable=True),
        sa.Column("financials_end", sa.Date(), nullable=True),
        sa.Column("financials_end_newest", sa.Date(), nullable=True),
        sa.Column("noi", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("ncf", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("dscr", sa.Numeric(), nullable=True),
        sa.Column("occupancy", sa.Numeric(), nullable=True),
        sa.Column("property_note", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "beginning_balance >= 0", name=op.f("ck_loan_period_beginning_balance_not_negative")
        ),
        sa.CheckConstraint(
            "char_length(file_sha256) = 64", name=op.f("ck_loan_period_sha256_length")
        ),
        sa.CheckConstraint(
            "ending_balance >= 0", name=op.f("ck_loan_period_ending_balance_not_negative")
        ),
        sa.CheckConstraint(
            "extract(day FROM period) = 1", name=op.f("ck_loan_period_period_is_first_of_month")
        ),
        sa.CheckConstraint(
            "financials_end >= financials_start", name=op.f("ck_loan_period_financials_in_order")
        ),
        sa.CheckConstraint(
            "financials_end_newest >= financials_end",
            name=op.f("ck_loan_period_newest_financials_not_older"),
        ),
        sa.CheckConstraint(
            "property_count >= 0", name=op.f("ck_loan_period_property_count_not_negative")
        ),
        sa.ForeignKeyConstraint(
            ["accession", "file_name", "asset_position"],
            [
                "bronze.loan_tape_asset.accession",
                "bronze.loan_tape_asset.file_name",
                "bronze.loan_tape_asset.position",
            ],
            name=op.f("fk_loan_period_accession_loan_tape_asset"),
        ),
        sa.ForeignKeyConstraint(
            ["accession"], ["raw.filing.accession"], name=op.f("fk_loan_period_accession_filing")
        ),
        sa.ForeignKeyConstraint(
            ["deal_id"], ["silver.deal.id"], name=op.f("fk_loan_period_deal_id_deal")
        ),
        sa.PrimaryKeyConstraint(
            "deal_id", "loan", "period", "accession", name=op.f("pk_loan_period")
        ),
        schema="silver",
    )
    op.create_index(
        "ix_silver_loan_period_deal_period_filed",
        "loan_period",
        ["deal_id", "period", "filing_date"],
        unique=False,
        schema="silver",
    )
    op.create_table(
        "loan_quarantine",
        sa.Column("accession", sa.String(length=20), nullable=False),
        sa.Column("file_name", sa.String(length=256), nullable=False),
        sa.Column("asset_position", sa.Integer(), nullable=False),
        sa.Column("deal_id", sa.Integer(), nullable=False),
        sa.Column("loan", sa.Text(), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("filing_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["accession", "file_name", "asset_position"],
            [
                "bronze.loan_tape_asset.accession",
                "bronze.loan_tape_asset.file_name",
                "bronze.loan_tape_asset.position",
            ],
            name=op.f("fk_loan_quarantine_accession_loan_tape_asset"),
        ),
        sa.ForeignKeyConstraint(
            ["deal_id"], ["silver.deal.id"], name=op.f("fk_loan_quarantine_deal_id_deal")
        ),
        sa.PrimaryKeyConstraint(
            "accession", "file_name", "asset_position", name=op.f("pk_loan_quarantine")
        ),
        schema="silver",
    )
    op.create_index(
        op.f("ix_silver_loan_quarantine_deal_id"),
        "loan_quarantine",
        ["deal_id"],
        unique=False,
        schema="silver",
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_silver_loan_quarantine_deal_id"), table_name="loan_quarantine", schema="silver"
    )
    op.drop_table("loan_quarantine", schema="silver")
    op.drop_index(
        "ix_silver_loan_period_deal_period_filed", table_name="loan_period", schema="silver"
    )
    op.drop_table("loan_period", schema="silver")
    op.drop_table("loan_tape_asset", schema="bronze")
    op.drop_index("ix_bronze_loan_tape_deal_period", table_name="loan_tape", schema="bronze")
    op.drop_table("loan_tape", schema="bronze")
