"""Build a deal's monthly alerts, read them with the exposure behind them, and move them through
their shared lifecycle.

Building compares a deal's month with the month before, both as published: each is read point
in time, as of the day the newer month was published (the filing date of its current tape, or
of its 10-D when that came later), so later amendments of the earlier month do not change what
the newer month is compared with. A loan's findings come from `cmbs.alerts.rules`.

Every alert is stored once per subject, month and rule (`INSERT ... ON CONFLICT DO NOTHING`),
so building a month again changes nothing, and two builds at once store each alert once. The
subject is the deal's loan, or the whole loan when a quant has linked it across deals, so the
same whole loan held through two deals raises one alert that lists both. An alert records the
settings version in force when it was built and the filing that showed the change.

Exposure is read, not stored: the book on the last day of the alert's month (positions are
point in time), summed over every deal the alert covers. A deal the book does not hold still
has its alerts, with zero exposure; they are left out of the book view.
"""

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import NoReturn

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.access.roles import PERMISSIONS, Action, Role, acting_role, refusal_message
from cmbs.access.service import Actor, UnknownUser, authorize, roles_of
from cmbs.alerts import lifecycle
from cmbs.alerts.lifecycle import OPEN_STATES, State, Status
from cmbs.alerts.rules import (
    SEVERITY_ORDER,
    Comments,
    Context,
    Finding,
    LoanMonth,
    Month,
    Severity,
    compare,
)
from cmbs.alerts.tables import Alert, AlertBuild, AlertLoan
from cmbs.audit.service import record, refuse
from cmbs.book.service import Book, book_as_of, holding, whole_loans_of
from cmbs.deals.tables import Deal
from cmbs.distribution.tables import DistributionReport, ReportComment
from cmbs.domain import Period
from cmbs.errors import Refused
from cmbs.filings.tables import Filing
from cmbs.panel.service import as_of, current_tapes, deal_named, loan_order
from cmbs.settings.service import in_force
from cmbs.tape.service import quarantined_loans
from cmbs.tape.tables import LoanPeriod

ALERT = "alert"
RAISE = "raise alert"
ACKNOWLEDGE = "acknowledge alert"
ASSIGN = "assign alert"
CLOSE = "close alert"


class NoTape(Refused):
    """The month to alert on has no loan tape published yet."""


class UnknownAlert(Refused):
    pass


class CannotTakeAlerts(Refused):
    """An alert is assigned only to someone who may close it."""


# ---------------------------------------------------------------------------- views


@dataclass(frozen=True)
class CoveredLoan:
    """One deal's loan an alert covers, with the book's holding in that deal."""

    deal: str
    loan: str
    accession: str
    balance: Decimal | None
    exposure: Decimal
    classes: tuple[str, ...]


@dataclass(frozen=True)
class AlertView:
    id: int
    period: Period
    severity: Severity
    title: str
    message: str
    added: tuple[str, ...]
    settings_version: int
    as_of: dt.date
    raised_at: dt.datetime
    state: State
    state_text: str
    note: str | None
    assigned_to: str | None
    loans: tuple[CoveredLoan, ...]

    @property
    def exposure(self) -> Decimal:
        """Total face the book holds in every deal the alert covers."""
        return sum((loan.exposure for loan in self.loans), Decimal("0.00"))

    @property
    def classes(self) -> tuple[str, ...]:
        """The classes held, in deal order then class order, each deal's once."""
        return tuple(c for loan in self.loans for c in loan.classes)

    @property
    def balance(self) -> Decimal | None:
        """The loan's balance on the month's tapes, summed over the deals; None if unreported."""
        reported = [loan.balance for loan in self.loans if loan.balance is not None]
        return sum(reported, Decimal("0.00")) if reported else None

    @property
    def accessions(self) -> tuple[str, ...]:
        return tuple(loan.accession for loan in self.loans)

    @property
    def deals(self) -> tuple[str, ...]:
        return tuple(loan.deal for loan in self.loans)

    @property
    def is_open(self) -> bool:
        return self.state in OPEN_STATES


