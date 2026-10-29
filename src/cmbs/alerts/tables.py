"""Alerts and the record of each month's build, in `app`: they carry shared workflow state.

app.alert_build  every build of a deal's month that read something new: a vintage, with what
                 it compared and under which settings. Never changed once written.
app.alert        one alert per loan (or linked whole loan, or deal), month and rule, with its
                 state; an alert a later vintage no longer supports is marked superseded, never
                 deleted
app.alert_loan   each deal's loan an alert covers, the filing that showed the change and the
                 loan's balance on it; a linked whole loan has one row per deal, each of which a
                 later vintage of its own deal can supersede; an alert on a deal has one row,
                 with no loan, listing the loans it names and their total balance
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
    text,
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
    """One vintage of a deal's month: the month compared with the month before, as published on
    `as_of`. `compared_with` and `compared_report` are None when the month before had no tape or
    no 10-D. A build that would read exactly what the latest vintage read writes nothing."""

    __tablename__ = "alert_build"
    __table_args__ = (
        CheckConstraint("extract(day FROM period) = 1", name="period_is_first_of_month"),
        Index("ix_app_alert_build_deal_period", "deal_id", "period", "id"),
        {"schema": APP_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("silver.deal.id"))
    period: Mapped[date] = mapped_column(Date)
    as_of: Mapped[date] = mapped_column(Date)
    accession: Mapped[str] = mapped_column(ForeignKey("raw.filing.accession"))
    report_accession: Mapped[str | None] = mapped_column(ForeignKey("raw.filing.accession"))
    compared_with: Mapped[str | None] = mapped_column(ForeignKey("raw.filing.accession"))
    compared_report: Mapped[str | None] = mapped_column(ForeignKey("raw.filing.accession"))
    settings_version: Mapped[int] = mapped_column(Integer)
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    built_by: Mapped[str | None] = mapped_column(ForeignKey(_USER))
    run_id: Mapped[int | None] = mapped_column(ForeignKey(f"{APP_SCHEMA}.run.id"))


class Alert(Base):
    """One change worth a PM's attention. One alert not superseded per subject, month and rule.

    `subject` is "<deal id>/<loan>" for a loan, or "whole loan <id>" for a linked whole loan,
    so the same whole loan held through two deals raises one alert; "deal <id>" for an alert on
    the deal's tape as a whole (the loans it no longer or newly reports). `raised_in` is the vintage
    that raised it; `superseded_by` the vintage that no longer supports it, with the reason.
    """

    __tablename__ = "alert"
    __table_args__ = (
        Index(
            "uq_app_alert_open_subject_period_rule",
            "subject",
            "period",
            "rule",
            unique=True,
            postgresql_where=text("superseded_by IS NULL"),
        ),
        CheckConstraint(
            "(superseded_by IS NULL) = (superseded_at IS NULL) "
            "AND (superseded_by IS NULL) = (superseded_reason IS NULL)",
            name="superseded_with_a_reason",
        ),
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
    raised_in: Mapped[int] = mapped_column(ForeignKey(f"{APP_SCHEMA}.alert_build.id"))
    superseded_by: Mapped[int | None] = mapped_column(ForeignKey(f"{APP_SCHEMA}.alert_build.id"))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_reason: Mapped[str | None] = mapped_column(Text)
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
    """A deal's loan an alert covers, with the filing that showed the change; or, for an alert
    on the deal itself (loans no longer or newly reported), the loans it lists."""

    __tablename__ = "alert_loan"
    __table_args__ = (
        Index("ix_app_alert_loan_deal_loan", "deal_id", "loan"),
        CheckConstraint("balance >= 0", name="balance_not_negative"),
        CheckConstraint(
            "(loan IS NULL) = (jsonb_array_length(loans) > 0)", name="one_loan_or_a_list"
        ),
        {"schema": APP_SCHEMA},
    )

    alert_id: Mapped[int] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.alert.id", ondelete="CASCADE"), primary_key=True
    )
    # A whole loan has one note in each deal, so an alert covers one loan of each deal.
    deal_id: Mapped[int] = mapped_column(ForeignKey("silver.deal.id"), primary_key=True)
    # None for an alert on the deal, which lists its loans in `loans` instead.
    loan: Mapped[str | None] = mapped_column(Text)
    # The loans an alert on the deal lists, in loan order; empty for an alert on one loan.
    loans: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    accession: Mapped[str] = mapped_column(ForeignKey("raw.filing.accession"))
    # The loan's ending balance on the month's tape; None when not reported or not on the tape.
    # For an alert on the deal, the listed loans' total on the tape that reported them.
    balance: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    # The vintage of this deal's month that no longer supports the alert for this loan.
    superseded_by: Mapped[int | None] = mapped_column(ForeignKey(f"{APP_SCHEMA}.alert_build.id"))
