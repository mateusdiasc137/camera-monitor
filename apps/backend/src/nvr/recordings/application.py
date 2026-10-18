from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath

from nvr.cameras.domain import StreamProfile
from nvr.cameras.ports import CameraRepository
from nvr.recordings.domain import (
    Gap,
    RecordingInterval,
    Segment,
    StatusPoint,
    align_to_recording,
    clip,
    find_gaps,
    merge_intervals,
    parse_segment_filename,
)
from nvr.recordings.location import ActiveRecordDir
from nvr.recordings.ports import (
    PlaybackServer,
    RecordingStream,
    SegmentIndex,
    SegmentStore,
    StorageLocation,
    StorageLocations,
)
from nvr.shared.clock import Clock
from nvr.shared.errors import NotFound, ValidationFailed
from nvr.streaming.domain import parse_path_name, path_name
from nvr.streaming.ports import CameraStatusLog

log = logging.getLogger(__name__)

MAX_QUERY_RANGE = timedelta(days=31)
LIVE_TAIL = timedelta(seconds=90)  # índice atualizado a cada ~30 s + 1 parte em escrita


class EnsureStorageLocation:
    """Garante um local de gravação ativo: na primeira execução, `recordings_dir`; depois, o escolhido
    pela UI (F1-11b). Mantém `record_dir` (usado no recordPath do MediaMTX) em dia com o banco."""

    def __init__(
        self, locations: StorageLocations, default_path: Path, record_dir: ActiveRecordDir | None = None
    ) -> None:
        self._locations, self._default, self._record_dir = locations, default_path, record_dir

    async def __call__(self) -> StorageLocation:
        location = await self._locations.active()
        if location is None:
            location = StorageLocation(str(uuid.uuid4()), self._default, active=True)
            await asyncio.to_thread(self._default.mkdir, parents=True, exist_ok=True)
            await self._locations.add(location)
            log.info("local de gravação padrão criado: %s", self._default)
        if self._record_dir is not None:
            self._record_dir.path = location.path
        return location


@dataclass
class IndexReport:
    added: int = 0
    updated: int = 0
    removed: int = 0
    skipped: dict[str, int] = field(default_factory=lambda: defaultdict(int))


class IndexSegments:
    """Sincroniza o índice com os arquivos do local de gravação ativo.

    Idempotente e incremental: só lê (probe) arquivos novos ou que mudaram de tamanho — inclusive o
    segmento ainda em gravação, que é relido enquanto cresce. Arquivos que sumiram saem do índice.
    """

    def __init__(
        self,
        locations: StorageLocations,
        index: SegmentIndex,
        store: SegmentStore,
        cameras: CameraRepository,
    ) -> None:
        self._locations, self._index, self._store, self._cameras = locations, index, store, cameras
        self._lock = asyncio.Lock()
        self._warned: set[str] = set()

    async def __call__(self) -> IndexReport:
        async with self._lock:
            return await self._run()

    def _warn_once(self, key: str, message: str, *args: object) -> None:
        if key not in self._warned:
            self._warned.add(key)
            log.warning(message, *args)

    async def _run(self) -> IndexReport:
        report = IndexReport()
        location = await self._locations.active()
        if location is None:
            return report
        files = await asyncio.to_thread(self._store.scan, location.path)
        known = await self._index.known(location.id)
        camera_ids = {c.id for c in await self._cameras.list()}

        changed: list[Segment] = []
        for file in files:
            folder, name = PurePosixPath(file.rel_path).parent.name, PurePosixPath(file.rel_path).name
            parsed = parse_path_name(folder)
            if parsed is None:
                report.skipped["pasta desconhecida"] += 1
                continue
            camera_id, profile = parsed
            if profile is not StreamProfile.MAIN:
                report.skipped["perfil não gravado"] += 1
                continue
            if camera_id not in camera_ids:
                report.skipped["câmera removida"] += 1
                self._warn_once(folder, "gravações de câmera removida em %s (não indexadas)", folder)
                continue
            if known.get(file.rel_path) == file.size_bytes:
                continue
            try:
                probed = await asyncio.to_thread(self._store.probe, location.path, file.rel_path)
            except (OSError, ValueError) as exc:
                report.skipped["ilegível"] += 1
                self._warn_once(file.rel_path, "segmento ilegível %s: %s", file.rel_path, exc)
                continue
            start = probed.start_at or parse_segment_filename(name)
            if start is None or probed.duration_s <= 0:
                report.skipped["sem dados"] += 1
                continue
            if file.rel_path in known:
                report.updated += 1
            else:
                report.added += 1
            changed.append(
                Segment(camera_id, location.id, file.rel_path, start, probed.duration_s, file.size_bytes)
            )

        if changed:
            await self._index.upsert(changed)
        vanished = sorted(known.keys() - {f.rel_path for f in files})
        if vanished:
            await self._index.remove(location.id, vanished)
            report.removed = len(vanished)
        if report.added or report.removed:
            log.info("índice de gravações: +%d ~%d -%d", report.added, report.updated, report.removed)
        return report


