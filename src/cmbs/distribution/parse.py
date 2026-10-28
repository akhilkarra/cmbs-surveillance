"""Turn a distribution report's HTML into typed per-loan records. No database, no network.

What each format yields:
- Computershare: special-servicing comments from "Specially Serviced Loan Detail - Part 2",
  appraisal reductions, and delinquency detail.
- Citi: appraisal reductions and delinquency detail. It prints no comment text, and the
  summary says so rather than reporting zero comments as if some could have been found.

Values are kept as reported. A blank or dash is "not reported" and produces no value; a filled
cell that cannot be read is skipped with a note naming the table, the loan and the text. An
appraisal reduction of 0.00 means none is in effect, so only non-zero amounts are kept.
"""

import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from cmbs.distribution import values
from cmbs.distribution.extract import Record, read_table, sections
from cmbs.distribution.grid import document_text, flatten, read_document
from cmbs.distribution.layouts import (
    APPRAISAL_TABLES,
    COMMENT_TABLES,
    DELINQUENCY_TABLES,
    TITLE_KEYS,
    Administrator,
    detect,
)
from cmbs.errors import Refused

NO_COMMENT_COLUMN = "this administrator's format has no comment column"
# Where each format states the distribution date on its first page: Computershare as
# "Distribution Date: 09/17/26", Citi as "STATEMENT TO NOTEHOLDERS September 14, 2026".
_DISTRIBUTION_DATES = (
    re.compile(r"distribution date\s*:?\s*(\d{1,2}/\d{1,2}/\d{2,4})", re.I),
    re.compile(r"statement to (?:note|certificate)holders\s+([a-z]+ \d{1,2}, \d{4})", re.I),
)


class UnrecognisedFormat(Refused):
    """No known certificate administrator's format."""


@dataclass(frozen=True)
class Comment:
    loan: str
    text: str
    source_table: str


@dataclass(frozen=True)
class AppraisalReduction:
    loan: str
    amount: Decimal
    source_table: str
    reduction_date: dt.date | None = None


@dataclass(frozen=True)
class Delinquency:
    """A loan's row in the delinquency table, as reported. The status code is not interpreted
    here: each administrator has its own legend, and reconciliation with the tape reads it."""

    loan: str
    source_table: str
    status: str | None = None
    paid_through: dt.date | None = None
    current_advances: Decimal | None = None
    outstanding_advances: Decimal | None = None
    actual_balance: Decimal | None = None


@dataclass(frozen=True)
class ParsedReport:
    administrator: Administrator
    distribution_date: dt.date | None
    comments: tuple[Comment, ...]
    appraisal_reductions: tuple[AppraisalReduction, ...]
    delinquencies: tuple[Delinquency, ...]
    notes: tuple[str, ...]

    @property
    def has_comment_column(self) -> bool:
        return bool(COMMENT_TABLES[self.administrator])

    @property
    def summary(self) -> str:
        """One line per fact: the format, what was found, then any notes."""
        if self.has_comment_column:
            comments = f"{len(self.comments)} special-servicing comments"
        else:
            comments = NO_COMMENT_COLUMN
        lines = [
            f"{self.administrator.value} format",
            comments,
            f"{len(self.appraisal_reductions)} appraisal reductions",
            f"{len(self.delinquencies)} loans in delinquency detail",
            *self.notes,
        ]
        return "\n".join(lines)


def distribution_date(text: str) -> dt.date | None:
    """The report's distribution date, from the first place a known format states it."""
    for pattern in _DISTRIBUTION_DATES:
        match = pattern.search(text)
        if match is None:
            continue
        try:
            return values.date(match.group(1))
        except ValueError:
            return None
    return None


def _read[T](
    value: str, reader: Callable[[str], T | None], notes: list[str], where: str
) -> T | None:
    """The value, or None with a note when the cell is filled but unreadable."""
    try:
        return reader(value)
    except ValueError as error:
        notes.append(f"{where}: {error}")
        return None


def parse_report(content: bytes) -> ParsedReport:
    """Parse one EX-99.1 distribution report. Refuses a file whose format is not recognised."""
    root = read_document(content)
    text = document_text(root)
    administrator = detect(text)
    if administrator is None:
        raise UnrecognisedFormat("the report's certificate administrator is not recognised")
    report = sections(flatten(root, TITLE_KEYS))
    notes: list[str] = []

    comments: list[Comment] = []
    for spec in COMMENT_TABLES[administrator]:
        records = read_table(spec, report, notes)
        if records is None:
            notes.append(f"no {spec.name} table")
            continue
        for record in records:
            comment = values.text(record.values.get("comment", ""))
            if comment is not None:
                comments.append(Comment(record.loan, comment, spec.name))

    reductions: list[AppraisalReduction] = []
    found_appraisals = False
    for spec in APPRAISAL_TABLES[administrator]:
        records = read_table(spec, report, notes)
        if records is None:
            continue
        found_appraisals = True
        for record in records:
            reduction = _appraisal(record, spec.name, notes)
            if reduction is not None:
                reductions.append(reduction)
    if not found_appraisals:
        notes.append("no appraisal reduction table")

    delinquencies: list[Delinquency] = []
    for spec in DELINQUENCY_TABLES[administrator]:
        records = read_table(spec, report, notes)
        if records is None:
            notes.append(f"no {spec.name} table")
            continue
        delinquencies.extend(_delinquency(record, spec.name, notes) for record in records)

    return ParsedReport(
        administrator=administrator,
        distribution_date=distribution_date(text),
        comments=tuple(comments),
        appraisal_reductions=tuple(reductions),
        delinquencies=tuple(delinquencies),
        notes=tuple(notes),
    )


def _appraisal(record: Record, table: str, notes: list[str]) -> AppraisalReduction | None:
    where = f"{table}, loan {record.loan}"
    amount = _read(record.values.get("amount", ""), values.amount, notes, where)
    if not amount:  # not reported, unreadable, or a reported zero: no reduction in effect
        return None
    reduction_date = _read(record.values.get("reduction_date", ""), values.date, notes, where)
    return AppraisalReduction(record.loan, amount, table, reduction_date)


def _delinquency(record: Record, table: str, notes: list[str]) -> Delinquency:
    """Kept even when a field is unreadable: being listed is itself the delinquency fact."""
    where = f"{table}, loan {record.loan}"
    get = record.values.get
    return Delinquency(
        loan=record.loan,
        source_table=table,
        status=values.text(get("status", "")),
        paid_through=_read(get("paid_through", ""), values.date, notes, where),
        current_advances=_read(get("current_advances", ""), values.amount, notes, where),
        outstanding_advances=_read(get("outstanding_advances", ""), values.amount, notes, where),
        actual_balance=_read(get("actual_balance", ""), values.amount, notes, where),
    )
