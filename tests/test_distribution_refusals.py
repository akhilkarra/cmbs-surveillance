"""Refused loads: audited, checked before anything is written, never erasing stored items."""

import dataclasses
import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cmbs.audit.service import entries_for_subject
from cmbs.deals.service import get_or_create
from cmbs.distribution import service
from cmbs.distribution.parse import parse_report
from cmbs.distribution.service import AUDIT_ACTION
from cmbs.distribution.tables import ReportAppraisalReduction, ReportComment, ReportDelinquency
from cmbs.domain import Period
from cmbs.errors import Refused
from cmbs.filings.tables import FilingFile
from tests.conftest import needs_db

pytestmark = needs_db

FIXTURES = Path(__file__).parent / "fixtures" / "distribution"
REPORT = FIXTURES / "fixture-trust-2026-f1_10d_2026-09.htm"
CITI = FIXTURES / "0001628297-26-000742" / "ex99_1.htm"
ACCESSION = "0009900001-26-000009"
DEAL = "Fixture Trust 2026-F1"


def load(
    db: Session,
    *,
    content: bytes | None = None,
    name: str = REPORT.name,
    period: Period | None = None,
) -> service.LoadedReport:
    return service.load_report(
        db,
        user="quinn",
        deal=DEAL,
        accession=ACCESSION,
        cik=9900001,
        filing_date=dt.date(2026, 9, 29),
        file_name=name,
        content=REPORT.read_bytes() if content is None else content,
        period=period,
    )


def stored_items(db: Session) -> int:
    tables = (ReportComment, ReportAppraisalReduction, ReportDelinquency)
    return sum(db.scalar(select(func.count()).select_from(table)) or 0 for table in tables)


def loaded_once(db: Session) -> None:
    load(db)
    db.commit()


def deal_with_another_cik(db: Session) -> None:
    get_or_create(db, DEAL, cik=1)
    db.commit()


CASES = {
    "unreadable": (None, {"content": b""}, "the distribution report is empty or not HTML"),
    "unrecognised format": (
        None,
        {"content": b"<html><body><p>A trustee statement</p></body></html>"},
        "the report's certificate administrator is not recognised",
    ),
    "wrong period": (
        None,
        {"period": Period(2026, 8)},
        "the report is for September 2026, not August 2026",
    ),
    "another file": (
        loaded_once,
        {"name": "other.htm"},
        f"{ACCESSION} was already parsed from {REPORT.name}, not other.htm",
    ),
    "other bytes under the same name": (
        loaded_once,
        {"content": CITI.read_bytes()},
        f"{ACCESSION}/{REPORT.name} is already stored with other content",
    ),
    "deal registered with another CIK": (
        deal_with_another_cik,
        {},
        f"{DEAL} already has CIK 1, not 9900001",
    ),
}


@pytest.mark.parametrize("case", CASES)
def test_every_refused_load_is_audited_and_the_record_survives_the_rollback(seeded, case):
    setup, arguments, message = CASES[case]
    if setup is not None:
        setup(seeded)
    with pytest.raises(Refused) as refused:
        load(seeded, **arguments)
    assert str(refused.value) == message
    seeded.rollback()
    refusals = [
        entry
        for entry in entries_for_subject(seeded, "filing", ACCESSION)
        if entry.outcome == "refused"
    ]
    assert [(e.user_name, e.role, e.action, e.reason) for e in refusals] == [
        ("quinn", "quant", AUDIT_ACTION, message)
    ]


def test_another_file_is_refused_before_anything_is_written(seeded):
    loaded_once(seeded)
    with pytest.raises(Refused):
        load(seeded, name="other.htm")
    # Still inside the refused transaction: the other file was never stored.
    assert seeded.get(FilingFile, (ACCESSION, "other.htm")) is None


def test_a_reload_that_finds_nothing_keeps_the_stored_items(seeded, monkeypatch):
    first = parse_report(REPORT.read_bytes())
    empty = dataclasses.replace(first, comments=(), appraisal_reductions=(), delinquencies=())
    monkeypatch.setattr(service, "parse_report", lambda _content: first)
    loaded_once(seeded)
    monkeypatch.setattr(service, "parse_report", lambda _content: empty)
    with pytest.raises(Refused) as refused:
        load(seeded)
    assert str(refused.value) == (
        f"{ACCESSION}: this parse of {REPORT.name} finds no comments, appraisal reductions or "
        "delinquencies, so the 4 items stored from it are kept"
    )
    seeded.rollback()
    assert stored_items(seeded) == 4


def test_a_first_load_that_finds_nothing_is_stored(seeded, monkeypatch):
    first = parse_report(REPORT.read_bytes())
    empty = dataclasses.replace(first, comments=(), appraisal_reductions=(), delinquencies=())
    monkeypatch.setattr(service, "parse_report", lambda _content: empty)
    loaded = load(seeded)
    seeded.commit()
    assert (loaded.comments, loaded.appraisal_reductions, loaded.delinquencies) == (0, 0, 0)
    assert stored_items(seeded) == 0
    load(seeded)  # nothing stored, so nothing to lose
