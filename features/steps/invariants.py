"""Steps for features/data/pool_invariants.feature.

Each scenario's tape is the fixture tape (tests/fixtures/schedule_al_minimal.xml, September 2026)
with the loans and tags its Given steps state. The When step loads it as filed on the 29th of the
month and reads it on the 1st of the next, with the clock pinned, as a quant checking the month
(the roll-forward) or a PM reading the pool (buckets and WAC).

The roll-forward ties the scheduled balance (grill log Q81), so "the tape reports an ending
balance" writes both the scheduled and the actual ending balance; "began with a balance" is the
beginning scheduled balance. No August tape is loaded, so the month-to-month continuity is not
checked and cannot break.
"""

import datetime as dt
import xml.etree.ElementTree as ET
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import parse
from behave import given, register_type, then, when  # pyright: ignore[reportAttributeAccessIssue]

from cmbs import clock
from cmbs.absee.schedule_al import NAMESPACE
from cmbs.checks.service import flags_for
from cmbs.crosscheck import rollforward as rf
from cmbs.crosscheck.service import check_rollforward
from cmbs.deals.service import by_name
from cmbs.domain import Period, format_money, money
from cmbs.invariants import report
from cmbs.invariants import service as invariants
from cmbs.invariants.partition import Bucket
from cmbs.invariants.wac import NO_BALANCE, format_rate
from cmbs.tape.service import load_tape
from cmbs.testing.bdd import in_session
from cmbs.testing.world import FIXTURE_CIK, FIXTURE_DEAL

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_TAPE = ROOT / "tests" / "fixtures" / "schedule_al_minimal.xml"
URI = NAMESPACE.strip("{}")
CHECKER = "quinn"  # a quant runs the checks
READER = "pat"  # a PM reads the pool
NOT_REPORTED = "not reported"
BEGINNING = "reportPeriodBeginningScheduleLoanBalanceAmount"
ENDING_ACTUAL = "reportPeriodEndActualBalanceAmount"
ENDING_SCHEDULED = "reportPeriodEndScheduledLoanBalanceAmount"
STATUS = "paymentStatusLoanCode"
RATE = "reportPeriodInterestRatePercentage"


@parse.with_pattern(r"[A-Z][a-z]+ \d{4}")
def _month(string: str) -> Period:
    return Period.parse(string)


@parse.with_pattern(r"\$[\d,]+\.\d{2}")
def _money(string: str) -> Decimal:
    return money(string)


@parse.with_pattern(r"\d+(?:\.\d+)?%")
def _rate(string: str) -> Fraction:
    """'5.025%' -> 5025/100000, exactly."""
    return Fraction(Decimal(string.removesuffix("%"))) / 100


@parse.with_pattern(r"[^\"]+")
def _name(string: str) -> str:
    return string


register_type(InvMonth=_month, InvMoney=_money, InvRate=_rate, InvName=_name)


def _amount(value: Decimal) -> str:
    """As tapes write amounts: '50000000.00'."""
    return f"{value:.2f}"


class Tape:
    """The fixture tape, changed loan by loan."""

    def __init__(self, period: Period) -> None:
        self.period = period
        self.root = ET.fromstring(FIXTURE_TAPE.read_bytes())

    def assets(self) -> list[ET.Element]:
        return self.root.findall(NAMESPACE + "assets")

    def asset(self, loan: str) -> ET.Element:
        for asset in self.assets():
            number = asset.find(NAMESPACE + "assetNumber")
            if number is not None and (number.text or "").strip() == loan:
                return asset
        raise AssertionError(f"the fixture tape has no loan {loan!r}")

    def keep_only(self, loans: list[str]) -> None:
        for asset in self.assets():
            number = asset.find(NAMESPACE + "assetNumber")
            if number is None or (number.text or "").strip() not in loans:
                self.root.remove(asset)

    def set(self, loan: str, tag: str, value: str | None) -> None:
        self.set_on(self.asset(loan), tag, value)

    @staticmethod
    def set_on(asset: ET.Element, tag: str, value: str | None) -> None:
        element = asset.find(NAMESPACE + tag)
        if value is None:
            if element is not None:
                asset.remove(element)
            return
        if element is None:
            element = ET.Element(NAMESPACE + tag)
            # Loan-level tags go before the <property> element, as Schedule AL lists them.
            prop = asset.find(NAMESPACE + "property")
            index = list(asset).index(prop) if prop is not None else len(asset)
            asset.insert(index, element)
        element.text = value

    def content(self) -> bytes:
        ET.register_namespace("", URI)
        return ET.tostring(self.root, encoding="utf-8", xml_declaration=True)


