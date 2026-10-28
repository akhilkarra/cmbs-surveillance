from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from cmbs.db.models import Base


class Filing(Base):
    """One EDGAR filing. `period` is the first day of the distribution month it reports on."""

    __tablename__ = "filing"
    __table_args__ = {"schema": "raw"}

    accession: Mapped[str] = mapped_column(String(20), primary_key=True)
    cik: Mapped[int] = mapped_column(Integer, index=True)
    deal_id: Mapped[int | None] = mapped_column(ForeignKey("silver.deal.id"), index=True)
    form: Mapped[str] = mapped_column(String(16))
    filing_date: Mapped[date] = mapped_column(Date, index=True)
    report_date: Mapped[date | None] = mapped_column(Date)
    period: Mapped[date | None] = mapped_column(Date, index=True)
    amends: Mapped[str | None] = mapped_column(String(20))
    primary_document: Mapped[str | None] = mapped_column(String(256))


class FilingFile(Base):
    """A file downloaded from a filing, stored byte for byte so the panel can be rebuilt."""

    __tablename__ = "filing_file"
    __table_args__ = (
        CheckConstraint("char_length(sha256) = 64", name="sha256_length"),
        {"schema": "raw"},
    )

    accession: Mapped[str] = mapped_column(
        ForeignKey("raw.filing.accession", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(256), primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
