"""Value types shared across features: reporting periods and money.

Money is always a `Decimal` rounded to the cent, never a float.
"""

import calendar
import datetime as dt
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
_MONTHS = {name.lower(): number for number, name in enumerate(calendar.month_name) if name}
_PERIOD = re.compile(r"^\s*([A-Za-z]+)\s+(\d{4})\s*$")
# One optional minus, before or after the dollar sign but not both. The whole part is plain
# digits or correctly grouped thousands ("1,234,567"); a fractional part needs digits after the
# point. "1,23", "12,,345", "--12" and "12." are rejected rather than guessed at.
_MONEY = re.compile(
    r"^\s*(?P<minus>-)?\s*\$?\s*(?P<inner_minus>-)?"
    r"(?P<whole>\d{1,3}(?:,\d{3})+|\d+)?(?:\.(?P<fraction>\d+))?\s*$"
)


@dataclass(frozen=True, order=True)
class Period:
    """A monthly reporting period, named by its distribution month."""

    year: int
    month: int

    def __post_init__(self) -> None:
        if not 1 <= self.month <= 12:
            raise ValueError(f"month must be 1 to 12, not {self.month}")

    @classmethod
    def parse(cls, text: str) -> "Period":
        """'September 2026' -> Period(2026, 9)."""
        match = _PERIOD.match(text)
        if not match or match.group(1).lower() not in _MONTHS:
            raise ValueError(f"not a period like 'September 2026': {text!r}")
        return cls(int(match.group(2)), _MONTHS[match.group(1).lower()])

    @classmethod
    def of(cls, day: dt.date) -> "Period":
        return cls(day.year, day.month)

    @property
    def first_day(self) -> dt.date:
        return dt.date(self.year, self.month, 1)

    @property
    def label(self) -> str:
        return f"{calendar.month_name[self.month]} {self.year}"

    def shift(self, months: int) -> "Period":
        index = self.year * 12 + (self.month - 1) + months
        return Period(index // 12, index % 12 + 1)

    def months_until(self, other: "Period") -> int:
        return (other.year - self.year) * 12 + (other.month - self.month)

    def __str__(self) -> str:
        return self.label


def money(text: str | int | Decimal) -> Decimal:
    """'$50,000,000.00', '-$12,000,000.00' or '6,500,000' -> Decimal rounded to the cent."""
    if isinstance(text, Decimal | int):
        return Decimal(text).quantize(CENT, rounding=ROUND_HALF_UP)
    match = _MONEY.match(text)
    if (
        not match
        or (match["minus"] and match["inner_minus"])
        or not (match["whole"] or match["fraction"])
    ):
        raise ValueError(f"not an amount of money: {text!r}")
    sign = "-" if (match["minus"] or match["inner_minus"]) else ""
    whole = (match["whole"] or "0").replace(",", "")
    value = Decimal(f"{sign}{whole}.{match['fraction'] or '0'}")
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def format_money(value: Decimal) -> str:
    """Decimal('-12000000') -> '-$12,000,000.00'."""
    value = value.quantize(CENT, rounding=ROUND_HALF_UP)
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"
