"""Parsed distribution reports and the per-loan items taken from them, in `silver`.

Every item is keyed by accession, loan and the report table it came from, so one report can
state a loan in two tables without either value being overwritten; deal and period are stored
alongside for lookups. A value is visible from its filing's date (`raw.filing.filing_date`).
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, ForeignKeyConstraint, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from cmbs.db.models import Base

MONEY = Numeric(18, 2)


class DistributionReport(Base):
    """One parsed EX-99.1, tied to the stored file it was read from."""

    __tablename__ = "distribution_report"
    __table_args__ = (
        ForeignKeyConstraint(
            ["accession", "file_name"], ["raw.filing_file.accession", "raw.filing_file.name"]
        ),
        {"schema": "silver"},
    )

    # The stored file, and through it the filing.
    accession: Mapped[str] = mapped_column(String(20), primary_key=True)
    file_name: Mapped[str] = mapped_column(String(256))
    deal_id: Mapped[int] = mapped_column(ForeignKey("silver.deal.id"), index=True)
    period: Mapped[date] = mapped_column(Date)
    administrator: Mapped[str] = mapped_column(String(32))
    distribution_date: Mapped[date | None] = mapped_column(Date)
    summary: Mapped[str] = mapped_column(Text)


def _accession() -> Mapped[str]:
    return mapped_column(
        ForeignKey("silver.distribution_report.accession", ondelete="CASCADE"), primary_key=True
    )


class ReportComment(Base):
    """A special-servicing comment, verbatim apart from whitespace."""

    __tablename__ = "report_comment"
    __table_args__ = (
        Index("ix_report_comment_deal_loan", "deal_id", "loan_id", "period"),
        {"schema": "silver"},
    )

    accession: Mapped[str] = _accession()
    loan_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_table: Mapped[str] = mapped_column(String(128), primary_key=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("silver.deal.id"))
    period: Mapped[date] = mapped_column(Date)
    comment: Mapped[str] = mapped_column(Text)


class ReportAppraisalReduction(Base):
    """An appraisal reduction amount in effect for a loan, as the report states it."""

    __tablename__ = "report_appraisal_reduction"
    __table_args__ = (
        Index("ix_report_appraisal_reduction_deal_loan", "deal_id", "loan_id", "period"),
        {"schema": "silver"},
    )

    accession: Mapped[str] = _accession()
    loan_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_table: Mapped[str] = mapped_column(String(128), primary_key=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("silver.deal.id"))
    period: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    reduction_date: Mapped[date | None] = mapped_column(Date)


class ReportDelinquency(Base):
    """A loan's row in the delinquency table. `status` is the administrator's code, unread."""

    __tablename__ = "report_delinquency"
    __table_args__ = (
        Index("ix_report_delinquency_deal_loan", "deal_id", "loan_id", "period"),
        {"schema": "silver"},
    )

    accession: Mapped[str] = _accession()
    loan_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_table: Mapped[str] = mapped_column(String(128), primary_key=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("silver.deal.id"))
    period: Mapped[date] = mapped_column(Date)
    status: Mapped[str | None] = mapped_column(Text)
    paid_through: Mapped[date | None] = mapped_column(Date)
    current_advances: Mapped[Decimal | None] = mapped_column(MONEY)
    outstanding_advances: Mapped[Decimal | None] = mapped_column(MONEY)
    actual_balance: Mapped[Decimal | None] = mapped_column(MONEY)
