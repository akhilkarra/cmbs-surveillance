"""The pool's buckets and WAC as text for the command line and as JSON for the API.

Money travels as text with two decimals and rates as reported; the WAC is given exactly, as a
fraction, beside its percent. Nothing here computes: it formats what `cmbs.invariants` found.
"""

from decimal import Decimal
from typing import Any

from cmbs.domain import format_money
from cmbs.invariants.partition import Tally
from cmbs.invariants.service import Pool
from cmbs.invariants.wac import Wac, format_rate

NOT_AVAILABLE = "not available"


def _loans(count: int) -> str:
    return f"{count} loan" if count == 1 else f"{count} loans"


def tally_text(tally: Tally) -> str:
    """'2 loans, $58,000,000.00', plus the loans whose balance is not reported."""
    text = f"{_loans(tally.loans)}, {format_money(tally.balance)}"
    if tally.balance_not_reported:
        text += f" (balance not reported: loan {', '.join(tally.balance_not_reported)})"
    return text


def wac_text(found: Wac) -> str:
    if found.rate is None:
        return f"weighted-average coupon: {NOT_AVAILABLE} ({found.reason})"
    assert found.lowest is not None and found.highest is not None
    text = (
        f"weighted-average coupon: {format_rate(found.rate)}, "
        f"between {format_rate(found.lowest)} and {format_rate(found.highest)}, "
        f"over {_loans(len(found.averaged))} and {format_money(found.balance)}"
    )
    for label, loans in (
        ("rate not reported", found.rate_not_reported),
        ("balance not reported", found.balance_not_reported),
        ("reported rate 0.00", found.zero_rate),
    ):
        if loans:
            text += f"; {label}: loan {', '.join(loans)}"
    return text


def pool_text(found: Pool) -> str:
    lines = [
        f"{found.deal}, {found.period.label}, as of {found.as_of.isoformat()} "
        f"(tape {found.accession})",
        "delinquency buckets:",
    ]
    width = max(len(bucket.value) for bucket in found.partition.buckets)
    for bucket, tally in found.partition.buckets.items():
        lines.append(f"  {bucket.value:<{width}}  {tally_text(tally)}")
    lines.append(f"  {'pool':<{width}}  {tally_text(found.partition.pool)}")
    lines.append(wac_text(found.wac))
    return "\n".join(lines) + "\n"


def _money(amount: Decimal) -> str:
    return f"{amount:.2f}"


def _rate(rate: Decimal | None) -> str | None:
    return None if rate is None else str(rate)


def tally_json(tally: Tally) -> dict[str, Any]:
    return {
        "loans": tally.loans,
        "loan_numbers": list(tally.members),
        "balance": _money(tally.balance),
        "balance_not_reported": list(tally.balance_not_reported),
    }


def wac_json(found: Wac) -> dict[str, Any]:
    exact = found.rate
    return {
        "available": found.available,
        "reason": found.reason,
        "rate": None if exact is None else f"{exact.numerator}/{exact.denominator}",
        "percent": None if exact is None else format_rate(exact),
        "lowest": _rate(found.lowest),
        "highest": _rate(found.highest),
        "averaged": list(found.averaged),
        "balance": _money(found.balance),
        "rate_not_reported": list(found.rate_not_reported),
        "balance_not_reported": list(found.balance_not_reported),
        "zero_rate": list(found.zero_rate),
    }


def pool_json(found: Pool) -> dict[str, Any]:
    return {
        "deal": found.deal,
        "period": found.period.label,
        "as_of": found.as_of.isoformat(),
        "accession": found.accession,
        "buckets": [
            {"bucket": bucket.value, **tally_json(tally)}
            for bucket, tally in found.partition.buckets.items()
        ],
        "pool": tally_json(found.partition.pool),
        "adds_up": found.partition.adds_up(),
        "wac": wac_json(found.wac),
    }
