"""Refresh the book, one deal or a range of months, as a numbered run.

A run, in order:
1. checks the user may run refreshes, and finds its deals (the book's, or the one named);
2. claims each deal (`cmbs.refresh.claims`). A deal another run holds refuses a run of that one
   deal, and is skipped by a book refresh, which still refreshes the rest;
3. per deal, runs filing discovery as of today and registers what it found, under the run;
4. skips a deal whose filings are not public monthly loan tapes (its coverage class says why,
   e.g. "not publicly reported");
5. loads the months the plan names, oldest first (`cmbs.refresh.plan`).

A month loads completely or not at all. Its two files are fetched with no transaction open, then
the loan tape and the distribution report are loaded, the report reconciled with the tape, and
the month's alerts built, all in one transaction with the month's run record. If any of it
fails, nothing of the month is stored and the month is recorded as not loaded, saying why.

The run's summary is saved after each deal, so a run that stops early still shows what it did.
An unexpected error (a bug, a lost database) finishes the run as failed, saying where it was,
and is raised again.

EDGAR is reached through `cmbs.refresh.sources`; tests replay recorded answers.
"""

import datetime as dt
import importlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.absee.schedule_al import MalformedTape
from cmbs.access.roles import Action
from cmbs.access.service import Actor, authorize
from cmbs.audit.service import record, refuse
from cmbs.book.service import book_as_of
from cmbs.coverage.rules import Coverage
from cmbs.coverage.service import classify_deal
from cmbs.db import engine
from cmbs.deals.service import get_or_create
from cmbs.deals.tables import Deal
from cmbs.distribution.parse import parse_report
from cmbs.distribution.reconcile import reconcile
from cmbs.distribution.service import WrongPeriod, load_report, report_period
from cmbs.distribution.tables import DistributionReport
from cmbs.domain import Period
from cmbs.edgar.discovery import Discovery, discover
from cmbs.edgar.replay import MissingRecording
from cmbs.edgar.service import filings_of, register
from cmbs.errors import Refused
from cmbs.refresh import plan
from cmbs.refresh.claims import (
    AlreadyRefreshing,
    Claims,
    held_message,
    record_claims,
    release_claims,
)
from cmbs.refresh.plan import (
    DealOutcome,
    DealResult,
    FilingRef,
    MonthFilings,
    MonthOutcome,
    MonthResult,
)
from cmbs.refresh.sources import Fetched, Sources, default_sources, fetch_exhibit
from cmbs.refresh.tables import RunMonth
from cmbs.runs import service as runs
from cmbs.runs.service import RunView
from cmbs.runs.tables import Run
from cmbs.tape.service import ORIGINAL, load_tape
from cmbs.tape.tables import LoanTape

REFRESH_BOOK = "refresh book"
REFRESH_DEAL = "refresh deal"
BACKFILL = "backfill"
START_ACTION = "start refresh run"
# What can go wrong reaching EDGAR that is not a bug: a refusal (an ambiguous name, a filing
# whose exhibit cannot be told apart), a missing recording, the network, a malformed answer.
FETCH_ERRORS: tuple[type[Exception], ...] = (
    Refused,
    MissingRecording,
    OSError,
    json.JSONDecodeError,
)

type Planner = Callable[[Mapping[Period, MonthFilings], set[Period]], list[Period]]


class EmptyBook(Refused):
    """A book refresh with no positions file to say which deals are in the book."""


class BadRange(Refused):
    """A backfill that ends before it starts."""


