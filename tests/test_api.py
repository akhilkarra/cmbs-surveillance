from pathlib import Path

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


def test_domain_refusals_become_readable_responses():
    from fastapi import FastAPI

    from cmbs.api.app import create_app
    from cmbs.filings.service import ConflictingFiling

    # No dashboard bundle: routes added after the app is built must not sit behind its mount.
    app: FastAPI = create_app(web_dist=Path("/nonexistent"))

    @app.get("/refuse-for-test")
    def refuse() -> None:
        raise ConflictingFiling("0001888524-26-017705 already has period '2026-09-01'")

    response = TestClient(app).get("/refuse-for-test")
    assert response.status_code == 422
    assert response.json() == {"detail": "0001888524-26-017705 already has period '2026-09-01'"}


@needs_db
def test_ready_when_the_database_answers(db, client):
    response = client.get("/health/ready")
    assert (response.status_code, response.json()) == (200, {"status": "ready"})


def test_not_ready_when_the_database_does_not_answer(client, monkeypatch):
    # Port 1 refuses connections at once, so this needs no database.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://cmbs:cmbs@127.0.0.1:1/none")
    response = client.get("/health/ready")
    assert (response.status_code, response.json()) == (503, {"status": "database unavailable"})


def test_not_ready_when_no_database_is_configured(client, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    response = client.get("/health/ready")
    assert (response.status_code, response.json()) == (503, {"status": "database unavailable"})