def tape(context, period: Period) -> Tape:
    found = getattr(context, "inv_tape", None)
    if found is None:
        found = context.inv_tape = Tape(period)
    assert found.period == period, f"one tape per scenario: {found.period} and {period}"
    context.deal = FIXTURE_DEAL
    return found


def _load(context) -> dt.date:
    """Load the scenario's tape as filed on the 29th; return the day it is read (the 1st of the
    next month), with the clock set to that afternoon."""
    target: Tape = context.inv_tape
    filed = dt.date(target.period.year, target.period.month, 29)
    clock.set_now(dt.datetime.combine(filed, dt.time(18), dt.UTC))
    in_session(
        lambda db: load_tape(
            db,
            deal=FIXTURE_DEAL,
            content=target.content(),
            file_name=f"fixture-trust-2026-f1_{target.period.first_day:%Y-%m}.xml",
            accession=f"{FIXTURE_CIK:010d}-{filed:%y}-{target.period.month:04d}01",
            filing_date=filed,
            cik=FIXTURE_CIK,
        )
    )
    read = target.period.shift(1).first_day
    clock.set_now(dt.datetime.combine(read, dt.time(15), dt.UTC))
    return read


def _measure(context) -> None:
    read = _load(context)
    context.pool = in_session(
        lambda db: invariants.pool(
            db, reader=READER, deal=FIXTURE_DEAL, period=context.inv_tape.period, as_of=read
        )
    )


# The balance roll-forward.


@given(
    'loan "{loan:InvName}" of "{deal:InvName}" began {period:InvMonth} with a balance of '
    "{amount:InvMoney}"
)
def step_began_with(context, loan, deal, period, amount):
    assert deal == FIXTURE_DEAL, f"the scenario's tape is the fixture deal's, not {deal!r}"
    tape(context, period).set(loan, BEGINNING, _amount(amount))
    context.loan = loan


@given(
    "it paid {scheduled:InvMoney} of scheduled principal and {unscheduled:InvMoney} of "
    "unscheduled principal"
)
def step_paid(context, scheduled, unscheduled):
    target = context.inv_tape
    target.set(context.loan, "scheduledPrincipalAmount", _amount(scheduled))
    target.set(context.loan, "unscheduledPrincipalCollectedAmount", _amount(unscheduled))


@given(
    "it had {other:InvMoney} of other principal adjustments and {loss:InvMoney} of realized loss"
)
def step_had(context, other, loss):
    target = context.inv_tape
    target.set(context.loan, "otherPrincipalAdjustmentAmount", _amount(other))
    target.set(context.loan, "realizedLossToTrustAmount", _amount(loss))


@given("the tape reports an ending balance of {amount:InvMoney}")
def step_tape_ending(context, amount):
    target = context.inv_tape
    target.set(context.loan, ENDING_SCHEDULED, _amount(amount))
    target.set(context.loan, ENDING_ACTUAL, _amount(amount))


@when("the {period:InvMonth} period is checked")
def step_period_checked(context, period):
    assert period == context.inv_tape.period, (period, context.inv_tape.period)
    _load(context)
    context.summary = in_session(
        lambda db: check_rollforward(db, user=CHECKER, deal=FIXTURE_DEAL, period=period)
    )


def _result(context, loan: str) -> rf.LoanRollForward:
    found = [result for result in context.summary.rollforward if result.loan == loan]
    assert len(found) == 1, f"roll-forward results for loan {loan}: {found}"
    return found[0]


def _flags(loan: str):
    def read(db):
        deal = by_name(db, FIXTURE_DEAL)
        assert deal is not None
        return flags_for(db, deal_id=deal.id, loan=loan)

    return in_session(read)


@then('the balance roll-forward check passes for loan "{loan:InvName}"')
def step_rollforward_passes(context, loan):
    result = _result(context, loan)
    assert result.roll.status is rf.Status.PASSED, result.roll
    assert result.roll.gap == 0 and result.roll.not_reported == (), result.roll
    assert result.status is rf.Status.PASSED, result
    flags = _flags(loan)
    assert flags == [], [(flag.check, flag.message) for flag in flags]


