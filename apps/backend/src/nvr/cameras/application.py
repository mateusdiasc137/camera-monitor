from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, replace

from nvr.cameras.domain import (
    Camera,
    CameraCredentials,
    CameraDriver,
    IngestMode,
    LensLayout,
    ProbeResult,
    StreamProfile,
    rtsp_url,
    validate_credentials,
    validate_host,
    validate_name,
    validate_port,
    validate_rtsp_path,
)
from nvr.cameras.ports import CameraRepository, SecretStore, StreamProbe
from nvr.shared.audit import AuditLog
from nvr.shared.clock import Clock
from nvr.shared.errors import Conflict, NotFound, ValidationFailed


class CameraValidationFailed(ValidationFailed):
    """Erros por campo, para a UI mostrar todos de uma vez."""

    def __init__(self, fields: dict[str, str]) -> None:
        super().__init__("; ".join(f"{k}: {v}" for k, v in fields.items()), fields)


@dataclass(frozen=True)
class CameraDraft:
    name: str
    host: str
    main_path: str
    credentials: CameraCredentials
    rtsp_port: int = 554
    sub_path: str | None = None
    driver: CameraDriver = CameraDriver.GENERIC_RTSP
    ingest: IngestMode = IngestMode.RELAY
    enabled: bool = True
    recording_enabled: bool = True
    lens_layout: LensLayout = LensLayout.SINGLE


@dataclass(frozen=True)
class CameraChanges:
    """Alteração parcial. `None` = manter; `sub_path=""` remove o substream."""

    name: str | None = None
    host: str | None = None
    rtsp_port: int | None = None
    main_path: str | None = None
    sub_path: str | None = None
    driver: CameraDriver | None = None
    ingest: IngestMode | None = None
    enabled: bool | None = None
    recording_enabled: bool | None = None
    lens_layout: LensLayout | None = None
    credentials: CameraCredentials | None = None


def _validate(camera: Camera, credentials: CameraCredentials | None) -> None:
    checks = {
        "name": validate_name(camera.name),
        "host": validate_host(camera.host),
        "rtsp_port": validate_port(camera.rtsp_port),
        "main_path": validate_rtsp_path(camera.main_path),
        "sub_path": validate_rtsp_path(camera.sub_path) if camera.sub_path else None,
        "credentials": validate_credentials(credentials) if credentials else None,
    }
    errors = {field: msg for field, msg in checks.items() if msg}
    if errors:
        raise CameraValidationFailed(errors)


class _CameraUseCase:
    def __init__(
        self, cameras: CameraRepository, secrets: SecretStore, audit: AuditLog, clock: Clock
    ) -> None:
        self._cameras, self._secrets, self._audit, self._clock = cameras, secrets, audit, clock

    async def _require(self, camera_id: str) -> Camera:
        camera = await self._cameras.get(camera_id)
        if camera is None:
            raise NotFound("câmera não encontrada")
        return camera

    async def _ensure_unique_name(self, name: str, own_id: str | None = None) -> None:
        other = await self._cameras.get_by_name(name)
        if other is not None and other.id != own_id:
            raise CameraValidationFailed({"name": "já existe uma câmera com esse nome"})


class ListCameras(_CameraUseCase):
    async def __call__(self) -> list[Camera]:
        return sorted(await self._cameras.list(), key=lambda c: c.name.lower())


class GetCamera(_CameraUseCase):
    async def __call__(self, camera_id: str) -> Camera:
        return await self._require(camera_id)


