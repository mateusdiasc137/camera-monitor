"""Estado das câmeras (snapshot, histórico) e fluxo de eventos em tempo real (SSE)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from nvr.shared.events import EventBus
from nvr.streaming.domain import CameraHealth

router = APIRouter(tags=["status"])

State = Literal["online", "degraded", "starting", "offline", "auth_error", "not_found", "disabled"]
KEEPALIVE_S = 15.0


class CameraStatusOut(BaseModel):
    camera_id: str
    state: State
    detail: str
    recording: bool
    since: datetime

    @classmethod
    def build(cls, health: CameraHealth, since: datetime) -> CameraStatusOut:
        return cls(
            camera_id=health.camera_id,
            state=health.state.value,
            detail=health.detail,
            recording=health.recording,
            since=since,
        )


@router.get("/cameras/status", response_model=list[CameraStatusOut])
async def current_status(request: Request) -> list[CameraStatusOut]:
    views = request.app.state.use_cases.monitor_camera_health.current()
    return [CameraStatusOut.build(v.health, v.since) for v in views.values()]


@router.get("/cameras/{camera_id}/status-history", response_model=list[CameraStatusOut])
async def status_history(camera_id: str, request: Request, limit: int = 50) -> list[CameraStatusOut]:
    await request.app.state.use_cases.get_camera(camera_id)  # 404 se não existir
    rows = await request.app.state.use_cases.get_camera_status_history(camera_id, limit)
    return [CameraStatusOut.build(health, at) for at, health in rows]


async def sse_stream(bus: EventBus, request: Request, keepalive: float = KEEPALIVE_S) -> AsyncIterator[str]:
    """Formato text/event-stream. Comentários periódicos mantêm a conexão viva através de proxies."""
    async with bus.subscribe() as queue:
        yield ": conectado\n\n"
        while not await request.is_disconnected():
            try:
                event = await asyncio.wait_for(queue.get(), keepalive)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield f"event: {event.type}\ndata: {json.dumps(event.data, ensure_ascii=False)}\n\n"


@router.get("/events/stream", response_class=StreamingResponse)
async def event_stream(request: Request) -> StreamingResponse:
    return StreamingResponse(
        sse_stream(request.app.state.event_bus, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
