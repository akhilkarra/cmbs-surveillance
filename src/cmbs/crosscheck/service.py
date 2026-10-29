"""Run the text-versus-tape and roll-forward checks on a deal's month, and read their flags.

Reads (`text_vs_tape`, `rollforward`) are point in time and write nothing, so the dashboard and
the refresh routine can call them freely. Each reads the month as known on `as_of` (default
today): the loan tape current on that day (`cmbs.panel.service`), and each comment as the panel
showed it then, through its public read (`cmbs.extraction.service.comment_view`), which lays
review decisions and overrides on top of the extraction. Only values whose status settles them
(accepted, human-reviewed, overridden) are compared; a value awaiting review or a declined
field is not. Reading comments needs the right to read the panel.

`check_text_vs_tape`, `check_rollforward` and `run_checks` raise what the reads find as check
flags (`cmbs.checks.service`), each keeping both sides. Neither source is changed. A flag already
raised with the same sides is not raised again, so running the checks twice changes nothing;
checks of one deal take turns on an advisory lock, and the existing flags are read after the
lock is taken. Every flag raised is audited against its loan, naming the run when there is one.
"""

import datetime as dt
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, NoReturn

from sqlalchemy import select, text, tuple_
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.access.roles import Action
from cmbs.access.service import Actor, authorize
from cmbs.audit.service import LOAN, loan_key, record, refuse
from cmbs.checks.service import raise_flag
from cmbs.checks.tables import CheckFlag
from cmbs.crosscheck import rollforward as rf
from cmbs.crosscheck import rules
from cmbs.deals import service as deals
from cmbs.deals.tables import Deal
from cmbs.distribution.tables import DistributionReport
from cmbs.domain import Period
from cmbs.errors import Refused
from cmbs.extraction.fields import spec, typed
from cmbs.extraction.service import OVERRIDDEN, CommentView, comment_view
from cmbs.extraction.tables import AUTO_ACCEPTED, HUMAN_REVIEWED, Extraction
from cmbs.filings.tables import Filing
from cmbs.panel import service as panel
from cmbs.panel.service import loan_order
from cmbs.settings.service import in_force
from cmbs.tape.tables import LoanPeriod, TapeAsset

RUN_CHECKS = "run checks"
RAISE_FLAG = "raise check flag"
DEAL = "deal"
# Panel statuses whose values are compared: an override (cmbs.review) included, a field
# declined as not determinable or awaiting review not.
COMPARED = frozenset({AUTO_ACCEPTED, HUMAN_REVIEWED, OVERRIDDEN})
# An arbitrary constant naming the checks' advisory locks; the second key is the deal id.
CHECK_LOCK_SPACE = 0x0C4EC4


class UnknownDeal(Refused):
    pass


class NoTape(Refused):
    """No loan tape of the month had been filed by the day the checks read."""


def _deal(db: Session, deal: str) -> Deal:
    found = deals.by_name(db, deal)
    if found is None:
        raise UnknownDeal(f"no deal named {deal!r}")
    return found


def deal_id(db: Session, deal: str) -> int:
    """The registry id of the deal a name (or an alias) names; UnknownDeal otherwise."""
    return _deal(db, deal).id


# Reading text versus tape.


@dataclass(frozen=True)
class LoanTextVsTape:
    """One loan's comparisons; `skipped` says why a comment could not be compared."""

    loan: str
    findings: list[rules.Finding]
    skipped: str | None = None


def _end_of(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), dt.UTC)


def _commented_loans(db: Session, deal_id: int, period: Period, day: dt.date) -> list[str]:
    """Loans with an extracted comment for the month as known on `day`: from a 10-D public by
    then, extracted by the end of the day (UTC), as `comment_view` reads them."""
    loans = db.scalars(
        select(Extraction.loan_id)
        .join(Filing, Filing.accession == Extraction.accession)
        .where(
            Extraction.deal_id == deal_id,
            Extraction.period == period.first_day,
            Filing.filing_date <= day,
            Extraction.extracted_at < _end_of(day),
        )
        .distinct()
    ).all()
    return sorted(loans, key=loan_order)


def accepted_values(view: CommentView) -> dict[str, Any]:
    """The comment's values the panel shows with a status that settles them (accepted,
    human-reviewed, overridden), typed, with their sentences. Anything else, a value awaiting
    review or a declined field, is not compared."""
    values: dict[str, Any] = {}
    for name, shown in view.fields.items():
        if shown.status in COMPARED and shown.value is not None:
            values[name] = typed(spec(name), shown.value)
            values[f"{name}.sentence"] = shown.sentence
    return values


