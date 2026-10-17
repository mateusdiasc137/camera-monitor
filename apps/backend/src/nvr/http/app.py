"""Fábrica da aplicação HTTP. Recebe os casos de uso prontos; não instancia adaptadores."""

from __future__ import annotations

from typing import Protocol

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from nvr import __version__
from nvr.http.routers import system
from nvr.shared import errors
from nvr.system.application import GetSystemHealth

_STATUS = {
    errors.NotFound: 404,
    errors.ValidationFailed: 422,
    errors.Conflict: 409,
    errors.Unauthorized: 401,
    errors.Forbidden: 403,
    errors.UnsupportedFeature: 409,
    errors.CameraOffline: 503,
    errors.AuthenticationFailed: 502,
    errors.OperationTimeout: 504,
    errors.ResourceBusy: 409,
}


class UseCases(Protocol):
    @property
    def get_system_health(self) -> GetSystemHealth: ...


def create_app(use_cases: UseCases) -> FastAPI:
    app = FastAPI(
        title="NVR local",
        version=__version__,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url=None,
    )
    app.state.use_cases = use_cases

    @app.exception_handler(errors.AppError)
    async def app_error_handler(_request: Request, exc: errors.AppError) -> JSONResponse:
        status = next((code for cls, code in _STATUS.items() if isinstance(exc, cls)), 500)
        return JSONResponse(status_code=status, content={"error": exc.code, "message": exc.message})

    app.include_router(system.router, prefix="/api/v1")
    return app
