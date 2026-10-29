"""Each loan period's interest rate (pool invariants: the weighted-average coupon).

silver.loan_period gains `interest_rate`, Schedule AL's `reportPeriodInterestRatePercentage`: a
fraction (0.03768 is 3.768%), stored with every digit reported, null when not reported.

Rows loaded before this column existed are filled from the tag as stored in bronze, read as the
loader reads it: a value that is not a plain decimal is left null, never guessed at.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-29 15:14:54.685799
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The tape loader's number pattern (cmbs.tape.convert), on the tag's text without surrounding
# whitespace.
BACKFILL = r"""
UPDATE silver.loan_period AS row
SET interest_rate = CAST(btrim(asset.tags ->> 'reportPeriodInterestRatePercentage', E' \t\r\n')
                         AS numeric)
FROM bronze.loan_tape_asset AS asset
WHERE asset.accession = row.accession
  AND asset.file_name = row.file_name
  AND asset.position = row.asset_position
  AND btrim(asset.tags ->> 'reportPeriodInterestRatePercentage', E' \t\r\n')
      ~ '^[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)$'
"""


def upgrade() -> None:
    op.add_column(
        "loan_period", sa.Column("interest_rate", sa.Numeric(), nullable=True), schema="silver"
    )
    op.execute(BACKFILL)


def downgrade() -> None:
    op.drop_column("loan_period", "interest_rate", schema="silver")
