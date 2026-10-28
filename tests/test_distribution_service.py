"""Loading distribution reports and reading them back (cmbs.distribution.service)."""

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from cmbs import clock
from cmbs.access.service import PermissionRefused
from cmbs.audit.service import entries_for_subject
from cmbs.distribution.layouts import Administrator
from cmbs.distribution.parse import NO_COMMENT_COLUMN, ParsedReport
from cmbs.distribution.service import (
    AUDIT_ACTION,
    ConflictingReport,
    LoadedReport,
    StoredAppraisalReduction,
    StoredComment,
    StoredDelinquency,
    UnknownDeal,
    WrongPeriod,
    appraisal_reductions,
    canonical_loan,
    comments_for_loan,
    delinquencies,
    load_report,
    report_period,
    summary_of,
)
from cmbs.distribution.tables import (
    DistributionReport,
    ReportAppraisalReduction,
    ReportComment,
    ReportDelinquency,
)
from cmbs.domain import Period
from cmbs.filings.service import ConflictingFiling
from cmbs.filings.tables import Filing, FilingFile
from tests.conftest import needs_db

FIXTURES = Path(__file__).parent / "fixtures" / "distribution"
REPORT = FIXTURES / "fixture-trust-2026-f1_10d_2026-09.htm"
CITI = FIXTURES / "cgcmt-2016-c1_ex99_1_2026-09.reconstructed.htm"
DEAL = "Fixture Trust 2026-F1"
ACCESSION = "0009900001-26-000009"
FILED = dt.date(2026, 9, 29)
SEPTEMBER = Period(2026, 9)


def parsed(day: dt.date | None) -> ParsedReport:
    return ParsedReport(Administrator.CITI, day, (), (), (), ())


def test_the_period_comes_from_the_report_when_not_given():
    assert report_period(parsed(dt.date(2026, 9, 17)), None) == SEPTEMBER


def test_a_given_period_must_match_the_report():
    assert report_period(parsed(dt.date(2026, 9, 17)), SEPTEMBER) == SEPTEMBER
    assert report_period(parsed(None), SEPTEMBER) == SEPTEMBER
    with pytest.raises(WrongPeriod) as refused:
        report_period(parsed(dt.date(2026, 8, 17)), SEPTEMBER)
    assert str(refused.value) == "the report is for August 2026, not September 2026"


def test_a_report_without_a_date_needs_a_period():
    with pytest.raises(WrongPeriod) as refused:
        report_period(parsed(None), None)
    assert str(refused.value) == "the report does not state its distribution date; give the period"


def test_canonical_loan_ids():
    assert canonical_loan("002") == "2"
    assert canonical_loan(" 2-001 ") == "2-001"


def load(db, *, user="quinn", path=REPORT, name=None, period=None, accession=ACCESSION):
    return load_report(
        db,
        user=user,
        deal=DEAL,
        accession=accession,
        cik=9900001,
        filing_date=FILED,
        file_name=name or path.name,
        content=path.read_bytes(),
        period=period,
    )


def counts(db) -> dict[str, int]:
    tables = {
        "reports": DistributionReport,
        "comments": ReportComment,
        "appraisals": ReportAppraisalReduction,
        "delinquencies": ReportDelinquency,
    }
    return {
        name: db.scalar(select(func.count()).select_from(table)) for name, table in tables.items()
    }


@needs_db
def test_loading_a_report_stores_the_filing_the_file_and_every_item(seeded):
    loaded = load(seeded, period=SEPTEMBER)
    seeded.commit()
    assert loaded == LoadedReport(
        accession=ACCESSION,
        deal=DEAL,
        period=SEPTEMBER,
        administrator="computershare",
        summary=(
            "computershare format\n1 special-servicing comments\n1 appraisal reductions\n"
            "2 loans in delinquency detail"
        ),
        comments=1,
        appraisal_reductions=1,
        delinquencies=2,
    )
    filing = seeded.get(Filing, ACCESSION)
    assert filing is not None
    assert (filing.cik, filing.form, filing.filing_date, filing.period) == (
        9900001,
        "10-D",
        FILED,
        dt.date(2026, 9, 1),
    )
    stored_file = seeded.get(FilingFile, (ACCESSION, REPORT.name))
    assert stored_file is not None and stored_file.content == REPORT.read_bytes()
    report = seeded.get(DistributionReport, ACCESSION)
    assert report is not None
    assert (report.file_name, report.administrator, report.distribution_date, report.period) == (
        REPORT.name,
        "computershare",
        dt.date(2026, 9, 17),
        dt.date(2026, 9, 1),
    )
    assert report.deal_id == filing.deal_id
    assert counts(seeded) == {"reports": 1, "comments": 1, "appraisals": 1, "delinquencies": 2}
    assert summary_of(seeded, ACCESSION) == loaded.summary
    assert summary_of(seeded, "0000000000-00-000000") is None


@needs_db
def test_loading_is_audited(seeded):
    load(seeded)
    seeded.commit()
    [entry] = entries_for_subject(seeded, "filing", ACCESSION)
    assert (entry.user_name, entry.role, entry.action, entry.outcome) == (
        "quinn",
        "quant",
        AUDIT_ACTION,
        "done",
    )
    assert entry.detail == {
        "file": REPORT.name,
        "comments": 1,
        "appraisal_reductions": 1,
        "delinquencies": 2,
    }


