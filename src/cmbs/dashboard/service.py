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
  month by that day, and the month's alerts on the loan.

Every value says what verified it:
- a tape value passed the loan-row contract (a row that fails it is quarantined, never in the
  panel); a balance also shows its roll-forward check;
- an extracted value shows how it was accepted (auto-accepted, human-reviewed, or whatever
  status the panel gives it) and "evidence checked" when the sentence behind it is one of the
  stored comment's sentences; a value awaiting review has no value to show.
"""

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.access.roles import Action
from cmbs.access.service import authorize
from cmbs.alerts import service as alerts
from cmbs.alerts.codes import PAYMENT_STATUS, WORKOUT_STRATEGY, name_of
from cmbs.alerts.service import AlertView
from cmbs.alerts.tables import AlertBuild
from cmbs.analysis.staleness import loan_staleness
from cmbs.checks.tables import CheckFlag
from cmbs.distribution.service import canonical_loan
from cmbs.domain import Period
from cmbs.edgar.archives import filing_base_url
from cmbs.errors import Refused
from cmbs.extraction.service import CommentView, FieldView, comment_view
from cmbs.extraction.tables import Extraction
from cmbs.filings.tables import Filing
from cmbs.panel import service as panel
from cmbs.runs import service as runs
from cmbs.tape.tables import LoanPeriod

# What a check found: "passed", "failed", or "not checked" (the check could not run yet).
PASSED = "passed"
FAILED = "failed"
NOT_CHECKED = "not checked"
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
ROLLFORWARD_NOT_CHECKED = Check("roll-forward not checked", NOT_CHECKED)
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
    deal: str
    loan: str


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
        loans=tuple(LoanRef(loan.deal, loan.loan) for loan in view.loans),
        exposure=view.exposure,
        balance=view.balance,
        classes=view.classes,
        state=view.state.value,
        state_text=view.state_text,
        note=view.note,
    )


def book_note(period: Period, empty: bool) -> str | None:
    return f"no open alerts on the book for {period.label}" if empty else None


def _latest_built(db: Session) -> Period | None:
    latest = db.scalar(select(func.max(AlertBuild.period)))
    return Period.of(latest) if latest is not None else None


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
        note=book_note(month, not rows),
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
    decided_by: str | None


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
    check: str
    message: str


@dataclass(frozen=True)
class LoanAlert:
    alert_id: int
    severity: str
    message: str
    state_text: str


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
            "beginning balance",
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


def rollforward_check(db: Session, *, deal_id: int, period: Period, loan: str) -> Check:
    """The balance roll-forward's verdict on the loan's month.

    The roll-forward check (`cmbs.crosscheck.rollforward`) is being built in parallel; until it
    is merged the dashboard says the balance was not checked rather than imply it passed.
    """
    del db, deal_id, period, loan
    return ROLLFORWARD_NOT_CHECKED


def financials(row: LoanPeriod) -> Financials:
    measured = loan_staleness(panel.tape_loan(row))
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
    """What verified an extracted value: how it was accepted (its status), and "evidence
    checked" when the sentence behind it is one of the comment's. A field with no value (one
    awaiting review, or not stated) has nothing verified."""
    if value is None:
        return ()
    accepted = Check(status, PASSED)
    if sentence is not None and 1 <= sentence <= len(sentences):
        return (accepted, EVIDENCE_CHECKED)
    return (accepted,)


def comment_value(view: FieldView, sentences: Sequence[str]) -> CommentValue:
    return CommentValue(
        field=view.field,
        label=view.label,
        status=view.status,
        value=view.display,
        sentence=view.sentence,
        evidence=view.evidence,
        checks=field_checks(view.display, view.status, view.sentence, sentences),
        review_reason=view.review_reason,
        decided_by=view.decided_by,
    )


def comment_note(period: Period) -> str:
    return f"no servicer comment extracted for {period.label}"


def _source(db: Session, accession: str) -> FilingSource:
    filing = db.get_one(Filing, accession)
    return FilingSource(
        form=filing.form,
        accession=accession,
        filing_date=filing.filing_date,
        url=edgar_url(filing.cik, accession, filing.primary_document),
    )


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
    sentences = tuple(view.sentences)
    return CommentSection(
        source=_source(db, view.accession),
        loaded=loaded(run, by, at, "extracted"),
        sentences=sentences,
        model=view.model,
        prompt_version=view.prompt_version,
        settings_version=view.settings_version,
        values=tuple(comment_value(field, sentences) for field in view.fields.values()),
    )


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
    return tuple(LoanFlag(flag.check, flag.message) for flag in found)


def covers(view: AlertView, deal: str, loan: str) -> bool:
    return any(covered.deal == deal and covered.loan == loan for covered in view.loans)


def loan_alerts(views: Sequence[AlertView], deal: str, loan: str) -> tuple[LoanAlert, ...]:
    return tuple(
        LoanAlert(view.id, view.severity.value, view.message, view.state_text)
        for view in views
        if covers(view, deal, loan)
    )


def loan_page(
    db: Session,
    *,
    reader: str,
    deal: str,
    loan: str,
    period: Period | None = None,
    as_of: dt.date | None = None,
) -> LoanPage:
    """One loan as the panel shows it on `as_of` (today), for a month (the deal's latest)."""
    authorize(db, reader, Action.READ_PANEL)
    deal_row = panel.deal_named(db, deal)
    day = as_of or clock.today()
    month = period or panel.tape_as_of(db, deal_row.name, day).period
    traced = runs.trace(db, reader, deal=deal_row.name, loan=loan, period=month, as_of=day)
    row = panel.as_of(db, day, deal_id=deal_row.id, period=month, loan=loan)[0]
    rollforward = rollforward_check(db, deal_id=deal_row.id, period=month, loan=loan)
    view = comment_view(db, reader, deal=deal_row.name, loan=loan, period=month, as_of=day)
    listed = alerts.month_alerts(db, reader=reader, deal=deal_row.name, period=month)
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
        alerts=loan_alerts(listed.alerts, deal_row.name, loan),
    )
