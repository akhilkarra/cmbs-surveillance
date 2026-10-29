"""The delinquency partition: every loan in exactly one bucket, buckets adding up to the pool."""

from decimal import Decimal

import pytest

from cmbs.invariants.partition import (
    BY_CODE,
    Bucket,
    Partition,
    PoolLoan,
    Tally,
    bucket_of,
    code_of,
    partition,
)


@pytest.mark.parametrize(
    ("status", "bucket"),
    [
        ("0", Bucket.CURRENT),
        ("A", Bucket.GRACE_OR_LATE),
        ("B", Bucket.GRACE_OR_LATE),
        ("1", Bucket.DAYS_30),
        ("2", Bucket.DAYS_60),
        ("3", Bucket.DAYS_90_PLUS),
        ("4", Bucket.PERFORMING_MATURED),
        ("5", Bucket.NON_PERFORMING_MATURED),
        ("6", Bucket.UNRECOGNIZED),
        ("X", Bucket.UNRECOGNIZED),
        ("", Bucket.UNRECOGNIZED),
        (None, Bucket.NOT_REPORTED),
    ],
)
def test_each_code_has_its_bucket(status, bucket):
    assert bucket_of(status) is bucket


def test_codes_are_read_as_the_alerts_read_them():
    assert [code_of(status) for status in ("01", "b", " 3 ", "00", "a")] == [
        "1",
        "B",
        "3",
        "0",
        "A",
    ]
    assert code_of(" 6 ") == "6"
    assert code_of(None) is None
    assert bucket_of("04") is Bucket.PERFORMING_MATURED


def test_the_code_list_has_eight_codes_and_every_bucket_is_named():
    assert list(BY_CODE) == ["0", "A", "B", "1", "2", "3", "4", "5"]
    assert [bucket.value for bucket in Bucket] == [
        "current",
        "grace or late",
        "30 days",
        "60 days",
        "90 days or more",
        "performing matured",
        "non-performing matured",
        "status not on the code list",
        "status not reported",
    ]


POOL = [
    PoolLoan("1", "0", Decimal("50000000.00")),
    PoolLoan("2", "3", Decimal("30000000.00")),
    PoolLoan("3", "0", Decimal("8000000.00")),
    PoolLoan("4", "1", Decimal("4000000.00")),
    PoolLoan("5", "B", Decimal("12000000.00")),
    PoolLoan("7", None, Decimal("9000000.00")),
    PoolLoan("8", "4", None),
    PoolLoan("9", "7", Decimal("1.25")),
]


def test_the_pool_split_into_buckets():
    found = partition(POOL)
    assert list(found.buckets) == list(Bucket)
    assert found.buckets == {
        Bucket.CURRENT: Tally(("1", "3"), Decimal("58000000.00")),
        Bucket.GRACE_OR_LATE: Tally(("5",), Decimal("12000000.00")),
        Bucket.DAYS_30: Tally(("4",), Decimal("4000000.00")),
        Bucket.DAYS_60: Tally(),
        Bucket.DAYS_90_PLUS: Tally(("2",), Decimal("30000000.00")),
        Bucket.PERFORMING_MATURED: Tally(("8",), Decimal("0.00"), ("8",)),
        Bucket.NON_PERFORMING_MATURED: Tally(),
        Bucket.UNRECOGNIZED: Tally(("9",), Decimal("1.25")),
        Bucket.NOT_REPORTED: Tally(("7",), Decimal("9000000.00")),
    }
    assert found.pool == Tally(
        ("1", "2", "3", "4", "5", "7", "8", "9"), Decimal("113000001.25"), ("8",)
    )
    assert found.pool.loans == 8
    assert found.adds_up()


def test_an_empty_pool_has_empty_buckets():
    found = partition([])
    assert found.buckets == dict.fromkeys(Bucket, Tally())
    assert found.pool == Tally()
    assert str(found.pool.balance) == "0.00"
    assert found.adds_up()


def test_a_missing_balance_is_listed_and_not_added():
    tally = Tally().add(PoolLoan("8", "0", None)).add(PoolLoan("9", "0", Decimal("2.00")))
    assert tally == Tally(("8", "9"), Decimal("2.00"), ("8",))
    assert tally.loans == 2


def _broken(**change) -> Partition:
    pool = Tally(("1",), Decimal("5.00"), ("1",))
    bucket = Tally(("1",), Decimal("5.00"), ("1",))
    for name, value in change.items():
        bucket = Tally(**{**bucket.__dict__, name: value})
    return Partition({Bucket.CURRENT: bucket}, pool)


def test_adds_up_fails_when_any_of_the_three_sums_differs():
    assert _broken().adds_up()
    assert not _broken(members=("1", "2")).adds_up()
    assert not _broken(balance=Decimal("5.01")).adds_up()
    assert not _broken(balance_not_reported=()).adds_up()
