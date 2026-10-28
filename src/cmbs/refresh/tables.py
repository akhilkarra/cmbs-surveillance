"""What refresh runs hold and what they did, month by month. Logs in `app`, not market data.

app.run_claim  the run working on each deal now. A run takes a deal's advisory lock before it
               writes the row and deletes the row before it lets the lock go; a row whose lock is
               free was left by a run that stopped without finishing.
app.run_month  every deal-month a run tried to load, with its outcome. A month that was not
               loaded says why, so the month is "not loaded" until a later run loads it.
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from cmbs.db.models import APP_SCHEMA, Base

MONTH_OUTCOMES = ("loaded", "not loaded")


class RunClaim(Base):
    __tablename__ = "run_claim"
    __table_args__ = {"schema": APP_SCHEMA}

    deal_id: Mapped[int] = mapped_column(ForeignKey("silver.deal.id"), primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey(f"{APP_SCHEMA}.run.id"), index=True)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RunMonth(Base):
    __tablename__ = "run_month"
    __table_args__ = (
        CheckConstraint(
            "outcome IN (" + ", ".join(f"'{o}'" for o in MONTH_OUTCOMES) + ")",
            name="known_outcome",
        ),
        CheckConstraint("extract(day FROM period) = 1", name="period_is_first_of_month"),
        CheckConstraint("loan_rows >= 0", name="loan_rows_not_negative"),
        Index("ix_app_run_month_deal_period", "deal_id", "period", "at"),
        {"schema": APP_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey(f"{APP_SCHEMA}.run.id"), index=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("silver.deal.id"))
    period: Mapped[date] = mapped_column(Date)
    outcome: Mapped[str] = mapped_column(String(16))
    # Why a month was not loaded ("10-D failed to parse"), and the refusal or error behind it.
    reason: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)
    tape_accession: Mapped[str | None] = mapped_column(String(20))
    report_accession: Mapped[str | None] = mapped_column(String(20))
    loan_rows: Mapped[int] = mapped_column(Integer, default=0)
    # None until alerts are built for loaded months (cmbs.alerts).
    alerts: Mapped[int | None] = mapped_column(Integer)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