@dataclass(frozen=True)
class AlertList:
    """A deal's alerts for one month. `note` explains an empty list."""

    deal: str
    period: Period
    alerts: tuple[AlertView, ...]
    note: str | None


@dataclass(frozen=True)
class AlertSummary:
    """What one build did: how many alerts it raised, and the month's alerts afterwards."""

    deal: str
    period: Period
    as_of: dt.date
    settings_version: int
    raised: int
    alerts: tuple[AlertView, ...]
    note: str | None

    def text(self) -> str:
        noun = "alert" if self.raised == 1 else "alerts"
        line = (
            f"{self.deal} {self.period.label} as of {self.as_of.isoformat()} "
            f"(settings version {self.settings_version}): {self.raised} new {noun}"
        )
        return f"{line}; {self.note}" if self.note else line


def period_end(period: Period) -> dt.date:
    """The last day of the month: the book date an alert's exposure is read on."""
    return period.shift(1).first_day - dt.timedelta(days=1)


def list_note(period: Period, *, built: bool, compared: bool, empty: bool) -> str | None:
    """Why a month's alert list is empty; None when it is not."""
    if not empty:
        return None
    if not built:
        return f"the {period.label} alerts have not been built"
    if not compared:
        return f"no {period.shift(-1).label} loan tape to compare with"
    return f"no changes since {period.shift(-1).label}"


def order_key(view: AlertView) -> tuple[object, ...]:
    """Severity, then exposure (largest first), then the loan's balance (largest first)."""
    return (
        SEVERITY_ORDER[view.severity],
        -view.exposure,
        -(view.balance or Decimal(0)),
        view.deals,
        tuple(loan_order(loan.loan) for loan in view.loans),
        view.id,
    )


def status_of(alert: Alert) -> Status:
    return Status(
        state=State(alert.state),
        acknowledged_by=alert.acknowledged_by,
        acknowledged_on=alert.acknowledged_at.date() if alert.acknowledged_at else None,
        assigned_to=alert.assigned_to,
        closed_by=alert.closed_by,
    )


def _note(alert: Alert) -> str | None:
    if alert.state == State.CLOSED:
        return alert.close_note
    if alert.state == State.ASSIGNED:
        return alert.assign_note
    return None


def _views(db: Session, alerts: Sequence[Alert]) -> list[AlertView]:
    """Views of the alerts, with each deal's holding from the book at the end of their month."""
    if not alerts:
        return []
    rows = db.execute(
        select(AlertLoan, Deal.name)
        .join(Deal, Deal.id == AlertLoan.deal_id)
        .where(AlertLoan.alert_id.in_([alert.id for alert in alerts]))
        .order_by(Deal.name)
    ).all()
    periods = {alert.id: Period.of(alert.period) for alert in alerts}
    books: dict[dt.date, Book | None] = {}
    covered: dict[int, list[CoveredLoan]] = {}
    for loan, deal in rows:
        day = period_end(periods[loan.alert_id])
        if day not in books:
            books[day] = book_as_of(db, day)
        held = holding(books[day], loan.deal_id)
        covered.setdefault(loan.alert_id, []).append(
            CoveredLoan(deal, loan.loan, loan.accession, loan.balance, held.face, held.classes)
        )
    return [
        AlertView(
            id=alert.id,
            period=Period.of(alert.period),
            severity=Severity(alert.severity),
            title=alert.title,
            message=alert.message,
            added=tuple(alert.added),
            settings_version=alert.settings_version,
            as_of=alert.as_of,
            raised_at=alert.raised_at,
            state=State(alert.state),
            state_text=lifecycle.state_text(status_of(alert)),
            note=_note(alert),
            assigned_to=alert.assigned_to,
            loans=tuple(covered.get(alert.id, [])),
        )
        for alert in alerts
    ]


