"""Loading a filing again replaces its items: the panel holds exactly what the latest parse found.

A parser change is simulated by replacing `parse_report` in the service, so the same stored file
yields different items the second time.
"""

import dataclasses
import datetime as dt
import threading
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cmbs.audit.service import entries_for_subject
from cmbs.db import engine
from cmbs.distribution import service
from cmbs.distribution.parse import ParsedReport, parse_report
from cmbs.distribution.tables import (
    DistributionReport,
    ReportAppraisalReduction,
    ReportComment,
    ReportDelinquency,
)
from cmbs.testing.bdd import data_snapshot
from tests.conftest import needs_db

pytestmark = needs_db

REPORT = (
    Path(__file__).parent / "fixtures" / "distribution" / "fixture-trust-2026-f1_10d_2026-09.htm"
)
ACCESSION = "0009900001-26-000009"
FIRST = parse_report(REPORT.read_bytes())
# What a changed parser finds in the same file: the comment gone, the reduction restated.
SECOND = dataclasses.replace(
    FIRST,
    comments=(),
    appraisal_reductions=(
        dataclasses.replace(FIRST.appraisal_reductions[0], amount=Decimal("6000000.00")),
    ),
)


def load(db: Session) -> service.LoadedReport:
    return service.load_report(
        db,
        user="quinn",
        deal="Fixture Trust 2026-F1",
        accession=ACCESSION,
        cik=9900001,
        filing_date=dt.date(2026, 9, 29),
        file_name=REPORT.name,
        content=REPORT.read_bytes(),
    )


def parser_returning(*results: ParsedReport):
    """A stand-in for `parse_report` that returns each result in turn."""
    queue = list(results)
    return lambda _content: queue.pop(0)


def stored(db: Session) -> dict[str, Any]:
    """Everything stored for the filing, as plain values."""
    db.expire_all()
    report = db.get(DistributionReport, ACCESSION)
    assert report is not None
    return {
        "report": (report.administrator, report.distribution_date, report.summary),
        "comments": sorted(
            (row.loan_id, row.source_table, row.comment)
            for row in db.scalars(select(ReportComment))
        ),
        "appraisals": sorted(
            (row.loan_id, row.source_table, row.amount, row.reduction_date)
            for row in db.scalars(select(ReportAppraisalReduction))
        ),
        "delinquencies": sorted(
            (row.loan_id, row.status, row.paid_through, row.actual_balance)
            for row in db.scalars(select(ReportDelinquency))
        ),
    }


def expected(parsed: ParsedReport) -> dict[str, Any]:
    return {
        "report": (parsed.administrator.value, parsed.distribution_date, parsed.summary),
        "comments": sorted((c.loan, c.source_table, c.text) for c in parsed.comments),
        "appraisals": sorted(
            (a.loan, a.source_table, a.amount, a.reduction_date)
            for a in parsed.appraisal_reductions
        ),
        "delinquencies": sorted(
            (d.loan, d.status, d.paid_through, d.actual_balance) for d in parsed.delinquencies
        ),
    }


def test_a_reparse_leaves_only_what_the_new_parse_found(seeded, monkeypatch):
    monkeypatch.setattr(service, "parse_report", parser_returning(FIRST, SECOND))
    load(seeded)
    seeded.commit()
    assert stored(seeded) == expected(FIRST)
    reloaded = load(seeded)
    seeded.commit()
    assert stored(seeded) == expected(SECOND)
    assert stored(seeded)["comments"] == []
    assert stored(seeded)["appraisals"] == [
        ("2", "Mortgage Loan Detail (Part 2)", Decimal("6000000.00"), dt.date(2026, 8, 11))
    ]
    assert (reloaded.comments, reloaded.appraisal_reductions) == (0, 1)
    details = [entry.detail for entry in entries_for_subject(seeded, "filing", ACCESSION)]
    assert details == [
        {"file": REPORT.name, "comments": 0, "appraisal_reductions": 1, "delinquencies": 2},
        {"file": REPORT.name, "comments": 1, "appraisal_reductions": 1, "delinquencies": 2},
    ]


def test_loading_the_same_file_twice_leaves_identical_rows(seeded):
    load(seeded)
    seeded.commit()
    before, snapshot = stored(seeded), data_snapshot()
    load(seeded)
    seeded.commit()
    assert stored(seeded) == before == expected(FIRST)
    assert data_snapshot() == snapshot
    assert len(entries_for_subject(seeded, "filing", ACCESSION)) == 2


def test_concurrent_loads_end_with_one_consistent_set_of_items(seeded, monkeypatch):
    """The second load waits for the first to commit, then replaces its items whole."""
    monkeypatch.setattr(service, "parse_report", parser_returning(FIRST, SECOND))
    outcome: dict[str, object] = {}

    def second_load() -> None:
        with Session(engine.engine(), expire_on_commit=False) as other:
            try:
                outcome["value"] = load(other)
                other.commit()
            except Exception as error:  # reported by the assertion below
                outcome["error"] = error

    with Session(engine.engine(), expire_on_commit=False) as holder:
        load(holder)  # holds its locks until it commits
        thread = threading.Thread(target=second_load)
        thread.start()
        time.sleep(0.3)  # let the second load reach the rows the first one holds
        assert thread.is_alive(), "the second load did not wait for the first"
        holder.commit()
        thread.join(timeout=10)
    assert "error" not in outcome, f"second load failed: {outcome.get('error')!r}"
    assert stored(seeded) == expected(SECOND)