@then('loan "{loan:InvName}" is flagged "{message}" with a gap of {gap:InvMoney}')
def step_flagged_with_gap(context, loan, message, gap):
    result = _result(context, loan)
    assert result.roll.status is rf.Status.BREAK and result.roll.gap == gap, result.roll
    flags = [flag for flag in _flags(loan) if flag.message == message]
    assert len(flags) == 1, [(flag.check, flag.message) for flag in _flags(loan)]
    assert flags[0].check == rf.ROLLFORWARD, flags[0].check
    assert flags[0].sides["gap"] == format_money(gap), flags[0].sides


# The delinquency partition.


@given('the {period:InvMonth} pool of "{deal:InvName}" is:')
def step_pool_is(context, period, deal):
    assert deal == FIXTURE_DEAL, f"the scenario's tape is the fixture deal's, not {deal!r}"
    target = tape(context, period)
    loans = [row["loan"] for row in context.table]
    target.keep_only(loans)
    for row in context.table:
        status = row["payment status"]
        target.set(row["loan"], STATUS, None if status == NOT_REPORTED else status)
        target.set(row["loan"], ENDING_ACTUAL, _amount(money(row["ending balance"])))
    context.pool_loans = loans


@when("the pool is split into delinquency buckets")
def step_split(context):
    _measure(context)


@then("the buckets are:")
def step_buckets_are(context):
    buckets = context.pool.partition.buckets
    listed = set()
    for row in context.table:
        bucket = Bucket(row["bucket"])
        listed.add(bucket)
        tally = buckets[bucket]
        found = (tally.loans, tally.balance)
        assert found == (int(row["loans"]), money(row["balance"])), (bucket, tally)
        assert tally.balance_not_reported == (), (bucket, tally)
    # Any bucket the scenario does not list must be empty.
    for bucket in set(Bucket) - listed:
        assert buckets[bucket].loans == 0, (bucket, buckets[bucket])


@then("the buckets add up to {count:d} loans and {amount:InvMoney}, the pool balance")
def step_buckets_add_up(context, count, amount):
    found = context.pool.partition
    assert (found.pool.loans, found.pool.balance) == (count, amount), found.pool
    assert sorted(found.pool.members) == sorted(context.pool_loans), found.pool.members
    tallies = found.buckets.values()
    assert sum(tally.loans for tally in tallies) == count
    assert sum(tally.balance for tally in tallies) == amount
    assert found.adds_up()


# The weighted-average coupon.


@given("a pool of three loans:")
def step_pool_of_three(context):
    rows = list(context.table)
    assert len(rows) == 3, rows
    target = tape(context, Period(2026, 9))
    target.keep_only([row["loan"] for row in rows])
    for row in rows:
        # Tapes write the rate as a fraction: 4.50% is 0.0450.
        written = Decimal(row["interest rate"].removesuffix("%")).scaleb(-2)
        target.set(row["loan"], ENDING_ACTUAL, _amount(money(row["ending balance"])))
        target.set(row["loan"], RATE, str(written))


@given("every loan in the pool has an ending balance of {amount:InvMoney}")
def step_every_balance(context, amount):
    target = tape(context, Period(2026, 9))
    for asset in target.assets():
        Tape.set_on(asset, ENDING_ACTUAL, _amount(amount))


@when("the weighted-average coupon is computed")
def step_wac_computed(context):
    _measure(context)


@then("the weighted-average coupon is {rate:InvRate}")
def step_wac_is(context, rate):
    found = context.pool.wac
    assert found.rate == rate, (found.rate, rate)
    assert report.wac_text(found).startswith(f"weighted-average coupon: {format_rate(rate)}, "), (
        report.wac_text(found)
    )


@then("it lies between {low:InvRate} and {high:InvRate}")
def step_wac_between(context, low, high):
    found = context.pool.wac
    assert (found.lowest, found.highest) == (low, high), (found.lowest, found.highest)
    assert found.rate is not None and low <= found.rate <= high, found.rate


@then("the weighted-average coupon is reported as not available")
def step_wac_not_available(context):
    found = context.pool.wac
    assert found.rate is None and not found.available, found
    assert found.reason == NO_BALANCE, found.reason
    assert report.wac_text(found) == f"weighted-average coupon: not available ({NO_BALANCE})"