def _report_dates(db: Session, accessions: set[str]) -> dict[str, dt.date | None]:
    if not accessions:
        return {}
    rows = db.execute(
        select(DistributionReport.accession, DistributionReport.distribution_date).where(
            DistributionReport.accession.in_(accessions)
        )
    ).all()
    return {accession: day for accession, day in rows}


def text_side(view: CommentView, as_of: dt.date | None, values: dict[str, Any]) -> rules.TextSide:
    return rules.TextSide(
        accession=view.accession,
        filed=view.filing_date,
        as_of=as_of or view.filing_date,
        transfer_date=values.get("transfer_date"),
        transfer_sentence=values.get("transfer_date.sentence"),
        dscr=values.get("dscr"),
        dscr_as_of=values.get("dscr_as_of"),
        occupancy=values.get("occupancy"),
        occupancy_as_of=values.get("occupancy_as_of"),
    )


def tape_side(row: LoanPeriod) -> rules.TapeSide:
    return rules.TapeSide(
        accession=row.accession,
        filed=row.filing_date,
        as_of=row.reporting_period_end,
        transfer_date=row.special_servicer_transfer_date,
        dscr=row.dscr,
        occupancy=row.occupancy,
        financials_end=row.financials_end_newest or row.financials_end,
    )


def text_vs_tape(
    db: Session, *, reader: str, deal_id: int, period: Period, as_of: dt.date | None = None
) -> list[LoanTextVsTape]:
    """Every extracted comment of the deal's month against the tape, as known on `as_of`.

    The comment's values are read through the panel's public read (`comment_view`, as `reader`
    on `as_of`), so whatever the panel lays on top of an extraction, a review decision or an
    override, is what is compared.
    """
    authorize(db, reader, Action.READ_PANEL)
    day = as_of or clock.today()
    deal = db.get_one(Deal, deal_id)
    tolerance = in_force(db).values.transfer_date_tolerance_periods
    tape = {row.loan: row for row in panel.as_of(db, day, deal_id=deal_id, period=period)}
    views = [
        view
        for loan in _commented_loans(db, deal_id, period, day)
        if (view := comment_view(db, reader, deal=deal.name, loan=loan, period=period, as_of=day))
        is not None
    ]
    report_dates = _report_dates(db, {view.accession for view in views})
    results: list[LoanTextVsTape] = []
    for view in views:
        row = tape.get(view.loan)
        if row is None:
            skipped = f"loan {view.loan} is not on the {period.label} tape as of {day.isoformat()}"
            results.append(LoanTextVsTape(view.loan, [], skipped))
            continue
        side = text_side(view, report_dates.get(view.accession), accepted_values(view))
        results.append(
            LoanTextVsTape(view.loan, rules.compare(side, tape_side(row), tolerance=tolerance))
        )
    return results


# Reading the roll-forward.


def _scheduled_endings(
    db: Session, rows: Sequence[LoanPeriod]
) -> dict[tuple[str, str, int], str | None]:
    """Each row's ending scheduled balance tag as reported, from the bronze asset it came from."""
    keys = [(row.accession, row.file_name, row.asset_position) for row in rows]
    if not keys:
        return {}
    assets = db.execute(
        select(TapeAsset.accession, TapeAsset.file_name, TapeAsset.position, TapeAsset.tags).where(
            tuple_(TapeAsset.accession, TapeAsset.file_name, TapeAsset.position).in_(keys)
        )
    ).all()
    return {
        (accession, name, position): tags.get(rf.ENDING_SCHEDULED_TAG)
        for accession, name, position, tags in assets
    }


def _ending(row: LoanPeriod, scheduled: dict[tuple[str, str, int], str | None]) -> rf.Ending:
    return rf.ending_of(scheduled.get((row.accession, row.file_name, row.asset_position)))


def balances_of(row: LoanPeriod, ending: rf.Ending) -> rf.Balances:
    return rf.Balances(
        beginning=row.beginning_balance,
        scheduled_principal=row.scheduled_principal,
        unscheduled_principal=row.unscheduled_principal,
        other_principal_adjustments=row.other_principal_adjustments,
        realized_loss=row.realized_loss,
        ending=ending,
    )