def _deal_month_alerts(db: Session, deal_id: int, period: Period) -> list[AlertView]:
    alerts = db.scalars(
        select(Alert)
        .join(AlertLoan, AlertLoan.alert_id == Alert.id)
        .where(AlertLoan.deal_id == deal_id, Alert.period == period.first_day)
    ).all()
    return sorted(_views(db, alerts), key=order_key)


# ---------------------------------------------------------------------------- reading a month


def _report(db: Session, deal_id: int, period: Period, day: dt.date) -> DistributionReport | None:
    """The deal's latest 10-D for the period filed on or before `day`."""
    return db.scalars(
        select(DistributionReport)
        .join(Filing, Filing.accession == DistributionReport.accession)
        .where(
            DistributionReport.deal_id == deal_id,
            DistributionReport.period == period.first_day,
            Filing.filing_date <= day,
        )
        .order_by(Filing.filing_date.desc(), DistributionReport.accession.desc())
        .limit(1)
    ).one_or_none()


def _filed(db: Session, accession: str) -> dt.date:
    return db.get_one(Filing, accession).filing_date


def _comments(db: Session, report: DistributionReport | None) -> Comments | None:
    """The report's special-servicing comments by loan; a loan's comments from several tables
    are joined in table order."""
    if report is None:
        return None
    by_loan: dict[str, list[str]] = {}
    for item in db.scalars(
        select(ReportComment)
        .where(ReportComment.accession == report.accession)
        .order_by(ReportComment.loan_id, ReportComment.source_table)
    ):
        by_loan.setdefault(item.loan_id, []).append(item.comment)
    return Comments(report.accession, {loan: " ".join(texts) for loan, texts in by_loan.items()})


def loan_month(row: LoanPeriod) -> LoanMonth:
    return LoanMonth(
        loan=row.loan,
        accession=row.accession,
        ending_balance=row.ending_balance,
        paid_through=row.paid_through_date,
        payment_status=row.payment_status,
        transfer_date=row.special_servicer_transfer_date,
        workout_strategy=row.workout_strategy,
        dscr=row.dscr,
    )


def read_month(db: Session, deal_id: int, period: Period, day: dt.date) -> Month | None:
    """The deal's month as published on `day`; None when no tape of it had been filed."""
    tapes = current_tapes(db, day, deal_id=deal_id, period=period)
    if not tapes:
        return None
    tape = tapes[0]
    rows = as_of(db, day, deal_id=deal_id, period=period)
    return Month(
        period=period,
        accession=tape.accession,
        loans={row.loan: loan_month(row) for row in rows},
        quarantined={q.loan: q.reason for q in quarantined_loans(db, tape.accession)},
        comments=_comments(db, _report(db, deal_id, period, day)),
    )


def publication_day(db: Session, deal: Deal, period: Period, today: dt.date) -> dt.date:
    """The day the month was published, as far as is known `today`: its current tape's filing
    date, or its 10-D's when that was filed later."""
    tapes = current_tapes(db, today, deal_id=deal.id, period=period)
    if not tapes:
        raise NoTape(f"no {period.label} loan tape of {deal.name} had been filed by {today}")
    day = tapes[0].filing_date
    report = _report(db, deal.id, period, today)
    if report is not None:
        day = max(day, _filed(db, report.accession))
    return day


# ---------------------------------------------------------------------------- building


def subject_of(deal_id: int, loan: str, whole_loan_id: int | None) -> str:
    """An alert's subject: the linked whole loan, else the deal's loan."""
    return f"whole loan {whole_loan_id}" if whole_loan_id is not None else f"{deal_id}/{loan}"


