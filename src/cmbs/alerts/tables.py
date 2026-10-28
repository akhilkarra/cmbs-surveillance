"""Alerts and the record of each month's build, in `app`: they carry shared workflow state.

app.alert_build  one row per deal and month built: what was compared, under which settings
app.alert        one alert per loan (or linked whole loan), month and rule, with its state
app.alert_loan   each deal's loan an alert covers, the filing that showed the change and the
                 loan's balance on it; a linked whole loan has one row per deal
"""

from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cmbs.alerts.lifecycle import State
from cmbs.alerts.rules import Severity
from cmbs.db.models import APP_SCHEMA, Base

_USER = f"{APP_SCHEMA}.user.name"


def _one_of(column: str, values: Iterable[str]) -> str:
    return f"{column} IN ({', '.join(f"'{value}'" for value in values)})"


class AlertBuild(Base):
    """A deal's month, compared with the month before. `compared_with` is None when there was
    no earlier tape to compare with."""

    __tablename__ = "alert_build"
    __table_args__ = (
        CheckConstraint("extract(day FROM period) = 1", name="period_is_first_of_month"),
        {"schema": APP_SCHEMA},
    )

    deal_id: Mapped[int] = mapped_column(ForeignKey("silver.deal.id"), primary_key=True)
    period: Mapped[date] = mapped_column(Date, primary_key=True)
    as_of: Mapped[date] = mapped_column(Date)
    accession: Mapped[str] = mapped_column(ForeignKey("raw.filing.accession"))
    compared_with: Mapped[str | None] = mapped_column(ForeignKey("raw.filing.accession"))
    report_accession: Mapped[str | None] = mapped_column(ForeignKey("raw.filing.accession"))
    settings_version: Mapped[int] = mapped_column(Integer)
    first_built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Alert(Base):
    """One change worth a PM's attention. Stored once per subject, month and rule.

    `subject` is "<deal id>/<loan>" for a loan, or "whole loan <id>" for a linked whole loan,
    so the same whole loan held through two deals raises one alert.
    """

    __tablename__ = "alert"
    __table_args__ = (
        UniqueConstraint("subject", "period", "rule", name="uq_alert_subject_period_rule"),
        CheckConstraint("extract(day FROM period) = 1", name="period_is_first_of_month"),
        CheckConstraint(_one_of("severity", Severity), name="known_severity"),
        CheckConstraint(_one_of("state", State), name="known_state"),
        CheckConstraint(
            "state <> 'acknowledged' OR acknowledged_by IS NOT NULL", name="acknowledged_by_someone"
        ),
        CheckConstraint(
            "state <> 'assigned' OR assigned_to IS NOT NULL", name="assigned_to_someone"
        ),
        CheckConstraint(
            "state <> 'closed' OR (closed_by IS NOT NULL AND close_note IS NOT NULL)",
            name="closed_with_a_note",
        ),
        Index("ix_app_alert_period_state", "period", "state"),
        {"schema": APP_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject: Mapped[str] = mapped_column(String(64))
    period: Mapped[date] = mapped_column(Date)
    rule: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(8))
    title: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    # The comment sentences a "servicer comment changed" alert shows; empty for other rules.
    added: Mapped[list[str]] = mapped_column(JSONB)
    whole_loan_id: Mapped[int | None] = mapped_column(ForeignKey(f"{APP_SCHEMA}.whole_loan.id"))
    settings_version: Mapped[int] = mapped_column(Integer)
    as_of: Mapped[date] = mapped_column(Date)
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    run_id: Mapped[int | None] = mapped_column(ForeignKey(f"{APP_SCHEMA}.run.id"))
    state: Mapped[str] = mapped_column(String(16), default=State.NEW.value)
    acknowledged_by: Mapped[str | None] = mapped_column(ForeignKey(_USER))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_to: Mapped[str | None] = mapped_column(ForeignKey(_USER), index=True)
    assigned_by: Mapped[str | None] = mapped_column(ForeignKey(_USER))
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assign_note: Mapped[str | None] = mapped_column(Text)
    closed_by: Mapped[str | None] = mapped_column(ForeignKey(_USER))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_note: Mapped[str | None] = mapped_column(Text)


class AlertLoan(Base):
    """A deal's loan an alert covers, with the filing that showed the change."""

    __tablename__ = "alert_loan"
    __table_args__ = (
        Index("ix_app_alert_loan_deal_loan", "deal_id", "loan"),
        CheckConstraint("balance >= 0", name="balance_not_negative"),
        {"schema": APP_SCHEMA},
    )

    alert_id: Mapped[int] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.alert.id", ondelete="CASCADE"), primary_key=True
    )
    # A whole loan has one note in each deal, so an alert covers one loan of each deal.
    deal_id: Mapped[int] = mapped_column(ForeignKey("silver.deal.id"), primary_key=True)
    loan: Mapped[str] = mapped_column(Text)
    accession: Mapped[str] = mapped_column(ForeignKey("raw.filing.accession"))
    # The loan's ending balance on the month's tape; None when not reported or not on the tape.
    balance: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