class MonthNotLoaded(Exception):
    """The month could not be loaded: why, in the run summary's words, and the error behind it."""

    def __init__(self, reason: str, detail: str | None = None) -> None:
        super().__init__(reason if detail is None else f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class RunReport:
    run: RunView
    deals: tuple[DealResult, ...]

    def text(self) -> str:
        return self.run.text()


# ---------------------------------------------------------------------------- the month


def build_alerts(
    db: Session, *, deal_id: int, period: Period, actor: Actor, run_id: int
) -> int | None:
    """Build the month's alerts against the month before; None while `cmbs.alerts` is absent.

    The alerts feature provides `cmbs.alerts.service.build_alerts`, which returns a summary
    with the number raised. It is looked up when called, so the month's load needs no change
    when that feature arrives.
    """
    try:
        alerts = importlib.import_module("cmbs.alerts.service")
    except ModuleNotFoundError as missing:
        if missing.name not in ("cmbs.alerts", "cmbs.alerts.service"):
            raise
        return None
    built = alerts.build_alerts(db, deal_id=deal_id, period=period, actor=actor, run_id=run_id)
    return built.raised


def load_month(
    db: Session,
    *,
    actor: Actor,
    run_id: int,
    deal: Deal,
    month: MonthFilings,
    tape: Fetched,
    report: Fetched,
    on: dt.date,
) -> MonthResult:
    """Load one deal-month in the caller's transaction; raises MonthNotLoaded when it cannot.

    The caller rolls the transaction back on MonthNotLoaded, so nothing of the month stays.
    """
    if month.tape is None or month.report is None:  # pragma: no cover - planned months have both
        raise ValueError(f"{month.period} lacks a filing")
    try:
        loaded = load_tape(
            db,
            deal=deal.name,
            content=tape.content,
            file_name=tape.file_name,
            accession=tape.accession,
            filing_date=month.tape.filing_date,
            form=ORIGINAL,
            cik=month.tape.cik,
            actor=actor,
            run_id=run_id,
        )
    except MalformedTape as error:
        raise MonthNotLoaded("ABS-EE failed to parse", str(error)) from error
    except Refused as error:
        raise MonthNotLoaded("ABS-EE was refused", str(error)) from error
    if loaded.period != month.period:
        raise MonthNotLoaded(
            "ABS-EE reports another month", f"{tape.accession} reports {loaded.period}"
        )
    try:
        report_period(parse_report(report.content), month.period)
    except WrongPeriod as error:
        raise MonthNotLoaded("10-D reports another month", str(error)) from error
    except (Refused, ValueError) as error:
        raise MonthNotLoaded("10-D failed to parse", str(error)) from error
    try:
        load_report(
            db,
            user=actor.user,
            deal=deal.name,
            accession=report.accession,
            cik=month.report.cik,
            filing_date=month.report.filing_date,
            file_name=report.file_name,
            content=report.content,
            period=month.period,
            run_id=run_id,
        )
    except Refused as error:
        raise MonthNotLoaded("10-D was refused", str(error)) from error
    reconciliation = reconcile(db, deal_id=deal.id, period=month.period, as_of=on)
    alerts = build_alerts(db, deal_id=deal.id, period=month.period, actor=actor, run_id=run_id)
    return MonthResult(
        period=month.period,
        outcome=MonthOutcome.LOADED,
        loan_rows=0 if loaded.already_loaded else loaded.loans_loaded,
        quarantined=len(loaded.quarantined),
        flags=len(reconciliation.disagreements),
        alerts=alerts,
    )


def _record_month(
    db: Session, run_id: int, deal_id: int, month: MonthFilings | None, result: MonthResult
) -> None:
    db.add(
        RunMonth(
            run_id=run_id,
            deal_id=deal_id,
            period=result.period.first_day,
            outcome=result.outcome.value,
            reason=result.reason,
            detail=result.detail,
            tape_accession=month.tape.accession if month and month.tape else None,
            report_accession=month.report.accession if month and month.report else None,
            loan_rows=result.loan_rows,
            alerts=result.alerts,
            at=clock.now(),
        )
    )
    db.flush()


def loaded_months(db: Session, deal_id: int) -> set[Period]:
    """Months with both a loan tape and a distribution report stored."""
    tapes = db.scalars(select(LoanTape.period).where(LoanTape.deal_id == deal_id).distinct())
    reports = db.scalars(
        select(DistributionReport.period).where(DistributionReport.deal_id == deal_id).distinct()
    )
    return {Period.of(day) for day in set(tapes) & set(reports)}


@dataclass(frozen=True)
class MonthStatus:
    status: str
    reason: str | None = None
    run: int | None = None


def month_status(db: Session, *, deal_id: int, period: Period) -> MonthStatus:
    """ "loaded", or "not loaded" with the reason the latest run that tried it gave."""
    if period in loaded_months(db, deal_id):
        return MonthStatus(MonthOutcome.LOADED.value)
    latest = db.scalars(
        select(RunMonth)
        .where(RunMonth.deal_id == deal_id, RunMonth.period == period.first_day)
        .order_by(RunMonth.at.desc(), RunMonth.id.desc())
        .limit(1)
    ).one_or_none()
    if latest is None:
        return MonthStatus(MonthOutcome.NOT_LOADED.value)
    return MonthStatus(MonthOutcome.NOT_LOADED.value, latest.reason, latest.run_id)


# ---------------------------------------------------------------------------- one deal


@dataclass
class Progress:
    """Where the run is, for the summary of a run that stops on an unexpected error."""

    deal: str | None = None
    period: Period | None = None
    done: list[DealResult] = field(default_factory=list)


def _filing_refs(db: Session, deal_id: int, on: dt.date) -> list[FilingRef]:
    return [
        FilingRef(f.accession, f.cik, f.form, f.filing_date, Period.of(f.period))
        for f in filings_of(db, deal_id, on)
        if f.period is not None and f.deal_id == deal_id
    ]


def _register(db: Session, found: Discovery, actor: Actor, run_id: int) -> Deal:
    deal = register(db, found, user=actor.user)
    record(
        db,
        user=actor.user,
        role=actor.role.value,
        run_id=run_id,
        action="discover filings",
        subject="deal",
        subject_key=deal.name,
        after=(
            f"CIK {found.match.cik}, {len(found.filings)} filings"
            if found.match.cik is not None
            else f"no issuing entity, {len(found.filings)} filings"
        ),
        detail={"on": found.on.isoformat(), "cik": found.match.cik, "filings": len(found.filings)},
    )
    return deal


def _fetch(sources: Sources, month: MonthFilings) -> tuple[Fetched, Fetched]:
    if month.tape is None or month.report is None:  # pragma: no cover - planned months have both
        raise ValueError(f"{month.period} lacks a filing")
    try:
        tape = fetch_exhibit(
            sources.archive, cik=month.tape.cik, accession=month.tape.accession, exhibit="EX-102"
        )
    except FETCH_ERRORS as error:
        raise MonthNotLoaded("ABS-EE could not be fetched", str(error)) from error
    try:
        report = fetch_exhibit(
            sources.archive,
            cik=month.report.cik,
            accession=month.report.accession,
            exhibit="EX-99.1",
        )
    except FETCH_ERRORS as error:
        raise MonthNotLoaded("10-D could not be fetched", str(error)) from error
    return tape, report


def _not_loaded(period: Period, error: MonthNotLoaded) -> MonthResult:
    return MonthResult(period, MonthOutcome.NOT_LOADED, error.reason, error.detail, failed=True)


def refresh_one(
    *,
    actor: Actor,
    run_id: int,
    deal: Deal,
    planner: Planner,
    sources: Sources,
    on: dt.date,
    progress: Progress,
) -> DealResult:
    """Discover, classify and load one claimed deal. Each step commits on its own."""
    progress.deal, progress.period = deal.name, None
    try:
        found = discover(sources.edgar, deal.name, on)
    except FETCH_ERRORS as error:
        return DealResult(deal.name, DealOutcome.FAILED, f"filing discovery failed: {error}")
    try:
        with engine.session() as db:
            _register(db, found, actor, run_id)
            registered = db.get_one(Deal, deal.id, populate_existing=True)
            coverage = classify_deal(db, registered, on)
            available = plan.monthly_filings(_filing_refs(db, deal.id, on))
            months = planner(available, loaded_months(db, deal.id))
    except Refused as error:
        return DealResult(deal.name, DealOutcome.FAILED, f"filing discovery failed: {error}")
    if coverage.coverage is not Coverage.FULL:
        return DealResult(deal.name, DealOutcome.SKIPPED, coverage.coverage.value)
    results: list[MonthResult] = []
    for period in months:
        progress.period = period
        month = available.get(period, MonthFilings(period))
        missing = month.missing()
        if missing is not None:
            result = MonthResult(period, MonthOutcome.NOT_LOADED, missing)
            with engine.session() as db:
                _record_month(db, run_id, deal.id, month, result)
            results.append(result)
            continue
        try:
            tape, report = _fetch(sources, month)
            with engine.session() as db:
                result = load_month(
                    db,
                    actor=actor,
                    run_id=run_id,
                    deal=deal,
                    month=month,
                    tape=tape,
                    report=report,
                    on=on,
                )
                _record_month(db, run_id, deal.id, month, result)
        except MonthNotLoaded as error:
            result = _not_loaded(period, error)
            with engine.session() as db:
                _record_month(db, run_id, deal.id, month, result)
            results.append(result)
            break  # later months wait for this one: see cmbs.refresh.plan
        results.append(result)
    return plan.deal_outcome(deal.name, results)


# ---------------------------------------------------------------------------- runs


def _book_deals(db: Session, on: dt.date) -> list[Deal]:
    book = book_as_of(db, on)
    if book is None:
        raise EmptyBook(f"no positions file is dated on or before {on.isoformat()}")
    ids = sorted({held.deal_id for held in book.positions})
    return sorted((db.get_one(Deal, deal_id) for deal_id in ids), key=lambda d: d.name)


def _run(
    user: str,
    *,
    kind: str,
    pick: Callable[[Session], list[Deal]],
    planner: Planner,
    sources: Sources | None,
    one_deal: bool,
) -> RunReport:
    sources = sources or default_sources()
    on = clock.today()
    with engine.session() as db:
        actor = authorize(db, user, Action.RUN_REFRESH)
        try:
            deals = pick(db)
        except Refused as refusal:
            refuse(db, refusal, user=user, role=actor.role.value, action=kind)
    claims = Claims.open()
    try:
        held = [deal for deal in deals if not claims.try_take(deal.id)]
        if one_deal and held:
            with engine.session() as db:
                refusal = AlreadyRefreshing(held_message(db, held[0]))
                refuse(
                    db,
                    refusal,
                    user=user,
                    role=actor.role.value,
                    action=kind,
                    subject="deal",
                    subject_key=held[0].name,
                )
        with engine.session() as db:
            skipped = {
                deal.id: DealResult(deal.name, DealOutcome.SKIPPED, held_message(db, deal))
                for deal in held
            }
            run = runs.start_run(db, kind=kind, started_by=user)
            record_claims(db, run, claims.held)
            record(
                db,
                user=user,
                role=actor.role.value,
                run_id=run.id,
                action=START_ACTION,
                subject="run",
                subject_key=str(run.id),
                detail={"kind": kind, "deals": [deal.name for deal in deals]},
            )
        progress = Progress()
        try:
            for deal in deals:
                result = skipped.get(deal.id) or refresh_one(
                    actor=actor,
                    run_id=run.id,
                    deal=deal,
                    planner=planner,
                    sources=sources,
                    on=on,
                    progress=progress,
                )
                progress.done.append(result)
                with engine.session() as db:
                    runs.record_progress(db, db.get_one(Run, run.id), plan.summary(progress.done))
        except BaseException as error:
            _crashed(run.id, progress, error)
            raise
        with engine.session() as db:
            finished = runs.finish_run(
                db,
                db.get_one(Run, run.id),
                status=plan.run_status(progress.done),
                summary=plan.summary(progress.done),
            )
            release_claims(db, finished)
            view = RunView.of(finished)
    finally:
        claims.close()
    return RunReport(view, tuple(progress.done))


def _crashed(run_id: int, progress: Progress, error: BaseException) -> None:
    """Finish the run as failed, saying where it was, then let the error go on."""
    summary = plan.summary(progress.done)
    summary["stopped"] = {
        "deal": progress.deal,
        "period": progress.period.label if progress.period else None,
        "error": f"{type(error).__name__}: {error}",
        "detail": None,
    }
    with engine.session() as db:
        run = db.get_one(Run, run_id)
        runs.finish_run(db, run, status=runs.FAILED, summary=summary)
        release_claims(db, run)


def refresh_book(user: str, *, sources: Sources | None = None) -> RunReport:
    """Refresh every deal in today's book."""
    return _run(
        user,
        kind=REFRESH_BOOK,
        pick=lambda db: _book_deals(db, clock.today()),
        planner=plan.refresh_months,
        sources=sources,
        one_deal=False,
    )


def refresh_deal(user: str, deal: str, *, sources: Sources | None = None) -> RunReport:
    """Bring one deal up to date. A name not registered yet is registered, as positions are."""
    return _run(
        user,
        kind=REFRESH_DEAL,
        pick=lambda db: [get_or_create(db, deal)],
        planner=plan.refresh_months,
        sources=sources,
        one_deal=True,
    )


def backfill(
    user: str, deal: str, start: Period, end: Period, *, sources: Sources | None = None
) -> RunReport:
    """Load every month of a deal from `start` to `end` that is not loaded yet, oldest first."""
    if end < start:
        with engine.session() as db:
            actor = authorize(db, user, Action.RUN_REFRESH)
            refuse(
                db,
                BadRange(f"a backfill from {start} to {end} ends before it starts"),
                user=user,
                role=actor.role.value,
                action=BACKFILL,
                subject="deal",
                subject_key=deal,
            )
    return _run(
        user,
        kind=BACKFILL,
        pick=lambda db: [get_or_create(db, deal)],
        planner=lambda _available, loaded: plan.backfill_months(start, end, loaded),
        sources=sources,
        one_deal=True,
    )
