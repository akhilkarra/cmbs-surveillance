"""Extensions and layer schemas

Enables pgvector and pg_trgm (grill log Q53) and creates one schema per medallion layer (Q20).

Revision ID: 0001
Revises:
Create Date: 2026-10-27 22:52:41.177821
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXTENSIONS = ("vector", "pg_trgm")
LAYERS = ("raw", "bronze", "silver", "gold")


def upgrade() -> None:
    for extension in EXTENSIONS:
        op.execute(f"CREATE EXTENSION IF NOT EXISTS {extension}")
    for layer in LAYERS:
        op.execute(f"CREATE SCHEMA {layer}")


def downgrade() -> None:
    for layer in reversed(LAYERS):
        op.execute(f"DROP SCHEMA {layer}")
    for extension in reversed(EXTENSIONS):
        op.execute(f"DROP EXTENSION {extension}")
