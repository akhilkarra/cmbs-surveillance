"""The surveillance dashboard's two pages: the book's alerts for a month, and one loan.

Nothing here owns data or decides anything: every value, label and ordering comes from the
feature that owns it, and this module puts them together for one screen. The web app formats
what it is sent and computes nothing.

- **The book page** is `cmbs.alerts.service.book_view`: the month's open alerts on deals the
  book holds, by severity, then exposure, then the loan's balance. Closed and superseded alerts
  are not on it.
- **The loan page** is one loan as the panel shows it on a day (today by default), point in
  time: the current tape's values (`cmbs.panel`) and the run that loaded them (the audit entry
  `cmbs.runs.service.trace` reads), the most recent financials and their age
  (`cmbs.analysis.staleness`), the servicer comment's extracted values with the sentence behind
  each (`cmbs.extraction.service.comment_view`), the check flags raised on the loan for the
  month by that day, the month's alerts on the loan, and the loan's history for the chart:
  every month as published by that day (ending balance, DSCR, payment status and the filing
  each came from), with the months its payment status changed.

Every value says what verified it:
- a tape value passed the loan-row contract (a row that fails it is quarantined, never in the
  panel); a balance also shows its roll-forward check;
- an extracted value shows how it was accepted (auto-accepted, human-reviewed, or whatever
  status the panel gives it) and "evidence checked" when the sentence behind it is one of the
  stored comment's sentences; a value awaiting review has no value to show.
"""

import datetime as dt
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from cmbs import clock
from cmbs.access.roles import Action
from cmbs.access.service import authorize
from cmbs.alerts import service as alerts
from cmbs.alerts.codes import PAYMENT_STATUS, WORKOUT_STRATEGY, name_of
from cmbs.alerts.lifecycle import State
from cmbs.alerts.rules import SEVERITY_ORDER, Severity
from cmbs.alerts.service import AlertView
from cmbs.alerts.tables import Alert, AlertBuild, AlertLoan
from cmbs.analysis.staleness import loan_staleness
from cmbs.audit.tables import AuditEntry
from cmbs.checks.tables import CheckFlag
from cmbs.crosscheck import rollforward as rf
from cmbs.crosscheck import service as crosscheck
from cmbs.distribution.service import canonical_loan
from cmbs.domain import Period
from cmbs.edgar.archives import filing_base_url
from cmbs.errors import Refused
from cmbs.extraction.service import (
    NOT_DETERMINABLE,
    OVERRIDDEN,
    CommentView,
    FieldView,
    comment_view,
)
from cmbs.extraction.tables import AUTO_ACCEPTED, ExtractedField, Extraction, ReviewItem
from cmbs.filings.tables import Filing
from cmbs.invariants.partition import PoolLoan, bucket_of, code_of
from cmbs.panel import service as panel
from cmbs.review.tables import ReviewDecline
from cmbs.runs import service as runs
from cmbs.tape.tables import LoanPeriod

# What a check found: "passed", "failed", or "not checked" (the check could not run yet); or
# "decided" when a person settled the value (a review decision or an override), not a check.
PASSED = "passed"
FAILED = "failed"
NOT_CHECKED = "not checked"
DECIDED = "decided"
NOT_IN_AUDIT_TRAIL = "load not in the audit trail"

# How the web app formats a value; the value itself is always sent as text, so no digit is lost.
MONEY = "money"
DATE = "date"
RATIO = "ratio"
TEXT = "text"

_MONTH = re.compile(r"(\d{4})-(\d{2})")


class BadMonth(Refused):
    pass


@dataclass(frozen=True)
class Check:
    """One thing that verified a value, as the page names it, and what it found."""

    label: str
    outcome: str


CONTRACT_PASSED = Check("contract passed", PASSED)
# Grill log Q81: the roll-forward ties the scheduled balance.
ROLLFORWARD_NOT_CHECKED = Check("scheduled balance roll-forward not checked", NOT_CHECKED)
EVIDENCE_CHECKED = Check("evidence checked", PASSED)


