"""Report-to-tape reconciliation (cmbs.distribution.reconcile): the rules, then the database."""

import datetime as dt
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from typer.testing import CliRunner

from cmbs.api.app import create_app
from cmbs.audit.tables import AuditEntry
from cmbs.cli import app as cli
from cmbs.deals.service import by_name
from cmbs.distribution.reconcile import (
    CHECK,
    Disagreement,
    NothingToReconcile,
    Reconciliation,
    ReportLine,
    TapeLine,
    code,
    compare,
    flags,
    has_delinquency_table,
    reconcile,
    reconcile_month,
    status_name,
)
from cmbs.distribution.service import UnknownDeal, load_report
from cmbs.domain import Period
from cmbs.tape.service import load_tape
from tests.conftest import needs_db

FIXTURES = Path(__file__).parent / "fixtures"
REPORT = FIXTURES / "distribution" / "fixture-trust-2026-f1_10d_2026-09.htm"
TAPE = FIXTURES / "schedule_al_minimal.xml"
DEAL, CIK = "Fixture Trust 2026-F1", 9_900_001
SEPTEMBER = Period(2026, 9)
FILED = dt.date(2026, 9, 29)


def listed(loan: str, status: str | None = None) -> ReportLine:
    return ReportLine(loan, status, "Delinquency Loan Detail", "R")


def tape(loan: str, status: str | None) -> TapeLine:
    return TapeLine(loan, status, "T")


def messages(report, rows, *, has_table=True) -> dict[str, str]:
    found = compare(
        {r.loan: r for r in report}, {t.loan: t for t in rows}, report_has_table=has_table
    )
    return {item.loan: item.message for item in found}


# ---------------------------------------------------------------------------- the rules


def test_codes_are_read_without_case_or_spaces():
    assert code(" b ") == "B"
    assert code("3") == "3"
    assert code("") is None
    assert code("  ") is None
    assert code(None) is None


def test_status_names():
    assert status_name("0") == "current"
    assert status_name("1") == "30 to 59 days delinquent"
    assert status_name("3") == "90 days or more delinquent"
    assert status_name("A") == "in its grace period"
    assert status_name("9") == "payment status '9', which is not a known code"


def test_a_listed_loan_delinquent_on_the_tape_agrees():
    report = [listed("2", "3"), listed("4", "1"), listed("5"), listed("6", "B"), listed("7", "x")]
    rows = [tape("2", "3"), tape("4", "1"), tape("5", "5"), tape("6", "B"), tape("7", "A")]
    rows += [tape("1", "0"), tape("3", None), tape("8", "4"), tape("9", "A")]
    assert messages(report, rows) == {}


def test_a_listed_loan_current_on_the_tape_is_flagged():
    report = [listed("4", "1"), listed("8", "1"), listed("9", "1"), listed("10")]
    rows = [tape("4", "0"), tape("8", "4"), tape("9", None), tape("10", "7")]
    assert messages(report, rows) == {
        "4": "report says delinquent, tape says current",
        "8": "report says delinquent, tape says a performing matured balloon",
        "9": "report says delinquent, tape reports no payment status",
        "10": "report says delinquent, tape says payment status '7', which is not a known code",
    }


def test_a_listed_loan_missing_from_the_tape_is_flagged():
    assert messages([listed("11", "3")], [tape("2", "0")]) == {
        "11": "report says delinquent, tape does not list the loan"
    }


def test_both_behind_but_on_different_codes_is_flagged():
    report = [listed("2", "3"), listed("4", " a "), listed("5", "1")]
    rows = [tape("2", "1"), tape("4", "2"), tape("5", "a")]
    assert messages(report, rows) == {
        "2": "report says 90 days or more delinquent, tape says 30 to 59 days delinquent",
        "4": "report says in its grace period, tape says 60 to 89 days delinquent",
        "5": "report says 30 to 59 days delinquent, tape says in its grace period",
    }


def test_a_delinquent_loan_the_report_leaves_out_is_flagged():
    rows = [tape("1", "1"), tape("2", "2"), tape("3", "3"), tape("5", "5")]
    rows += [tape("6", "A"), tape("7", "B"), tape("8", "0"), tape("9", None), tape("10", "4")]
    assert messages([], rows) == {
        "1": "tape says 30 to 59 days delinquent, report does not list the loan",
        "2": "tape says 60 to 89 days delinquent, report does not list the loan",
        "3": "tape says 90 days or more delinquent, report does not list the loan",
        "5": "tape says a non-performing matured balloon, report does not list the loan",
    }


def test_a_report_without_a_delinquency_table_cannot_leave_a_loan_out():
    assert messages([], [tape("3", "3")], has_table=False) == {}
    # What it does list is still checked.
    assert messages([listed("4", "1")], [tape("4", "0")], has_table=False) == {
        "4": "report says delinquent, tape says current"
    }


