"""The pool identities on real tapes, typed as the loader types them (no database needed).

BANK 2018-BNK13's September 2026 tape lists 42 loans: six paid off (no status, no ending balance,
a rate of 0.00), which are "not outstanding" (M-026), and none of the deal's co-op loans, so its
WAC is over 36 loans and $595.9 million, where the 10-D's stratification (56 loans, $695.6
million of scheduled balance) prints 4.5925%. BANK5 2026-5YR23 reports every loan's status,
balance and rate.
"""

from decimal import Decimal
from fractions import Fraction

from cmbs.absee.schedule_al import parse_assets
from cmbs.invariants.partition import Bucket, PoolLoan, partition
from cmbs.invariants.service import pool_loans, rated_loans
from cmbs.invariants.wac import format_rate, wac
from cmbs.tape.service import plan
from tests.fixtures.tape.tapes import real_bytes

D = Decimal
PAID_OFF = ("2", "3", "10", "17", "34", "41")


def rows(name: str):
    work = plan(parse_assets(real_bytes(name)))
    assert work.quarantined == []
    return [row for _, row in work.loaded]


def loans(name: str) -> list[PoolLoan]:
    return pool_loans(rows(name))


def test_bnk13_buckets():
    found = partition(loans("bank-2018-bnk13_2026-09_ex102.xml.gz"))
    counts = {bucket: (tally.loans, tally.balance) for bucket, tally in found.buckets.items()}
    assert counts == {
        Bucket.CURRENT: (34, D("547393388.80")),
        Bucket.GRACE_OR_LATE: (1, D("11032552.34")),
        Bucket.DAYS_30: (0, D("0.00")),
        Bucket.DAYS_60: (0, D("0.00")),
        Bucket.DAYS_90_PLUS: (1, D("37500000.00")),
        Bucket.PERFORMING_MATURED: (0, D("0.00")),
        Bucket.NON_PERFORMING_MATURED: (0, D("0.00")),
        Bucket.UNRECOGNIZED: (0, D("0.00")),
        Bucket.NOT_REPORTED: (0, D("0.00")),
        Bucket.NOT_OUTSTANDING: (6, D("0.00")),
    }
    assert found.buckets[Bucket.GRACE_OR_LATE].members == ("22",)
    assert found.buckets[Bucket.DAYS_90_PLUS].members == ("11",)
    assert found.buckets[Bucket.NOT_OUTSTANDING].members == PAID_OFF
    assert found.buckets[Bucket.NOT_OUTSTANDING].balance_not_reported == PAID_OFF
    assert (found.pool.loans, found.pool.balance) == (42, D("595925941.14"))
    assert found.adds_up()


def test_bnk13_wac_leaves_the_paid_off_loans_out():
    typed = rows("bank-2018-bnk13_2026-09_ex102.xml.gz")
    assert {row.loan: row.interest_rate for row in typed if row.loan in PAID_OFF} == dict.fromkeys(
        PAID_OFF, D("0.00")
    )
    found = wac(rated_loans(typed))
    assert found.rate == Fraction(139209342386201, 2979629705700000)
    assert found.rate is not None
    assert format_rate(found.rate) == "4.672035% (rounded)"
    assert (found.lowest, found.highest) == (D("0.037086"), D("0.05567"))
    assert (len(found.averaged), found.balance) == (36, D("595925941.14"))
    assert found.balance_not_reported == PAID_OFF
    assert found.rate_not_reported == found.zero_rate == ()


def test_bank5_every_loan_counts():
    typed = rows("bank5-2026-5yr23_2026-09_ex102.xml.gz")
    found = partition(loans("bank5-2026-5yr23_2026-09_ex102.xml.gz"))
    assert (found.pool.loans, found.pool.balance) == (33, D("1174226727.84"))
    assert found.buckets[Bucket.GRACE_OR_LATE].members == ("9",)
    assert found.buckets[Bucket.CURRENT].loans == 32
    coupon = wac(rated_loans(typed))
    assert coupon.rate is not None
    assert format_rate(coupon.rate) == "6.507996% (rounded)"
    assert (coupon.lowest, coupon.highest) == (D("0.05096768"), D("0.07818000"))
    assert len(coupon.averaged) == 33
