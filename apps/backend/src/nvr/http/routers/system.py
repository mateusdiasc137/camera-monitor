from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from nvr.system.domain import SystemHealth

router = APIRouter(tags=["system"])


class ComponentHealthOut(BaseModel):
    name: str
    state: Literal["ok", "degraded", "down"]
    detail: str


class SystemHealthOut(BaseModel):
    version: str
    state: Literal["ok", "degraded", "down"]
    components: list[ComponentHealthOut]

    @classmethod
    def from_domain(cls, health: SystemHealth) -> SystemHealthOut:
        return cls(
            version=health.version,
            state=health.state.value,
            components=[
                ComponentHealthOut(name=c.name, state=c.state.value, detail=c.detail)
                for c in health.components
            ],
        )


@router.get("/system/health", response_model=SystemHealthOut)
async def get_health(request: Request) -> SystemHealthOut:
    health = await request.app.state.use_cases.get_system_health()
    return SystemHealthOut.from_domain(health)
