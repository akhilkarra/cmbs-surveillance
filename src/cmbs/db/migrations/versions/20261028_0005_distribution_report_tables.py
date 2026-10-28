"""Distribution report tables

Parsed 10-D distribution reports (EX-99.1) in `silver`: one row per report, tied to the stored
file it was read from, and the per-loan items taken from it (special-servicing comments,
appraisal reductions, delinquency detail), each keyed by accession, loan and source table.

Revision ID: 0005
Revises: 0002
Create Date: 2026-10-28 09:31:31.899789
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "distribution_report",
        sa.Column("accession", sa.String(length=20), nullable=False),
        sa.Column("file_name", sa.String(length=256), nullable=False),
        sa.Column("deal_id", sa.Integer(), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("administrator", sa.String(length=32), nullable=False),
        sa.Column("distribution_date", sa.Date(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["accession", "file_name"],
            ["raw.filing_file.accession", "raw.filing_file.name"],
            name=op.f("fk_distribution_report_accession_filing_file"),
        ),
        sa.ForeignKeyConstraint(
            ["deal_id"], ["silver.deal.id"], name=op.f("fk_distribution_report_deal_id_deal")
        ),
        sa.PrimaryKeyConstraint("accession", name=op.f("pk_distribution_report")),
        schema="silver",
    )
    op.create_index(
        op.f("ix_silver_distribution_report_deal_id"),
        "distribution_report",
        ["deal_id"],
        unique=False,
        schema="silver",
    )
    op.create_table(
        "report_appraisal_reduction",
        sa.Column("accession", sa.String(length=20), nullable=False),
        sa.Column("loan_id", sa.String(length=32), nullable=False),
        sa.Column("source_table", sa.String(length=128), nullable=False),
        sa.Column("deal_id", sa.Integer(), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("reduction_date", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(
            ["accession"],
            ["silver.distribution_report.accession"],
            name=op.f("fk_report_appraisal_reduction_accession_distribution_report"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["deal_id"], ["silver.deal.id"], name=op.f("fk_report_appraisal_reduction_deal_id_deal")
        ),
        sa.PrimaryKeyConstraint(
            "accession", "loan_id", "source_table", name=op.f("pk_report_appraisal_reduction")
        ),
        schema="silver",
    )
    op.create_index(
        "ix_report_appraisal_reduction_deal_loan",
        "report_appraisal_reduction",
        ["deal_id", "loan_id", "period"],
        unique=False,
        schema="silver",
    )
    op.create_table(
        "report_comment",
        sa.Column("accession", sa.String(length=20), nullable=False),
        sa.Column("loan_id", sa.String(length=32), nullable=False),
        sa.Column("source_table", sa.String(length=128), nullable=False),
        sa.Column("deal_id", sa.Integer(), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["accession"],
            ["silver.distribution_report.accession"],
            name=op.f("fk_report_comment_accession_distribution_report"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["deal_id"], ["silver.deal.id"], name=op.f("fk_report_comment_deal_id_deal")
        ),
        sa.PrimaryKeyConstraint(
            "accession", "loan_id", "source_table", name=op.f("pk_report_comment")
        ),
        schema="silver",
    )
    op.create_index(
        "ix_report_comment_deal_loan",
        "report_comment",
        ["deal_id", "loan_id", "period"],
        unique=False,
        schema="silver",
    )
    op.create_table(
        "report_delinquency",
        sa.Column("accession", sa.String(length=20), nullable=False),
        sa.Column("loan_id", sa.String(length=32), nullable=False),
        sa.Column("source_table", sa.String(length=128), nullable=False),
        sa.Column("deal_id", sa.Integer(), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("paid_through", sa.Date(), nullable=True),
        sa.Column("current_advances", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("outstanding_advances", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("actual_balance", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.ForeignKeyConstraint(
            ["accession"],
            ["silver.distribution_report.accession"],
            name=op.f("fk_report_delinquency_accession_distribution_report"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["deal_id"], ["silver.deal.id"], name=op.f("fk_report_delinquency_deal_id_deal")
        ),
        sa.PrimaryKeyConstraint(
            "accession", "loan_id", "source_table", name=op.f("pk_report_delinquency")
        ),
        schema="silver",
    )
    op.create_index(
        "ix_report_delinquency_deal_loan",
        "report_delinquency",
        ["deal_id", "loan_id", "period"],
        unique=False,
        schema="silver",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_report_delinquency_deal_loan", table_name="report_delinquency", schema="silver"
    )
    op.drop_table("report_delinquency", schema="silver")
    op.drop_index("ix_report_comment_deal_loan", table_name="report_comment", schema="silver")
    op.drop_table("report_comment", schema="silver")
    op.drop_index(
        "ix_report_appraisal_reduction_deal_loan",
        table_name="report_appraisal_reduction",
        schema="silver",
    )
    op.drop_table("report_appraisal_reduction", schema="silver")
    op.drop_index(
        op.f("ix_silver_distribution_report_deal_id"),
        table_name="distribution_report",
        schema="silver",
    )
    op.drop_table("distribution_report", schema="silver")