# ---------------------------------------------------------------------------- months


@dataclass(frozen=True)
class MonthRef:
    """A month as the dashboard links to it ('2026-09') and shows it ('September 2026')."""

    key: str
    label: str


def month_ref(period: Period) -> MonthRef:
    return MonthRef(key=f"{period.year:04d}-{period.month:02d}", label=period.label)


def parse_month(text: str) -> Period:
    """'2026-09' -> Period(2026, 9)."""
    match = _MONTH.fullmatch(text.strip())
    if match is None or not 1 <= int(match.group(2)) <= 12:
        raise BadMonth(f"not a month like 2026-09: {text!r}")
    return Period(int(match.group(1)), int(match.group(2)))


def default_month(latest_built: Period | None, today: dt.date) -> Period:
    """The book page's month when none is asked for: the latest month with alerts built, or
    else last month."""
    return latest_built if latest_built is not None else Period.of(today).shift(-1)


# ---------------------------------------------------------------------------- the book


@dataclass(frozen=True)
class LoanRef:
    """A deal's loan an alert is on, and how the book's loan column shows it: the loan ('7'),
    or for an alert on the deal no loan (nothing to open) and how many it lists ('19 loans')."""

    deal: str
    loan: str | None
    label: str


@dataclass(frozen=True)
class BookRow:
    """One open alert, in the order the alerts service gives."""

    alert_id: int
    severity: str
    title: str
    message: str
    loans: tuple[LoanRef, ...]
    exposure: Decimal
    balance: Decimal | None
    classes: tuple[str, ...]
    state: str
    state_text: str
    note: str | None


@dataclass(frozen=True)
class BookPage:
    month: MonthRef
    previous: MonthRef
    next: MonthRef
    as_of: dt.date
    rows: tuple[BookRow, ...]
    note: str | None


def book_row(view: AlertView) -> BookRow:
    return BookRow(
        alert_id=view.id,
        severity=view.severity.value,
        title=view.title,
        message=view.message,
        loans=tuple(LoanRef(loan.deal, loan.loan, loan.label) for loan in view.loans),
        exposure=view.exposure,
        balance=view.balance,
        classes=view.classes,
        state=view.state.value,
        state_text=view.state_text,
        note=view.note,
    )


def book_note(period: Period, *, built: bool, empty: bool) -> str | None:
    """Why the book view is empty: the month's alerts were not built yet, or none is open on
    the book (as `cmbs.alerts.service.list_note` tells a deal's month apart)."""
    if not empty:
        return None
    if not built:
        return f"the {period.label} alerts have not been built"
    return f"no open alerts on the book for {period.label}"


def _latest_built(db: Session) -> Period | None:
    latest = db.scalar(select(func.max(AlertBuild.period)))
    return Period.of(latest) if latest is not None else None


def _built(db: Session, period: Period) -> bool:
    found = db.scalar(select(AlertBuild.id).where(AlertBuild.period == period.first_day).limit(1))
    return found is not None


def book_page(db: Session, *, reader: str, period: Period | None = None) -> BookPage:
    """The month's open alerts on the book, most severe first, then by exposure and balance."""
    authorize(db, reader, Action.READ_PANEL)
    month = period or default_month(_latest_built(db), clock.today())
    rows = tuple(book_row(view) for view in alerts.book_view(db, reader=reader, period=month))
    return BookPage(
        month=month_ref(month),
        previous=month_ref(month.shift(-1)),
        next=month_ref(month.shift(1)),
        as_of=clock.today(),
        rows=rows,
        note=book_note(month, built=_built(db, month), empty=not rows),
    )


# ---------------------------------------------------------------------------- a loan


@dataclass(frozen=True)
class FilingSource:
    """The filing a value came from, and where to read it on EDGAR."""

    form: str
    accession: str
    filing_date: dt.date
    url: str


