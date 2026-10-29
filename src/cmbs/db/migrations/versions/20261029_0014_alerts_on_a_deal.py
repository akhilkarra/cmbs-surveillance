"""Alerts on a deal's tape as a whole: the loans it no longer or newly reports.

A tape that drops loans usually drops many at once, so those rules raise one alert per deal and
month listing every loan. Such an alert's row in app.alert_loan names no loan and lists its loans
instead: `loan` becomes nullable, `loans` holds the list, and a check keeps exactly one of them.

Revision ID: 0014
Revises: 0012
Create Date: 2026-10-29 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "alert_loan",
        sa.Column(
            "loans",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        schema="app",
    )
    op.alter_column("alert_loan", "loan", existing_type=sa.Text(), nullable=True, schema="app")
    op.create_check_constraint(
        op.f("ck_alert_loan_one_loan_or_a_list"),
        "alert_loan",
        "(loan IS NULL) = (jsonb_array_length(loans) > 0)",
        schema="app",
    )


def downgrade() -> None:
    # The earlier schema cannot hold an alert on a deal: those alerts go (their rows in
    # app.alert_loan with them, by cascade); the audit log keeps what was raised and done.
    op.execute(
        "DELETE FROM app.alert WHERE id IN (SELECT alert_id FROM app.alert_loan WHERE loan IS NULL)"
    )
    op.drop_constraint(
        op.f("ck_alert_loan_one_loan_or_a_list"), "alert_loan", type_="check", schema="app"
    )
    op.alter_column("alert_loan", "loan", existing_type=sa.Text(), nullable=False, schema="app")
    op.drop_column("alert_loan", "loans", schema="app")
