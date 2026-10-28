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
