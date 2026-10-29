"""Each loan period's interest rate (pool invariants: the weighted-average coupon).

silver.loan_period gains `interest_rate`, Schedule AL's `reportPeriodInterestRatePercentage`: a
fraction (0.03768 is 3.768%), stored with every digit reported, null when not reported; and
`interest_rate_note`, why the rate is not used: it is not a number (the rate is then null), or
it lies outside 0 to 0.25 (kept as reported). A bad rate never keeps the loan from loading.

Rows loaded before these columns existed are filled from the tag as stored in bronze, exactly as
the loader reads it (`cmbs.tape.convert.read_rate`): the same numbers, nulls and notes.

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

# The tape loader's number pattern and rate limit (cmbs.tape.convert), on the tag's text without
# surrounding whitespace. CASE tests its branches in order, so a text is cast only once it is
# known to be a number.
BACKFILL = r"""
UPDATE silver.loan_period AS row
SET interest_rate = CASE WHEN reported.text ~ '^[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)$'
                         THEN CAST(reported.text AS numeric) END,
    interest_rate_note = CASE
        WHEN reported.text = '' THEN NULL
        WHEN reported.text !~ '^[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)$'
            THEN 'interest rate is not a number: ''' || reported.text || ''''
        WHEN CAST(reported.text AS numeric) < 0 OR CAST(reported.text AS numeric) > 0.25
            THEN 'interest rate ''' || reported.text || ''' is outside 0 to 0.25'
    END
FROM (
    SELECT accession, file_name, position,
           btrim(tags ->> 'reportPeriodInterestRatePercentage', E' \t\r\n') AS text
    FROM bronze.loan_tape_asset
    WHERE tags ? 'reportPeriodInterestRatePercentage'
) AS reported
WHERE reported.accession = row.accession
  AND reported.file_name = row.file_name
  AND reported.position = row.asset_position
"""


def upgrade() -> None:
    op.add_column(
        "loan_period", sa.Column("interest_rate", sa.Numeric(), nullable=True), schema="silver"
    )
    op.add_column(
        "loan_period", sa.Column("interest_rate_note", sa.Text(), nullable=True), schema="silver"
    )
    op.execute(BACKFILL)


def downgrade() -> None:
    op.drop_column("loan_period", "interest_rate_note", schema="silver")
    op.drop_column("loan_period", "interest_rate", schema="silver")
