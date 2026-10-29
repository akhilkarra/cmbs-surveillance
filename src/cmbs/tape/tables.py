"""Loan tapes in the bronze and silver layers.

bronze.loan_tape        one row per loaded EX-102 file: its filing, period and amendment depth
bronze.loan_tape_asset  every asset of an EX-102 file exactly as reported, loans or not
silver.loan_period      one typed row per top-level loan per filing; amendments add rows
silver.loan_quarantine  loans that failed the loan_period contract, with the reason

The raw layer is the shared filings registry (`raw.filing`, `raw.filing_file`).
"""

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cmbs.absee.schedule_al import AssetKind
from cmbs.db.models import Base

MONEY = Numeric(18, 2)
_KNOWN_KINDS = "kind IN ({})".format(", ".join(f"'{kind.value}'" for kind in AssetKind))


class LoanTape(Base):
    """A loaded EX-102 file. One per filing; its versions are ordered by filing date, then by
    amendment depth (0 for an original, 1 for its amendment, 2 for an amendment of that)."""

    __tablename__ = "loan_tape"
    __table_args__ = (
        ForeignKeyConstraint(
            ["accession", "file_name"],
            ["raw.filing_file.accession", "raw.filing_file.name"],
        ),
        CheckConstraint("amendment_depth >= 0", name="depth_not_negative"),
        CheckConstraint("extract(day FROM period) = 1", name="period_is_first_of_month"),
        Index("ix_bronze_loan_tape_deal_period", "deal_id", "period", "filing_date"),
        {"schema": "bronze"},
    )

    accession: Mapped[str] = mapped_column(
        String(20), ForeignKey("raw.filing.accession"), primary_key=True
    )
    file_name: Mapped[str] = mapped_column(String(256))
    file_sha256: Mapped[str] = mapped_column(String(64))
    deal_id: Mapped[int] = mapped_column(ForeignKey("silver.deal.id"))
    period: Mapped[date] = mapped_column(Date)
    filing_date: Mapped[date] = mapped_column(Date)
    amendment_depth: Mapped[int] = mapped_column(Integer)


class TapeAsset(Base):
    """One `<assets>` element. `tags` are its leaf tags, `properties` its property elements."""

    __tablename__ = "loan_tape_asset"
    __table_args__ = (
        ForeignKeyConstraint(
            ["accession", "file_name"],
            ["raw.filing_file.accession", "raw.filing_file.name"],
        ),
        CheckConstraint(_KNOWN_KINDS, name="known_kind"),
        CheckConstraint("position >= 1", name="position_from_one"),
        {"schema": "bronze"},
    )

    accession: Mapped[str] = mapped_column(
        String(20), ForeignKey("bronze.loan_tape.accession"), primary_key=True
    )
    file_name: Mapped[str] = mapped_column(String(256), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_number: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(24))
    tags: Mapped[dict[str, Any]] = mapped_column(JSONB)
    properties: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)


