"""Casos de uso de armazenamento: cota, substituição dos mais antigos e panorama (ADR-006)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import PurePosixPath

from nvr.cameras.ports import CameraRepository
from nvr.recordings.domain import (
    GIB,
    DiskUsage,
    ReplacementPolicy,
    StoragePolicy,
    StorageState,
    StoredFile,
    parse_segment_filename,
    plan_cleanup,
    should_resume_recording,
)
from nvr.recordings.ports import (
    DiskProbe,
    SegmentIndex,
    SegmentStore,
    StorageLocation,
    StorageLocations,
    StoragePolicies,
)
from nvr.shared.audit import AuditLog
from nvr.shared.clock import Clock
from nvr.shared.errors import ConfirmationRequired, ValidationFailed
from nvr.streaming.domain import parse_path_name

log = logging.getLogger(__name__)
MIN_QUOTA_GB = 1.0


def _gb(value: float) -> str:
    """Gigabytes com vírgula decimal, para mensagens ao usuário."""
    return f"{value / GIB:.1f}".replace(".", ",")


class RecordingGate:
    """Trava global de gravação (política "parar quando encher"). A reconciliação de mídia a consulta."""

    def __init__(self) -> None:
        self.allowed = True
        self.reason = ""

    def __call__(self) -> bool:
        return self.allowed


@dataclass(frozen=True)
class StorageSnapshot:
    location: StorageLocation
    files: list[StoredFile]
    disk: DiskUsage

    @property
    def used_bytes(self) -> int:
        return sum(f.size_bytes for f in self.files)


async def take_snapshot(
    locations: StorageLocations, store: SegmentStore, disk: DiskProbe
) -> StorageSnapshot | None:
    location = await locations.active()
    if location is None:
        return None
    raw = await asyncio.to_thread(store.scan, location.path)
    usage = await asyncio.to_thread(disk.usage, location.path)
    files: list[StoredFile] = []
    for f in raw:
        path = PurePosixPath(f.rel_path)
        if parse_path_name(path.parent.name) is None:
            continue  # só pastas de câmera gerenciadas pelo NVR contam e podem ser apagadas
        start = parse_segment_filename(path.name)
        if start is None:
            continue
        files.append(StoredFile(f.rel_path, path.parent.name, start, f.size_bytes, f.modified_at))
    return StorageSnapshot(location, files, usage)


@dataclass
class CleanupResult:
    at: datetime
    state: StorageState
    reason: str
    deleted_files: int = 0
    freed_bytes: int = 0
    used_bytes: int = 0
    disk: DiskUsage | None = None
    recording_paused: bool = False
    errors: list[str] = field(default_factory=list)


class EnforceStoragePolicy:
    """Aplica a cota e o piso de espaço livre: apaga os mais antigos ou pausa a gravação."""

    def __init__(
        self,
        locations: StorageLocations,
        policies: StoragePolicies,
        store: SegmentStore,
        index: SegmentIndex,
        disk: DiskProbe,
        gate: RecordingGate,
        audit: AuditLog,
        clock: Clock,
        cameras: CameraRepository,
    ) -> None:
        self._locations, self._policies, self._store, self._index = locations, policies, store, index
        self._disk, self._gate, self._audit, self._clock = disk, gate, audit, clock
        self._cameras = cameras
        self._lock = asyncio.Lock()
        self.last: CleanupResult | None = None  # última verificação
        self.last_deletion: CleanupResult | None = None  # última verificação que apagou algo
        self.on_gate_change: Callable[[], None] | None = None  # ex.: reconciliar a mídia já

    async def __call__(self) -> CleanupResult | None:
        async with self._lock:
            return await self._run()

    def _set_gate(self, allowed: bool, reason: str) -> None:
        if self._gate.allowed == allowed:
            return
        self._gate.allowed, self._gate.reason = allowed, reason
        log.warning("gravação %s: %s", "retomada" if allowed else "PAUSADA", reason or "espaço disponível")
        if self.on_gate_change is not None:
            self.on_gate_change()

    async def _run(self) -> CleanupResult | None:
        snapshot = await take_snapshot(self._locations, self._store, self._disk)
        if snapshot is None:
            return None
        policy = await self._policies.get()
        active = {f"cam-{c.id}-{p}" for c in await self._cameras.list() for p in ("main", "sub")}
        plan = plan_cleanup(policy, snapshot.files, snapshot.disk, time.time(), active)
        result = CleanupResult(self._clock.now(), plan.state, plan.reason, used_bytes=snapshot.used_bytes,
                               disk=snapshot.disk)  # fmt: skip

        deleted: list[StoredFile] = []
        for f in plan.delete:
            try:
                await asyncio.to_thread(self._store.delete, snapshot.location.path, f.rel_path)
                deleted.append(f)
            except OSError as exc:
                result.errors.append(f"{f.rel_path}: {type(exc).__name__}")
        if deleted:
            await self._index.remove(snapshot.location.id, [f.rel_path for f in deleted])
            result.deleted_files = len(deleted)
            result.freed_bytes = sum(f.size_bytes for f in deleted)
            result.used_bytes -= result.freed_bytes
            detail = {
                "motivo": plan.reason,
                "arquivos": len(deleted),
                "bytes": result.freed_bytes,
                "de": min(f.start_at for f in deleted).isoformat(),
                "ate": max(f.start_at for f in deleted).isoformat(),
            }
            await self._audit.record("system", "storage.cleanup", snapshot.location.id, detail)
            log.info("armazenamento (%s): %d arquivo(s) antigos apagados, %.2f GB liberados",
                     plan.reason, len(deleted), result.freed_bytes / GIB)  # fmt: skip
        if result.errors:
            log.warning(
                "armazenamento: falha ao apagar %d arquivo(s): %s", len(result.errors), result.errors[:3]
            )

        if policy.replacement is ReplacementPolicy.STOP_WHEN_FULL:
            if plan.stop_recording:
                self._set_gate(False, f"armazenamento cheio ({plan.reason})")
            elif not self._gate.allowed and should_resume_recording(
                policy, snapshot.used_bytes, snapshot.disk
            ):
                self._set_gate(True, "")
        else:
            self._set_gate(True, "")
        result.recording_paused = not self._gate.allowed
        if result.recording_paused:
            result.state = StorageState.FULL
        self.last = result
        if result.deleted_files:
            self.last_deletion = result
        return result


@dataclass(frozen=True)
class CameraUsage:
    camera_id: str
    camera_name: str | None  # None = câmera removida (gravações órfãs)
    bytes: int
    oldest: datetime | None
    newest: datetime | None
    bytes_per_day: float | None  # medido nas últimas 24 h de gravação


@dataclass(frozen=True)
class StorageOverview:
    location: StorageLocation
    policy: StoragePolicy
    used_bytes: int
    disk: DiskUsage
    cameras: list[CameraUsage]
    days_covered: float | None  # quantos dias a cota (ou o disco) comporta no ritmo atual
    last_cleanup: CleanupResult | None
    recording_paused: bool
    pause_reason: str


class GetStorageOverview:
    def __init__(
        self,
        locations: StorageLocations,
        policies: StoragePolicies,
        store: SegmentStore,
        index: SegmentIndex,
        disk: DiskProbe,
        cameras: CameraRepository,
        enforcer: EnforceStoragePolicy,
        gate: RecordingGate,
        clock: Clock,
    ) -> None:
        self._locations, self._policies, self._store, self._index = locations, policies, store, index
        self._disk, self._cameras, self._enforcer, self._gate, self._clock = (
            disk,
            cameras,
            enforcer,
            gate,
            clock,
        )

    async def __call__(self) -> StorageOverview | None:
        snapshot = await take_snapshot(self._locations, self._store, self._disk)
        if snapshot is None:
            return None
        policy = await self._policies.get()
        names = {c.id: (c.name, c.recording_enabled) for c in await self._cameras.list()}
        now = self._clock.now()

        per_folder: dict[str, list[StoredFile]] = {}
        for f in snapshot.files:
            per_folder.setdefault(f.folder, []).append(f)
        usage: list[CameraUsage] = []
        total_rate = 0.0
        for folder, files in sorted(per_folder.items()):
            parsed = parse_path_name(folder)
            camera_id = parsed[0] if parsed else folder
            segments = await self._index.in_range(camera_id, now - timedelta(hours=24), now)
            seconds = sum(s.duration_s for s in segments)
            rate = sum(s.size_bytes for s in segments) / seconds * 86400 if seconds > 60 else None
            name, recording = names.get(camera_id, (None, False))
            if rate and recording:
                total_rate += rate
            starts = [f.start_at for f in files]
            usage.append(
                CameraUsage(camera_id, name, sum(f.size_bytes for f in files), min(starts), max(starts), rate)
            )

        capacity = policy.quota_bytes or (
            snapshot.used_bytes + snapshot.disk.free_bytes - policy.min_free_bytes
        )
        days = capacity * policy.low_watermark / total_rate if total_rate > 0 and capacity > 0 else None
        last = self._enforcer.last_deletion
        return StorageOverview(snapshot.location, policy, snapshot.used_bytes, snapshot.disk, usage, days,
                               last, not self._gate.allowed, self._gate.reason)  # fmt: skip


class UpdateStoragePolicy:
    def __init__(
        self,
        locations: StorageLocations,
        policies: StoragePolicies,
        store: SegmentStore,
        disk: DiskProbe,
        audit: AuditLog,
    ) -> None:
        self._locations, self._policies, self._store, self._disk, self._audit = (
            locations,
            policies,
            store,
            disk,
            audit,
        )
        self.on_change: Callable[[], None] | None = None  # aplica a política imediatamente

    async def __call__(
        self, quota_gb: float | None, replacement: ReplacementPolicy, confirm: bool, actor: str
    ) -> StoragePolicy:
        snapshot = await take_snapshot(self._locations, self._store, self._disk)
        current = await self._policies.get()
        quota = None if quota_gb is None else int(quota_gb * GIB)
        if quota_gb is not None and quota_gb < MIN_QUOTA_GB:
            raise ValidationFailed("cota muito pequena", {"quota_gb": f"mínimo {MIN_QUOTA_GB:.0f} GB"})
        if quota is not None and snapshot is not None:
            possible = snapshot.used_bytes + snapshot.disk.free_bytes - current.min_free_bytes
            if quota > possible:
                raise ValidationFailed(
                    "a cota não cabe no disco",
                    {"quota_gb": f"o máximo possível neste disco é {_gb(max(possible, 0))} GB"},
                )
            would_delete = snapshot.used_bytes - int(quota * current.low_watermark)
            if replacement is ReplacementPolicy.OVERWRITE_OLDEST and would_delete > 0 and not confirm:
                raise ConfirmationRequired(
                    "a nova cota é menor que o já gravado: cerca de "
                    f"{_gb(would_delete)} GB das gravações mais antigas serão apagados",
                    {"would_delete_bytes": would_delete, "used_bytes": snapshot.used_bytes},
                )
        policy = replace(current, quota_bytes=quota, replacement=replacement)
        await self._policies.save(policy)
        await self._audit.record(actor, "storage.policy_updated", None, {
            "cota_gb": quota_gb, "substituicao": replacement.value, "confirmado": confirm,
        })  # fmt: skip
        if self.on_change is not None:
            self.on_change()
        return policy