@dataclass(frozen=True)
class LoadedBy:
    """Who put a value in the panel: the run, or the user outside a run."""

    run: int | None
    by: str | None
    at: dt.datetime | None
    text: str


@dataclass(frozen=True)
class TapeValue:
    field: str
    label: str
    kind: str
    value: str | None
    checks: tuple[Check, ...]


@dataclass(frozen=True)
class TapeSection:
    source: FilingSource
    loaded: LoadedBy
    values: tuple[TapeValue, ...]


@dataclass(frozen=True)
class Financials:
    """The most recent financials the tape reports, and how old they were."""

    start: dt.date | None
    end: dt.date | None
    days_at_period_end: int | None
    days_at_filing: int | None
    status: str


@dataclass(frozen=True)
class CommentValue:
    field: str
    label: str
    status: str
    value: str | None
    sentence: int | None
    evidence: str | None
    checks: tuple[Check, ...]
    review_reason: str | None
    model_value: str | None
    decided_by: str | None
    decided_at: dt.datetime | None
    overridden_by: str | None
    overridden_at: dt.datetime | None
    override_reason: str | None
    declined_reason: str | None
    # Who settled the value by hand, when and why, as the page says it; None when nobody did.
    decision: str | None


@dataclass(frozen=True)
class CommentSection:
    source: FilingSource
    loaded: LoadedBy
    sentences: tuple[str, ...]
    model: str
    prompt_version: int
    settings_version: int
    values: tuple[CommentValue, ...]


@dataclass(frozen=True)
class LoanFlag:
    id: int
    check: str
    message: str


@dataclass(frozen=True)
class LoanAlert:
    """An alert on the loan for the month, in its state on the page's day."""

    alert_id: int
    severity: str
    message: str
    state_text: str


@dataclass(frozen=True)
class PaymentStatus:
    """A payment status as the tape reports it ('A') and its name ('in grace period')."""

    code: str
    name: str


@dataclass(frozen=True)
class HistoryMonth:
    """One month of a loan's history as the deal's current tape for it reported the loan.
    `day` is the month's first day, where the chart places it. A value the tape does not report
    is None, never zero; a month whose tape leaves the loan out (`on_tape` false) reports
    nothing, so the chart has a gap there rather than a line drawn across it."""

    month: MonthRef
    day: dt.date
    on_tape: bool
    ending_balance: str | None
    dscr: str | None
    payment_status: PaymentStatus | None
    source: FilingSource


@dataclass(frozen=True)
class StatusChange:
    """A month whose payment status differs from the last one reported before it, and the
    delinquency bucket it moved into ('grace or late', `cmbs.invariants.partition`)."""

    month: MonthRef
    day: dt.date
    code: str
    bucket: str


@dataclass(frozen=True)
class LoanHistory:
    """The loan's months, oldest first, as published by the page's day, and its payment status
    changes."""

    months: tuple[HistoryMonth, ...]
    status_changes: tuple[StatusChange, ...]


def payment_status(code: str | None) -> PaymentStatus | None:
    if code is None:
        return None
    return PaymentStatus(code=code.strip(), name=name_of(PAYMENT_STATUS, code))


def history_month(period: Period, source: FilingSource, row: LoanPeriod | None) -> HistoryMonth:
    """A month of the loan's history from the tape `source`, which reports the loan as `row`
    (None: the tape leaves the loan out)."""
    return HistoryMonth(
        month=month_ref(period),
        day=period.first_day,
        on_tape=row is not None,
        ending_balance=None if row is None else _money(row.ending_balance),
        dscr=None if row is None or row.dscr is None else str(row.dscr),
        payment_status=None if row is None else payment_status(row.payment_status),
        source=source,
    )


