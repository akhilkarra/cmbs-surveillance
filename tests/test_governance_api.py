"""The governance routes: role changes, the settings ledger and the audit log over HTTP."""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from cmbs import clock
from cmbs.api.app import create_app
from cmbs.audit.service import loan_key, record
from tests.conftest import needs_db

pytestmark = needs_db

DEAL = "Fixture Trust 2026-F1"


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def as_user(name: str) -> dict[str, str]:
    return {"X-CMBS-User": name}


@pytest.fixture(autouse=True)
def _after_the_baseline():
    clock.set_now(dt.datetime(2026, 9, 20, tzinfo=dt.UTC))


def test_operators_list_users_and_change_roles(seeded, client):
    listed = client.get("/users", headers=as_user("olly")).json()
    assert listed["morgan"] == ["analyst", "quant"]
    granted = client.put("/users/quinn/roles/risk", headers=as_user("olly"))
    assert granted.json() == {"user": "quinn", "roles": ["quant", "risk"]}
    revoked = client.delete("/users/quinn/roles/quant", headers=as_user("olly"))
    assert revoked.json() == {"user": "quinn", "roles": ["risk"]}


def test_role_change_refusals_are_readable(seeded, client):
    refused = client.put("/users/pat/roles/risk", headers=as_user("quinn"))
    assert (refused.status_code, refused.json()) == (
        403,
        {"detail": "quant may not manage users and roles"},
    )
    own = client.put("/users/olly/roles/risk", headers=as_user("olly"))
    assert (own.status_code, own.json()) == (403, {"detail": "nobody changes their own roles"})
    held = client.put("/users/quinn/roles/quant", headers=as_user("olly"))
    assert (held.status_code, held.json()) == (
        422,
        {"detail": "quinn already holds the role quant"},
    )
    assert client.put("/users/quinn/roles/trader", headers=as_user("olly")).status_code == 422


def test_settings_are_proposed_approved_and_read(seeded, client):
    made = client.post(
        "/settings/proposals",
        headers=as_user("quinn"),
        json={"changes": {"dscr_alert_threshold": "1.05"}, "reason": "market moved"},
    )
    assert made.status_code == 201
    proposal = made.json()
    assert (proposal["status"], proposal["proposer_role"], proposal["changes"]) == (
        "awaiting approval",
        "quant",
        {"dscr_alert_threshold": "1.05"},
    )
    waiting = client.get(
        "/settings/proposals", headers=as_user("rae"), params={"status": "awaiting approval"}
    )
    assert [item["id"] for item in waiting.json()] == [proposal["id"]]
    clock.set_now(dt.datetime(2026, 10, 1, tzinfo=dt.UTC))
    approved = client.post(f"/settings/proposals/{proposal['id']}/approve", headers=as_user("rae"))
    assert approved.json() == {"version": 8, "approved_at": "2026-10-01T00:00:00+00:00"}
    settings = client.get("/settings", headers=as_user("pat")).json()
    assert settings["version"] == 8
    dscr = settings["settings"][0]
    assert (dscr["label"], dscr["value"]) == ("DSCR alert threshold", "1.05x")
    assert dscr["history"][1] == {
        "version": 7,
        "value": "1.00x",
        "proposed_by": "quinn",
        "approved_by": "rae",
        "approved_at": "2026-09-15T00:00:00+00:00",
    }
    earlier = client.get("/settings", headers=as_user("pat"), params={"at": "2026-09-30T00:00:00Z"})
    assert earlier.json()["version"] == 7


def test_settings_before_the_first_version_are_refused_readably(seeded, client):
    response = client.get(
        "/settings", headers=as_user("pat"), params={"at": "2026-09-14T23:59:59Z"}
    )
    assert (response.status_code, response.json()) == (
        422,
        {"detail": "no settings approved by 2026-09-14T23:59:59+00:00"},
    )


def test_a_time_without_a_zone_is_read_as_utc(monkeypatch, client):
    from cmbs.api import deps
    from cmbs.settings import routes
    from cmbs.settings.service import SettingsView

    asked: list[dt.datetime | None] = []

    def fake_read(db, user, at=None):
        asked.append(at)
        return SettingsView(version=7, lines=[])

    monkeypatch.setattr(routes, "read_settings", fake_read)
    app = create_app()
    app.dependency_overrides[deps.db_session] = lambda: None
    response = TestClient(app).get(
        "/settings", headers=as_user("pat"), params={"at": "2026-09-15T00:00:00"}
    )
    assert response.json() == {"version": 7, "settings": []}
    assert asked == [dt.datetime(2026, 9, 15, tzinfo=dt.UTC)]
    assert asked[0] is not None and asked[0].tzinfo is dt.UTC


def test_settings_proposals_are_rejected_with_a_reason(seeded, client):
    made = client.post(
        "/settings/proposals",
        headers=as_user("quinn"),
        json={"changes": {"abstention_low": "0.40", "abstention_high": "0.60"}, "reason": "r"},
    ).json()
    rejected = client.post(
        f"/settings/proposals/{made['id']}/reject",
        headers=as_user("rae"),
        json={"reason": "needs a quality report first"},
    ).json()
    assert (rejected["status"], rejected["decision_reason"], rejected["decided_by"]) == (
        "rejected",
        "needs a quality report first",
        "rae",
    )


def test_settings_refusals_are_readable(seeded, client):
    refused = client.post(
        "/settings/proposals",
        headers=as_user("quinn"),
        json={"changes": {"prompt_version": 4}, "reason": "better prompt"},
    )
    assert (refused.status_code, refused.json()) == (
        422,
        {"detail": "a model or prompt change needs a quality report"},
    )
    unknown = client.post("/settings/proposals/42/approve", headers=as_user("rae"))
    assert (unknown.status_code, unknown.json()) == (422, {"detail": "there is no proposal 42"})
    bad_status = client.get(
        "/settings/proposals", headers=as_user("rae"), params={"status": "open"}
    )
    assert bad_status.status_code == 422


def test_the_audit_log_is_read_by_loan_user_and_date(seeded, client):
    clock.set_now(dt.datetime(2026, 9, 30, 15, tzinfo=dt.UTC))
    record(
        seeded,
        user="alex",
        action="override value",
        subject="loan",
        subject_key=loan_key(DEAL, "2"),
    )
    seeded.commit()
    history = client.get("/audit/loan", headers=as_user("pat"), params={"deal": DEAL, "loan": "2"})
    (entry,) = history.json()
    assert (entry["user"], entry["action"], entry["at"]) == (
        "alex",
        "override value",
        "2026-09-30T15:00:00+00:00",
    )
    by_user = client.get(
        "/audit",
        headers=as_user("rae"),
        params={"by": "alex", "start": "2026-09-01", "end": "2026-09-30"},
    )
    assert [item["id"] for item in by_user.json()] == [entry["id"]]
    refused = client.get("/audit", headers=as_user("pat"), params={"by": "alex"})
    assert (refused.status_code, refused.json()) == (
        403,
        {"detail": "pm may not read the audit log by user"},
    )


@pytest.mark.parametrize("method", ["DELETE", "PATCH"])
def test_audit_entries_cannot_be_removed_or_changed(seeded, client, method):
    response = client.request(method, "/audit/1", headers=as_user("olly"))
    assert (response.status_code, response.json()) == (
        422,
        {"detail": "the audit log is append-only"},
    )