def rollforward(
    db: Session, *, deal_id: int, period: Period, as_of: dt.date | None = None
) -> list[rf.LoanRollForward]:
    """Each loan's roll-forward for the month, on the tapes current on `as_of` (default today).

    Empty when no tape of the month had been filed by then.
    """
    day = as_of or clock.today()
    rows = panel.as_of(db, day, deal_id=deal_id, period=period)
    before = period.shift(-1)
    previous = {row.loan: row for row in panel.as_of(db, day, deal_id=deal_id, period=before)}
    scheduled = _scheduled_endings(db, [*rows, *previous.values()])
    results: list[rf.LoanRollForward] = []
    for row in rows:
        balances = balances_of(row, _ending(row, scheduled))
        last = previous.get(row.loan)
        results.append(
            rf.LoanRollForward(
                loan=row.loan,
                period=period,
                accession=row.accession,
                filed=row.filing_date,
                balances=balances,
                roll=rf.roll(balances),
                continuity=rf.continuity(
                    row.beginning_balance,
                    _ending(last, scheduled) if last is not None else None,
                    before,
                ),
                previous_accession=last.accession if last is not None else None,
            )
        )
    return results


# Raising flags.


@dataclass(frozen=True)
class Raised:
    """A flag a check found: raised now, or already standing from an earlier run."""

    loan: str
    check: str
    message: str
    sides: dict[str, Any]
    new: bool


@dataclass(frozen=True)
class CheckSummary:
    deal: str
    period: Period
    as_of: dt.date
    text_vs_tape: list[LoanTextVsTape] = field(default_factory=list)
    rollforward: list[rf.LoanRollForward] = field(default_factory=list)
    flags: list[Raised] = field(default_factory=list)

    @property
    def rollforward_breaks(self) -> int:
        return sum(1 for result in self.rollforward if result.status is rf.Status.BREAK)

    @property
    def rollforward_passed(self) -> int:
        return sum(1 for result in self.rollforward if result.status is rf.Status.PASSED)


def _found(results: CheckSummary) -> list[tuple[str, str, str, dict[str, Any]]]:
    """(loan, check, message, sides) of every flag or note the reads found, in loan order."""
    found: list[tuple[str, str, str, dict[str, Any]]] = []
    for loan in results.text_vs_tape:
        for finding in loan.findings:
            if finding.status is not rules.Status.AGREE:
                found.append((loan.loan, finding.check, finding.message, finding.sides))
    for result in results.rollforward:
        if result.roll.status is rf.Status.BREAK:
            found.append((result.loan, rf.ROLLFORWARD, rf.ROLL_BREAK, rf.roll_sides(result)))
        if result.continuity.status is rf.Status.BREAK:
            sides = rf.continuity_sides(result)
            found.append((result.loan, rf.CONTINUITY, rf.CONTINUITY_BREAK, sides))
    return found


def _lock(db: Session, deal_id: int) -> None:
    """Checks of one deal take turns until the transaction ends."""
    db.execute(
        text("SELECT pg_advisory_xact_lock(CAST(:space AS integer), CAST(:deal AS integer))"),
        {"space": CHECK_LOCK_SPACE, "deal": deal_id},
    )


def _standing(db: Session, deal_id: int, period: Period) -> set[tuple[str, str, str, str]]:
    flags = db.scalars(
        select(CheckFlag).where(CheckFlag.deal_id == deal_id, CheckFlag.period == period.first_day)
    ).all()
    return {(flag.loan, flag.check, flag.message, _key(flag.sides)) for flag in flags}


def _key(sides: dict[str, Any]) -> str:
    return json.dumps(sides, sort_keys=True)


def _refuse(db: Session, error: Refused, actor: Actor, deal: str) -> NoReturn:
    refuse(
        db,
        error,
        user=actor.user,
        role=actor.role.value,
        action=RUN_CHECKS,
        subject=DEAL,
        subject_key=deal,
    )


