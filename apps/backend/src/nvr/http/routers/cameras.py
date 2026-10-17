"""Cadastro de câmeras. Respostas nunca contêm usuário/senha da câmera nem URL RTSP completa."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from nvr.cameras.application import CameraChanges, CameraDraft
from nvr.cameras.domain import Camera, CameraCredentials, CameraDriver, IngestMode, ProbeResult
from nvr.http.security import AdminUser, require_admin

router = APIRouter(prefix="/cameras", tags=["cameras"])

Driver = Literal["generic_rtsp", "xiongmai"]
Ingest = Literal["relay", "direct"]


def _sync_media(request: Request) -> None:
    """Pede uma reconciliação imediata do MediaMTX/relays (se a mídia estiver ativa)."""
    media_sync = getattr(request.app.state, "media_sync", None)
    if media_sync is not None:
        media_sync.trigger()


class CameraOut(BaseModel):
    id: str
    name: str
    driver: Driver
    host: str
    rtsp_port: int
    main_path: str = Field(description="caminho RTSP; credenciais aparecem só como {user}/{password}")
    sub_path: str | None
    ingest: Ingest
    enabled: bool
    recording_enabled: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, c: Camera) -> CameraOut:
        return cls(
            id=c.id,
            name=c.name,
            driver=c.driver.value,
            host=c.host,
            rtsp_port=c.rtsp_port,
            main_path=c.main_path,
            sub_path=c.sub_path,
            ingest=c.ingest.value,
            enabled=c.enabled,
            recording_enabled=c.recording_enabled,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )


class CameraCreateIn(BaseModel):
    name: str = Field(max_length=80)
    host: str = Field(max_length=255)
    rtsp_port: int = 554
    main_path: str = Field(max_length=512)
    sub_path: str | None = Field(default=None, max_length=512)
    username: str = Field(max_length=128)
    password: str = Field(max_length=256, json_schema_extra={"format": "password", "writeOnly": True})
    driver: Driver = "generic_rtsp"
    ingest: Ingest = "relay"
    enabled: bool = True
    recording_enabled: bool = True


class CameraUpdateIn(BaseModel):
    """Campos omitidos não mudam. Usuário e senha só são trocados se os dois forem enviados."""

    name: str | None = Field(default=None, max_length=80)
    host: str | None = Field(default=None, max_length=255)
    rtsp_port: int | None = None
    main_path: str | None = Field(default=None, max_length=512)
    sub_path: str | None = Field(default=None, max_length=512, description="string vazia remove o substream")
    username: str | None = Field(default=None, max_length=128)
    password: str | None = Field(
        default=None, max_length=256, json_schema_extra={"format": "password", "writeOnly": True}
    )
    driver: Driver | None = None
    ingest: Ingest | None = None
    enabled: bool | None = None
    recording_enabled: bool | None = None


class TrackOut(BaseModel):
    kind: str
    codec: str
    clock_rate: int | None


class ProbeResultOut(BaseModel):
    profile: Literal["main", "sub"]
    outcome: Literal["online", "auth_error", "not_found", "offline", "protocol_error"]
    detail: str
    tracks: list[TrackOut]
    latency_ms: int | None

    @classmethod
    def from_domain(cls, r: ProbeResult) -> ProbeResultOut:
        return cls(
            profile=r.profile.value,
            outcome=r.outcome.value,
            detail=r.detail,
            tracks=[TrackOut(kind=t.kind, codec=t.codec, clock_rate=t.clock_rate) for t in r.tracks],
            latency_ms=r.latency_ms,
        )


@router.get("", response_model=list[CameraOut])
async def list_cameras(request: Request) -> list[CameraOut]:
    return [CameraOut.from_domain(c) for c in await request.app.state.use_cases.list_cameras()]


@router.get("/{camera_id}", response_model=CameraOut)
async def get_camera(camera_id: str, request: Request) -> CameraOut:
    return CameraOut.from_domain(await request.app.state.use_cases.get_camera(camera_id))


@router.post("", response_model=CameraOut, status_code=201)
async def create_camera(body: CameraCreateIn, request: Request, admin: AdminUser) -> CameraOut:
    draft = CameraDraft(
        name=body.name,
        host=body.host,
        rtsp_port=body.rtsp_port,
        main_path=body.main_path,
        sub_path=body.sub_path,
        credentials=CameraCredentials(body.username, body.password),
        driver=CameraDriver(body.driver),
        ingest=IngestMode(body.ingest),
        enabled=body.enabled,
        recording_enabled=body.recording_enabled,
    )
    camera = await request.app.state.use_cases.register_camera(draft, admin.username)
    _sync_media(request)
    return CameraOut.from_domain(camera)


@router.patch("/{camera_id}", response_model=CameraOut)
async def update_camera(
    camera_id: str, body: CameraUpdateIn, request: Request, admin: AdminUser
) -> CameraOut:
    credentials = None
    if body.username is not None and body.password is not None:
        credentials = CameraCredentials(body.username, body.password)
    changes = CameraChanges(
        name=body.name,
        host=body.host,
        rtsp_port=body.rtsp_port,
        main_path=body.main_path,
        sub_path=body.sub_path,
        driver=CameraDriver(body.driver) if body.driver else None,
        ingest=IngestMode(body.ingest) if body.ingest else None,
        enabled=body.enabled,
        recording_enabled=body.recording_enabled,
        credentials=credentials,
    )
    camera = await request.app.state.use_cases.update_camera(camera_id, changes, admin.username)
    _sync_media(request)
    return CameraOut.from_domain(camera)


@router.delete("/{camera_id}", status_code=204)
async def delete_camera(camera_id: str, request: Request, admin: AdminUser) -> None:
    await request.app.state.use_cases.delete_camera(camera_id, admin.username)
    _sync_media(request)


@router.post("/{camera_id}/test", response_model=list[ProbeResultOut], dependencies=[Depends(require_admin)])
async def test_camera(camera_id: str, request: Request) -> list[ProbeResultOut]:
    results = await request.app.state.use_cases.test_camera_connection(camera_id)
    return [ProbeResultOut.from_domain(r) for r in results]
