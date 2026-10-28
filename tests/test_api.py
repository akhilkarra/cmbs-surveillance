import pytest
from fastapi.testclient import TestClient

from cmbs.api.app import create_app
from tests.conftest import needs_db


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_health_needs_no_user(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_requests_without_a_user_are_rejected(client):
    response = client.get("/me")
    assert response.status_code == 401
    assert response.json()["detail"] == "send the X-CMBS-User header"


@needs_db
def test_me_returns_the_users_roles(seeded, client):
    response = client.get("/me", headers={"X-CMBS-User": "morgan"})
    assert response.json() == {"user": "morgan", "roles": ["analyst", "quant"]}


@needs_db
def test_unknown_user_is_unauthorized(seeded, client):
    response = client.get("/me", headers={"X-CMBS-User": "nobody"})
    assert response.status_code == 401