def _run(
    db: Session,
    *,
    user: str,
    deal: str,
    period: Period,
    run_id: int | None,
    reads: Callable[[Session, Deal, dt.date], CheckSummary],
) -> CheckSummary:
    actor = authorize(db, user, Action.RUN_REFRESH)
    day = clock.today()
    try:
        deal_row = _deal(db, deal)
        _lock(db, deal_row.id)
        if not panel.current_tapes(db, day, deal_id=deal_row.id, period=period):
            raise NoTape(
                f"no {period.label} loan tape of {deal_row.name} had been filed by "
                f"{day.isoformat()}"
            )
        summary = reads(db, deal_row, day)
    except Refused as error:
        _refuse(db, error, actor, deal)
    standing = _standing(db, deal_row.id, period)
    flags: list[Raised] = []
    for loan, check, message, sides in _found(summary):
        new = (loan, check, message, _key(sides)) not in standing
        flags.append(Raised(loan, check, message, sides, new))
        if not new:
            continue
        raise_flag(
            db,
            deal_id=deal_row.id,
            loan=loan,
            check=check,
            message=message,
            period=period.first_day,
            sides=sides,
        )
        record(
            db,
            user=actor.user,
            role=actor.role.value,
            run_id=run_id,
            action=RAISE_FLAG,
            subject=LOAN,
            subject_key=loan_key(deal_row.name, loan),
            field=check,
            after=message,
            detail={"period": period.first_day.isoformat(), "sides": sides},
        )
    return CheckSummary(
        deal=summary.deal,
        period=period,
        as_of=day,
        text_vs_tape=summary.text_vs_tape,
        rollforward=summary.rollforward,
        flags=flags,
    )


def check_text_vs_tape(
    db: Session, *, user: str, deal: str, period: Period, run_id: int | None = None
) -> CheckSummary:
    """Compare the month's comments with its tape as known today, and flag what disagrees."""

    def reads(db: Session, deal_row: Deal, day: dt.date) -> CheckSummary:
        found = text_vs_tape(db, reader=user, deal_id=deal_row.id, period=period, as_of=day)
        return CheckSummary(deal_row.name, period, day, text_vs_tape=found)

    return _run(db, user=user, deal=deal, period=period, run_id=run_id, reads=reads)


def check_rollforward(
    db: Session, *, user: str, deal: str, period: Period, run_id: int | None = None
) -> CheckSummary:
    """Roll each loan's balance forward on the month's tape as known today, and flag breaks."""

    def reads(db: Session, deal_row: Deal, day: dt.date) -> CheckSummary:
        found = rollforward(db, deal_id=deal_row.id, period=period, as_of=day)
        return CheckSummary(deal_row.name, period, day, rollforward=found)

    return _run(db, user=user, deal=deal, period=period, run_id=run_id, reads=reads)


def run_checks(
    db: Session, *, user: str, deal: str, period: Period, run_id: int | None = None
) -> CheckSummary:
    """Every check on the month: text versus tape, then the roll-forward."""

    def reads(db: Session, deal_row: Deal, day: dt.date) -> CheckSummary:
        return CheckSummary(
            deal_row.name,
            period,
            day,
            text_vs_tape=text_vs_tape(
                db, reader=user, deal_id=deal_row.id, period=period, as_of=day
            ),
            rollforward=rollforward(db, deal_id=deal_row.id, period=period, as_of=day),
        )

    return _run(db, user=user, deal=deal, period=period, run_id=run_id, reads=reads)


# The checks by the names the command line and the API use.
CHECKS: dict[str, Callable[..., CheckSummary]] = {
    "all": run_checks,
    "text-vs-tape": check_text_vs_tape,
    "rollforward": check_rollforward,
}


# Reading flags.


@dataclass(frozen=True)
class FlagView:
    id: int
    deal: str
    loan: str
    period: Period | None
    check: str
    message: str
    sides: dict[str, Any]
    raised_at: dt.datetime

    @property
    def is_note(self) -> bool:
        return self.check in rules.NOTE_CHECKS


def flags(
    db: Session,
    *,
    reader: str,
    deal: str,
    period: Period | None = None,
    loan: str | None = None,
) -> list[FlagView]:
    """A deal's check flags and notes, optionally for one month or loan, oldest first."""
    authorize(db, reader, Action.READ_PANEL)
    deal_row = _deal(db, deal)
    query = select(CheckFlag).where(CheckFlag.deal_id == deal_row.id)
    if period is not None:
        query = query.where(CheckFlag.period == period.first_day)
    if loan is not None:
        query = query.where(CheckFlag.loan == loan)
    rows = db.scalars(query.order_by(CheckFlag.id)).all()
    return [
        FlagView(
            id=row.id,
            deal=deal_row.name,
            loan=row.loan,
            period=Period.of(row.period) if row.period is not None else None,
            check=row.check,
            message=row.message,
            sides=dict(row.sides),
            raised_at=row.raised_at,
        )
        for row in rows
    ]
