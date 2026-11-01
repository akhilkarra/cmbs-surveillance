"""A deal's loans with their special-servicing state as known on a day (grill log Q96). Read-only.

The roster is the deal's latest month as the panel showed it on the day
(`cmbs.panel.service.tape_as_of`, the same read as `GET /deals/{deal}/loans`), one row per loan,
each citing that month's ABS-EE and the tape fields it read. A loan's state is read from the
tape's dates and balance only (`state_of`):

- *paid off* from the first month its ending balance is zero, after the last month it was above
  zero (a loan that has left the pool is not "with the special servicer", whatever its dates);
- *in special servicing* since the special-servicer transfer date, when no master-servicer
  return date is on or after it;
- *returned* on the master-servicer return date, when it is on or after the latest transfer
  (or no transfer date is reported: a return implies a transfer);
- otherwise *never transferred*.

The workout strategy code is shown beside the state and never decides it, so a code that
disagrees with the dates ("resolved" while still transferred) stays visible. The roster opens
with the panel's freshness (`cmbs.runs.service.freshness`).
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
from cmbs.tape.tables import LoanPeriod


class State(StrEnum):
    SPECIAL_SERVICING = "in special servicing"
    RETURNED = "returned"
    PAID_OFF = "paid off"
    NEVER_TRANSFERRED = "never transferred"


# The order rows are listed in: the loans with the special servicer first.
ORDER = tuple(State)
NOT_REPORTED = "not reported"
# The Schedule AL fields every row reads.
ROSTER_FIELDS = (
    "propertyName",
    "reportPeriodEndActualBalanceAmount",
    "mostRecentSpecialServicerTransferDate",
    "mostRecentMasterServicerReturnDate",
    "workoutStrategyCode",
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


def state_of(
    transfer: dt.date | None, returned: dt.date | None, paid_off: Period | None
) -> tuple[State, str | None]:
    """The state and since when ('2025-03-06', or the month '2026-08' for a paid-off loan)."""
    if paid_off is not None:
        return State.PAID_OFF, f"{paid_off.year:04d}-{paid_off.month:02d}"
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
    loan: str
    property_name: str | None
    ending_balance: str
    state: State
    since: str | None
    workout_strategy: str | None
    citation: Citation
    tape_fields: tuple[str, ...] = ROSTER_FIELDS
    # For a paid-off loan: the tape of the first month its balance was zero.
    paid_off_citation: Citation | None = None

    def reading(self) -> str:
        """'in special servicing since 2024-02-01; tape workout strategy: resolved (code 8)'."""
        text = self.state.value if self.since is None else f"{self.state} since {self.since}"
        if self.workout_strategy is not None:
            text += f"; tape workout strategy: {self.workout_strategy}"
        return text

    def text(self) -> str:
        name = self.property_name or "property name not reported"
        cited = f"{self.citation.form} {self.citation.accession}: {', '.join(self.tape_fields)}"
        if self.paid_off_citation is not None:
            first = self.paid_off_citation
            cited += f"; first zero balance in {first.form} {first.accession}"
        return f"loan {self.loan}, {name}, {self.ending_balance}: {self.reading()} ({cited})"


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


def _citation(row: LoanPeriod, cik: int) -> Citation:
    return Citation(
        form=TAPE_FORM,
        accession=row.accession,
        filing_date=row.filing_date,
        url=filing_base_url(cik, row.accession),
    )


def roster_loan(row: LoanPeriod, months: Sequence[LoanPeriod], cik: int) -> RosterLoan:
    """One loan's row from its latest month's tape row and all its months as known."""
    ordered = sorted(months, key=lambda month: month.period)
    since = paid_off_since([(Period.of(m.period), m.ending_balance) for m in ordered])
    state, when = state_of(
        row.special_servicer_transfer_date, row.master_servicer_return_date, since
    )
    first_zero = next((m for m in ordered if since and Period.of(m.period) == since), None)
    return RosterLoan(
        loan=row.loan,
        property_name=row.property_name,
        ending_balance=NOT_REPORTED
        if row.ending_balance is None
        else format_money(row.ending_balance),
        state=state,
        since=when,
        workout_strategy=workout_text(row.workout_strategy),
        citation=_citation(row, cik),
        paid_off_citation=_citation(first_zero, cik) if first_zero is not None else None,
    )


def ordered(loans: Sequence[RosterLoan]) -> list[RosterLoan]:
    """The loans in special servicing first, then returned, paid off and never transferred;
    by loan number within each."""
    return sorted(loans, key=lambda loan: (ORDER.index(loan.state), panel.loan_order(loan.loan)))


def loans(
    db: Session,
    reader: str,
    *,
    deal: str,
    as_of: dt.date | None = None,
    special_servicing: bool = False,
) -> Roster:
    """The deal's loans and their state as known on `as_of` (today by default); with
    `special_servicing`, only the loans in special servicing."""
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
    history: dict[str, list[LoanPeriod]] = {}
    for month in panel.as_of(db, day, deal_id=deal_row.id):
        history.setdefault(month.loan, []).append(month)
    cik = deal_row.cik or 0
    rows = [roster_loan(row, history.get(row.loan, [row]), cik) for row in tape.rows]
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
