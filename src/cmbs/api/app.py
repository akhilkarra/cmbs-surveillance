"""The FastAPI application. Feature routers are found by convention: `cmbs.<feature>.routes`.

When the dashboard has been built (`web/dist`, made by `npm run build` in the web container), the
app also serves it, so the page and the API it calls share one origin: `cmbs serve`, then open
http://127.0.0.1:8000/?user=pat. API routes always win over files of the bundle.
"""

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import Response
from starlette.types import Scope

from cmbs.access.service import PermissionRefused, UnknownUser
from cmbs.db import engine
from cmbs.db.registry import all_route_modules
from cmbs.errors import Refused

# The built dashboard in a checkout of the repository; CMBS_WEB_DIST points elsewhere.
WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


def _web_dist() -> Path:
    return Path(os.environ.get("CMBS_WEB_DIST") or WEB_DIST)


class Dashboard(StaticFiles):
    """The built bundle. Vite names every file under `assets/` by a hash of its content, so a
    browser may keep those for a year; any other file, `index.html` above all, is checked on every
    load, so a rebuilt dashboard is never a cached page asking for assets that no longer exist."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        hashed = path.startswith("assets/")
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable" if hashed else "no-cache"
        )
        return response


def database_answers() -> bool:
    """Whether the database answers a query now: not configured or not reachable is False."""
    try:
        with engine.session() as db:
            db.execute(text("SELECT 1"))
    except (SQLAlchemyError, RuntimeError):  # RuntimeError: DATABASE_URL is not set
        return False
    return True


def create_app(*, web_dist: Path | None = None) -> FastAPI:
    app = FastAPI(title="CMBS surveillance", version="0.1.0")

    @app.exception_handler(Refused)
    async def refused(_: Request, error: Refused) -> JSONResponse:
        status = 403 if isinstance(error, PermissionRefused) else 422
        if isinstance(error, UnknownUser):
            status = 401
        return JSONResponse(status_code=status, content={"detail": str(error)})

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/health/ready",
        tags=["meta"],
        responses={503: {"description": "The database does not answer"}},
    )
    def ready() -> JSONResponse:
        """Ready to serve: the process is up and the database answers `SELECT 1`. The api
        container's healthcheck (and so `docker compose up --wait`) waits on this."""
        if database_answers():
            return JSONResponse({"status": "ready"})
        return JSONResponse(status_code=503, content={"status": "database unavailable"})

    for module in all_route_modules():
        app.include_router(module.router)
    dist = web_dist if web_dist is not None else _web_dist()
    if (dist / "index.html").is_file():
        # Mounted last, so every API route above is matched first.
        app.mount("/", Dashboard(directory=dist, html=True), name="dashboard")
    return app


app = create_app()
