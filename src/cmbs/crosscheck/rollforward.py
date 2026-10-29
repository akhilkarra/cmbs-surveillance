"""The balance roll-forward: does a loan's tape explain its own balance? Pure logic.

Within a month, to the cent (CREFC's loan-level identity, which Schedule AL reports):

    beginning scheduled balance - scheduled principal - unscheduled principal
        - other principal adjustments - realized loss = ending scheduled balance

and across months, this month's beginning balance equals last month's ending balance.

**Which ending balance.** The identity holds for the *scheduled* balance: a delinquent loan's
actual balance does not fall by the scheduled principal it failed to pay, so comparing it with
the scheduled roll would break on every delinquent amortizing loan. The check uses the ending
scheduled balance when the tape reports it, and otherwise the actual ending balance the panel
holds, saying which (`basis`). A reported value that is not an amount is never guessed at: the
check is not run.

**Missing amounts are "not reported", never zero.**
- Without a beginning or an ending balance there is nothing to roll: "not checked".
- A missing deduction (filers leave realized loss and other adjustments out when there are
  none) is not taken as zero. The identity is evaluated on the amounts reported and the
  missing ones are listed with the result. If the reported amounts already roll to the
  ending balance, the check passes on them ("passed", with what was not reported); if they do
  not, it is a break, because the tape as filed does not explain the balance, whether a
  missing amount or a wrong one is the cause. The break lists the missing amounts as places
  to look.

The gap is the balance the components explain minus the balance reported: a positive gap means
the reported balance is lower than the components account for.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from cmbs.domain import Period, format_money
from cmbs.tape.convert import Unreadable, read_money

ROLLFORWARD = "balance_rollforward"
CONTINUITY = "balance_continuity"
ROLL_BREAK = "balance does not roll forward"
CONTINUITY_BREAK = "beginning balance does not match last month's ending balance"

SCHEDULED = "scheduled"
ENDING_SCHEDULED = "ending scheduled balance"
# The Schedule AL tag of the ending scheduled balance, read from the tape as stored in bronze.
ENDING_SCHEDULED_TAG = "reportPeriodEndScheduledLoanBalanceAmount"
ACTUAL = "actual"
# The deductions from the beginning balance, in the order Schedule AL lists them.
DEDUCTIONS = (
    ("scheduled_principal", "scheduled principal"),
    ("unscheduled_principal", "unscheduled principal"),
    ("other_principal_adjustments", "other principal adjustments"),
    ("realized_loss", "realized loss"),
)


class Status(StrEnum):
    PASSED = "passed"
    BREAK = "break"
    NOT_CHECKED = "not checked"


@dataclass(frozen=True)
class Ending:
    """The ending balance the roll is compared with, and which balance it is."""

    amount: Decimal | None
    basis: str
    problem: str | None = None


@dataclass(frozen=True)
class Balances:
    """One loan's month as its tape reports it."""

    beginning: Decimal | None
    scheduled_principal: Decimal | None
    unscheduled_principal: Decimal | None
    other_principal_adjustments: Decimal | None
    realized_loss: Decimal | None
    ending: Ending


