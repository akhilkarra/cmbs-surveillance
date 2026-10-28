from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from cmbs.db.models import APP_SCHEMA, Base


class VendorSource(Base):
    """A vendor loan-tape feed configured for a deal, which public filings may not cover."""

    __tablename__ = "vendor_source"
    __table_args__ = (UniqueConstraint("deal_id", "name"), {"schema": APP_SCHEMA})

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("silver.deal.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    configured_by: Mapped[str] = mapped_column(ForeignKey(f"{APP_SCHEMA}.user.name"))
    configured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