class RegisterCamera(_CameraUseCase):
    async def __call__(self, draft: CameraDraft, actor: str) -> Camera:
        now = self._clock.now()
        camera_id = str(uuid.uuid4())
        camera = Camera(
            id=camera_id,
            name=draft.name.strip(),
            driver=draft.driver,
            host=draft.host.strip(),
            rtsp_port=draft.rtsp_port,
            main_path=draft.main_path.strip(),
            sub_path=(draft.sub_path or "").strip() or None,
            secret_ref=f"camera:{camera_id}",
            ingest=draft.ingest,
            enabled=draft.enabled,
            recording_enabled=draft.recording_enabled,
            lens_layout=draft.lens_layout,
            created_at=now,
            updated_at=now,
        )
        _validate(camera, draft.credentials)
        await self._ensure_unique_name(camera.name)
        await asyncio.to_thread(self._secrets.put, camera.secret_ref, draft.credentials)
        try:
            await self._cameras.add(camera)
        except Exception:
            await asyncio.to_thread(self._secrets.delete, camera.secret_ref)
            raise
        await self._audit.record(
            actor, "camera.created", camera.id, {"name": camera.name, "host": camera.host}
        )
        return camera


class UpdateCamera(_CameraUseCase):
    async def __call__(self, camera_id: str, changes: CameraChanges, actor: str) -> Camera:
        current = await self._require(camera_id)
        camera = replace(
            current,
            name=current.name if changes.name is None else changes.name.strip(),
            host=current.host if changes.host is None else changes.host.strip(),
            rtsp_port=current.rtsp_port if changes.rtsp_port is None else changes.rtsp_port,
            main_path=current.main_path if changes.main_path is None else changes.main_path.strip(),
            sub_path=current.sub_path if changes.sub_path is None else (changes.sub_path.strip() or None),
            driver=changes.driver or current.driver,
            ingest=changes.ingest or current.ingest,
            enabled=current.enabled if changes.enabled is None else changes.enabled,
            recording_enabled=(
                current.recording_enabled if changes.recording_enabled is None else changes.recording_enabled
            ),
            lens_layout=changes.lens_layout or current.lens_layout,
            updated_at=self._clock.now(),
        )
        _validate(camera, changes.credentials)
        await self._ensure_unique_name(camera.name, own_id=camera.id)
        if changes.credentials is not None:
            await asyncio.to_thread(self._secrets.put, camera.secret_ref, changes.credentials)
        await self._cameras.update(camera)
        compared = (
            "name",
            "host",
            "rtsp_port",
            "main_path",
            "sub_path",
            "driver",
            "ingest",
            "enabled",
            "recording_enabled",
            "lens_layout",
        )
        changed = [f for f in compared if getattr(camera, f) != getattr(current, f)]
        changed += ["credentials"] if changes.credentials else []
        await self._audit.record(actor, "camera.updated", camera.id, {"fields": changed})
        return camera


class DeleteCamera(_CameraUseCase):
    async def __call__(self, camera_id: str, actor: str) -> None:
        camera = await self._require(camera_id)
        await self._cameras.delete(camera.id)
        await asyncio.to_thread(self._secrets.delete, camera.secret_ref)
        await self._audit.record(actor, "camera.deleted", camera.id, {"name": camera.name})


class TestCameraConnection(_CameraUseCase):
    """Sonda os perfis cadastrados (principal e, se houver, secundário). Somente leitura."""

    __test__ = False  # não é uma classe de teste do pytest

    def __init__(
        self,
        cameras: CameraRepository,
        secrets: SecretStore,
        audit: AuditLog,
        clock: Clock,
        probe: StreamProbe,
    ) -> None:
        super().__init__(cameras, secrets, audit, clock)
        self._probe = probe

    async def __call__(self, camera_id: str) -> list[ProbeResult]:
        camera = await self._require(camera_id)
        credentials = await asyncio.to_thread(self._secrets.get, camera.secret_ref)
        if credentials is None:
            raise Conflict(
                "as credenciais desta câmera não estão no cofre; edite a câmera e informe-as de novo"
            )
        profiles = [StreamProfile.MAIN] + ([StreamProfile.SUB] if camera.sub_path else [])
        return list(
            await asyncio.gather(*(self._probe.probe(rtsp_url(camera, p, credentials), p) for p in profiles))
        )
