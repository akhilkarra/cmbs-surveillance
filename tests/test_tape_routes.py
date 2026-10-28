"""POST /deals/{deal}/tapes: load an EX-102 file sent as the request body.

The tests share one module database (`app_database`); each posts under its own deal, CIK and
accession, so what one test commits never changes what another sees.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from cmbs.api.app import create_app
from cmbs.audit.tables import AuditEntry
from cmbs.deals.tables import Deal
from cmbs.tape.tables import LoanPeriod
from tests.conftest import needs_db
from tests.fixtures.tape.database import app_database_fixture, module_database_fixture  # noqa: F401
from tests.fixtures.tape.tapes import asset, tape

XML = {"Content-Type": "application/xml"}
SMALL = tape(
    asset("1", reportPeriodEndActualBalanceAmount="50000000.00"),
    asset("5", reportPeriodEndActualBalanceAmount="-12000000.00"),
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def post(client: TestClient, number: int, user: str = "quinn", content: bytes = SMALL, **params):
    """Post as deal "Route Trust <number>" with CIK 99002<number> and accession
    ...-26-000<number>, unless overridden."""
    query = {
        "accession": f"0009900001-26-{number:06d}",
        "filing_date": "2026-09-29",
        "cik": str(9_900_200 + number),
    } | params
    headers = XML | {"X-CMBS-User": user}
    return client.post(
        f"/deals/Route Trust {number}/tapes", params=query, content=content, headers=headers
    )


def loan_rows(db, deal: str) -> int:
    query = select(func.count()).select_from(LoanPeriod).join(Deal).where(Deal.name == deal)
    return db.scalar(query) or 0


def test_loading_needs_a_user(client):
    response = client.post(
        "/deals/Route Trust 1/tapes",
        params={"accession": "0009900001-26-000001", "filing_date": "2026-09-29"},
        content=SMALL,
        headers=XML,
    )
    assert response.status_code == 401


@needs_db
def test_a_tape_is_loaded_and_audited_under_the_caller(app_database, client):
    response = post(client, 1)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "deal": "Route Trust 1",
        "accession": "0009900001-26-000001",
        "period": "September 2026",
        "filing_date": "2026-09-29",
        "file_sha256": response.json()["file_sha256"],
        "assets": 2,
        "loans_loaded": 1,
        "quarantined": [{"loan": "5", "reason": "ending balance is negative"}],
        "notes": [],
        "already_loaded": False,
        "summary": (
            "Route Trust 1 September 2026 (0009900001-26-000001, filed 2026-09-29): "
            "loaded; 2 assets, 1 loan loaded, 1 loan quarantined"
        ),
    }
    assert len(response.json()["file_sha256"]) == 64
    assert loan_rows(app_database, "Route Trust 1") == 1
    users = set(
        app_database.scalars(
            select(AuditEntry.user_name).where(
                AuditEntry.subject == "loan", AuditEntry.subject_key.startswith("Route Trust 1/")
            )
        )
    )
    assert users == {"quinn"}


@needs_db
def test_posting_again_changes_nothing(app_database, client):
    post(client, 2)
    again = post(client, 2)
    assert again.status_code == 200
    assert again.json()["already_loaded"] is True


@needs_db
def test_a_pm_may_not_load_tapes(app_database, client):
    response = post(client, 3, user="pat")
    assert response.status_code == 403
    assert response.json() == {"detail": "pm may not run refreshes and backfills"}
    assert loan_rows(app_database, "Route Trust 3") == 0


@needs_db
def test_a_refused_tape_is_a_readable_response_and_is_audited(app_database, client):
    response = post(client, 4, content=b"not xml")
    assert response.status_code == 422
    assert response.json() == {"detail": "the file is not XML: syntax error: line 1, column 0"}
    entry = app_database.scalars(
        select(AuditEntry).where(
            AuditEntry.outcome == "refused", AuditEntry.subject_key == "0009900001-26-000004"
        )
    ).one()
    assert (entry.user_name, entry.action, entry.reason) == (
        "quinn",
        "load loan tape",
        "the file is not XML: syntax error: line 1, column 0",
    )


@needs_db
def test_an_amendment_names_what_it_amends(app_database, client):
    post(client, 5)
    response = post(
        client,
        5,
        accession="0009900001-26-000105",
        filing_date="2026-10-05",
        form="ABS-EE/A",
        amends="0009900001-26-000005",
        file_name="corrected.xml",
    )
    assert response.status_code == 200, response.text
    assert response.json()["accession"] == "0009900001-26-000105"
