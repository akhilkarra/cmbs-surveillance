from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from cmbs.db.models import APP_SCHEMA, Base


class PositionsFile(Base):
    """One loaded positions file. The book on a date is the latest file dated on or before it."""

    __tablename__ = "positions_file"
    __table_args__ = {"schema": APP_SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    loaded_by: Mapped[str] = mapped_column(ForeignKey(f"{APP_SCHEMA}.user.name"))
    loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_name: Mapped[str | None] = mapped_column(String(256))


class Position(Base):
    """Face held in one class of one deal, as one line of a positions file."""

    __tablename__ = "position"
    __table_args__ = (
        UniqueConstraint("file_id", "deal_id", "tranche"),
        CheckConstraint("face_amount > 0", name="positive_face"),
        {"schema": APP_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[int] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.positions_file.id", ondelete="CASCADE"), index=True
    )
    deal_id: Mapped[int] = mapped_column(ForeignKey("silver.deal.id"), index=True)
    # The certificate class held ("E"); `class` is a reserved word in SQL and Python.
    tranche: Mapped[str] = mapped_column(String(16))
    face_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
