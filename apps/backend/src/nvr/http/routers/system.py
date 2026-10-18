from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from nvr.system.domain import CameraSummary, SystemHealth

router = APIRouter(tags=["system"])
# Sem login: só "estou no ar", para o systemd/proxy/monitoramento. Nenhum detalhe do sistema.
public_router = APIRouter(tags=["system"])

State = Literal["ok", "degraded", "down"]
CameraState = Literal["online", "degraded", "starting", "offline", "auth_error", "not_found", "disabled"]


class ComponentHealthOut(BaseModel):
    name: str = Field(description="database | media_server | storage | cameras | recording")
    state: State
    detail: str


class CameraSummaryOut(BaseModel):
    camera_id: str
    name: str
    state: CameraState
    detail: str
    since: datetime | None
    recording_expected: bool = Field(description="ativa, gravação ligada e não pausada por falta de espaço")
    recording: bool = Field(description="o servidor de mídia está gravando o perfil principal agora")
    last_recording_at: datetime | None = Field(description="fim do último trecho gravado e indexado")

    @classmethod
    def build(cls, c: CameraSummary) -> CameraSummaryOut:
        return cls(
            camera_id=c.camera_id,
            name=c.name,
            state=c.state,  # type: ignore[arg-type]
            detail=c.detail,
            since=c.since,
            recording_expected=c.recording_expected,
            recording=c.recording,
            last_recording_at=c.last_recording_at,
        )


class SystemHealthOut(BaseModel):
    version: str
    state: State
    components: list[ComponentHealthOut]
    cameras: list[CameraSummaryOut] = []
    checked_at: datetime | None = None

    @classmethod
    def from_domain(cls, health: SystemHealth) -> SystemHealthOut:
        return cls(
            version=health.version,
            state=health.state.value,
            components=[
                ComponentHealthOut(name=c.name, state=c.state.value, detail=c.detail)
                for c in health.components
            ],
            cameras=[CameraSummaryOut.build(c) for c in health.cameras],
            checked_at=health.checked_at,
        )


class PingOut(BaseModel):
    status: Literal["ok"] = "ok"


@router.get("/system/health", response_model=SystemHealthOut)
async def get_health(request: Request) -> SystemHealthOut:
    health = await request.app.state.use_cases.get_system_health()
    return SystemHealthOut.from_domain(health)


@public_router.get("/system/ping", response_model=PingOut)
async def ping() -> PingOut:
    """Responde enquanto o processo atende requisições (verificação de vida, sem login)."""
    return PingOut()
