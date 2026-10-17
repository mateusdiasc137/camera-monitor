"""Sessão do painel, papéis e proteções de requisição (ADR-003)."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from nvr.identity.domain import User
from nvr.shared.errors import Forbidden

SESSION_COOKIE = "nvr_session"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


async def current_user(request: Request) -> User:
    user: User = await request.app.state.use_cases.authenticate(request.cookies.get(SESSION_COOKIE))
    return user


async def require_admin(user: Annotated[User, Depends(current_user)]) -> User:
    if not user.is_admin:
        raise Forbidden("apenas administradores podem fazer isso")
    return user


CurrentUser = Annotated[User, Depends(current_user)]
AdminUser = Annotated[User, Depends(require_admin)]


def client_address(request: Request) -> str:
    """IP do cliente. `X-Forwarded-For` só é considerado se a conexão vier de um proxy confiável."""
    peer = request.client.host if request.client else "desconhecido"
    trusted: frozenset[str] = request.app.state.trusted_proxies
    forwarded = request.headers.get("x-forwarded-for")
    if peer in trusted and forwarded:
        return forwarded.split(",")[-1].strip() or peer
    return peer


class SameOriginMiddleware(BaseHTTPMiddleware):
    """Recusa mutações vindas de outra origem (defesa adicional ao cookie SameSite=Strict)."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method in UNSAFE_METHODS:
            origin = request.headers.get("origin")
            host = request.headers.get("host", "")
            cross_site = request.headers.get("sec-fetch-site") == "cross-site"
            if cross_site or (origin and urlsplit(origin).netloc != host):
                return JSONResponse(
                    status_code=403,
                    content={"error": "forbidden", "message": "origem da requisição não permitida"},
                )
        return await call_next(request)