@dataclass(frozen=True)
class Roll:
    status: Status
    expected_ending: Decimal | None = None
    gap: Decimal | None = None
    not_reported: tuple[str, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class Continuity:
    status: Status
    previous_ending: Decimal | None = None
    gap: Decimal | None = None
    reason: str | None = None


def ending_of(scheduled: str | None, actual: Decimal | None) -> Ending:
    """The ending scheduled balance as reported (the tag's text, read as the tape loader reads
    amounts), else the actual balance the panel holds."""
    try:
        amount = read_money((scheduled or "").strip(), ENDING_SCHEDULED)
    except Unreadable as unreadable:
        return Ending(None, SCHEDULED, str(unreadable))
    return Ending(actual, ACTUAL) if amount is None else Ending(amount, SCHEDULED)


def _missing(names: list[str]) -> str:
    """['beginning balance'] -> 'beginning balance not reported'."""
    return " and ".join(names) + " not reported"


def roll(balances: Balances) -> Roll:
    ending = balances.ending
    if ending.problem is not None:
        return Roll(Status.NOT_CHECKED, reason=ending.problem)
    if balances.beginning is None or ending.amount is None:
        absent = [
            label
            for label, amount in (
                ("beginning balance", balances.beginning),
                ("ending balance", ending.amount),
            )
            if amount is None
        ]
        return Roll(Status.NOT_CHECKED, reason=_missing(absent))
    reported: dict[str, Decimal | None] = {name: getattr(balances, name) for name, _ in DEDUCTIONS}
    not_reported = tuple(label for name, label in DEDUCTIONS if reported[name] is None)
    expected = balances.beginning
    for amount in reported.values():
        if amount is not None:
            expected -= amount
    gap = expected - ending.amount
    status = Status.PASSED if gap == 0 else Status.BREAK
    return Roll(status, expected_ending=expected, gap=gap, not_reported=not_reported)


def continuity(
    beginning: Decimal | None, previous: Ending | None, previous_period: Period
) -> Continuity:
    """This month's beginning balance against last month's ending balance (`previous`, None
    when last month's tape does not show the loan)."""
    if previous is None:
        return Continuity(
            Status.NOT_CHECKED, reason=f"no {previous_period.label} tape row for the loan"
        )
    if previous.problem is not None:
        return Continuity(Status.NOT_CHECKED, reason=f"{previous_period.label}: {previous.problem}")
    if beginning is None:
        return Continuity(Status.NOT_CHECKED, reason=_missing(["beginning balance"]))
    if previous.amount is None:
        return Continuity(
            Status.NOT_CHECKED,
            reason=f"{previous_period.label} {_missing(['ending balance'])}",
        )
    gap = previous.amount - beginning
    status = Status.PASSED if gap == 0 else Status.BREAK
    return Continuity(status, previous_ending=previous.amount, gap=gap)


@dataclass(frozen=True)
class LoanRollForward:
    """One loan's roll-forward for one month, with what it was read from."""

    loan: str
    period: Period
    accession: str
    filed: dt.date
    balances: Balances
    roll: Roll
    continuity: Continuity
    previous_accession: str | None = None

    @property
    def status(self) -> Status:
        """A break in either check is a break; otherwise passed if the roll passed."""
        if Status.BREAK in (self.roll.status, self.continuity.status):
            return Status.BREAK
        return self.roll.status

    @property
    def label(self) -> str:
        """'roll-forward passed', 'roll-forward break' or 'roll-forward not checked'."""
        return f"roll-forward {self.status.value}"


def _money(amount: Decimal | None) -> str:
    return "not reported" if amount is None else format_money(amount)


def roll_sides(result: LoanRollForward) -> dict[str, object]:
    """Both sides of a roll-forward break: what the components explain and what was reported."""
    balances = result.balances
    deductions = {label: _money(getattr(balances, name)) for name, label in DEDUCTIONS}
    return {
        "components": {"beginning balance": _money(balances.beginning), **deductions},
        "explained ending balance": _money(result.roll.expected_ending),
        "reported ending balance": _money(balances.ending.amount),
        "ending basis": balances.ending.basis,
        "gap": _money(result.roll.gap),
        "not reported": list(result.roll.not_reported),
        "accession": result.accession,
        "filed": result.filed.isoformat(),
    }


def continuity_sides(result: LoanRollForward) -> dict[str, object]:
    previous = result.period.shift(-1)
    return {
        "beginning balance": _money(result.balances.beginning),
        "accession": result.accession,
        f"{previous.label} ending balance": _money(result.continuity.previous_ending),
        "previous accession": result.previous_accession,
        "gap": _money(result.continuity.gap),
    }