class LoanPeriod(Base):
    """A loan's values for one period, as one filing reported them.

    The filing date is when these values became public. An amendment is a new row with its own
    accession and filing date; the original row is never changed.
    """

    __tablename__ = "loan_period"
    __table_args__ = (
        ForeignKeyConstraint(
            ["accession", "file_name", "asset_position"],
            [
                "bronze.loan_tape_asset.accession",
                "bronze.loan_tape_asset.file_name",
                "bronze.loan_tape_asset.position",
            ],
        ),
        CheckConstraint("ending_balance >= 0", name="ending_balance_not_negative"),
        CheckConstraint("beginning_balance >= 0", name="beginning_balance_not_negative"),
        CheckConstraint("financials_end >= financials_start", name="financials_in_order"),
        CheckConstraint(
            "financials_end_newest >= financials_end", name="newest_financials_not_older"
        ),
        CheckConstraint("property_count >= 0", name="property_count_not_negative"),
        CheckConstraint("extract(day FROM period) = 1", name="period_is_first_of_month"),
        CheckConstraint("char_length(file_sha256) = 64", name="sha256_length"),
        Index("ix_silver_loan_period_deal_period_filed", "deal_id", "period", "filing_date"),
        {"schema": "silver"},
    )

    deal_id: Mapped[int] = mapped_column(ForeignKey("silver.deal.id"), primary_key=True)
    loan: Mapped[str] = mapped_column(Text, primary_key=True)
    period: Mapped[date] = mapped_column(Date, primary_key=True)
    accession: Mapped[str] = mapped_column(ForeignKey("raw.filing.accession"), primary_key=True)
    filing_date: Mapped[date] = mapped_column(Date)
    file_name: Mapped[str] = mapped_column(String(256))
    file_sha256: Mapped[str] = mapped_column(String(64))
    asset_position: Mapped[int] = mapped_column(Integer)
    reporting_period_end: Mapped[date] = mapped_column(Date)
    beginning_balance: Mapped[Decimal | None] = mapped_column(MONEY)
    scheduled_principal: Mapped[Decimal | None] = mapped_column(MONEY)
    unscheduled_principal: Mapped[Decimal | None] = mapped_column(MONEY)
    other_principal_adjustments: Mapped[Decimal | None] = mapped_column(MONEY)
    realized_loss: Mapped[Decimal | None] = mapped_column(MONEY)
    ending_balance: Mapped[Decimal | None] = mapped_column(MONEY)
    paid_through_date: Mapped[date | None] = mapped_column(Date)
    payment_status: Mapped[str | None] = mapped_column(Text)
    special_servicer_transfer_date: Mapped[date | None] = mapped_column(Date)
    master_servicer_return_date: Mapped[date | None] = mapped_column(Date)
    workout_strategy: Mapped[str | None] = mapped_column(Text)
    modification_code: Mapped[str | None] = mapped_column(Text)
    # The period's interest rate as a fraction (0.03768 is 3.768%), every digit as reported.
    interest_rate: Mapped[Decimal | None] = mapped_column(Numeric)
    # Property values: one property's as reported, or several combined (cmbs.tape.properties).
    property_count: Mapped[int] = mapped_column(Integer)
    property_type: Mapped[str | None] = mapped_column(Text)
    property_name: Mapped[str | None] = mapped_column(Text)
    defeased_status: Mapped[str | None] = mapped_column(Text)
    financials_start: Mapped[date | None] = mapped_column(Date)
    financials_end: Mapped[date | None] = mapped_column(Date)
    financials_end_newest: Mapped[date | None] = mapped_column(Date)
    noi: Mapped[Decimal | None] = mapped_column(MONEY)
    ncf: Mapped[Decimal | None] = mapped_column(MONEY)
    # Ratios keep every digit reported: no precision is imposed.
    dscr: Mapped[Decimal | None] = mapped_column(Numeric)
    occupancy: Mapped[Decimal | None] = mapped_column(Numeric)
    property_note: Mapped[str | None] = mapped_column(Text)


class QuarantinedLoan(Base):
    """A loan from a tape that failed the loan_period contract and was not loaded."""

    __tablename__ = "loan_quarantine"
    __table_args__ = (
        ForeignKeyConstraint(
            ["accession", "file_name", "asset_position"],
            [
                "bronze.loan_tape_asset.accession",
                "bronze.loan_tape_asset.file_name",
                "bronze.loan_tape_asset.position",
            ],
        ),
        {"schema": "silver"},
    )

    accession: Mapped[str] = mapped_column(String(20), primary_key=True)
    file_name: Mapped[str] = mapped_column(String(256), primary_key=True)
    asset_position: Mapped[int] = mapped_column(Integer, primary_key=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("silver.deal.id"), index=True)
    loan: Mapped[str] = mapped_column(Text)
    period: Mapped[date] = mapped_column(Date)
    filing_date: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(Text)