def build_alerts(
    db: Session,
    *,
    deal_id: int,
    period: Period,
    actor: Actor | None = None,
    run_id: int | None = None,
    as_of: dt.date | None = None,
) -> AlertSummary:
    """Compare the deal's month with the month before and store every new alert once.

    `as_of` defaults to the day the month was published (`publication_day`). With an actor
    (who must be one allowed to run refreshes), every alert raised is audited, under the run
    when one is given, and a refusal is recorded before it is raised.
    """
    if run_id is not None and actor is None:
        raise ValueError("a run's writes are audited, so a run id needs an actor")
    if actor is not None and Action.RUN_REFRESH not in PERMISSIONS[actor.role]:
        raise ValueError(f"an actor acting as {actor.role.value} cannot build alerts")
    deal = db.get(Deal, deal_id)
    if deal is None:
        raise ValueError(f"no deal with id {deal_id}")
    try:
        day = as_of or publication_day(db, deal, period, clock.today())
        after = read_month(db, deal_id, period, day)
        if after is None:
            raise NoTape(f"no {period.label} loan tape of {deal.name} had been filed by {day}")
        settings = in_force(db)
    except Refused as refusal:
        if actor is None:
            raise
        refuse(
            db,
            refusal,
            user=actor.user,
            role=actor.role.value,
            action=RAISE,
            subject="deal",
            subject_key=deal.name,
        )
    before = read_month(db, deal_id, period.shift(-1), day)
    findings = (
        compare(before, after, Context(period, settings.values.dscr_alert_threshold))
        if before is not None
        else []
    )
    linked = whole_loans_of(db, deal_id)
    raised = 0
    for finding in findings:
        alert_id, new = _store(
            db,
            finding,
            deal=deal,
            period=period,
            whole_loan_id=linked.get(finding.loan),
            balance=_balance(after, finding.loan),
            settings_version=settings.version,
            day=day,
            run_id=run_id,
        )
        if new:
            raised += 1
            if actor is not None:
                _audit_raised(db, actor, run_id, alert_id, finding, deal.name, period)
    db.execute(
        insert(AlertBuild)
        .values(
            deal_id=deal_id,
            period=period.first_day,
            as_of=day,
            accession=after.accession,
            compared_with=before.accession if before is not None else None,
            report_accession=after.comments.accession if after.comments is not None else None,
            settings_version=settings.version,
            first_built_at=clock.now(),
        )
        .on_conflict_do_update(
            index_elements=[AlertBuild.deal_id, AlertBuild.period],
            set_={
                "as_of": day,
                "accession": after.accession,
                "compared_with": before.accession if before is not None else None,
                "report_accession": (
                    after.comments.accession if after.comments is not None else None
                ),
                "settings_version": settings.version,
            },
        )
    )
    db.flush()
    alerts = _deal_month_alerts(db, deal_id, period)
    return AlertSummary(
        deal=deal.name,
        period=period,
        as_of=day,
        settings_version=settings.version,
        raised=raised,
        alerts=tuple(alerts),
        note=list_note(period, built=True, compared=before is not None, empty=not alerts),
    )


def _balance(month: Month, loan: str) -> Decimal | None:
    row = month.loans.get(loan)
    return row.ending_balance if row is not None else None


def _store(
    db: Session,
    finding: Finding,
    *,
    deal: Deal,
    period: Period,
    whole_loan_id: int | None,
    balance: Decimal | None,
    settings_version: int,
    day: dt.date,
    run_id: int | None,
) -> tuple[int, bool]:
    """Store the finding's alert unless its subject already has one for the month and rule, and
    add the deal's loan to it. Returns the alert's id and whether it is new."""
    subject = subject_of(deal.id, finding.loan, whole_loan_id)
    new_id = db.scalar(
        insert(Alert)
        .values(
            subject=subject,
            period=period.first_day,
            rule=finding.rule.value,
            severity=finding.severity.value,
            title=finding.title,
            message=finding.message,
            added=list(finding.added),
            whole_loan_id=whole_loan_id,
            settings_version=settings_version,
            as_of=day,
            raised_at=clock.now(),
            run_id=run_id,
            state=State.NEW.value,
        )
        .on_conflict_do_nothing(constraint="uq_alert_subject_period_rule")
        .returning(Alert.id)
    )
    alert_id = new_id
    if alert_id is None:
        alert_id = db.scalars(
            select(Alert.id).where(
                Alert.subject == subject,
                Alert.period == period.first_day,
                Alert.rule == finding.rule.value,
            )
        ).one()
    db.execute(
        insert(AlertLoan)
        .values(
            alert_id=alert_id,
            deal_id=deal.id,
            loan=finding.loan,
            accession=finding.accession,
            balance=balance,
        )
        .on_conflict_do_nothing(index_elements=[AlertLoan.alert_id, AlertLoan.deal_id])
    )
    return alert_id, new_id is not None


