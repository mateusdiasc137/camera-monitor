"""Ao vivo via WebRTC (WHEP), intermediado pelo backend.

O navegador só fala com esta API (mesma origem, cookie de sessão). O backend repassa a negociação ao
MediaMTX com uma credencial interna; só o tráfego de mídia (UDP/ICE) vai direto ao MediaMTX.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request, Response

from nvr.cameras.domain import StreamProfile
from nvr.shared.errors import ValidationFailed

router = APIRouter(prefix="/cameras", tags=["live"])

MAX_BODY = 64 * 1024
Profile = Literal["main", "sub"]


async def _body(request: Request, expected_type: str) -> str:
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != expected_type:
        raise ValidationFailed(f"Content-Type deve ser {expected_type}")
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise ValidationFailed("corpo grande demais")
    return raw.decode("utf-8", errors="replace")


@router.post(
    "/{camera_id}/whep",
    status_code=201,
    response_class=Response,
    responses={201: {"content": {"application/sdp": {}}, "description": "SDP de resposta (WHEP)"}},
)
async def start_live(camera_id: str, request: Request, profile: Profile = "main") -> Response:
    """Recebe a oferta SDP (application/sdp) e devolve a resposta; `Location` aponta para a sessão."""
    offer = await _body(request, "application/sdp")
    answer = await request.app.state.use_cases.live_viewing.start(camera_id, StreamProfile(profile), offer)
    location = f"/api/v1/cameras/{camera_id}/whep/{answer.session_id}?profile={profile}"
    return Response(
        answer.sdp,
        status_code=201,
        media_type="application/sdp",
        headers={"Location": location, "Cache-Control": "no-store"},
    )


@router.patch("/{camera_id}/whep/{session_id}", status_code=204, response_class=Response)
async def add_candidates(
    camera_id: str, session_id: str, request: Request, profile: Profile = "main"
) -> Response:
    fragment = await _body(request, "application/trickle-ice-sdpfrag")
    await request.app.state.use_cases.live_viewing.add_candidates(
        camera_id, StreamProfile(profile), session_id, fragment
    )
    return Response(status_code=204)


@router.delete("/{camera_id}/whep/{session_id}", status_code=204, response_class=Response)
async def stop_live(camera_id: str, session_id: str, request: Request, profile: Profile = "main") -> Response:
    await request.app.state.use_cases.live_viewing.stop(camera_id, StreamProfile(profile), session_id)
    return Response(status_code=204)
