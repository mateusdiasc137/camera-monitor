"""Armazenamento: uso, cota e política de substituição (ADR-006)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from nvr.http.security import AdminUser, require_admin
from nvr.recordings.domain import GIB, ReplacementPolicy
from nvr.recordings.storage import StorageOverview
from nvr.shared.errors import NotFound

router = APIRouter(prefix="/storage", tags=["storage"])

Replacement = Literal["overwrite_oldest", "stop_when_full"]


class PolicyOut(BaseModel):
    quota_gb: float | None
    replacement: Replacement
    high_watermark: float
    low_watermark: float
    min_free_gb: float


class CameraUsageOut(BaseModel):
    camera_id: str
    camera_name: str | None = Field(description="null = câmera removida (gravações órfãs)")
    bytes: int
    oldest: datetime | None
    newest: datetime | None
    gb_per_day: float | None


class CleanupOut(BaseModel):
    at: datetime
    state: Literal["ok", "cleaning", "full"]
    reason: str
    deleted_files: int
    freed_bytes: int


class StorageOut(BaseModel):
    location_path: str
    policy: PolicyOut
    used_bytes: int
    disk_total_bytes: int
    disk_free_bytes: int
    cameras: list[CameraUsageOut]
    days_covered: float | None
    recording_paused: bool
    pause_reason: str
    last_cleanup: CleanupOut | None

    @classmethod
    def from_overview(cls, o: StorageOverview) -> StorageOut:
        p = o.policy
        last = o.last_cleanup
        return cls(
            location_path=str(o.location.path),
            policy=PolicyOut(
                quota_gb=None if p.quota_bytes is None else round(p.quota_bytes / GIB, 2),
                replacement=p.replacement.value,
                high_watermark=p.high_watermark,
                low_watermark=p.low_watermark,
                min_free_gb=round(p.min_free_bytes / GIB, 2),
            ),
            used_bytes=o.used_bytes,
            disk_total_bytes=o.disk.total_bytes,
            disk_free_bytes=o.disk.free_bytes,
            cameras=[
                CameraUsageOut(
                    camera_id=c.camera_id,
                    camera_name=c.camera_name,
                    bytes=c.bytes,
                    oldest=c.oldest,
                    newest=c.newest,
                    gb_per_day=None if c.bytes_per_day is None else round(c.bytes_per_day / GIB, 2),
                )
                for c in o.cameras
            ],
            days_covered=None if o.days_covered is None else round(o.days_covered, 1),
            recording_paused=o.recording_paused,
            pause_reason=o.pause_reason,
            last_cleanup=None
            if last is None
            else CleanupOut(
                at=last.at,
                state=last.state.value,
                reason=last.reason,
                deleted_files=last.deleted_files,
                freed_bytes=last.freed_bytes,
            ),
        )


class PolicyIn(BaseModel):
    quota_gb: float | None = Field(description="null = sem cota (só o piso de espaço livre)")
    replacement: Replacement = "overwrite_oldest"
    confirm: bool = Field(
        default=False, description="confirma apagar gravações se a cota for menor que o uso"
    )


@router.get("", response_model=StorageOut)
async def get_storage(request: Request) -> StorageOut:
    overview = await request.app.state.use_cases.get_storage_overview()
    if overview is None:
        raise NotFound("nenhum local de gravação configurado")
    return StorageOut.from_overview(overview)


@router.put("/policy", response_model=PolicyOut, dependencies=[Depends(require_admin)])
async def update_policy(body: PolicyIn, request: Request, admin: AdminUser) -> PolicyOut:
    policy = await request.app.state.use_cases.update_storage_policy(
        body.quota_gb, ReplacementPolicy(body.replacement), body.confirm, admin.username
    )
    return PolicyOut(
        quota_gb=None if policy.quota_bytes is None else round(policy.quota_bytes / GIB, 2),
        replacement=policy.replacement.value,
        high_watermark=policy.high_watermark,
        low_watermark=policy.low_watermark,
        min_free_gb=round(policy.min_free_bytes / GIB, 2),
    )
