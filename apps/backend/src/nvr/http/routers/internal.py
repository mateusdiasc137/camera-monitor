"""Endpoints chamados só por processos locais (MediaMTX). Fora de /api: o proxy HTTPS não os expõe."""

from __future__ import annotations

import ipaddress

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from nvr.streaming.application import MediaAccessRequest

router = APIRouter(prefix="/internal", include_in_schema=False)


class MediaMtxAuthIn(BaseModel):
    """Corpo enviado pelo MediaMTX v1.21 (`authHTTPAddress`)."""

    ip: str = ""
    user: str = ""
    password: str = ""
    token: str = ""
    action: str = ""
    path: str = ""
    protocol: str = ""
    query: str = ""


def _from_loopback(request: Request) -> bool:
    try:
        return request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        return False


@router.post("/mediamtx/auth")
async def mediamtx_auth(body: MediaMtxAuthIn, request: Request) -> Response:
    if not _from_loopback(request):
        return Response(status_code=403)
    allowed = request.app.state.use_cases.authorize_media_access(
        MediaAccessRequest(body.action, body.path, body.ip, body.user, body.password, body.protocol)
    )
    return Response(status_code=200 if allowed else 401)
