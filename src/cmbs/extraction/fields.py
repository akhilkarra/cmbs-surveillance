"""The typed fields read from a comment: what each one is, and how its values are written.

A field is a category (one of fixed options) or a figure the comment prints: a date, a ratio
(DSCR) or a percentage (occupancy). Figures are found by code ("select instead of generate"):
the model only picks which printed figure is meant, so a value can never be invented or
mistyped. Dates in comments are US month/day/year.

Values are stored as canonical text ('2025-03-06', '0.71', '61.4', 'maturity extension') and
read back typed with `typed`.
"""

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from cmbs.errors import Refused


class Kind(StrEnum):
    CATEGORY = "category"
    DATE = "date"
    RATIO = "ratio"
    PERCENT = "percent"


class InvalidValue(Refused):
    """A value that is not one the field can hold."""


@dataclass(frozen=True)
class FieldSpec:
    name: str
    label: str
    kind: Kind
    options: tuple[str, ...] = field(default=())


TRANSFER_REASONS = (
    "imminent monetary default",
    "monetary default",
    "imminent maturity default",
    "maturity default",
    "non-monetary default",
    "bankruptcy",
    "other",
)
BORROWER_REQUESTS = (
    "maturity extension",
    "forbearance",
    "loan modification",
    "discounted payoff",
    "loan assumption",
    "collateral release",
    "other",
)
REQUEST_STATUSES = ("pending", "granted", "denied")
# Schedule AL's workout strategy codes, by name.
WORKOUT_STRATEGIES = (
    "modification",
    "foreclosure",
    "bankruptcy",
    "extension",
    "note sale",
    "discounted payoff",
    "property in REO",
    "resolved",
    "pending return to master servicer",
    "deed in lieu of foreclosure",
    "full payoff",
    "representations and warranties",
    "other",
)

FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("transfer_reason", "transfer reason", Kind.CATEGORY, TRANSFER_REASONS),
    FieldSpec("transfer_date", "transfer date", Kind.DATE),
    FieldSpec("dscr", "DSCR", Kind.RATIO),
    FieldSpec("dscr_as_of", "DSCR as-of date", Kind.DATE),
    FieldSpec("occupancy", "occupancy", Kind.PERCENT),
    FieldSpec("occupancy_as_of", "occupancy as-of date", Kind.DATE),
    FieldSpec("borrower_request", "borrower request", Kind.CATEGORY, BORROWER_REQUESTS),
    FieldSpec("request_status", "request status", Kind.CATEGORY, REQUEST_STATUSES),
    FieldSpec("workout_strategy", "workout strategy", Kind.CATEGORY, WORKOUT_STRATEGIES),
)
BY_NAME = {spec.name: spec for spec in FIELDS}
BY_LABEL = {spec.label: spec for spec in FIELDS}

# Figures as comments print them. Each pattern over-finds a little; `canonical` rejects a match
# that is not a real value (such as 13/45/2025), so it never becomes a candidate.
_PATTERNS = {
    Kind.DATE: re.compile(r"(?<![\d/])\d{1,2}/\d{1,2}/(?:\d{4}|\d{2})(?![\d/])"),
    Kind.RATIO: re.compile(r"(?<![\d.])\d+(?:\.\d+)?\s?x\b", re.IGNORECASE),
    Kind.PERCENT: re.compile(r"(?<![\d.])\d+(?:\.\d+)?\s?%"),
}


def spec(name: str) -> FieldSpec:
    return BY_NAME[name]


def by_label(label: str) -> FieldSpec:
    try:
        return BY_LABEL[label]
    except KeyError:
        raise InvalidValue(f"there is no field named {label!r}") from None


def _us_date(text: str) -> dt.date:
    """'3/6/2025' -> 2025-03-06; a two-digit year is in the 2000s."""
    month, day, year = (int(part) for part in text.split("/"))
    return dt.date(year + 2000 if year < 100 else year, month, day)


def canonical(spec: FieldSpec, text: str) -> str:
    """A value as stored: 'maturity extension', '2025-03-06', '0.71', '61.4'.

    Accepts a category's option, a date as US month/day/year or ISO, a ratio with or without
    its 'x', a percentage with or without its '%'. Anything else is InvalidValue.
    """
    value = " ".join(text.split())
    if spec.kind is Kind.CATEGORY:
        if value not in spec.options:
            raise InvalidValue(
                f"{value!r} is not a {spec.label}; choose one of: {', '.join(spec.options)}"
            )
        return value
    try:
        if spec.kind is Kind.DATE:
            day = dt.date.fromisoformat(value) if "-" in value else _us_date(value)
            return day.isoformat()
        number = Decimal(value.rstrip("xX%").strip())
    except (ValueError, InvalidOperation):
        raise InvalidValue(f"{value!r} is not a {spec.label}") from None
    if not number.is_finite() or number < 0:
        raise InvalidValue(f"{value!r} is not a {spec.label}")
    return str(number)


def candidates(spec: FieldSpec, text: str) -> tuple[str, ...]:
    """The figures of the field's kind printed in `text`, as printed, first seen first."""
    if spec.kind is Kind.CATEGORY:
        return ()
    found: list[str] = []
    for match in _PATTERNS[spec.kind].finditer(text):
        printed = match.group(0)
        try:
            canonical(spec, printed)
        except InvalidValue:
            continue
        if printed not in found:
            found.append(printed)
    return tuple(found)


def typed(spec: FieldSpec, value: str) -> str | dt.date | Decimal:
    """A stored value as its type: a date, a Decimal, or the category's name."""
    if spec.kind is Kind.DATE:
        return dt.date.fromisoformat(value)
    if spec.kind in (Kind.RATIO, Kind.PERCENT):
        return Decimal(value)
    return value


def display(spec: FieldSpec, value: str) -> str:
    """A stored value as the panel shows it: '0.71x', '61.4%', '2025-03-06'."""
    if spec.kind is Kind.RATIO:
        return f"{value}x"
    if spec.kind is Kind.PERCENT:
        return f"{value}%"
    return value
