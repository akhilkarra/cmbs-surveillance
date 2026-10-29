"""Text versus tape: what a servicer comment says against what the loan tape says. Pure logic.

Each side is read point in time by the service; these rules only compare. Neither side is ever
changed: a disagreement becomes a check flag that keeps both values and their dates.

- **Transfer date.** The comment's transfer date (a value the panel shows as accepted,
  human-reviewed or overridden, never one awaiting review) against the tape's most recent
  special-servicer transfer date for the same loan and month. The tape reports by distribution
  month (its reporting period may end on any day of the month), so the tolerance in force (two
  in settings version 7) counts distribution months: dates whose months are at most that many
  apart agree, whatever their days. 2025-01-20 and 2025-03-06 are two months apart (January to
  March), 2024-11-15 and 2025-03-06 four. A transfer the comment gives and the tape does not
  report is flagged "transfer in text, not in tape".
- **Newer financials.** When the comment dates its DSCR or occupancy later than the tape's
  most recent financials, the loan gets a note (not a disagreement: servicers see operating
  statements before the tape is updated). The tape's figures stay as filed.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from cmbs.domain import Period

TEXT_VS_TAPE = "text_vs_tape"
TRANSFER_DATE = f"{TEXT_VS_TAPE}.transfer_date"
TRANSFER_MISSING = f"{TEXT_VS_TAPE}.transfer_missing"
NEWER_FINANCIALS = f"{TEXT_VS_TAPE}.newer_financials"
TRANSFER_CHECKS = frozenset({TRANSFER_DATE, TRANSFER_MISSING})
# Checks whose findings are notes for the reader, not disagreements between the sources.
NOTE_CHECKS = frozenset({NEWER_FINANCIALS})

NOT_IN_TAPE = "transfer in text, not in tape"
NEWER_THAN_TAPE = "text has newer financials than tape"
TAPE_HAS_NONE = "text has financials the tape does not report"
NOT_REPORTED = "not reported"
TEXT_SOURCE = "10-D comment"
TAPE_SOURCE = "loan tape"

_NUMBERS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")


class Status(StrEnum):
    AGREE = "agree"
    FLAG = "flag"
    NOTE = "note"


@dataclass(frozen=True)
class TextSide:
    """What one comment states, as the panel holds it: only accepted or decided values.

    `as_of` is the date the comment speaks as of (its report's distribution date, or the 10-D's
    filing date when the report gives none).
    """

    accession: str
    filed: dt.date
    as_of: dt.date
    transfer_date: dt.date | None = None
    transfer_sentence: int | None = None
    dscr: Decimal | None = None
    dscr_as_of: dt.date | None = None
    occupancy: Decimal | None = None
    occupancy_as_of: dt.date | None = None


@dataclass(frozen=True)
class TapeSide:
    """One loan's row on the tape current for the month. `as_of` is its reporting period end."""

    accession: str
    filed: dt.date
    as_of: dt.date
    transfer_date: dt.date | None = None
    dscr: Decimal | None = None
    occupancy: Decimal | None = None
    financials_end: dt.date | None = None


@dataclass(frozen=True)
class Finding:
    check: str
    status: Status
    message: str
    sides: dict[str, Any]


def periods_apart(first: dt.date, second: dt.date) -> int:
    """Distribution months between the months the two dates fall in, whichever comes first."""
    return abs(Period.of(first).months_until(Period.of(second)))


def periods_phrase(count: int) -> str:
    """1 -> 'one period', 2 -> 'two periods', 12 -> '12 periods'."""
    word = _NUMBERS[count] if 0 <= count < len(_NUMBERS) else str(count)
    return f"{word} period" if count == 1 else f"{word} periods"


def transfer_message(tolerance: int) -> str:
    return f"transfer dates differ by more than {periods_phrase(tolerance)}"


def _side(source: str, side: TextSide | TapeSide, **values: Any) -> dict[str, Any]:
    """One source's values with where they come from and the dates they are known by."""
    return {
        "source": source,
        **{name: _plain(value) for name, value in values.items()},
        "accession": side.accession,
        "filed": side.filed.isoformat(),
        "as_of": side.as_of.isoformat(),
    }


def _plain(value: object) -> object:
    """JSON-ready: dates as ISO text, Decimals as text, None as 'not reported'."""
    if value is None:
        return NOT_REPORTED
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dt.date):
        return value.isoformat()
    return value


def compare_transfer(text: TextSide, tape: TapeSide, *, tolerance: int) -> Finding | None:
    """The comment's transfer date against the tape's; None when the comment gives none."""
    if text.transfer_date is None:
        return None
    sides = {
        "text": _side(
            TEXT_SOURCE, text, transfer_date=text.transfer_date, sentence=text.transfer_sentence
        ),
        "tape": _side(TAPE_SOURCE, tape, transfer_date=tape.transfer_date),
        "tolerance_periods": tolerance,
    }
    if tape.transfer_date is None:
        return Finding(TRANSFER_MISSING, Status.FLAG, NOT_IN_TAPE, sides)
    apart = periods_apart(text.transfer_date, tape.transfer_date)
    sides["periods_apart"] = apart
    if apart > tolerance:
        return Finding(TRANSFER_DATE, Status.FLAG, transfer_message(tolerance), sides)
    message = f"transfer dates agree within {periods_phrase(tolerance)}"
    return Finding(TRANSFER_DATE, Status.AGREE, message, sides)


def text_financials_date(text: TextSide) -> dt.date | None:
    """The latest date the comment's DSCR or occupancy is stated as of, if any."""
    dated = [
        as_of
        for figure, as_of in ((text.dscr, text.dscr_as_of), (text.occupancy, text.occupancy_as_of))
        if figure is not None and as_of is not None
    ]
    return max(dated) if dated else None


def compare_financials(text: TextSide, tape: TapeSide) -> Finding | None:
    """A note when the comment's financials are dated after the tape's; None when the comment
    dates none."""
    stated = text_financials_date(text)
    if stated is None:
        return None
    sides = {
        "text": _side(
            TEXT_SOURCE,
            text,
            dscr=text.dscr,
            dscr_as_of=text.dscr_as_of,
            occupancy=text.occupancy,
            occupancy_as_of=text.occupancy_as_of,
        ),
        "tape": _side(
            TAPE_SOURCE,
            tape,
            dscr=tape.dscr,
            occupancy=tape.occupancy,
            financials_end=tape.financials_end,
        ),
    }
    if tape.financials_end is None:
        return Finding(NEWER_FINANCIALS, Status.NOTE, TAPE_HAS_NONE, sides)
    if stated > tape.financials_end:
        return Finding(NEWER_FINANCIALS, Status.NOTE, NEWER_THAN_TAPE, sides)
    return Finding(NEWER_FINANCIALS, Status.AGREE, "tape financials are as recent as text", sides)


def compare(text: TextSide, tape: TapeSide, *, tolerance: int) -> list[Finding]:
    """Every comparison the comment allows, transfer first."""
    found = (compare_transfer(text, tape, tolerance=tolerance), compare_financials(text, tape))
    return [finding for finding in found if finding is not None]
