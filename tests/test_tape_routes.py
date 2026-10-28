"""POST /deals/{deal}/tapes: load an EX-102 file sent as the request body."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from cmbs.api.app import create_app
from cmbs.audit.tables import AuditEntry
from cmbs.tape.tables import LoanPeriod
from tests.conftest import needs_db
from tests.fixtures.tape.tapes import asset, tape

DEAL = "Fixture Trust 2026-F1"
XML = {"Content-Type": "application/xml"}
SMALL = tape(
    asset("1", reportPeriodEndActualBalanceAmount="50000000.00"),
    asset("5", reportPeriodEndActualBalanceAmount="-12000000.00"),
)
PARAMS = {"accession": "0009900001-26-000101", "filing_date": "2026-09-29", "cik": "9900001"}


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def post(client: TestClient, user: str = "quinn", content: bytes = SMALL, **params):
    headers = XML | {"X-CMBS-User": user}
    return client.post(
        f"/deals/{DEAL}/tapes", params=PARAMS | params, content=content, headers=headers
    )


def test_loading_needs_a_user(client):
    response = client.post(f"/deals/{DEAL}/tapes", params=PARAMS, content=SMALL, headers=XML)
    assert response.status_code == 401


@needs_db
def test_a_tape_is_loaded_and_audited_under_the_caller(seeded, client):
    response = post(client)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "deal": DEAL,
        "accession": "0009900001-26-000101",
        "period": "September 2026",
        "filing_date": "2026-09-29",
        "file_sha256": response.json()["file_sha256"],
        "assets": 2,
        "loans_loaded": 1,
        "quarantined": [{"loan": "5", "reason": "ending balance is negative"}],
        "notes": [],
        "already_loaded": False,
        "summary": (
            "Fixture Trust 2026-F1 September 2026 (0009900001-26-000101, filed 2026-09-29): "
            "loaded; 2 assets, 1 loan loaded, 1 loan quarantined"
        ),
    }
    assert len(response.json()["file_sha256"]) == 64
    assert seeded.scalar(select(func.count()).select_from(LoanPeriod)) == 1
    users = set(seeded.scalars(select(AuditEntry.user_name).where(AuditEntry.subject == "loan")))
    assert users == {"quinn"}


@needs_db
def test_posting_again_changes_nothing(seeded, client):
    post(client)
    again = post(client)
    assert again.status_code == 200
    assert again.json()["already_loaded"] is True


@needs_db
def test_a_pm_may_not_load_tapes(seeded, client):
    response = post(client, user="pat")
    assert response.status_code == 403
    assert response.json() == {"detail": "pm may not run refreshes and backfills"}
    assert seeded.scalar(select(func.count()).select_from(LoanPeriod)) == 0


@needs_db
def test_a_refused_tape_is_a_readable_response_and_is_audited(seeded, client):
    response = post(client, content=b"not xml")
    assert response.status_code == 422
    assert response.json() == {"detail": "the file is not XML: syntax error: line 1, column 0"}
    entry = seeded.scalars(select(AuditEntry).where(AuditEntry.outcome == "refused")).one()
    assert (entry.user_name, entry.action, entry.subject_key) == (
        "quinn",
        "load loan tape",
        "0009900001-26-000101",
    )


@needs_db
def test_an_amendment_names_what_it_amends(seeded, client):
    post(client)
    response = post(
        client,
        accession="0009900001-26-000102",
        filing_date="2026-10-05",
        form="ABS-EE/A",
        amends="0009900001-26-000101",
        file_name="corrected.xml",
    )
    assert response.status_code == 200, response.text
    assert response.json()["accession"] == "0009900001-26-000102"
