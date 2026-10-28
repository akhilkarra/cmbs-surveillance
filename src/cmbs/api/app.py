"""The FastAPI application. Feature routers are found by convention: `cmbs.<feature>.routes`."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from cmbs.access.service import PermissionRefused, UnknownUser
from cmbs.db.registry import all_route_modules
from cmbs.errors import Refused


def create_app() -> FastAPI:
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

    for module in all_route_modules():
        app.include_router(module.router)
    return app


app = create_app()
