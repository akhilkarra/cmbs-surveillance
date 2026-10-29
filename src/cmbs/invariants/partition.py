"""The delinquency partition: every loan of a pool in exactly one bucket. Pure logic.

Buckets follow the loan's Schedule AL payment status (`paymentStatusLoanCode`, read as
`cmbs.alerts.codes` reads codes, so "01" is "1" and "b" is "B"):

| code        | bucket                      |
|-------------|-----------------------------|
| 0           | current                     |
| A, B        | grace or late               |
| 1           | 30 days                     |
| 2           | 60 days                     |
| 3           | 90 days or more             |
| 4           | performing matured          |
| 5           | non-performing matured      |
| any other   | status not on the code list |
| none        | status not reported         |

**A performing matured balloon (4) has a bucket of its own.** The loan is past its maturity
date without paying off, so it is not current; it keeps making its monthly payments, so it is
not delinquent by any number of days either. Counting it with "current" would hide a maturity
default, and counting it with "30 days" would claim a missed payment the servicer did not
report.

**Nothing is dropped.** A code that is not on the list has its own bucket rather than being
guessed into one, and a loan without a status has "status not reported". A loan whose ending
balance is not reported is counted in its bucket and listed as such; its balance is never taken
as zero, so each bucket's balance is the sum of the balances reported.

`lean/Invariants/Delinquency.lean` proves, for every pool, that every loan lands in exactly one
bucket and that the buckets' loans, balances and loans without a balance add up to the pool's.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from cmbs.alerts.codes import same_code


class Bucket(StrEnum):
    CURRENT = "current"
    GRACE_OR_LATE = "grace or late"
    DAYS_30 = "30 days"
    DAYS_60 = "60 days"
    DAYS_90_PLUS = "90 days or more"
    PERFORMING_MATURED = "performing matured"
    NON_PERFORMING_MATURED = "non-performing matured"
    UNRECOGNIZED = "status not on the code list"
    NOT_REPORTED = "status not reported"


# Schedule AL payment status codes and their buckets.
BY_CODE: dict[str, Bucket] = {
    "0": Bucket.CURRENT,
    "A": Bucket.GRACE_OR_LATE,
    "B": Bucket.GRACE_OR_LATE,
    "1": Bucket.DAYS_30,
    "2": Bucket.DAYS_60,
    "3": Bucket.DAYS_90_PLUS,
    "4": Bucket.PERFORMING_MATURED,
    "5": Bucket.NON_PERFORMING_MATURED,
}
ZERO = Decimal("0.00")


def code_of(status: str | None) -> str | None:
    """The code on the list a reported status means ('01' -> '1', 'b' -> 'B'), or the status as
    reported, without surrounding space, when it is not on the list."""
    if status is None:
        return None
    return next((code for code in BY_CODE if same_code(status, code)), status.strip())


def bucket_of(status: str | None) -> Bucket:
    code = code_of(status)
    if code is None:
        return Bucket.NOT_REPORTED
    return BY_CODE.get(code, Bucket.UNRECOGNIZED)


@dataclass(frozen=True)
class PoolLoan:
    loan: str
    status: str | None
    balance: Decimal | None


@dataclass(frozen=True)
class Tally:
    """A bucket's loans (or the pool's), their reported balance, and the loans among them whose
    balance is not reported."""

    members: tuple[str, ...] = ()
    balance: Decimal = ZERO
    balance_not_reported: tuple[str, ...] = ()

    @property
    def loans(self) -> int:
        return len(self.members)

    def add(self, loan: PoolLoan) -> "Tally":
        members = (*self.members, loan.loan)
        if loan.balance is None:
            return Tally(members, self.balance, (*self.balance_not_reported, loan.loan))
        return Tally(members, self.balance + loan.balance, self.balance_not_reported)


@dataclass(frozen=True)
class Partition:
    buckets: dict[Bucket, Tally]
    pool: Tally

    def adds_up(self) -> bool:
        """The identity the theorem proves: the buckets' loans, balances and loans without a
        balance add up to the pool's."""
        tallies = self.buckets.values()
        return (
            sum(tally.loans for tally in tallies) == self.pool.loans
            and sum((tally.balance for tally in tallies), ZERO) == self.pool.balance
            and sum(len(tally.balance_not_reported) for tally in tallies)
            == len(self.pool.balance_not_reported)
        )


def partition(loans: Iterable[PoolLoan]) -> Partition:
    """Every bucket, in the order of `Bucket`, with the loans that land in it."""
    buckets = dict.fromkeys(Bucket, Tally())
    pool = Tally()
    for loan in loans:
        bucket = bucket_of(loan.status)
        buckets[bucket] = buckets[bucket].add(loan)
        pool = pool.add(loan)
    return Partition(buckets, pool)