@needs_db
def test_loading_the_same_report_twice_changes_nothing(seeded):
    first = load(seeded)
    seeded.commit()
    again = load(seeded)
    seeded.commit()
    assert again == first
    assert counts(seeded) == {"reports": 1, "comments": 1, "appraisals": 1, "delinquencies": 2}


@needs_db
def test_the_same_filing_from_another_file_is_refused(seeded):
    load(seeded)
    seeded.commit()
    with pytest.raises(ConflictingReport) as refused:
        load(seeded, name="other.htm")
    assert str(refused.value) == (
        f"{ACCESSION} was already parsed from {REPORT.name}, not other.htm"
    )


@needs_db
def test_other_bytes_under_the_same_name_are_refused(seeded):
    load(seeded)
    seeded.commit()
    with pytest.raises(ConflictingFiling):
        load(seeded, path=CITI, name=REPORT.name, period=SEPTEMBER)


@needs_db
def test_only_roles_that_run_refreshes_may_load(seeded):
    with pytest.raises(PermissionRefused) as refused:
        load(seeded, user="pat")
    assert str(refused.value) == "pm may not run refreshes and backfills"
    seeded.rollback()
    assert counts(seeded)["reports"] == 0
    load(seeded, user="olly")


@needs_db
def test_a_period_mismatch_writes_nothing(seeded):
    with pytest.raises(WrongPeriod):
        load(seeded, period=Period(2026, 8))
    seeded.rollback()
    assert seeded.get(Filing, ACCESSION) is None


@needs_db
def test_a_citi_report_stores_no_comments_and_says_why(seeded):
    loaded = load(seeded, path=CITI)
    seeded.commit()
    assert loaded.comments == 0
    assert NO_COMMENT_COLUMN in (summary_of(seeded, ACCESSION) or "")
    assert counts(seeded)["comments"] == 0


@needs_db
def test_items_read_back_point_in_time(seeded):
    load(seeded)
    seeded.commit()
    comment = StoredComment(
        accession=ACCESSION,
        period=SEPTEMBER,
        filing_date=FILED,
        source_table="Specially Serviced Loan Detail - Part 2",
        loan="2",
        comment=(
            "The loan transferred to Special Servicing effective 3/6/2025 due to imminent "
            "monetary default. As of 12/31/2024, DSCR NOI was 0.71x and occupancy was 61.4%. "
            "The borrower has requested a 12-month maturity extension. The special servicer is "
            "evaluating the request."
        ),
    )
    assert comments_for_loan(seeded, deal=DEAL, loan="02", as_of=FILED) == [comment]
    assert comments_for_loan(seeded, deal=DEAL, loan="2", as_of=FILED - dt.timedelta(1)) == []
    assert comments_for_loan(seeded, deal=DEAL, loan="4", as_of=FILED) == []
    with clock.frozen(dt.date(2026, 9, 28)):
        assert comments_for_loan(seeded, deal=DEAL, loan="2") == []
    with clock.frozen(FILED):
        assert comments_for_loan(seeded, deal=DEAL, loan="2") == [comment]

    reduction = StoredAppraisalReduction(
        accession=ACCESSION,
        period=SEPTEMBER,
        filing_date=FILED,
        source_table="Mortgage Loan Detail (Part 2)",
        loan="2",
        amount=Decimal("6500000.00"),
        reduction_date=dt.date(2026, 8, 11),
    )
    assert appraisal_reductions(seeded, deal=DEAL, loan="2", period=SEPTEMBER, as_of=FILED) == [
        reduction
    ]
    assert appraisal_reductions(seeded, deal=DEAL, as_of=FILED) == [reduction]
    assert appraisal_reductions(seeded, deal=DEAL, period=Period(2026, 8), as_of=FILED) == []

    found = delinquencies(seeded, deal=DEAL, period=SEPTEMBER, as_of=FILED)
    assert [(item.loan, item.status) for item in found] == [("2", "3"), ("4", "1")]
    assert found[1] == StoredDelinquency(
        accession=ACCESSION,
        period=SEPTEMBER,
        filing_date=FILED,
        source_table="Delinquency Loan Detail",
        loan="4",
        status="1",
        paid_through=dt.date(2026, 7, 11),
        current_advances=Decimal("18500.00"),
        outstanding_advances=Decimal("37000.00"),
        actual_balance=Decimal("4200000.00"),
    )
    assert delinquencies(seeded, deal=DEAL, loan="4", as_of=FILED) == [found[1]]


@needs_db
def test_newest_period_comes_first(seeded):
    load(seeded)
    october = REPORT.read_bytes().replace(b"09/17/26", b"10/16/26")
    october_path = FIXTURES / "october.htm"
    load_report(
        seeded,
        user="quinn",
        deal=DEAL,
        accession="0009900001-26-000010",
        cik=9900001,
        filing_date=dt.date(2026, 10, 28),
        file_name=october_path.name,
        content=october,
    )
    seeded.commit()
    found = comments_for_loan(seeded, deal=DEAL, loan="2", as_of=dt.date(2026, 10, 31))
    assert [item.period for item in found] == [Period(2026, 10), SEPTEMBER]


@needs_db
def test_an_unknown_deal_is_refused(seeded):
    with pytest.raises(UnknownDeal) as refused:
        comments_for_loan(seeded, deal="Nobody 2099-X", loan="1")
    assert str(refused.value) == "no deal named 'Nobody 2099-X'"
