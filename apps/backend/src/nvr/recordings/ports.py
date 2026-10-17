from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from nvr.recordings.domain import Segment


@dataclass(frozen=True)
class StorageLocation:
    id: str
    path: Path
    active: bool


@dataclass(frozen=True)
class SegmentFile:
    rel_path: str  # com "/", relativo à raiz do local
    size_bytes: int
    modified_at: float  # epoch (s)


@dataclass(frozen=True)
class ProbedSegment:
    start_at: datetime | None  # None: usar o nome do arquivo
    duration_s: float
    unfinished: bool  # gravação interrompida (duração calculada pelos fragmentos)


class StorageLocations(Protocol):
    async def active(self) -> StorageLocation | None: ...
    async def add(self, location: StorageLocation) -> None: ...


class SegmentIndex(Protocol):
    async def known(self, location_id: str) -> dict[str, int]:
        """{rel_path: size_bytes} dos segmentos já indexados no local."""
        ...

    async def upsert(self, segments: list[Segment]) -> None: ...
    async def remove(self, location_id: str, rel_paths: list[str]) -> None: ...
    async def in_range(self, camera_id: str, start: datetime, end: datetime) -> list[Segment]: ...


class SegmentStore(Protocol):
    """Acesso aos arquivos de gravação (sistema de arquivos)."""

    def scan(self, root: Path) -> list[SegmentFile]:
        """Arquivos .mp4 em subpastas de primeiro nível da raiz (uma por path do MediaMTX)."""
        ...

    def probe(self, root: Path, rel_path: str) -> ProbedSegment: ...


class RecordingStream(Protocol):
    """Fluxo de bytes de um trecho gravado, aberto no servidor de playback (já validado: 200 OK)."""

    content_type: str

    def chunks(self) -> AsyncIterator[bytes]: ...
    async def aclose(self) -> None: ...


class PlaybackServer(Protocol):
    async def open(self, path: str, start: datetime, duration_s: float, fmt: str) -> RecordingStream: ...
