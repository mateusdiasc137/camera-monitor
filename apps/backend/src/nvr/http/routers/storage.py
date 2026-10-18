"""Armazenamento: uso, cota, política de substituição e local de gravação (ADR-006, D7)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from nvr.http.security import AdminUser, require_admin
from nvr.recordings.domain import GIB, LocationMode, ReplacementPolicy
from nvr.recordings.location import FolderListing, LocationCheck, MigrationView
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


class MigrationOut(BaseModel):
    """Também é o conteúdo do evento SSE `storage.migration`."""

    id: str
    state: Literal["running", "done", "failed", "abandoned"]
    from_path: str | None
    to_path: str | None
    total_files: int
    total_bytes: int
    moved_files: int
    moved_bytes: int
    error: str
    started_at: datetime
    finished_at: datetime | None

    @classmethod
    def from_view(cls, view: MigrationView) -> MigrationOut:
        return cls.model_validate(view.payload())


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
    migration: MigrationOut | None = Field(default=None, description="a troca de local mais recente")

    @classmethod
    def from_overview(cls, o: StorageOverview, migration: MigrationView | None = None) -> StorageOut:
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
            migration=None if migration is None else MigrationOut.from_view(migration),
        )


class PolicyIn(BaseModel):
    quota_gb: float | None = Field(description="null = sem cota (só o piso de espaço livre)")
    replacement: Replacement = "overwrite_oldest"
    confirm: bool = Field(
        default=False, description="confirma apagar gravações se a cota for menor que o uso"
    )


@router.get("", response_model=StorageOut)
async def get_storage(request: Request) -> StorageOut:
    use_cases = request.app.state.use_cases
    overview = await use_cases.get_storage_overview()
    if overview is None:
        raise NotFound("nenhum local de gravação configurado")
    return StorageOut.from_overview(overview, await use_cases.get_storage_migration())


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


# ------------------------------------------------------------ local de gravação (D7, F1-11b)


class FolderOut(BaseModel):
    name: str
    path: str


class FolderListingOut(BaseModel):
    path: str | None = Field(description="null = lista das raízes permitidas")
    parent: str | None = Field(description="null = subir volta à lista das raízes")
    folders: list[FolderOut]

    @classmethod
    def build(cls, listing: FolderListing) -> FolderListingOut:
        return cls(
            path=None if listing.path is None else str(listing.path),
            parent=None if listing.parent is None else str(listing.parent),
            folders=[FolderOut(name=f.name, path=str(f.path)) for f in listing.folders],
        )


class LocationIn(BaseModel):
    path: str = Field(min_length=1, max_length=1024)


class LocationCheckOut(BaseModel):
    path: str
    ok: bool
    errors: list[str]
    warnings: list[str]
    will_create: bool
    disk_total_bytes: int | None
    disk_free_bytes: int | None
    current_path: str | None
    current_bytes: int = Field(description="gravações do NVR no local atual (o que a migração moveria)")
    existing_bytes: int = Field(description="gravações do NVR que já estão na pasta escolhida")
    same_volume: bool
    migrate_problem: str = Field(description="vazio = dá para mover as gravações")

    @classmethod
    def build(cls, c: LocationCheck) -> LocationCheckOut:
        return cls(
            path=str(c.path),
            ok=c.ok,
            errors=c.errors,
            warnings=c.warnings,
            will_create=c.will_create,
            disk_total_bytes=None if c.disk is None else c.disk.total_bytes,
            disk_free_bytes=None if c.disk is None else c.disk.free_bytes,
            current_path=None if c.current_path is None else str(c.current_path),
            current_bytes=c.current_bytes,
            existing_bytes=c.existing_bytes,
            same_volume=c.same_volume,
            migrate_problem=c.migrate_problem,
        )


class LocationChangeIn(LocationIn):
    mode: Literal["migrate", "fresh"] = Field(
        description="migrate = mover as gravações existentes; "
        "fresh = começar vazio (as antigas ficam na pasta anterior)"
    )


class LocationChangeOut(BaseModel):
    location_path: str
    migration: MigrationOut | None
    unindexed_segments: int = Field(description="modo fresh: segmentos que saíram da linha do tempo")
    warnings: list[str]


@router.get("/browse", response_model=FolderListingOut, dependencies=[Depends(require_admin)])
async def browse(request: Request, path: str | None = None) -> FolderListingOut:
    return FolderListingOut.build(await request.app.state.use_cases.browse_storage(path))


@router.post("/location/validate", response_model=LocationCheckOut, dependencies=[Depends(require_admin)])
async def validate_location(body: LocationIn, request: Request) -> LocationCheckOut:
    return LocationCheckOut.build(await request.app.state.use_cases.validate_storage_location(body.path))


@router.put("/location", response_model=LocationChangeOut, dependencies=[Depends(require_admin)])
async def change_location(body: LocationChangeIn, request: Request, admin: AdminUser) -> LocationChangeOut:
    change = await request.app.state.use_cases.change_storage_location(
        body.path, LocationMode(body.mode), admin.username
    )
    return LocationChangeOut(
        location_path=str(change.location.path),
        migration=None if change.migration is None else MigrationOut.from_view(change.migration),
        unindexed_segments=change.unindexed_segments,
        warnings=change.warnings,
    )


@router.post("/migration/resume", response_model=MigrationOut, dependencies=[Depends(require_admin)])
async def resume_migration(request: Request, admin: AdminUser) -> MigrationOut:
    view = await request.app.state.use_cases.resolve_storage_migration.resume(admin.username)
    return MigrationOut.from_view(view)


@router.post("/migration/abandon", response_model=MigrationOut, dependencies=[Depends(require_admin)])
async def abandon_migration(request: Request, admin: AdminUser) -> MigrationOut:
    view = await request.app.state.use_cases.resolve_storage_migration.abandon(admin.username)
    return MigrationOut.from_view(view)