def test_disagreements_come_in_loan_order_and_keep_both_sides():
    found = compare(
        {"10": listed("10", "1"), "2": listed("2", "1")},
        {"10": tape("10", "0"), "2": tape("2", "0"), "9": tape("9", "3")},
        report_has_table=True,
    )
    assert [item.loan for item in found] == ["2", "9", "10"]
    assert found[0].sides() == {
        "report": {
            "accession": "R",
            "source_table": "Delinquency Loan Detail",
            "listed_as_delinquent": True,
            "status": "1",
        },
        "tape": {"accession": "T", "payment_status": "0"},
    }
    assert found[1].sides() == {"report": None, "tape": {"accession": "T", "payment_status": "3"}}
    assert Disagreement("1", "m", None, None).sides() == {"report": None, "tape": None}


def test_whether_the_report_had_a_delinquency_table():
    summary = "computershare format\n1 special-servicing comments"
    assert has_delinquency_table("computershare", summary, 0)
    assert has_delinquency_table("citi", summary, 0)
    missing = summary + "\nno Delinquency Loan Detail table"
    assert not has_delinquency_table("computershare", missing, 0)
    assert has_delinquency_table("computershare", missing, 2)


def test_reconciliation_text():
    passed = Reconciliation(DEAL, SEPTEMBER, "R", "T", (), 0)
    assert passed.passed
    assert passed.text() == (
        "Fixture Trust 2026-F1 September 2026: report R, tape T: the report and the tape agree"
    )
    item = Disagreement("4", "report says delinquent, tape says current", None, None)
    failed = Reconciliation(DEAL, SEPTEMBER, "R", "T", (item,), 1)
    assert not failed.passed
    assert failed.text() == (
        "Fixture Trust 2026-F1 September 2026: report R, tape T: 1 loans disagree\n"
        "  loan 4: report says delinquent, tape says current"
    )


# ---------------------------------------------------------------------------- the database


def with_status(content: bytes, loan: str, status: str) -> bytes:
    """Set one loan's payment status in the fixture tape (text edit: the tags are simple)."""
    text = content.decode()
    pattern = re.compile(rf"(<assetNumber>{re.escape(loan)}</assetNumber>.*?)(</assets>)", re.S)
    match = pattern.search(text)
    assert match is not None
    block = re.sub(r"\s*<paymentStatusLoanCode>[^<]*</paymentStatusLoanCode>", "", match.group(1))
    block = block.replace(
        "</reportingPeriodEndDate>",
        f"</reportingPeriodEndDate>\n    <paymentStatusLoanCode>{status}</paymentStatusLoanCode>",
        1,
    )
    return (text[: match.start()] + block + match.group(2) + text[match.end() :]).encode()


def load_month(db, *, report_filed=FILED, tape_filed=FILED, statuses=None, tape_accession="T1"):
    load_report(
        db,
        user="quinn",
        deal=DEAL,
        accession="0009900001-26-000009",
        cik=CIK,
        filing_date=report_filed,
        file_name=REPORT.name,
        content=REPORT.read_bytes(),
        period=SEPTEMBER,
    )
    content = TAPE.read_bytes()
    for loan, status in (statuses or {"4": "1"}).items():
        content = with_status(content, loan, status)
    load_tape(
        db,
        deal=DEAL,
        content=content,
        file_name=TAPE.name,
        accession=f"0009900001-26-00001{tape_accession[-1]}",
        filing_date=tape_filed,
        cik=CIK,
    )
    db.commit()
    deal = by_name(db, DEAL)
    assert deal is not None
    return deal.id


@needs_db
def test_an_agreeing_month_passes_and_raises_nothing(seeded):
    deal_id = load_month(seeded)
    result = reconcile(seeded, deal_id=deal_id, period=SEPTEMBER, as_of=dt.date(2026, 10, 1))
    assert result.passed, result.text()
    assert (result.deal, result.report_accession, result.tape_accession) == (
        DEAL,
        "0009900001-26-000009",
        "0009900001-26-000011",
    )
    assert flags(seeded, deal_id=deal_id, period=SEPTEMBER) == []


@needs_db
def test_a_disagreement_is_flagged_once_with_both_sides(seeded):
    deal_id = load_month(seeded, statuses={"4": "0", "5": "2"})
    day = dt.date(2026, 10, 1)
    first = reconcile(seeded, deal_id=deal_id, period=SEPTEMBER, as_of=day)
    assert [(d.loan, d.message) for d in first.disagreements] == [
        ("4", "report says delinquent, tape says current"),
        ("5", "tape says 60 to 89 days delinquent, report does not list the loan"),
    ]
    assert first.flags_raised == 2
    again = reconcile(seeded, deal_id=deal_id, period=SEPTEMBER, as_of=day)
    assert again.flags_raised == 0
    assert len(again.disagreements) == 2
    stored = flags(seeded, deal_id=deal_id, period=SEPTEMBER)
    assert [(f.loan, f.check, f.period, f.message) for f in stored] == [
        ("4", CHECK, SEPTEMBER.first_day, "report says delinquent, tape says current"),
        (
            "5",
            CHECK,
            SEPTEMBER.first_day,
            "tape says 60 to 89 days delinquent, report does not list the loan",
        ),
    ]
    assert stored[0].sides == {
        "report": {
            "accession": "0009900001-26-000009",
            "source_table": "Delinquency Loan Detail",
            "listed_as_delinquent": True,
            "status": "1",
        },
        "tape": {"accession": "0009900001-26-000011", "payment_status": "0"},
    }
    assert flags(seeded, deal_id=deal_id, period=Period(2026, 8)) == []


