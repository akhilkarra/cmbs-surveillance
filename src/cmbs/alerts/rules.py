"""The alert rules: pure functions from a loan's two months, as published, to what changed.

Each rule looks at one loan's row for the month before (`before`) and for the month alerted on
(`after`) and returns a `Finding`, or None when there is nothing to say. A value that is not
reported in either month is never compared: "not reported" is not a change.

high    transferred to special servicing: a transfer date appears, or moves later
medium  payment status worsened: the status ranks further behind (`codes.PAYMENT_STATUS`)
medium  payment status changed: to a code of equal rank, or a code not on the Schedule AL list
medium  paid-through date did not advance: the date stayed, the loan is behind for the month
        and still has a balance
medium  DSCR fell below <threshold>: at or above the threshold last month, below it now
medium  workout strategy changed: the workout strategy code changed, started or ended
low     servicer comment changed: the 10-D comment has sentences it did not have before
medium  no longer reported: loans on last month's tape, not on this one, not having paid off
low     newly reported: loans on this tape, not on last month's

A loan that appears or disappears between two tapes of a static CMBS pool is itself worth a
look: a loan dropped without first paying off may hide a liquidation or a reporting error, and
a loan that appears was missing before. These two rules read the deal's whole month, not one
loan: a tape that drops loans usually drops many at once (BANK 2018-BNK13's September 2026 tape
omits its residential co-ops, docs/MESS-REGISTER.md M-022), so each gives one deal-level
finding listing every loan, not one finding per loan.
"""

import datetime as dt
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from cmbs.alerts.codes import PAYMENT_STATUS, WORKOUT_STRATEGY, lookup, name_of, same_code
from cmbs.domain import Period
from cmbs.extraction.sentences import split
from cmbs.panel.service import loan_order
from cmbs.settings.values import format_field

TWO_PLACES = Decimal("0.01")


class Severity(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# Most severe first.
SEVERITY_ORDER: dict[Severity, int] = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}


class Rule(StrEnum):
    """Stable keys: an alert is stored once per subject (a loan, or the deal for the loans no
    longer or newly reported), month and rule."""

    TRANSFER = "special servicing transfer"
    PAYMENT_WORSENED = "payment status worsened"
    PAYMENT_CHANGED = "payment status changed"
    PAID_THROUGH = "paid-through date"
    DSCR = "DSCR below threshold"
    WORKOUT = "workout strategy"
    COMMENT = "servicer comment"
    MISSING = "no longer reported"
    NEW = "newly reported"


@dataclass(frozen=True)
class LoanMonth:
    """What the rules read of one loan's row for one month, and the filing it came from."""

    loan: str
    accession: str
    ending_balance: Decimal | None = None
    paid_through: dt.date | None = None
    payment_status: str | None = None
    transfer_date: dt.date | None = None
    workout_strategy: str | None = None
    dscr: Decimal | None = None


@dataclass(frozen=True)
class Comments:
    """The special-servicing comments of one month's 10-D, by loan, and its accession."""

    accession: str
    by_loan: Mapping[str, str]


@dataclass(frozen=True)
class Month:
    """One month of a deal as published: the tape's loans, quarantined loans and the 10-D."""

    period: Period
    accession: str
    loans: Mapping[str, LoanMonth]
    quarantined: Mapping[str, str] = field(default_factory=dict)
    comments: Comments | None = None


@dataclass(frozen=True)
class Finding:
    """One change worth an alert. `accession` is the filing that shows it.

    A loan's finding names its `loan`. A deal-level finding (loans no longer or newly reported)
    has no `loan`: it lists its `loans` in loan order, with their total `balance` on the tape
    that reported them (None when none of them reported one) and, for a loan the new tape
    quarantined, the reason (`reasons`, in loan order). The loans and reasons are what the
    finding means: the same ones found again are the same alert.
    """

    loan: str | None
    rule: Rule
    severity: Severity
    title: str
    message: str
    accession: str
    added: tuple[str, ...] = ()
    loans: tuple[str, ...] = ()
    balance: Decimal | None = None
    reasons: tuple[tuple[str, str], ...] = ()


# ---------------------------------------------------------------------------- formatting


def format_dscr(value: Decimal) -> str:
    """Two places: Decimal('0.955') -> '0.96x'."""
    return f"{value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)}x"


def format_threshold(value: Decimal) -> str:
    """As the settings ledger shows it: '1.00x', or '1.125x' when set to three places."""
    return format_field("dscr_alert_threshold", value)


# ---------------------------------------------------------------------------- loan rules


def transfer(before: LoanMonth, after: LoanMonth) -> Finding | None:
    """A transfer date that appears, or moves later: the loan went (back) to special servicing."""
    new = after.transfer_date
    if new is None or (before.transfer_date is not None and new <= before.transfer_date):
        return None
    title = "transferred to special servicing"
    return Finding(
        loan=after.loan,
        rule=Rule.TRANSFER,
        severity=Severity.HIGH,
        title=title,
        message=f"{title} on {new.isoformat()}",
        accession=after.accession,
    )


