"""A deal's loans with their special-servicing state as known on a day (grill log Q96, Q99).
Read-only.

The roster is the deal's latest month as the panel showed it on the day
(`cmbs.panel.service.tape_as_of`, the same read as `GET /deals/{deal}/loans`), one row per loan,
each citing that month's ABS-EE and the tape fields it read. A loan's state is read from the
tape's dates, balance and realized loss only (`state_of`):

- *liquidated, realized loss $X* from the first month its ending balance is zero, after the
  last month it was above zero, when that month or one before it reports a realized loss above
  zero (Q99); *paid off* from that month when none does (a loan that has left the pool is not
  "with the special servicer", whatever its dates);
- *in special servicing* since the special-servicer transfer date, when no master-servicer
  return date is on or after it;
- *returned* on the master-servicer return date, when it is on or after the latest transfer
  (or no transfer date is reported: a return implies a transfer);
- otherwise *never transferred*.

No loan is silently missing (Q99). Besides the loans the tape reports, the roster lists each
loan the tape reports but the load quarantined ("quarantined: <reason>", citing the tape) and
each loan the panel holds from an earlier tape that the current one omits ("not on the
<month> tape; last reported <month>, balance $X", citing that last tape). Either kind also
gives the loan's last known state, when the panel holds a month of it, and the special-servicing
filter keeps it when that state is in special servicing.

The workout strategy code is shown beside the state and never decides it, so a code that
disagrees with the dates ("resolved" while still transferred) stays visible. The roster opens
with the panel's freshness (`cmbs.runs.service.freshness`); freshness, tape and history are
read in one snapshot (`cmbs.panel.service.snapshot`).
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.access.roles import Action
from cmbs.access.service import authorize
from cmbs.alerts.codes import WORKOUT_STRATEGY, lookup
from cmbs.ask.answer import TAPE_FORM, Citation
from cmbs.ask.service import UnknownDeal
from cmbs.deals.service import by_name
from cmbs.domain import Period, format_money
from cmbs.edgar.archives import filing_base_url
from cmbs.panel import service as panel
from cmbs.runs.service import freshness
from cmbs.tape.service import quarantined_loans
from cmbs.tape.tables import LoanPeriod, LoanTape


class State(StrEnum):
    SPECIAL_SERVICING = "in special servicing"
    RETURNED = "returned"
    LIQUIDATED = "liquidated"
    PAID_OFF = "paid off"
    NEVER_TRANSFERRED = "never transferred"


# The order rows are listed in: the loans with the special servicer first; a loan whose state
# the panel does not know (quarantined in every month it holds) last.
ORDER = tuple(State)
NOT_REPORTED = "not reported"
# The Schedule AL fields every row reads.
ROSTER_FIELDS = (
    "propertyName",
    "reportPeriodEndActualBalanceAmount",
    "mostRecentSpecialServicerTransferDate",
    "mostRecentMasterServicerReturnDate",
    "workoutStrategyCode",
    "realizedLossToTrustAmount",
)


def paid_off_since(balances: Sequence[tuple[Period, Decimal | None]]) -> Period | None:
    """The first month of the run of zero balances the loan ends on, from its months in order;
    None when its latest reported balance is not zero. A month that reports no balance is
    skipped: it says nothing either way."""
    since: Period | None = None
    for period, balance in balances:
        if balance is None:
            continue
        if balance > 0:
            since = None
        elif since is None:
            since = period
    return since


def loss_by(months: Sequence[LoanPeriod], since: Period) -> LoanPeriod | None:
    """The month reporting the loan's realized loss: the first zero month if it reports a loss
    above zero, else the latest month before it that does; None when none does. Months in
    order."""
    found = None
    for month in months:
        if Period.of(month.period) > since:
            break
        if month.realized_loss is not None and month.realized_loss > 0:
            found = month
    return found


def state_of(
    transfer: dt.date | None,
    returned: dt.date | None,
    paid_off: Period | None,
    loss: Decimal | None = None,
) -> tuple[State, str | None]:
    """The state and since when ('2025-03-06', or the month '2026-08' for a loan that left the
    pool: liquidated with a realized loss above zero, otherwise paid off)."""
    if paid_off is not None:
        state = State.LIQUIDATED if loss is not None and loss > 0 else State.PAID_OFF
        return state, f"{paid_off.year:04d}-{paid_off.month:02d}"
    if returned is not None and (transfer is None or returned >= transfer):
        return State.RETURNED, returned.isoformat()
    if transfer is not None:
        return State.SPECIAL_SERVICING, transfer.isoformat()
    return State.NEVER_TRANSFERRED, None


def workout_text(code: str | None) -> str | None:
    """'resolved (code 8)'; 'code 42' for a code not on the list; None when not reported."""
    if code is None:
        return None
    found = lookup(WORKOUT_STRATEGY, code)
    return f"{found.name} (code {code})" if found is not None else f"code {code}"


@dataclass(frozen=True)
class RosterLoan:
    """One loan of the roster. A loan the current tape reports has a `state`; one it omits or
    quarantined has a `note` saying so, and the state of the last month the panel holds of it,
    if any."""

    loan: str
    property_name: str | None
    # The current tape's balance; None for a loan it omits or that its load quarantined.
    ending_balance: str | None
    state: State | None
    since: str | None
    workout_strategy: str | None
    citation: Citation
    tape_fields: tuple[str, ...] = ROSTER_FIELDS
    # For a loan that left the pool: the tape of the first month its balance was zero.
    paid_off_citation: Citation | None = None
    # For a liquidated loan: the loss and the tape that reported it.
    realized_loss: str | None = None
    loss_citation: Citation | None = None
    # Why the current tape gives no row of the loan; None when it does.
    note: str | None = None

    def state_reading(self) -> str | None:
        """'in special servicing since 2024-02-01; tape workout strategy: resolved (code 8)',
        'liquidated, realized loss $13,049,503.19, since 2026-01'; None if the state is not
        known."""
        if self.state is None:
            return None
        text = self.state.value
        if self.realized_loss is not None:
            text += f", realized loss {self.realized_loss},"
        if self.since is not None:
            text += f" since {self.since}"
        if self.workout_strategy is not None:
            text += f"; tape workout strategy: {self.workout_strategy}"
        return text

    def reading(self) -> str:
        """The state as it reads; for a loan off the tape or quarantined, the note first and
        then its last known state."""
        state = self.state_reading()
        if self.note is None:
            return state or ""
        return self.note if state is None else f"{self.note}; last known: {state}"

    def text(self) -> str:
        name = self.property_name or "property name not reported"
        fields = f": {', '.join(self.tape_fields)}" if self.tape_fields else ""
        cited = f"{self.citation.form} {self.citation.accession}{fields}"
        for what, other in (
            ("first zero balance", self.paid_off_citation),
            ("realized loss", self.loss_citation),
        ):
            if other is not None:
                cited += f"; {what} in {other.form} {other.accession}"
        # A loan off the tape or quarantined has no balance on it: its note says why.
        head = f"loan {self.loan}, {name}"
        if self.ending_balance is not None:
            head += f", {self.ending_balance}:"
        else:
            head += ","
        return f"{head} {self.reading()} ({cited})"


@dataclass(frozen=True)
class Roster:
    deal: str
    as_of: dt.date
    freshness: str
    period: str | None
    accession: str | None
    special_servicing_only: bool
    loans: list[RosterLoan] = field(default_factory=list)

    def text(self) -> str:
        lines = [self.freshness]
        if self.period is None:
            return lines[0]
        which = "loans in special servicing" if self.special_servicing_only else "loans"
        lines.append(f"{self.deal}, {which} as of {self.as_of.isoformat()}, {self.period} tape:")
        lines += [f"- {loan.text()}" for loan in self.loans]
        if not self.loans:
            lines.append("- none")
        return "\n".join(lines)


def _citation(accession: str, filing_date: dt.date, cik: int) -> Citation:
    return Citation(
        form=TAPE_FORM,
        accession=accession,
        filing_date=filing_date,
        url=filing_base_url(cik, accession),
    )


def _cited(row: LoanPeriod | None, cik: int) -> Citation | None:
    return None if row is None else _citation(row.accession, row.filing_date, cik)


def _money(value: Decimal | None) -> str:
    return NOT_REPORTED if value is None else format_money(value)


def roster_loan(row: LoanPeriod, months: Sequence[LoanPeriod], cik: int) -> RosterLoan:
    """One loan's row from a month's tape row and its months up to that one, as known."""
    ordered = sorted(
        (month for month in months if month.period <= row.period), key=lambda m: m.period
    )
    since = paid_off_since([(Period.of(m.period), m.ending_balance) for m in ordered])
    lost = loss_by(ordered, since) if since is not None else None
    state, when = state_of(
        row.special_servicer_transfer_date,
        row.master_servicer_return_date,
        since,
        lost.realized_loss if lost is not None else None,
    )
    first_zero = next((m for m in ordered if since and Period.of(m.period) == since), None)
    return RosterLoan(
        loan=row.loan,
        property_name=row.property_name,
        ending_balance=_money(row.ending_balance),
        state=state,
        since=when,
        workout_strategy=workout_text(row.workout_strategy),
        citation=_citation(row.accession, row.filing_date, cik),
        paid_off_citation=_cited(first_zero, cik),
        realized_loss=_money(lost.realized_loss) if lost is not None else None,
        loss_citation=_cited(lost, cik),
    )


def off_tape(last: RosterLoan, row: LoanPeriod, tape_period: Period) -> RosterLoan:
    """A loan the panel holds from an earlier tape (`row`, its last) that the current omits."""
    note = (
        f"not on the {tape_period.label} tape; last reported {Period.of(row.period).label}, "
        f"balance {_money(row.ending_balance)}"
    )
    return RosterLoan(
        loan=last.loan,
        property_name=last.property_name,
        ending_balance=None,
        state=last.state,
        since=last.since,
        workout_strategy=last.workout_strategy,
        citation=last.citation,
        paid_off_citation=last.paid_off_citation,
        realized_loss=last.realized_loss,
        loss_citation=last.loss_citation,
        note=note,
    )


def quarantined(
    loan: str,
    reasons: Sequence[str],
    property_name: str | None,
    tape: Citation,
    last: RosterLoan | None,
) -> RosterLoan:
    """A loan the current tape reports but the load quarantined, with its last known state."""
    return RosterLoan(
        loan=loan,
        property_name=property_name or (last.property_name if last else None),
        ending_balance=None,
        state=last.state if last else None,
        since=last.since if last else None,
        workout_strategy=last.workout_strategy if last else None,
        citation=tape,
        tape_fields=(),
        realized_loss=last.realized_loss if last else None,
        note=f"quarantined: {'; '.join(reasons)}",
    )


def ordered(loans: Sequence[RosterLoan]) -> list[RosterLoan]:
    """The loans in special servicing first, then returned, liquidated, paid off, never
    transferred, and those whose state is not known; by loan number within each."""
    return sorted(
        loans,
        key=lambda loan: (
            ORDER.index(loan.state) if loan.state is not None else len(ORDER),
            panel.loan_order(loan.loan),
        ),
    )


def held_back(db: Session, accession: str, on_tape: set[str]) -> dict[str, list[str]]:
    """The loans the tape reports that its load quarantined, each with its distinct reasons."""
    found: dict[str, list[str]] = {}
    for item in quarantined_loans(db, accession):
        reasons = found.setdefault(item.loan, [])
        if item.loan not in on_tape and item.reason not in reasons:
            reasons.append(item.reason)
    return {loan: reasons for loan, reasons in found.items() if reasons}


def loans(
    db: Session,
    reader: str,
    *,
    deal: str,
    as_of: dt.date | None = None,
    special_servicing: bool = False,
) -> Roster:
    """The deal's loans and their state as known on `as_of` (today by default); with
    `special_servicing`, only the loans in special servicing (on the tape, or last known)."""
    panel.snapshot(db)
    authorize(db, reader, Action.ASK)
    day = as_of or clock.today()
    deal_row = by_name(db, deal)
    if deal_row is None:
        raise UnknownDeal(f"no deal named {deal!r}")
    fresh = freshness(db, deal_id=deal_row.id, deal=deal_row.name, day=day)
    try:
        tape = panel.tape_as_of(db, deal_row.name, day)
    except panel.NothingPublished:
        return Roster(deal_row.name, day, fresh.text(), None, None, special_servicing)
    cik = deal_row.cik or 0
    history: dict[str, list[LoanPeriod]] = {}
    for month in panel.as_of(db, day, deal_id=deal_row.id):
        history.setdefault(month.loan, []).append(month)
    for gone in tape.superseded:  # an earlier filing of this month reported it
        earlier = db.get(LoanPeriod, (gone.deal_id, gone.loan, gone.period, gone.accession))
        if earlier is not None:
            history.setdefault(gone.loan, []).append(earlier)
    rows = [roster_loan(row, history.get(row.loan, [row]), cik) for row in tape.rows]
    on_tape = {row.loan for row in tape.rows}

    def last_known(loan: str) -> tuple[RosterLoan, LoanPeriod] | None:
        months = sorted(history.get(loan, []), key=lambda m: (m.period, m.filing_date))
        if not months:
            return None
        return roster_loan(months[-1], months, cik), months[-1]

    filed = db.get_one(LoanTape, tape.accession)
    tape_citation = _citation(tape.accession, filed.filing_date, cik)
    kept = held_back(db, tape.accession, on_tape)
    names = panel.property_names(db, tape.accession, set(kept))
    for loan, reasons in kept.items():
        last = last_known(loan)
        rows.append(
            quarantined(loan, reasons, names.get(loan), tape_citation, last[0] if last else None)
        )
    for loan in history:
        last = last_known(loan)
        if loan not in on_tape and loan not in kept and last is not None:
            rows.append(off_tape(last[0], last[1], tape.period))
    if special_servicing:
        rows = [row for row in rows if row.state is State.SPECIAL_SERVICING]
    return Roster(
        deal=deal_row.name,
        as_of=day,
        freshness=fresh.text(),
        period=tape.period.label,
        accession=tape.accession,
        special_servicing_only=special_servicing,
        loans=ordered(rows),
    )
