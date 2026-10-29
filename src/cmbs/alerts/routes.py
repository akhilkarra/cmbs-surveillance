"""Alerts over HTTP: build a month, read alerts with their exposure, and change their state.

Periods travel as their label ("September 2026"); money as strings, so no digit is lost.
"""

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from cmbs.alerts import service
from cmbs.alerts.service import AlertList, AlertSummary, AlertView, CoveredLoan
from cmbs.api.deps import DB, User
from cmbs.domain import Period

router = APIRouter(prefix="/alerts", tags=["alerts"])


def _period(text: str) -> Period:
    try:
        return Period.parse(text)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _money(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"


class CoveredLoanOut(BaseModel):
    deal: str
    loan: str
    accession: str
    balance: str | None
    exposure: str
    classes: list[str]
    superseded: bool

    @classmethod
    def of(cls, loan: CoveredLoan) -> "CoveredLoanOut":
        return cls(
            deal=loan.deal,
            loan=loan.loan,
            accession=loan.accession,
            balance=_money(loan.balance),
            exposure=f"{loan.exposure:.2f}",
            classes=list(loan.classes),
            superseded=loan.superseded,
        )


class SupersessionOut(BaseModel):
    vintage: int
    at: dt.datetime
    reason: str


class AlertOut(BaseModel):
    id: int
    period: str
    severity: str
    title: str
    message: str
    added: list[str]
    settings_version: int
    as_of: dt.date
    raised_at: dt.datetime
    state: str
    state_text: str
    note: str | None
    assigned_to: str | None
    exposure: str
    classes: list[str]
    balance: str | None
    loans: list[CoveredLoanOut]
    superseded: SupersessionOut | None

    @classmethod
    def of(cls, view: AlertView) -> "AlertOut":
        gone = view.superseded
        return cls(
            id=view.id,
            period=view.period.label,
            severity=view.severity.value,
            title=view.title,
            message=view.message,
            added=list(view.added),
            settings_version=view.settings_version,
            as_of=view.as_of,
            raised_at=view.raised_at,
            state=view.state.value,
            state_text=view.state_text,
            note=view.note,
            assigned_to=view.assigned_to,
            exposure=f"{view.exposure:.2f}",
            classes=list(view.classes),
            balance=_money(view.balance),
            loans=[CoveredLoanOut.of(loan) for loan in view.loans],
            superseded=(
                SupersessionOut(vintage=gone.vintage, at=gone.at, reason=gone.reason)
                if gone is not None
                else None
            ),
        )


class AlertListOut(BaseModel):
    """The current vintage's alerts, and apart those a later vintage superseded."""

    deal: str
    period: str
    note: str | None
    alerts: list[AlertOut]
    superseded_alerts: list[AlertOut]

    @classmethod
    def of(cls, listed: AlertList) -> "AlertListOut":
        return cls(
            deal=listed.deal,
            period=listed.period.label,
            note=listed.note,
            alerts=[AlertOut.of(view) for view in listed.alerts],
            superseded_alerts=[AlertOut.of(view) for view in listed.superseded_alerts],
        )


class BuildIn(BaseModel):
    deal: str
    period: str
    as_of: dt.date | None = None


class BuildOut(AlertListOut):
    as_of: dt.date
    settings_version: int
    vintage: int | None
    raised: int
    superseded: int

    @classmethod
    def of_summary(cls, summary: AlertSummary) -> "BuildOut":
        return cls(
            deal=summary.deal,
            period=summary.period.label,
            note=summary.note,
            alerts=[AlertOut.of(view) for view in summary.alerts],
            superseded_alerts=[AlertOut.of(view) for view in summary.superseded_alerts],
            as_of=summary.as_of,
            settings_version=summary.settings_version,
            vintage=summary.vintage,
            raised=summary.raised,
            superseded=summary.superseded,
        )


class AssignIn(BaseModel):
    to: str
    note: str | None = None


class CloseIn(BaseModel):
    note: str


@router.post("/build")
def build(body: BuildIn, user: User, db: DB) -> BuildOut:
    """Compare the deal's month with the month before; needs the right to run refreshes."""
    summary = service.build_for(
        db, user=user, deal=body.deal, period=_period(body.period), as_of=body.as_of
    )
    return BuildOut.of_summary(summary)


@router.get("")
def month(user: User, db: DB, deal: str, period: str) -> AlertListOut:
    """A deal's alerts for a month, most severe first; an empty list says why."""
    return AlertListOut.of(service.month_alerts(db, reader=user, deal=deal, period=_period(period)))


@router.get("/open")
def open_alerts(user: User, db: DB, period: str | None = None) -> list[AlertOut]:
    chosen = _period(period) if period is not None else None
    return [AlertOut.of(view) for view in service.open_alerts(db, reader=user, period=chosen)]


@router.get("/book")
def book_view(user: User, db: DB, period: str) -> list[AlertOut]:
    """The month's open alerts on deals the book holds, by severity, exposure, then balance."""
    return [
        AlertOut.of(view) for view in service.book_view(db, reader=user, period=_period(period))
    ]


@router.get("/work-list")
def work_list(user: User, db: DB) -> list[AlertOut]:
    """The open alerts assigned to the caller."""
    return [AlertOut.of(view) for view in service.work_list(db, user=user)]


@router.get("/{alert_id}")
def read(alert_id: int, user: User, db: DB) -> AlertOut:
    return AlertOut.of(service.read_alert(db, reader=user, alert_id=alert_id))


@router.post("/{alert_id}/acknowledge")
def acknowledge(alert_id: int, user: User, db: DB) -> AlertOut:
    return AlertOut.of(service.acknowledge(db, user=user, alert_id=alert_id))


@router.post("/{alert_id}/assign")
def assign(alert_id: int, body: AssignIn, user: User, db: DB) -> AlertOut:
    return AlertOut.of(service.assign(db, user=user, alert_id=alert_id, to=body.to, note=body.note))


@router.post("/{alert_id}/close")
def close(alert_id: int, body: CloseIn, user: User, db: DB) -> AlertOut:
    return AlertOut.of(service.close(db, user=user, alert_id=alert_id, note=body.note))