def payment_status(before: LoanMonth, after: LoanMonth) -> Finding | None:
    """A status further behind; or any change when a code is not on the Schedule AL list."""
    if before.payment_status is None or after.payment_status is None:
        return None
    if same_code(before.payment_status, after.payment_status):
        return None
    old = lookup(PAYMENT_STATUS, before.payment_status)
    new = lookup(PAYMENT_STATUS, after.payment_status)
    names = (
        f"{name_of(PAYMENT_STATUS, before.payment_status)} to "
        f"{name_of(PAYMENT_STATUS, after.payment_status)}"
    )
    if old is not None and new is not None and new.rank < old.rank:
        return None
    if old is not None and new is not None and new.rank > old.rank:
        rule, title = Rule.PAYMENT_WORSENED, "payment status worsened"
    else:  # equal rank (30 days and a performing matured balloon), or a code not on the list
        rule, title = Rule.PAYMENT_CHANGED, "payment status changed"
    return Finding(
        loan=after.loan,
        rule=rule,
        severity=Severity.MEDIUM,
        title=title,
        message=f"{title}: {names}",
        accession=after.accession,
    )


def paid_through(before: LoanMonth, after: LoanMonth, period: Period) -> Finding | None:
    """The paid-through date did not move on although the loan is behind for the month.

    A loan paid through the month's due date or later is not behind, whether or not the date
    moved; a loan with no balance left has nothing to pay.
    """
    old, new = before.paid_through, after.paid_through
    if old is None or new is None or new > old or new >= period.first_day:
        return None
    if after.ending_balance is not None and after.ending_balance == 0:
        return None
    title = "paid-through date did not advance"
    moved = f"still {new.isoformat()}" if new == old else f"{old.isoformat()} to {new.isoformat()}"
    return Finding(
        loan=after.loan,
        rule=Rule.PAID_THROUGH,
        severity=Severity.MEDIUM,
        title=title,
        message=f"{title}: {moved}",
        accession=after.accession,
    )


def dscr(before: LoanMonth, after: LoanMonth, threshold: Decimal) -> Finding | None:
    """DSCR at or above the threshold last month and below it now."""
    old, new = before.dscr, after.dscr
    if old is None or new is None or old < threshold or new >= threshold:
        return None
    title = f"DSCR fell below {format_threshold(threshold)}"
    # Two places, unless rounding would show the new value at or above the threshold it fell
    # below (0.996x is not "1.00x"): then every digit reported.
    shown = format_dscr(new)
    if new.quantize(TWO_PLACES, rounding=ROUND_HALF_UP) >= threshold:
        shown = f"{new}x"
    return Finding(
        loan=after.loan,
        rule=Rule.DSCR,
        severity=Severity.MEDIUM,
        title=title,
        message=f"{title}: {format_dscr(old)} to {shown}",
        accession=after.accession,
    )


def workout(before: LoanMonth, after: LoanMonth) -> Finding | None:
    """Any change of workout strategy, including one starting or ending."""
    if same_code(before.workout_strategy, after.workout_strategy):
        return None
    title = "workout strategy changed"
    names = (
        f"{name_of(WORKOUT_STRATEGY, before.workout_strategy)} to "
        f"{name_of(WORKOUT_STRATEGY, after.workout_strategy)}"
    )
    return Finding(
        loan=after.loan,
        rule=Rule.WORKOUT,
        severity=Severity.MEDIUM,
        title=title,
        message=f"{title}: {names}",
        accession=after.accession,
    )


@dataclass(frozen=True)
class Context:
    """What the rules need beyond the two rows: the month alerted on and the settings."""

    period: Period
    dscr_threshold: Decimal


type LoanRule = Callable[[LoanMonth, LoanMonth, Context], Finding | None]

LOAN_RULES: tuple[LoanRule, ...] = (
    lambda before, after, _: transfer(before, after),
    lambda before, after, _: payment_status(before, after),
    lambda before, after, context: paid_through(before, after, context.period),
    lambda before, after, context: dscr(before, after, context.dscr_threshold),
    lambda before, after, _: workout(before, after),
)


# ---------------------------------------------------------------------------- comments


def sentences(text: str) -> tuple[str, ...]:
    """The comment's sentences in order, whitespace normalized, split as extraction splits them
    (`cmbs.extraction.sentences`), so an alert and an extraction agree on what a sentence is."""
    return tuple(sentence.text for sentence in split(text))


def added_sentences(before: str | None, after: str) -> tuple[str, ...]:
    """The sentences of `after` that `before` did not have, in order, each once."""
    known = set(sentences(before or ""))
    added: list[str] = []
    for sentence in sentences(after):
        if sentence not in known and sentence not in added:
            added.append(sentence)
    return tuple(added)