def history_months(
    tapes: Sequence[tuple[Period, FilingSource]], rows: Mapping[str, LoanPeriod]
) -> tuple[HistoryMonth, ...]:
    """The loan's months from the deal's current tapes, oldest first, and the loan's row on
    each tape that reports it (by accession): every month from the first tape that reports the
    loan to the last, so a month in between whose tape leaves the loan out is a gap."""
    reported = [index for index, (_, source) in enumerate(tapes) if source.accession in rows]
    if not reported:
        return ()
    return tuple(
        history_month(period, source, rows.get(source.accession))
        for period, source in tapes[reported[0] : reported[-1] + 1]
    )


def status_changes(loan: str, months: Sequence[HistoryMonth]) -> tuple[StatusChange, ...]:
    """The months whose payment status differs from the last one reported before them ('01'
    and '1' are the same code), each with its delinquency bucket. The first status reported is
    not a change, and a month that reports no status changes nothing: not reported is not
    current."""
    changes: list[StatusChange] = []
    last: str | None = None
    for month in months:
        if month.payment_status is None:
            continue
        code = code_of(month.payment_status.code)
        if last is not None and code != last:
            bucket = bucket_of(PoolLoan(loan, month.payment_status.code, None))
            changes.append(StatusChange(month.month, month.day, month.payment_status.code, bucket))
        last = code
    return tuple(changes)


@dataclass(frozen=True)
class LoanPage:
    deal: str
    loan: str
    month: MonthRef
    as_of: dt.date
    property_name: str | None
    property_type: str | None
    tape: TapeSection
    financials: Financials
    comment: CommentSection | None
    comment_note: str | None
    flags: tuple[LoanFlag, ...]
    alerts: tuple[LoanAlert, ...]
    history: LoanHistory


def edgar_url(cik: int, accession: str, primary_document: str | None) -> str:
    """The filing's primary document on EDGAR, or its index page when that is not known."""
    return filing_base_url(cik, accession) + (primary_document or f"{accession}-index.htm")


def loaded_text(run: int | None, by: str | None, at: dt.datetime | None, verb: str) -> str:
    """'run 43'; 'loaded by quinn outside a run'; or that the audit trail does not show it."""
    if at is None:
        return NOT_IN_AUDIT_TRAIL
    if run is None:
        return f"{verb} by {by} outside a run"
    return f"run {run}"


def loaded(
    run: int | None, by: str | None, at: dt.datetime | None, verb: str = "loaded"
) -> LoadedBy:
    return LoadedBy(run=run, by=by, at=at, text=loaded_text(run, by, at, verb))


