"""Run claims, run months and an index on run start times (batch refresh and run history).

app.run_claim holds the run working on each deal; app.run_month records every deal-month a run
tried to load. Both are logs in `app`, like app.run, so the data fingerprint ignores them.
Runs are kept however old they get: a trigger refuses deleting rows of app.run and
app.run_month, as the audit log's trigger does for its entries.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-28 19:43:14.209731
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEEP_RUNS_FUNCTION = """
CREATE FUNCTION app.refuse_run_deletion() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'runs are kept: the run history is never deleted';
END;
$$
"""
KEPT_TABLES = ("run", "run_month")


def upgrade() -> None:
    op.create_table(
        "run_claim",
        sa.Column("deal_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["deal_id"], ["silver.deal.id"], name=op.f("fk_run_claim_deal_id_deal")
        ),
        sa.ForeignKeyConstraint(["run_id"], ["app.run.id"], name=op.f("fk_run_claim_run_id_run")),
        sa.PrimaryKeyConstraint("deal_id", name=op.f("pk_run_claim")),
        schema="app",
    )
    op.create_index(
        op.f("ix_app_run_claim_run_id"), "run_claim", ["run_id"], unique=False, schema="app"
    )
    op.create_table(
        "run_month",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("deal_id", sa.Integer(), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("tape_accession", sa.String(length=20), nullable=True),
        sa.Column("report_accession", sa.String(length=20), nullable=True),
        sa.Column("loan_rows", sa.Integer(), nullable=False),
        sa.Column("alerts", sa.Integer(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('loaded', 'not loaded')", name=op.f("ck_run_month_known_outcome")
        ),
        sa.CheckConstraint(
            "extract(day FROM period) = 1", name=op.f("ck_run_month_period_is_first_of_month")
        ),
        sa.CheckConstraint("loan_rows >= 0", name=op.f("ck_run_month_loan_rows_not_negative")),
        sa.ForeignKeyConstraint(
            ["deal_id"], ["silver.deal.id"], name=op.f("fk_run_month_deal_id_deal")
        ),
        sa.ForeignKeyConstraint(["run_id"], ["app.run.id"], name=op.f("fk_run_month_run_id_run")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_month")),
        schema="app",
    )
    op.create_index(
        "ix_app_run_month_deal_period",
        "run_month",
        ["deal_id", "period", "at"],
        unique=False,
        schema="app",
    )
    op.create_index(
        op.f("ix_app_run_month_run_id"), "run_month", ["run_id"], unique=False, schema="app"
    )
    op.create_index(
        op.f("ix_app_run_started_at"), "run", ["started_at"], unique=False, schema="app"
    )
    op.execute(KEEP_RUNS_FUNCTION)
    for table in KEPT_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_kept BEFORE DELETE OR TRUNCATE ON app.{table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION app.refuse_run_deletion()"
        )


def downgrade() -> None:
    for table in KEPT_TABLES:
        op.execute(f"DROP TRIGGER {table}_kept ON app.{table}")
    op.execute("DROP FUNCTION app.refuse_run_deletion()")
    op.drop_index(op.f("ix_app_run_started_at"), table_name="run", schema="app")
    op.drop_index(op.f("ix_app_run_month_run_id"), table_name="run_month", schema="app")
    op.drop_index("ix_app_run_month_deal_period", table_name="run_month", schema="app")
    op.drop_table("run_month", schema="app")
    op.drop_index(op.f("ix_app_run_claim_run_id"), table_name="run_claim", schema="app")
    op.drop_table("run_claim", schema="app")
