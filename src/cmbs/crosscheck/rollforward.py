"""The balance roll-forward: does a loan's tape explain its own balance? Pure logic.

Within a month, to the cent, on the scheduled balance (the loan-level identity Schedule AL's
balance fields report):

    beginning scheduled balance - scheduled principal - unscheduled principal
        - other principal adjustments = ending scheduled balance

and across months, this month's beginning scheduled balance equals last month's ending
scheduled balance.

**Scheduled balances only.** A delinquent loan's actual balance does not fall by the scheduled
principal it failed to pay, so the actual balance would break the identity on every delinquent
amortizing loan. There is no fallback to it: when the tape does not report the ending scheduled
balance (`reportPeriodEndScheduledLoanBalanceAmount`, read from the tape as stored in bronze),
the loan is "not checked: scheduled ending balance not reported". Continuity compares scheduled
with scheduled, and is not checked when either month lacks it. A reported value that is not an
amount is never guessed at: the check is not run.

**Realized loss is not principal.** A month that reports a nonzero realized loss is not
checked ("a realized-loss month: the loss includes more than principal"): the loss covers
liquidations and trailing loss adjustments (expenses, accrued interest, advances), so the
balance cannot be rolled from the principal fields alone. Such a month is never a break.

**Other principal adjustments** are taken as reported, a positive amount reducing the balance.
That sign follows the field's name and Schedule AL's description; it is not yet verified
against filed tapes with a nonzero adjustment.

**Missing amounts are "not reported", never zero.**
- Without a beginning or an ending scheduled balance there is nothing to roll: "not checked".
- A missing deduction (filers leave other adjustments out when there are none) is not taken as
  zero. The identity is evaluated on the amounts reported and the missing ones are listed with
  the result. If the reported amounts already roll to the ending balance, the check passes on
  them ("passed", with what was not reported); if they do not, it is a break, because the tape
  as filed does not explain the balance, whether a missing amount or a wrong one is the cause.
  The break lists the missing amounts as places to look.

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
REALIZED_LOSS_MONTH = "a realized-loss month: the loss includes more than principal"

ENDING_SCHEDULED = "scheduled ending balance"
# The Schedule AL tag of the ending scheduled balance, read from the tape as stored in bronze.
ENDING_SCHEDULED_TAG = "reportPeriodEndScheduledLoanBalanceAmount"
# The principal deductions from the beginning balance, in the order Schedule AL lists them.
DEDUCTIONS = (
    ("scheduled_principal", "scheduled principal"),
    ("unscheduled_principal", "unscheduled principal"),
    ("other_principal_adjustments", "other principal adjustments"),
)


class Status(StrEnum):
    PASSED = "passed"
    BREAK = "break"
    NOT_CHECKED = "not checked"


@dataclass(frozen=True)
class Ending:
    """A month's ending scheduled balance as reported, or why it cannot be read."""

    amount: Decimal | None
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


def ending_of(scheduled: str | None) -> Ending:
    """The ending scheduled balance from its tag's text, read as the tape loader reads amounts.
    Empty or missing is not reported."""
    try:
        return Ending(read_money((scheduled or "").strip(), ENDING_SCHEDULED))
    except Unreadable as unreadable:
        return Ending(None, str(unreadable))


def _missing(names: list[str]) -> str:
    """['beginning balance'] -> 'beginning balance not reported'."""
    return " and ".join(names) + " not reported"


def roll(balances: Balances) -> Roll:
    ending = balances.ending
    if ending.problem is not None:
        return Roll(Status.NOT_CHECKED, reason=ending.problem)
    if balances.realized_loss is not None and balances.realized_loss != 0:
        return Roll(Status.NOT_CHECKED, reason=REALIZED_LOSS_MONTH)
    if balances.beginning is None or ending.amount is None:
        absent = [
            label
            for label, amount in (
                ("beginning balance", balances.beginning),
                (ENDING_SCHEDULED, ending.amount),
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
    """This month's beginning scheduled balance against last month's ending scheduled balance
    (`previous`, None when last month's tape does not show the loan)."""
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
            reason=f"{previous_period.label} {_missing([ENDING_SCHEDULED])}",
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
        "reported scheduled ending balance": _money(balances.ending.amount),
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
        f"{previous.label} scheduled ending balance": _money(result.continuity.previous_ending),
        "previous accession": result.previous_accession,
        "gap": _money(result.continuity.gap),
    }