def _money(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"


def _date(value: dt.date | None) -> str | None:
    return None if value is None else value.isoformat()


def _code(codes: dict, code: str | None) -> str | None:
    return None if code is None else name_of(codes, code)


def tape_values(row: LoanPeriod, rollforward: Check) -> tuple[TapeValue, ...]:
    """The tape's values for the loan page, each with what verified it. A value the tape does
    not report is None ("not reported"), never zero."""
    balance_checks = (CONTRACT_PASSED, rollforward)
    row_checks = (CONTRACT_PASSED,)
    return (
        TapeValue(
            "ending_balance", "ending balance", MONEY, _money(row.ending_balance), balance_checks
        ),
        TapeValue(
            "beginning_balance",
            "beginning scheduled balance",
            MONEY,
            _money(row.beginning_balance),
            balance_checks,
        ),
        TapeValue(
            "payment_status",
            "payment status",
            TEXT,
            _code(PAYMENT_STATUS, row.payment_status),
            row_checks,
        ),
        TapeValue(
            "paid_through_date", "paid-through date", DATE, _date(row.paid_through_date), row_checks
        ),
        TapeValue(
            "special_servicer_transfer_date",
            "special-servicer transfer date",
            DATE,
            _date(row.special_servicer_transfer_date),
            row_checks,
        ),
        TapeValue(
            "workout_strategy",
            "workout strategy",
            TEXT,
            _code(WORKOUT_STRATEGY, row.workout_strategy),
            row_checks,
        ),
        TapeValue("dscr", "DSCR", RATIO, None if row.dscr is None else str(row.dscr), row_checks),
    )


ROLLFORWARD = "scheduled balance roll-forward"


def rollforward_verdict(result: rf.LoanRollForward | None) -> Check:
    """The roll-forward's verdict as the page marks it (grill log Q81): passed; broken (the
    month's roll or its tie to last month's ending scheduled balance); or not checked, with the
    reason (a scheduled balance not reported, a realized-loss month), never as a pass."""
    if result is None:
        return ROLLFORWARD_NOT_CHECKED
    if result.status is rf.Status.PASSED:
        return Check(f"{ROLLFORWARD} passed", PASSED)
    if result.status is rf.Status.BREAK:
        return Check(f"{ROLLFORWARD} broken", FAILED)
    return Check(f"{ROLLFORWARD} not checked: {result.roll.reason}", NOT_CHECKED)


def rollforward_check(
    db: Session, *, deal_id: int, period: Period, loan: str, as_of: dt.date | None = None
) -> Check:
    """The balance roll-forward's verdict on the loan's month, on the tapes current on
    `as_of` (`cmbs.crosscheck.service.rollforward`)."""
    results = crosscheck.rollforward(db, deal_id=deal_id, period=period, as_of=as_of)
    return rollforward_verdict(next((r for r in results if r.loan == loan), None))


def financials(row: LoanPeriod) -> Financials:
    """The most recent financials and their age. Financials that end after the period end are
    a data problem, said as such, never a negative age."""
    measured = loan_staleness(panel.tape_loan(row))
    if measured.days_vs_period_end is not None and measured.days_vs_period_end < 0:
        return Financials(
            start=row.financials_start,
            end=row.financials_end,
            days_at_period_end=None,
            days_at_filing=None,
            status=f"financials end {row.financials_end}, after the period end",
        )
    return Financials(
        start=row.financials_start,
        end=row.financials_end,
        days_at_period_end=measured.days_vs_period_end,
        days_at_filing=measured.days_vs_filing,
        status=measured.status,
    )


def field_checks(
    value: str | None, status: str, sentence: int | None, sentences: Sequence[str]
) -> tuple[Check, ...]:
    """What verified an extracted value: how it was accepted, and "evidence checked" when the
    sentence behind it is one of the comment's. Auto-accepted passed the decision table; any
    other status with a value (human-reviewed, overridden) is a person's decision. A field with
    no value (awaiting review, not stated, not determinable) has nothing verified."""
    if value is None:
        return ()
    accepted = Check(status, PASSED if status == AUTO_ACCEPTED else DECIDED)
    if sentence is not None and 1 <= sentence <= len(sentences):
        return (accepted, EVIDENCE_CHECKED)
    return (accepted,)


def _day(moment: dt.datetime | None) -> str:
    return "an unknown day" if moment is None else moment.astimezone(dt.UTC).date().isoformat()


def decision_text(view: FieldView, declined_reason: str | None) -> str | None:
    """Who settled a field by hand, when and why:
    'overridden by alex on 2026-10-02: <reason>; the model read "modification"',
    'declined as not determinable by alex on 2026-10-02: <reason>',
    'human-reviewed by alex on 2026-10-02'. None when no person settled it."""
    if view.overridden_by is not None:
        model = (
            "the model gave no value"
            if view.model_value is None
            else f'the model read "{view.model_value}"'
        )
        return (
            f"{OVERRIDDEN} by {view.overridden_by} on {_day(view.overridden_at)}: "
            f"{view.override_reason}; {model}"
        )
    if view.decided_by is None:
        return None
    on = _day(view.decided_at)
    if view.status == NOT_DETERMINABLE:
        why = f": {declined_reason}" if declined_reason else ""
        return f"declined as {NOT_DETERMINABLE} by {view.decided_by} on {on}{why}"
    return f"{view.status} by {view.decided_by} on {on}"


def comment_value(
    view: FieldView, sentences: Sequence[str], declined_reason: str | None = None
) -> CommentValue:
    reason = declined_reason if view.status == NOT_DETERMINABLE else None
    return CommentValue(
        field=view.field,
        label=view.label,
        status=view.status,
        value=view.display,
        sentence=view.sentence,
        evidence=view.evidence,
        checks=field_checks(view.display, view.status, view.sentence, sentences),
        review_reason=view.review_reason,
        model_value=view.model_value,
        decided_by=view.decided_by,
        decided_at=view.decided_at,
        overridden_by=view.overridden_by,
        overridden_at=view.overridden_at,
        override_reason=view.override_reason,
        declined_reason=reason,
        decision=decision_text(view, reason),
    )


def comment_note(period: Period) -> str:
    return f"no servicer comment extracted for {period.label}"


def filing_source(filing: Filing) -> FilingSource:
    return FilingSource(
        form=filing.form,
        accession=filing.accession,
        filing_date=filing.filing_date,
        url=edgar_url(filing.cik, filing.accession, filing.primary_document),
    )


def _source(db: Session, accession: str) -> FilingSource:
    return filing_source(db.get_one(Filing, accession))


def _comment(db: Session, view: CommentView) -> CommentSection:
    extraction = db.scalars(
        select(Extraction).where(
            Extraction.accession == view.accession,
            Extraction.loan_id == canonical_loan(view.loan),
            Extraction.model_call == view.model_call,
        )
    ).first()
    run = extraction.run_id if extraction is not None else None
    by = extraction.extracted_by if extraction is not None else None
    at = extraction.extracted_at if extraction is not None else None
    declines = _decline_reasons(db, extraction.id) if extraction is not None else {}
    sentences = tuple(view.sentences)
    return CommentSection(
        source=_source(db, view.accession),
        loaded=loaded(run, by, at, "extracted"),
        sentences=sentences,
        model=view.model,
        prompt_version=view.prompt_version,
        settings_version=view.settings_version,
        values=tuple(
            comment_value(field, sentences, declines.get(name))
            for name, field in view.fields.items()
        ),
    )


def _decline_reasons(db: Session, extraction_id: int) -> dict[str, str]:
    """Why each declined field of the extraction could not be settled, by field name."""
    rows = db.execute(
        select(ReviewItem.field, ReviewDecline.reason)
        .join(ReviewDecline, ReviewDecline.item_id == ReviewItem.id)
        .join(ExtractedField, ExtractedField.id == ReviewItem.field_id)
        .where(ExtractedField.extraction_id == extraction_id)
    ).all()
    return {field: reason for field, reason in rows}


def _end_of(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), dt.UTC)


