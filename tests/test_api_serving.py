"""The API serves the built dashboard beside its routes, when a bundle exists."""

from pathlib import Path

from fastapi.testclient import TestClient

from cmbs.api.app import create_app


def _bundle(root: Path) -> Path:
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>CMBS</title><div id=root></div>")
    (dist / "assets" / "app.js").write_text("console.log('dashboard')")
    return dist


def test_the_dashboard_page_and_its_assets_are_served(tmp_path: Path):
    client = TestClient(create_app(web_dist=_bundle(tmp_path)))
    page = client.get("/")
    assert page.status_code == 200
    assert "<title>CMBS</title>" in page.text
    assert client.get("/assets/app.js").text == "console.log('dashboard')"


def test_the_page_is_checked_on_every_load_and_hashed_assets_are_kept(tmp_path: Path):
    # A rebuilt dashboard must never be a cached page asking for assets that no longer exist.
    client = TestClient(create_app(web_dist=_bundle(tmp_path)))
    assert client.get("/").headers["cache-control"] == "no-cache"
    assert client.get("/index.html").headers["cache-control"] == "no-cache"
    assert (
        client.get("/assets/app.js").headers["cache-control"]
        == "public, max-age=31536000, immutable"
    )
    assert "cache-control" not in client.get("/assets/missing.js").headers


def test_api_routes_win_over_the_bundle(tmp_path: Path):
    client = TestClient(create_app(web_dist=_bundle(tmp_path)))
    assert client.get("/health").json() == {"status": "ok"}


def test_without_a_bundle_only_the_api_is_served(tmp_path: Path):
    client = TestClient(create_app(web_dist=tmp_path / "missing"))
    assert client.get("/").status_code == 404
    assert client.get("/health").json() == {"status": "ok"}
