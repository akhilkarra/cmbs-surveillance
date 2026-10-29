"""Alerts on a deal's tape as a whole: the loans it no longer or newly reports (decision Q88).

A tape that drops loans usually drops many at once, so those rules raise one alert per deal and
month listing every loan. Such an alert's row in app.alert_loan names no loan and lists its loans
instead: `loan` becomes nullable, `loans` holds the list, `reasons` the quarantine reason of each
listed loan the new tape quarantined, and a check keeps exactly one of `loan` and `loans`.

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
    op.add_column(
        "alert_loan",
        sa.Column(
            "reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
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
    # The earlier schema cannot hold an alert on a deal, so downgrading DELETES every one of
    # them, whatever its state (their app.alert_loan rows go with them, by cascade). The audit
    # log keeps the entries recording that they were raised, acknowledged, assigned, closed or
    # superseded. The per-loan alerts they superseded are NOT restored: they stay superseded,
    # so the month shows no gap alert until a new filing gives its build something new to read.
    op.execute(
        "DELETE FROM app.alert WHERE id IN (SELECT alert_id FROM app.alert_loan WHERE loan IS NULL)"
    )
    op.drop_constraint(
        op.f("ck_alert_loan_one_loan_or_a_list"), "alert_loan", type_="check", schema="app"
    )
    op.alter_column("alert_loan", "loan", existing_type=sa.Text(), nullable=False, schema="app")
    op.drop_column("alert_loan", "reasons", schema="app")
    op.drop_column("alert_loan", "loans", schema="app")
