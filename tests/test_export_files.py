"""Rows as checksums and export files (cmbs.export.files). No database."""

import datetime as dt
import hashlib
import io
from decimal import Decimal
from types import SimpleNamespace

import polars as pl

from cmbs.export.files import (
    ROW,
    Difference,
    canonical,
    checksum,
    column_type,
    comment_columns,
    comment_values,
    differences,
    file_name,
    to_csv,
    to_parquet,
)
from cmbs.extraction import fields as typed_fields

DAY = dt.date(2026, 9, 1)


def test_canonical_writes_decimals_and_dates_exactly():
    assert canonical(Decimal("50000000.00")) == "50000000.00"
    assert canonical(Decimal("1.4500")) == "1.4500"
    assert canonical(DAY) == "2026-09-01"
    assert canonical(dt.datetime(2026, 9, 1, 9, 30, tzinfo=dt.UTC)) == "2026-09-01 09:30:00+00:00"
    assert canonical(7) == 7
    assert canonical(None) is None
    assert canonical("x") == "x"


def test_checksum_is_of_the_ordered_rows_and_every_digit():
    rows = [{"loan": "1", "balance": Decimal("1.00")}, {"loan": "2", "balance": None}]
    expected = hashlib.sha256(b'["1", "1.00"]\n["2", null]\n').hexdigest()
    assert checksum(rows, ["loan", "balance"]) == expected
    assert checksum(list(reversed(rows)), ["loan", "balance"]) != expected
    assert checksum([{"loan": "1", "balance": Decimal("1.0")}, rows[1]], ["loan", "balance"]) != (
        expected
    )
    assert checksum(rows, ["balance", "loan"]) != expected
    assert checksum([], ["loan"]) == hashlib.sha256(b"").hexdigest()


def test_differences_field_for_field_and_rows_on_one_side():
    columns = ["loan", "balance", "status"]
    stored = {
        ("a",): {"loan": "a", "balance": Decimal("1.00"), "status": "0"},
        ("b",): {"loan": "b", "balance": Decimal("2.00"), "status": "0"},
        ("c",): {"loan": "c", "balance": None, "status": None},
    }
    rebuilt = {
        ("a",): {"loan": "a", "balance": Decimal("1.00"), "status": "0"},
        ("b",): {"loan": "b", "balance": Decimal("2.0"), "status": "1"},
        ("d",): {"loan": "d", "balance": None, "status": None},
    }
    assert differences(stored, rebuilt, columns) == [
        Difference(("b",), "balance", Decimal("2.00"), Decimal("2.0")),
        Difference(("b",), "status", "0", "1"),
        Difference(("c",), ROW, "present", None),
        Difference(("d",), ROW, None, "present"),
    ]
    assert differences(stored, stored, columns) == []
    assert Difference((1, DAY, "A", "7"), "balance", Decimal("1"), None).text() == (
        "1/2026-09-01/A/7 balance: stored Decimal('1'), rebuilt None"
    )


def test_comment_columns_cover_every_field():
    columns = comment_columns()
    assert columns[:3] == ["comment_accession", "comment_model", "comment_prompt_version"]
    assert columns[3:7] == [
        "comment_transfer_reason",
        "comment_transfer_reason_status",
        "comment_transfer_reason_sentence",
        "comment_transfer_reason_by",
    ]
    assert len(columns) == 3 + 4 * len(typed_fields.FIELDS)


def _shown(value, status, sentence=None, decided_by=None, **extra):
    return SimpleNamespace(
        value=value, status=status, sentence=sentence, decided_by=decided_by, **extra
    )


def test_comment_values_mark_review_and_overrides():
    assert set(comment_values(None).values()) == {None}
    view = SimpleNamespace(
        accession="A-1",
        model="jev-1.14.0",
        prompt_version=3,
        fields={
            "borrower_request": _shown(
                "forbearance", "overridden", 3, decided_by=None, overridden_by="alex"
            ),
            "workout_strategy": _shown(None, "needs review"),
            "transfer_date": _shown("2025-03-06", "human-reviewed", 1, decided_by="morgan"),
        },
    )
    values = comment_values(view)
    assert (values["comment_accession"], values["comment_model"]) == ("A-1", "jev-1.14.0")
    assert values["comment_prompt_version"] == 3
    assert [
        values[f"comment_borrower_request{s}"] for s in ("", "_status", "_sentence", "_by")
    ] == [
        "forbearance",
        "overridden",
        3,
        "alex",
    ]
    assert [
        values[f"comment_workout_strategy{s}"] for s in ("", "_status", "_sentence", "_by")
    ] == [
        None,
        "needs review",
        None,
        None,
    ]
    assert values["comment_transfer_date_by"] == "morgan"
    assert values["comment_dscr"] is None


def test_file_names_say_what_produced_them():
    common = {"as_of": DAY, "settings_version": 7, "model": "jev-1.14.0", "prompt_version": 3}
    assert file_name(number=4, deal=None, fmt="csv", **common) == (
        "panel_as-of-2026-09-01_settings-7_jev-1.14.0_prompt-3_export-4.csv"
    )
    assert file_name(number=5, deal="Fixture Trust 2026-F1", fmt="parquet", **common) == (
        "fixture-trust-2026-f1_as-of-2026-09-01_settings-7_jev-1.14.0_prompt-3_export-5.parquet"
    )


def test_csv_writes_not_reported_as_empty():
    rows = [
        {"loan": "1", "balance": Decimal("1.00"), "end": DAY, "n": 0},
        {"loan": "2, A", "balance": None, "end": None, "n": None},
    ]
    assert to_csv(rows, ["loan", "balance", "end", "n"]) == (
        b'loan,balance,end,n\n1,1.00,2026-09-01,0\n"2, A",,,\n'
    )
    assert to_csv([], ["loan"]) == b"loan\n"


def test_column_types():
    assert column_type([Decimal("1.45"), None, Decimal("0.8812")]) == pl.Decimal(38, 4)
    assert column_type([Decimal("100"), Decimal("1E+2")]) == pl.Decimal(38, 0)
    assert column_type([DAY, None]) == pl.Date()
    assert column_type([1, None, 3]) == pl.Int64()
    assert column_type([True]) == pl.String()
    assert column_type([None, None]) == pl.String()
    assert column_type([]) == pl.String()
    assert column_type([1, "a"]) == pl.String()
    assert column_type([Decimal("1"), 1]) == pl.String()
    assert column_type([DAY, "a"]) == pl.String()


def test_parquet_keeps_types_and_metadata():
    rows = [
        {"loan": "1", "dscr": Decimal("1.45"), "end": DAY, "n": 3, "mixed": 1},
        {"loan": "2", "dscr": Decimal("0.8812"), "end": None, "n": None, "mixed": "a"},
    ]
    content = to_parquet(rows, ["loan", "dscr", "end", "n", "mixed"], {"as_of": "2026-09-01"})
    frame = pl.read_parquet(io.BytesIO(content))
    assert frame.schema == pl.Schema(
        {
            "loan": pl.String(),
            "dscr": pl.Decimal(38, 4),
            "end": pl.Date(),
            "n": pl.Int64(),
            "mixed": pl.String(),
        }
    )
    assert frame.rows() == [
        ("1", Decimal("1.4500"), DAY, 3, "1"),
        ("2", Decimal("0.8812"), None, None, "a"),
    ]
    assert pl.read_parquet_metadata(io.BytesIO(content))["as_of"] == "2026-09-01"