def _audit_raised(
    db: Session,
    actor: Actor,
    run_id: int | None,
    alert_id: int,
    finding: Finding,
    deal: str,
    period: Period,
) -> None:
    record(
        db,
        user=actor.user,
        role=actor.role.value,
        run_id=run_id,
        action=RAISE,
        subject=ALERT,
        subject_key=str(alert_id),
        after=finding.message,
        detail={
            "deal": deal,
            "loan": finding.loan,
            "period": period.label,
            "rule": finding.rule.value,
            "severity": finding.severity.value,
            "accession": finding.accession,
        },
    )


def build_for(
    db: Session, *, user: str, deal: str, period: Period, as_of: dt.date | None = None
) -> AlertSummary:
    """Build a deal's month for a user who may run refreshes."""
    actor = authorize(db, user, Action.RUN_REFRESH)
    return build_alerts(
        db, deal_id=deal_named(db, deal).id, period=period, actor=actor, as_of=as_of
    )


# ---------------------------------------------------------------------------- reading


def month_alerts(db: Session, *, reader: str, deal: str, period: Period) -> AlertList:
    """A deal's alerts for a month, most severe first; an empty list says why it is empty."""
    authorize(db, reader, Action.READ_PANEL)
    deal_row = deal_named(db, deal)
    alerts = _deal_month_alerts(db, deal_row.id, period)
    build = db.get(AlertBuild, (deal_row.id, period.first_day))
    return AlertList(
        deal=deal_row.name,
        period=period,
        alerts=tuple(alerts),
        note=list_note(
            period,
            built=build is not None,
            compared=build is not None and build.compared_with is not None,
            empty=not alerts,
        ),
    )


def _alert(db: Session, alert_id: int, *, lock: bool = False) -> Alert:
    alert = db.get(Alert, alert_id, with_for_update=lock, populate_existing=lock)
    if alert is None:
        raise UnknownAlert(f"no alert {alert_id}")
    return alert


def read_alert(db: Session, *, reader: str, alert_id: int) -> AlertView:
    authorize(db, reader, Action.READ_PANEL)
    return _views(db, [_alert(db, alert_id)])[0]


def _listed(db: Session, alerts: Iterable[Alert]) -> list[AlertView]:
    return sorted(_views(db, list(alerts)), key=order_key)


def open_alerts(db: Session, *, reader: str, period: Period | None = None) -> list[AlertView]:
    """Every alert not yet closed, optionally for one month."""
    authorize(db, reader, Action.READ_PANEL)
    query = select(Alert).where(Alert.state.in_([state.value for state in OPEN_STATES]))
    if period is not None:
        query = query.where(Alert.period == period.first_day)
    return _listed(db, db.scalars(query))


def work_list(db: Session, *, user: str) -> list[AlertView]:
    """The open alerts assigned to the user."""
    authorize(db, user, Action.READ_PANEL)
    query = select(Alert).where(Alert.assigned_to == user, Alert.state == State.ASSIGNED.value)
    return _listed(db, db.scalars(query))


def book_view(db: Session, *, reader: str, period: Period) -> list[AlertView]:
    """The month's open alerts on deals the book holds, by severity, exposure, then balance."""
    return [view for view in open_alerts(db, reader=reader, period=period) if view.exposure > 0]