def _flags(
    db: Session, deal_id: int, loan: str, period: Period, day: dt.date
) -> tuple[LoanFlag, ...]:
    """Check flags raised on the loan for the month by the end of `day`, oldest first."""
    found = db.scalars(
        select(CheckFlag)
        .where(
            CheckFlag.deal_id == deal_id,
            CheckFlag.loan == loan,
            CheckFlag.period == period.first_day,
            CheckFlag.raised_at < _end_of(day),
        )
        .order_by(CheckFlag.id)
    )
    return tuple(LoanFlag(flag.id, flag.check, flag.message) for flag in found)


def state_on(changes: Sequence[str]) -> str:
    """An alert's state on a day: the last state change made by then, or "new"."""
    return changes[-1] if changes else State.NEW.value


def _state_changes(db: Session, alert_id: int, end: dt.datetime) -> list[str]:
    """The alert's state after each change made before `end`, oldest first."""
    return [
        after
        for after in db.scalars(
            select(AuditEntry.after)
            .where(
                AuditEntry.subject == alerts.ALERT,
                AuditEntry.subject_key == str(alert_id),
                AuditEntry.field == "state",
                AuditEntry.outcome == "done",
                AuditEntry.at < end,
            )
            .order_by(AuditEntry.at, AuditEntry.id)
        )
        if after is not None
    ]


