"""Rows as an export file and as a checksum. Pure functions, no database.

A row is a mapping of column name to value: text, an integer, a `Decimal` (money is never a
float), a date, or None for a value not reported. Two encodings:

- `checksum`: the SHA-256 of the ordered rows, each value written exactly (a Decimal keeps its
  scale, a date is ISO), so equal checksums mean equal panels, field for field.
- `to_csv` and `to_parquet`: the export file. CSV writes a value not reported as an empty cell
  and defuses text a spreadsheet would run as a formula;
  Parquet keeps types (money as decimals with every digit, dates as dates) and carries the
  export's provenance (as-of date, settings version, model, prompt version) as file metadata.
"""

import csv
import datetime as dt
import hashlib
import io
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import polars as pl

from cmbs.extraction import fields as typed_fields

type Row = Mapping[str, Any]


def canonical(value: Any) -> Any:
    """A value as the checksum writes it: decimals and dates as exact text, the rest as is."""
    if isinstance(value, Decimal | dt.date):
        return str(value)
    return value


def checksum(rows: Iterable[Row], columns: Sequence[str]) -> str:
    """SHA-256 of the rows in the order given, every column in the order given."""
    digest = hashlib.sha256()
    for row in rows:
        line = json.dumps([canonical(row.get(column)) for column in columns])
        digest.update(line.encode() + b"\n")
    return digest.hexdigest()


@dataclass(frozen=True)
class Difference:
    """A field that differs between the stored and the rebuilt panel, or a row only one has."""

    key: tuple[Any, ...]
    column: str
    stored: Any
    rebuilt: Any

    def text(self) -> str:
        where = "/".join(str(part) for part in self.key)
        return f"{where} {self.column}: stored {self.stored!r}, rebuilt {self.rebuilt!r}"


ROW = "row"


def differences(
    stored: Mapping[tuple[Any, ...], Row],
    rebuilt: Mapping[tuple[Any, ...], Row],
    columns: Sequence[str],
) -> list[Difference]:
    """Every field that differs, by key; a row only one side has is one difference."""
    found: list[Difference] = []
    for key in sorted(stored.keys() | rebuilt.keys(), key=lambda k: tuple(map(str, k))):
        before, after = stored.get(key), rebuilt.get(key)
        if before is None or after is None:
            found.append(
                Difference(key, ROW, "present" if before else None, "present" if after else None)
            )
            continue
        found += [
            Difference(key, column, before.get(column), after.get(column))
            for column in columns
            if canonical(before.get(column)) != canonical(after.get(column))
        ]
    return found


# ---------------------------------------------------------------------------- the export file

COMMENT = "comment"


def comment_columns() -> list[str]:
    """The extracted fields' columns: the comment's filing and model, then for each field its
    value, its status, the sentence that backs it and who decided or overrode it."""
    columns = [f"{COMMENT}_accession", f"{COMMENT}_model", f"{COMMENT}_prompt_version"]
    for spec in typed_fields.FIELDS:
        base = f"{COMMENT}_{spec.name}"
        columns += [base, f"{base}_status", f"{base}_sentence", f"{base}_by"]
    return columns


def comment_values(view: Any | None) -> dict[str, Any]:
    """A loan-month's extracted fields as export columns; all empty when nothing is extracted.

    `view` is a `cmbs.extraction.service.CommentView` read as of the export date, so values
    awaiting review read "needs review" with no value, and an override reads "overridden".
    """
    values: dict[str, Any] = dict.fromkeys(comment_columns())
    if view is None:
        return values
    values[f"{COMMENT}_accession"] = view.accession
    values[f"{COMMENT}_model"] = view.model
    values[f"{COMMENT}_prompt_version"] = view.prompt_version
    for name, shown in view.fields.items():
        base = f"{COMMENT}_{name}"
        values[base] = shown.value
        values[f"{base}_status"] = shown.status
        values[f"{base}_sentence"] = shown.sentence
        values[f"{base}_by"] = shown.overridden_by or shown.decided_by
    return values


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", text.lower()).strip("-")


def file_name(
    *,
    number: int,
    deal: str | None,
    as_of: dt.date,
    settings_version: int,
    model: str,
    prompt_version: int,
    fmt: str,
) -> str:
    """'fixture-trust-2026-f1_as-of-2026-09-20_settings-7_jev-1.14.0_prompt-3_export-4.csv':
    the file says what produced it."""
    scope = _slug(deal) if deal else "panel"
    return (
        f"{scope}_as-of-{as_of.isoformat()}_settings-{settings_version}_{_slug(model)}"
        f"_prompt-{prompt_version}_export-{number}.{fmt}"
    )


def _text(value: Any) -> str:
    return "" if value is None else str(value)


# A cell a spreadsheet would read as a formula starts with one of these.
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def csv_cell(value: Any) -> str:
    """A value as a CSV cell. Text that a spreadsheet would run as a formula ("=HYPERLINK(...)",
    "@SUM(A1)") gets a leading apostrophe, so it is shown, never run; filed text can say anything.
    Numbers and dates are written as they are (a negative balance stays a number)."""
    if isinstance(value, str) and value.startswith(FORMULA_STARTS):
        return "'" + value
    return _text(value)


def to_csv(rows: Sequence[Row], columns: Sequence[str]) -> bytes:
    """A header, then one line per row; a value not reported is an empty cell."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([csv_cell(column) for column in columns])
    for row in rows:
        writer.writerow([csv_cell(row.get(column)) for column in columns])
    return out.getvalue().encode()


def _scale(values: Iterable[Any]) -> int:
    """The most digits after the point any decimal of a column has."""
    scales = [
        -exponent
        for value in values
        if isinstance(value, Decimal)
        and isinstance(exponent := value.as_tuple().exponent, int)
        and exponent < 0
    ]
    return max(scales, default=0)


def column_type(values: Sequence[Any]) -> pl.DataType:
    """A Parquet column's type from its values: decimal (wide enough for every digit), date,
    integer or text."""
    present = [value for value in values if value is not None]
    if present and all(isinstance(value, Decimal) for value in present):
        return pl.Decimal(38, _scale(present))
    if present and all(isinstance(value, dt.date) for value in present):
        return pl.Date()
    if present and all(isinstance(value, int) and not isinstance(value, bool) for value in present):
        return pl.Int64()
    return pl.String()


def to_parquet(rows: Sequence[Row], columns: Sequence[str], metadata: Mapping[str, str]) -> bytes:
    """The rows typed column by column, with `metadata` in the file's key-value metadata."""
    data: dict[str, list[Any]] = {column: [row.get(column) for row in rows] for column in columns}
    schema = {column: column_type(values) for column, values in data.items()}
    frame = pl.DataFrame(
        {
            column: [
                _text(value) if value is not None and schema[column] == pl.String() else value
                for value in values
            ]
            for column, values in data.items()
        },
        schema=schema,
    )
    out = io.BytesIO()
    frame.write_parquet(out, metadata=dict(metadata))
    return out.getvalue()