@needs_db
def test_both_sides_are_read_point_in_time(seeded):
    deal_id = load_month(seeded, report_filed=dt.date(2026, 9, 29), tape_filed=dt.date(2026, 9, 30))
    with pytest.raises(NothingToReconcile) as early:
        reconcile(seeded, deal_id=deal_id, period=SEPTEMBER, as_of=dt.date(2026, 9, 28))
    assert str(early.value) == (
        "no September 2026 distribution report had been filed by 2026-09-28"
    )
    with pytest.raises(NothingToReconcile) as no_tape:
        reconcile(seeded, deal_id=deal_id, period=SEPTEMBER, as_of=dt.date(2026, 9, 29))
    assert str(no_tape.value) == "no September 2026 loan tape had been filed by 2026-09-29"
    assert reconcile(seeded, deal_id=deal_id, period=SEPTEMBER, as_of=dt.date(2026, 9, 30)).passed


@needs_db
def test_reconcile_month_is_authorized_and_audited(seeded):
    load_month(seeded, statuses={"4": "0"})
    result = reconcile_month(
        seeded, user="olly", deal=DEAL, period=SEPTEMBER, as_of=dt.date(2026, 10, 1)
    )
    seeded.commit()
    assert result.flags_raised == 1
    entry = seeded.scalars(
        select(AuditEntry).where(AuditEntry.action == "reconcile report with tape")
    ).one()
    assert (entry.user_name, entry.role, entry.subject_key, entry.outcome) == (
        "olly",
        "operator",
        DEAL,
        "done",
    )
    assert entry.detail == {
        "period": "September 2026",
        "report": "0009900001-26-000009",
        "tape": "0009900001-26-000011",
        "disagreements": 1,
        "flags_raised": 1,
    }
    with pytest.raises(UnknownDeal, match="no deal named 'Nobody 2026-X'"):
        reconcile_month(seeded, user="olly", deal="Nobody 2026-X", period=SEPTEMBER)
    refused = seeded.scalars(
        select(AuditEntry).where(
            AuditEntry.action == "reconcile report with tape", AuditEntry.outcome == "refused"
        )
    ).one()
    assert (refused.subject_key, refused.reason) == (
        "Nobody 2026-X",
        "no deal named 'Nobody 2026-X'",
    )


@needs_db
def test_the_cli_and_api_reconcile(seeded):
    load_month(seeded, statuses={"4": "0"})
    runner = CliRunner()
    done = runner.invoke(
        cli,
        [
            "reports",
            "reconcile",
            "--deal",
            DEAL,
            "--period",
            "September 2026",
            "--user",
            "quinn",
            "--as-of",
            "2026-10-01",
        ],
    )
    assert done.exit_code == 0, done.output
    assert done.output == (
        "Fixture Trust 2026-F1 September 2026: report 0009900001-26-000009, tape "
        "0009900001-26-000011: 1 loans disagree\n"
        "  loan 4: report says delinquent, tape says current\n"
    )
    refused = runner.invoke(
        cli, ["reports", "reconcile", "--deal", DEAL, "--period", "September 2026", "--user", "pat"]
    )
    assert (refused.exit_code, refused.output) == (1, "pm may not run refreshes and backfills\n")
    client = TestClient(create_app())
    answer = client.post(
        "/reports/reconcile",
        params={"deal": DEAL, "period": "September 2026", "as_of": "2026-10-01"},
        headers={"X-CMBS-User": "quinn"},
    )
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert (body["passed"], body["flags_raised"], body["report"], body["tape"]) == (
        False,
        0,
        "0009900001-26-000009",
        "0009900001-26-000011",
    )
    assert body["disagreements"][0]["message"] == "report says delinquent, tape says current"
    assert body["disagreements"][0]["sides"]["tape"]["payment_status"] == "0"
    bad = client.post(
        "/reports/reconcile",
        params={"deal": DEAL, "period": "Sept 2026"},
        headers={"X-CMBS-User": "quinn"},
    )
    assert bad.status_code == 422
    assert bad.json() == {"detail": "not a period like 'September 2026': 'Sept 2026'"}