def _loan_alerts(
    db: Session, deal_id: int, loan: str, period: Period, day: dt.date
) -> tuple[LoanAlert, ...]:
    """The loan's alerts for the month as they stood at the end of `day`: raised by then and
    not yet superseded for this deal, each in the state the audit trail shows it was in. An
    alert on the deal that lists the loan (newly reported, say) is one of them."""
    end = _end_of(day)
    superseding = aliased(AlertBuild)
    found = db.scalars(
        select(Alert)
        .join(AlertLoan, AlertLoan.alert_id == Alert.id)
        .outerjoin(superseding, superseding.id == AlertLoan.superseded_by)
        .where(
            AlertLoan.deal_id == deal_id,
            or_(AlertLoan.loan == loan, AlertLoan.loans.contains([loan])),
            Alert.period == period.first_day,
            Alert.raised_at < end,
            or_(AlertLoan.superseded_by.is_(None), superseding.built_at >= end),
        )
    ).all()
    ordered = sorted(found, key=lambda alert: (SEVERITY_ORDER[Severity(alert.severity)], alert.id))
    return tuple(
        LoanAlert(
            alert.id, alert.severity, alert.message, state_on(_state_changes(db, alert.id, end))
        )
        for alert in ordered
    )


def loan_history(db: Session, *, deal_id: int, loan: str, day: dt.date) -> LoanHistory:
    """The loan's history as published by the end of `day`: each month as the deal's current
    tape for it on that day reports the loan (`cmbs.panel.service.as_of`), so a month filed
    later, or an amendment filed later, is not on it."""
    tapes = panel.current_tapes(db, day, deal_id=deal_id)
    filings = {
        filing.accession: filing_source(filing)
        for filing in db.scalars(
            select(Filing).where(Filing.accession.in_([tape.accession for tape in tapes]))
        )
    }
    rows = {row.accession: row for row in panel.as_of(db, day, deal_id=deal_id, loan=loan)}
    months = history_months(
        [(Period.of(tape.period), filings[tape.accession]) for tape in tapes], rows
    )
    return LoanHistory(months=months, status_changes=status_changes(loan, months))


def loan_page(
    db: Session,
    *,
    reader: str,
    deal: str,
    loan: str,
    period: Period | None = None,
    as_of: dt.date | None = None,
) -> LoanPage:
    """One loan as the panel shows it on `as_of` (today), for a month (the deal's latest).
    The loan is read as stored ('02' is loan 2), as the comment's extraction is."""
    authorize(db, reader, Action.READ_PANEL)
    loan = canonical_loan(loan)
    deal_row = panel.deal_named(db, deal)
    day = as_of or clock.today()
    month = period or panel.tape_as_of(db, deal_row.name, day).period
    traced = runs.trace(db, reader, deal=deal_row.name, loan=loan, period=month, as_of=day)
    row = panel.as_of(db, day, deal_id=deal_row.id, period=month, loan=loan)[0]
    rollforward = rollforward_check(db, deal_id=deal_row.id, period=month, loan=loan, as_of=day)
    view = comment_view(db, reader, deal=deal_row.name, loan=loan, period=month, as_of=day)
    return LoanPage(
        deal=deal_row.name,
        loan=loan,
        month=month_ref(month),
        as_of=day,
        property_name=row.property_name,
        property_type=row.property_type,
        tape=TapeSection(
            source=_source(db, row.accession),
            loaded=loaded(traced.run, traced.loaded_by, traced.loaded_at),
            values=tape_values(row, rollforward),
        ),
        financials=financials(row),
        comment=_comment(db, view) if view is not None else None,
        comment_note=comment_note(month) if view is None else None,
        flags=_flags(db, deal_row.id, loan, month, day),
        alerts=_loan_alerts(db, deal_row.id, loan, month, day),
        history=loan_history(db, deal_id=deal_row.id, loan=loan, day=day),
    )