def comment(loan: str, before: Comments, after: Comments) -> Finding | None:
    """New sentences in the loan's comment. Sentences dropped are not news; none are shown."""
    text = after.by_loan.get(loan)
    if text is None:
        return None
    added = added_sentences(before.by_loan.get(loan), text)
    if not added:
        return None
    title = "servicer comment changed"
    count = f"{len(added)} sentence added" if len(added) == 1 else f"{len(added)} sentences added"
    return Finding(
        loan=loan,
        rule=Rule.COMMENT,
        severity=Severity.LOW,
        title=title,
        message=f"{title}: {count}",
        accession=after.accession,
        added=added,
    )


# ---------------------------------------------------------------------------- the month


def count_loans(count: int) -> str:
    """'1 loan', '19 loans'."""
    return f"{count} loan" if count == 1 else f"{count} loans"


def name_loans(loans: Sequence[str]) -> str:
    """'loan 20', 'loans 20, 26, 29'."""
    return f"loan {loans[0]}" if len(loans) == 1 else f"loans {', '.join(loans)}"


def total_balance(rows: Iterable[LoanMonth]) -> Decimal | None:
    """The rows' ending balances summed; None when none of them reports one."""
    reported = [row.ending_balance for row in rows if row.ending_balance is not None]
    return sum(reported, Decimal("0.00")) if reported else None


def unreported_balances(rows: Sequence[LoanMonth]) -> str:
    """'; balance not reported for loan 7' (or 'loans 5, 7') when the total leaves some out."""
    unreported = [row.loan for row in rows if row.ending_balance is None]
    return f"; balance not reported for {name_loans(unreported)}" if unreported else ""


def _paid_off(row: LoanMonth) -> bool:
    return row.ending_balance is not None and row.ending_balance == 0


def _in_loan_order(rows: Iterable[LoanMonth]) -> list[LoanMonth]:
    return sorted(rows, key=lambda row: loan_order(row.loan))


def missing(before: Month, after: Month) -> Finding | None:
    """The loans last month's tape showed and this month's does not, except those that had paid
    off: one finding for the deal listing every loan in loan order, and for a loan quarantined
    on the new tape the reason. Its balance is their total on last month's tape; the message
    names any loan whose balance that total leaves out."""
    gone = _in_loan_order(
        row for loan, row in before.loans.items() if loan not in after.loans and not _paid_off(row)
    )
    if not gone:
        return None
    loans = tuple(row.loan for row in gone)
    reasons = tuple((loan, after.quarantined[loan]) for loan in loans if loan in after.quarantined)
    title = "no longer reported"
    why = "".join(f"; loan {loan} quarantined: {reason}" for loan, reason in reasons)
    return Finding(
        loan=None,
        rule=Rule.MISSING,
        severity=Severity.MEDIUM,
        title=title,
        message=(
            f"{count_loans(len(loans))} {title}: on the {before.period.label} tape, "
            f"not on the {after.period.label} tape ({name_loans(loans)}){why}"
            f"{unreported_balances(gone)}"
        ),
        accession=after.accession,
        loans=loans,
        balance=total_balance(gone),
        reasons=reasons,
    )


def new_loans(before: Month, after: Month) -> Finding | None:
    """The loans this month's tape shows and last month's did not: one finding for the deal
    listing every loan in loan order. Its balance is their total on this month's tape; the
    message names any loan whose balance that total leaves out."""
    found = _in_loan_order(row for loan, row in after.loans.items() if loan not in before.loans)
    if not found:
        return None
    loans = tuple(row.loan for row in found)
    title = "newly reported"
    return Finding(
        loan=None,
        rule=Rule.NEW,
        severity=Severity.LOW,
        title=title,
        message=(
            f"{count_loans(len(loans))} {title}: not on the {before.period.label} tape "
            f"({name_loans(loans)}){unreported_balances(found)}"
        ),
        accession=after.accession,
        loans=loans,
        balance=total_balance(found),
    )


def compare(before: Month, after: Month, context: Context) -> list[Finding]:
    """Every finding for the month: each loan's, by loan ('2' before '10') then in rule order,
    then the deal's loans no longer reported and its loans newly reported."""
    findings: list[Finding] = []
    loans = sorted(before.loans.keys() | after.loans.keys(), key=loan_order)
    for loan in loans:
        old, new = before.loans.get(loan), after.loans.get(loan)
        if old is not None and new is not None:
            findings.extend(_some(rule(old, new, context) for rule in LOAN_RULES))
        if before.comments is not None and after.comments is not None:
            findings.extend(_some([comment(loan, before.comments, after.comments)]))
    findings.extend(_some([missing(before, after), new_loans(before, after)]))
    return findings


def _some(found: Iterable[Finding | None]) -> list[Finding]:
    return [finding for finding in found if finding is not None]
