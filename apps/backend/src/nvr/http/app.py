"""Fábrica da aplicação HTTP. Recebe os casos de uso prontos; não instancia adaptadores."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.types import Lifespan

from nvr import __version__
from nvr.cameras.application import (
    DeleteCamera,
    GetCamera,
    ListCameras,
    RegisterCamera,
    TestCameraConnection,
    UpdateCamera,
)
from nvr.http.routers import auth, cameras, system
from nvr.http.security import SameOriginMiddleware, current_user
from nvr.identity.application import Authenticate, Login, Logout
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
    @property
    def login(self) -> Login: ...
    @property
    def logout(self) -> Logout: ...
    @property
    def authenticate(self) -> Authenticate: ...
    @property
    def list_cameras(self) -> ListCameras: ...
    @property
    def get_camera(self) -> GetCamera: ...
    @property
    def register_camera(self) -> RegisterCamera: ...
    @property
    def update_camera(self) -> UpdateCamera: ...
    @property
    def delete_camera(self) -> DeleteCamera: ...
    @property
    def test_camera_connection(self) -> TestCameraConnection: ...


def create_app(
    use_cases: UseCases,
    *,
    cookie_secure: bool = True,
    trusted_proxies: Iterable[str] = ("127.0.0.1", "::1"),
    lifespan: Lifespan[FastAPI] | None = None,
) -> FastAPI:
    app = FastAPI(
        lifespan=lifespan,
        title="NVR local",
        version=__version__,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url=None,
    )
    app.state.use_cases = use_cases
    app.state.cookie_secure = cookie_secure
    app.state.trusted_proxies = frozenset(trusted_proxies)
    app.add_middleware(SameOriginMiddleware)

    @app.exception_handler(errors.AppError)
    async def app_error_handler(_request: Request, exc: errors.AppError) -> JSONResponse:
        status = next((code for cls, code in _STATUS.items() if isinstance(exc, cls)), 500)
        content: dict[str, object] = {"error": exc.code, "message": exc.message}
        if isinstance(exc, errors.ValidationFailed) and exc.fields:
            content["fields"] = exc.fields
        return JSONResponse(status_code=status, content=content)

    # Público: só o login. Todo o resto exige sessão (ADR-003).
    app.include_router(auth.router, prefix="/api/v1")
    protected = [Depends(current_user)]
    app.include_router(system.router, prefix="/api/v1", dependencies=protected)
    app.include_router(cameras.router, prefix="/api/v1", dependencies=protected)
    return app
