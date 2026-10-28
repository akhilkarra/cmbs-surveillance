"""Read single cell values: prospectus loan ids, amounts and dates as administrators print them.

An empty cell, a dash or "N/A" is "not reported" (None), never zero. A cell that is filled but
unreadable raises ValueError, so the caller can say which loan and column it was.
"""

import datetime as dt
import re
from decimal import Decimal

from cmbs.distribution.grid import clean
from cmbs.domain import money

NOT_REPORTED = frozenset({"", "-", "--", "---", "n/a", "na", "nap", "none", "not available"})
# "11", "011", "11.00", "11A", "11-A", "11 a", "#11", "Loan 11". Up to four digits: a longer
# number is a servicer loan number, not a prospectus id. "1.01" (a property of loan 1) is not.
_LOAN_ID = re.compile(r"^(?:loan\s*|#\s*)?0*(\d{1,4})(?:\.0+)?(?:\s*-?\s*([a-z]{1,2}))?\*?$")
_DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d")


def loan_id(text: str) -> str | None:
    """The canonical prospectus loan id ('011' -> '11', '11-a' -> '11A'), or None."""
    match = _LOAN_ID.match(clean(text).lower())
    if match is None:
        return None
    number, suffix = match.groups()
    return f"{int(number)}{(suffix or '').upper()}"


def is_reported(text: str) -> bool:
    return clean(text).lower() not in NOT_REPORTED


def amount(text: str) -> Decimal | None:
    """'6,500,000.00', '$6,500,000.00' or '(1,250.00)' (negative) -> Decimal to the cent."""
    value = clean(text)
    if not is_reported(value):
        return None
    negative = value.startswith("(") and value.endswith(")")
    if negative:
        value = value[1:-1]
    parsed = money(value)
    return -parsed if negative and parsed else parsed


def date(text: str) -> dt.date | None:
    """'06/04/2026', '6/4/26' or '2026-06-04' -> date."""
    value = clean(text)
    if not is_reported(value):
        return None
    for layout in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(value, layout).date()
        except ValueError:
            continue
    raise ValueError(f"not a date: {value!r}")


def text(value: str) -> str | None:
    """Free text as reported, whitespace normalised; None when not reported."""
    value = clean(value)
    return value if is_reported(value) else None
