from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from cmbs.db.models import APP_SCHEMA, Base


class FilingDiscovery(Base):
    """One run of filing discovery for a deal: what EDGAR showed as of `as_of`.

    The coverage report needs to tell "looked and found nothing" from "never looked", so every
    discovery is logged, including the ones that find no issuing entity.
    """

    __tablename__ = "filing_discovery"
    __table_args__ = {"schema": APP_SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("silver.deal.id"), index=True)
    as_of: Mapped[date] = mapped_column(Date)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    checked_by: Mapped[str] = mapped_column(ForeignKey(f"{APP_SCHEMA}.user.name"))
    cik: Mapped[int | None] = mapped_column(Integer)
    edgar_name: Mapped[str | None] = mapped_column(String(256))
    note: Mapped[str | None] = mapped_column(Text)
    filings: Mapped[int] = mapped_column(Integer)


class DealAlias(Base):
    """Another name for a registered deal: "COMM 2014-CCRE18" for "COMM 2014-CR18".

    A lookup that resolves to an issuing entity already registered under another name records
    the name it was given here, so every name of one trust reaches the one deal.
    """

    __tablename__ = "deal_alias"
    __table_args__ = {"schema": "silver"}

    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("silver.deal.id"), index=True)


class DealFiling(Base):
    """A filing that names a deal without being filed by it: a depositor's ABS-15G.

    One depositor filing can cover several privately placed deals, so the link is many to many
    and `raw.filing.deal_id` stays empty for it.
    """

    __tablename__ = "deal_filing"
    __table_args__ = {"schema": "silver"}

    deal_id: Mapped[int] = mapped_column(ForeignKey("silver.deal.id"), primary_key=True)
    accession: Mapped[str] = mapped_column(
        ForeignKey("raw.filing.accession", ondelete="CASCADE"), primary_key=True, index=True
    )
