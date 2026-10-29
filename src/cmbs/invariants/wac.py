"""The weighted-average coupon of a pool. Pure logic, exact arithmetic.

    WAC = Σ (ending balance × interest rate) / Σ ending balance

over the loans that report a positive ending balance and a usable interest rate (Schedule AL's
`reportPeriodInterestRatePercentage`, a fraction: 0.03768 is 3.768%): one from 0 to
`RATE_LIMIT` (0.25).

**Exact.** Balances and rates are Decimals as reported; the sums and the quotient are taken as
fractions, so the WAC is exact (5.025% is 5.025%, not 5.02499999...). `format_rate` prints it
for people: exactly when the fraction has a finite decimal expansion, otherwise rounded to six
decimal places of a percent, which the text says.

**Not reported is never zero.** A loan with a positive balance and no rate is left out of the
average and listed, and so is a loan whose balance is not reported; neither is averaged in as a
zero. A loan with a zero balance (paid off, or a balance not yet funded) has no weight and is
left out as well.

**A rate the tape loader could not use is listed, not averaged.** A rate that is not a number is
stored as null with a note (`interest_rate_note`), and the loan is listed as "rate not
readable"; a rate outside 0 to 0.25 (a percent written for a fraction, M-028) is stored as
reported and listed as "rate out of range".

**A reported zero rate counts as zero.** Real tapes write `0.00` on loans that no longer accrue:
BANK 2018-BNK13's September 2026 tape reports 0.00 only for its six paid-off loans, which report
no ending balance and so never enter the average. A loan with a positive balance and a reported
0.00 rate is averaged in at zero: a defeased loan still pays its note rate from the defeasance
collateral, so its tape rate is the note rate, not zero, and a zero on an outstanding loan is
what the servicer reported. `zero_rate` lists such loans so a reader can see them.

**Not available** when no loan has a positive balance (every balance is zero or not reported),
or when no loan with a positive balance has a usable rate.

`lean/Invariants/Wac.lean` proves, for every pool, which loans are averaged and that the WAC lies
between the lowest and the highest rate of the loans it averages.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from fractions import Fraction

from cmbs.tape.convert import RATE_LIMIT

NO_BALANCE = "no loan has an outstanding balance"
NO_RATE = "no loan with an outstanding balance has a usable interest rate"
ZERO = Decimal("0.00")
# Places of a percent shown when a WAC has no finite decimal expansion.
SHOWN_PLACES = 6


@dataclass(frozen=True)
class RatedLoan:
    loan: str
    balance: Decimal | None
    rate: Decimal | None
    # The tape loader's note on the rate (cmbs.tape.convert.read_rate), when it has one.
    rate_note: str | None = None


@dataclass(frozen=True)
class Wac:
    """The WAC (None when not available, with the reason) and what it was computed from."""

    rate: Fraction | None
    reason: str | None = None
    lowest: Decimal | None = None
    highest: Decimal | None = None
    averaged: tuple[str, ...] = ()
    balance: Decimal = ZERO
    rate_not_reported: tuple[str, ...] = ()
    rate_not_readable: tuple[str, ...] = ()
    rate_out_of_range: tuple[str, ...] = ()
    balance_not_reported: tuple[str, ...] = ()
    zero_rate: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return self.rate is not None


def wac(loans: Iterable[RatedLoan]) -> Wac:
    averaged: list[tuple[str, Decimal, Decimal]] = []
    left_out: dict[str, list[str]] = {
        "rate_not_reported": [],
        "rate_not_readable": [],
        "rate_out_of_range": [],
        "balance_not_reported": [],
    }
    for loan in loans:
        if loan.balance is None:
            left_out["balance_not_reported"].append(loan.loan)
        elif loan.balance <= 0:
            continue
        elif loan.rate is None:
            why = "rate_not_reported" if loan.rate_note is None else "rate_not_readable"
            left_out[why].append(loan.loan)
        elif not 0 <= loan.rate <= RATE_LIMIT:
            left_out["rate_out_of_range"].append(loan.loan)
        else:
            averaged.append((loan.loan, loan.balance, loan.rate))
    listed = {name: tuple(loans) for name, loans in left_out.items()}
    if not averaged:
        no_rate = any(listed[name] for name in listed if name.startswith("rate_"))
        return Wac(
            None,
            NO_RATE if no_rate else NO_BALANCE,
            rate_not_reported=listed["rate_not_reported"],
            rate_not_readable=listed["rate_not_readable"],
            rate_out_of_range=listed["rate_out_of_range"],
            balance_not_reported=listed["balance_not_reported"],
        )
    weighted = sum(
        (Fraction(balance) * Fraction(rate) for _, balance, rate in averaged), Fraction()
    )
    balance = sum((balance for _, balance, _ in averaged), ZERO)
    rates = [rate for _, _, rate in averaged]
    return Wac(
        weighted / Fraction(balance),
        lowest=min(rates),
        highest=max(rates),
        averaged=tuple(loan for loan, _, _ in averaged),
        balance=balance,
        rate_not_reported=listed["rate_not_reported"],
        rate_not_readable=listed["rate_not_readable"],
        rate_out_of_range=listed["rate_out_of_range"],
        balance_not_reported=listed["balance_not_reported"],
        zero_rate=tuple(loan for loan, _, rate in averaged if rate == 0),
    )


def _terminates(value: Fraction) -> bool:
    """Whether the fraction has a finite decimal expansion: its denominator is 2^a × 5^b."""
    denominator = value.denominator
    for prime in (2, 5):
        while denominator % prime == 0:
            denominator //= prime
    return denominator == 1


def exact_decimal(value: Fraction) -> Decimal | None:
    """The fraction as an exact Decimal, or None when its decimal expansion does not end."""
    if not _terminates(value):
        return None
    places = 0
    while (value * 10**places).denominator != 1:
        places += 1
    scaled = value * 10**places
    return Decimal(scaled.numerator).scaleb(-places)


def format_rate(rate: Fraction | Decimal) -> str:
    """A rate as a percent with at least two decimal places: 0.045 -> '4.50%', 0.05025 ->
    '5.025%'; a rate without a finite decimal expansion is rounded, and says so."""
    percent = Fraction(rate) * 100
    exact = exact_decimal(percent)
    if exact is None:
        rounded = (Decimal(percent.numerator) / Decimal(percent.denominator)).quantize(
            Decimal(1).scaleb(-SHOWN_PLACES), rounding=ROUND_HALF_EVEN
        )
        return f"{rounded}% (rounded)"
    places = max(2, -int(exact.normalize().as_tuple().exponent))
    return f"{exact.quantize(Decimal(1).scaleb(-places)):f}%"