@dataclass(frozen=True)
class RecordingsView:
    camera_id: str
    start: datetime
    end: datetime
    intervals: list[RecordingInterval]
    gaps: list[Gap]
    segments: int
    recording_now: bool

    @property
    def recorded_s(self) -> float:
        return sum(i.duration_s for i in self.intervals)


class ListRecordings:
    def __init__(
        self, cameras: CameraRepository, index: SegmentIndex, status_log: CameraStatusLog, clock: Clock
    ) -> None:
        self._cameras, self._index, self._status, self._clock = cameras, index, status_log, clock

    async def __call__(self, camera_id: str, start: datetime, end: datetime) -> RecordingsView:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValidationFailed("informe datas com fuso horário (ISO 8601)")
        if end <= start:
            raise ValidationFailed("o fim deve ser depois do início")
        if end - start > MAX_QUERY_RANGE:
            raise ValidationFailed("intervalo máximo de consulta: 31 dias")
        if await self._cameras.get(camera_id) is None:
            raise NotFound("câmera não encontrada")

        now = self._clock.now()
        effective_end = min(end, now)
        segments = await self._index.in_range(camera_id, start, effective_end) if start < now else []
        intervals = merge_intervals(segments)
        changes = await self._status.between(camera_id, start, effective_end)
        history = [StatusPoint(at, h.state.value, h.recording) for at, h in changes]

        # "Gravando agora": o índice atrasa até ~30 s; estende o último trecho até agora.
        latest = history[-1] if history else None
        recording_now = bool(latest and latest.recording and latest.state in ("online", "degraded"))
        if recording_now and intervals and effective_end == now and now - intervals[-1].end <= LIVE_TAIL:
            intervals[-1] = RecordingInterval(intervals[-1].start, now)

        clipped = clip(intervals, start, effective_end)
        gaps = find_gaps(clipped, start, effective_end, history) if start < effective_end else []
        return RecordingsView(camera_id, start, end, clipped, gaps, len(segments), recording_now)


MAX_CLIP = timedelta(hours=2)
CLIP_FORMATS = ("fmp4", "mp4")


@dataclass(frozen=True)
class RecordingClip:
    stream: RecordingStream
    camera_name: str
    start: datetime
    duration_s: float
    fmt: str


class OpenRecording:
    """Abre um trecho gravado no servidor de playback do MediaMTX (via backend, ADR-003)."""

    def __init__(
        self, cameras: CameraRepository, playback: PlaybackServer, index: SegmentIndex | None = None
    ) -> None:
        self._cameras, self._playback, self._index = cameras, playback, index

    async def _align(self, camera_id: str, start: datetime, duration_s: float) -> tuple[datetime, float]:
        """Início num buraco curto (o gravador reiniciou e esperou o próximo quadro-chave: troca de local,
        câmera que reconectou...): o MediaMTX responde 404. Avança até o próximo segmento, mantendo o fim."""
        if self._index is None:
            return start, duration_s
        end = start + timedelta(seconds=duration_s)
        aligned = align_to_recording(await self._index.in_range(camera_id, start, end), start, end)
        return (start, duration_s) if aligned is None else aligned

    async def __call__(self, camera_id: str, start: datetime, duration_s: float, fmt: str) -> RecordingClip:
        if start.tzinfo is None:
            raise ValidationFailed("informe o início com fuso horário (ISO 8601)")
        if not 0 < duration_s <= MAX_CLIP.total_seconds():
            raise ValidationFailed("duração deve ser maior que zero e de no máximo 2 h")
        if fmt not in CLIP_FORMATS:
            raise ValidationFailed("formato deve ser fmp4 ou mp4")
        camera = await self._cameras.get(camera_id)
        if camera is None:
            raise NotFound("câmera não encontrada")
        start, duration_s = await self._align(camera.id, start, duration_s)
        if duration_s <= 0:
            raise NotFound("não há gravação nesse trecho")
        stream = await self._playback.open(path_name(camera.id, StreamProfile.MAIN), start, duration_s, fmt)
        return RecordingClip(stream, camera.name, start, duration_s, fmt)
