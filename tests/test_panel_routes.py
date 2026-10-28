"""GET /deals/{deal}/loans and /deals/{deal}/staleness, point in time."""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from cmbs import clock
from cmbs.api.app import create_app
from cmbs.tape.service import load_tape
from tests.conftest import needs_db
from tests.fixtures.tape.tapes import fixture_bytes

DEAL = "Fixture Trust 2026-F1"
QUINN = {"X-CMBS-User": "quinn"}


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def loaded(seeded):
    load_tape(
        seeded,
        deal=DEAL,
        content=fixture_bytes(),
        file_name="ex102.xml",
        accession="0009900001-26-000101",
        filing_date=dt.date(2026, 9, 29),
        cik=9_900_001,
    )
    seeded.commit()
    return seeded


def test_loans_need_a_user(client):
    assert client.get(f"/deals/{DEAL}/loans").status_code == 401


@needs_db
def test_loans_as_of_the_filing_date(loaded, client):
    response = client.get(f"/deals/{DEAL}/loans", params={"as_of": "2026-09-29"}, headers=QUINN)
    assert response.status_code == 200
    body = response.json()
    assert (body["deal"], body["as_of"], body["period"]) == (DEAL, "2026-09-29", "September 2026")
    assert [loan["loan"] for loan in body["loans"]] == ["1", "2", "3", "4", "5", "6", "7"]
    first = body["loans"][0]
    assert first["ending_balance"] == "50000000.00"
    assert first["dscr"] == "1.45"
    assert first["paid_through_date"] == "2026-09-01"
    assert first["accession"] == "0009900001-26-000101"
    assert len(first["file_sha256"]) == 64
    assert body["loans"][4]["noi"] is None


@needs_db
def test_loans_before_the_filing_date_are_empty(loaded, client):
    response = client.get(f"/deals/{DEAL}/loans", params={"as_of": "2026-09-28"}, headers=QUINN)
    assert response.json() == {"deal": DEAL, "as_of": "2026-09-28", "period": None, "loans": []}


@needs_db
def test_as_of_defaults_to_today(loaded, client):
    clock.set_now(dt.date(2026, 9, 28))
    assert client.get(f"/deals/{DEAL}/loans", headers=QUINN).json()["loans"] == []
    clock.set_now(dt.date(2026, 9, 29))
    assert len(client.get(f"/deals/{DEAL}/loans", headers=QUINN).json()["loans"]) == 7


@needs_db
def test_an_unknown_deal_is_refused(loaded, client):
    response = client.get("/deals/Nope/loans", headers=QUINN)
    assert (response.status_code, response.json()) == (422, {"detail": "no deal named 'Nope'"})


@needs_db
def test_operators_do_not_read_the_panel(loaded, client):
    response = client.get(f"/deals/{DEAL}/loans", headers={"X-CMBS-User": "olly"})
    assert response.status_code == 403
    assert response.json() == {"detail": "operator may not read the panel"}


@needs_db
def test_staleness(loaded, client):
    response = client.get(f"/deals/{DEAL}/staleness", params={"as_of": "2026-09-29"}, headers=QUINN)
    assert response.status_code == 200
    body = response.json()
    assert body["period"] == "September 2026"
    assert body["summary"] == {
        "active_loans": 4,
        "loans_with_financials": 3,
        "loans_without_financials": 1,
        "median_days": 164,
        "p90_days": 619,
        "over_six_months": 1,
        "median_days_vs_filing": 182,
        "note": None,
    }
    assert body["loans"][0] == {
        "loan": "1",
        "status": "73 days old at period end, 91 days old at filing",
        "financials_end": "2026-06-30",
        "days_vs_period_end": 73,
        "days_vs_filing": 91,
    }
    assert body["loans"][4]["status"] == "no updated financials"


@needs_db
def test_staleness_before_any_filing_is_refused(loaded, client):
    clock.set_now(dt.date(2026, 9, 1))
    response = client.get(f"/deals/{DEAL}/staleness", headers=QUINN)
    assert response.status_code == 422
    assert response.json() == {
        "detail": "no loan tape of Fixture Trust 2026-F1 had been filed by 2026-09-01"
    }