# ---------------------------------------------------------------------------- lifecycle


def _refuse(db: Session, error: Refused, actor: Actor, action: str, alert_id: int) -> NoReturn:
    refuse(
        db,
        error,
        user=actor.user,
        role=actor.role.value,
        action=action,
        subject=ALERT,
        subject_key=str(alert_id),
    )


def _locked(db: Session, actor: Actor, action: str, alert_id: int) -> Alert:
    try:
        return _alert(db, alert_id, lock=True)
    except UnknownAlert as unknown:
        _refuse(db, unknown, actor, action, alert_id)


def _record_change(
    db: Session, actor: Actor, action: str, alert: Alert, before: str, note: str | None
) -> None:
    db.flush()
    record(
        db,
        user=actor.user,
        role=actor.role.value,
        action=action,
        subject=ALERT,
        subject_key=str(alert.id),
        field="state",
        before=before,
        after=lifecycle.state_text(status_of(alert)),
        reason=note,
    )


def acknowledge(db: Session, *, user: str, alert_id: int) -> AlertView:
    """A new alert is acknowledged: someone has seen it."""
    actor = authorize(db, user, Action.ACKNOWLEDGE_ALERT)
    alert = _locked(db, actor, ACKNOWLEDGE, alert_id)
    status = status_of(alert)
    problem = lifecycle.acknowledge_problem(status)
    if problem is not None:
        _refuse(db, problem, actor, ACKNOWLEDGE, alert_id)
    alert.state = State.ACKNOWLEDGED.value
    alert.acknowledged_by = actor.user
    alert.acknowledged_at = clock.now()
    _record_change(db, actor, ACKNOWLEDGE, alert, lifecycle.state_text(status), None)
    return _views(db, [alert])[0]


def assignee_problem(to: str, roles: frozenset[Role]) -> CannotTakeAlerts | None:
    """An alert goes only to someone who may close it. Pure."""
    if acting_role(roles, Action.CLOSE_ALERT) is not None:
        return None
    return CannotTakeAlerts(
        f"{to} cannot be assigned alerts: {refusal_message(roles, Action.CLOSE_ALERT)}"
    )


def assign(db: Session, *, user: str, alert_id: int, to: str, note: str | None = None) -> AlertView:
    """An open alert is given to someone who may close it, optionally with a note."""
    actor = authorize(db, user, Action.ASSIGN_ALERT)
    try:
        problem: Refused | None = assignee_problem(to, roles_of(db, to))
    except UnknownUser as unknown:
        problem = unknown
    if problem is not None:
        _refuse(db, problem, actor, ASSIGN, alert_id)
    alert = _locked(db, actor, ASSIGN, alert_id)
    status = status_of(alert)
    problem = lifecycle.assign_problem(status, to)
    if problem is not None:
        _refuse(db, problem, actor, ASSIGN, alert_id)
    alert.state = State.ASSIGNED.value
    alert.assigned_to = to
    alert.assigned_by = actor.user
    alert.assigned_at = clock.now()
    alert.assign_note = note.strip() if note and note.strip() else None
    _record_change(db, actor, ASSIGN, alert, lifecycle.state_text(status), alert.assign_note)
    return _views(db, [alert])[0]


def close(db: Session, *, user: str, alert_id: int, note: str) -> AlertView:
    """An open alert is closed with a note saying why. It never reopens."""
    actor = authorize(db, user, Action.CLOSE_ALERT)
    alert = _locked(db, actor, CLOSE, alert_id)
    status = status_of(alert)
    problem = lifecycle.close_problem(status, note)
    if problem is not None:
        _refuse(db, problem, actor, CLOSE, alert_id)
    alert.state = State.CLOSED.value
    alert.closed_by = actor.user
    alert.closed_at = clock.now()
    alert.close_note = note.strip()
    _record_change(db, actor, CLOSE, alert, lifecycle.state_text(status), alert.close_note)
    return _views(db, [alert])[0]
