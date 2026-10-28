from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cmbs.db.models import Base


class CheckFlag(Base):
    __tablename__ = "check_flag"
    __table_args__ = {"schema": "silver"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("silver.deal.id"), index=True)
    loan: Mapped[str] = mapped_column(String(32))
    period: Mapped[date | None] = mapped_column(Date)
    check: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    sides: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
