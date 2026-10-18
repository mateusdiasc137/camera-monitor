from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from nvr.recordings.domain import DiskUsage, Segment, StorageMigration, StoragePolicy


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
    async def get(self, location_id: str) -> StorageLocation | None: ...
    async def by_path(self, path: Path) -> StorageLocation | None: ...

    async def activate(self, location_id: str) -> None:
        """Torna este o único local ativo (atômico)."""
        ...


class SegmentIndex(Protocol):
    async def known(self, location_id: str) -> dict[str, int]:
        """{rel_path: size_bytes} dos segmentos já indexados no local."""
        ...

    async def upsert(self, segments: list[Segment]) -> None: ...
    async def remove(self, location_id: str, rel_paths: list[str]) -> None: ...

    async def remove_location(self, location_id: str) -> int:
        """Tira do índice tudo o que está no local (os arquivos ficam). Devolve quantos saíram."""
        ...

    async def relocate(self, from_location_id: str, to_location_id: str, rel_path: str) -> None:
        """O arquivo mudou de local (mesmo caminho relativo). Idempotente; não duplica."""
        ...

    async def in_range(self, camera_id: str, start: datetime, end: datetime) -> list[Segment]: ...


class SegmentStore(Protocol):
    """Acesso aos arquivos de gravação (sistema de arquivos)."""

    def scan(self, root: Path) -> list[SegmentFile]:
        """Arquivos .mp4 em subpastas de primeiro nível da raiz (uma por path do MediaMTX)."""
        ...

    def probe(self, root: Path, rel_path: str) -> ProbedSegment: ...

    def delete(self, root: Path, rel_path: str) -> None:
        """Apaga um segmento (só dentro da raiz, só arquivos de gravação). Ausente = já apagado."""
        ...

    def move(self, src_root: Path, dst_root: Path, rel_path: str) -> None:
        """Move um segmento para outro local, mesmo caminho relativo. Seguro para retomar:
        nunca apaga a origem antes de o destino estar completo."""
        ...

    def remove_empty_folders(self, root: Path) -> None:
        """Remove as pastas de câmera vazias (só subpastas de primeiro nível, nunca a raiz)."""
        ...


class RecordingStream(Protocol):
    """Fluxo de bytes de um trecho gravado, aberto no servidor de playback (já validado: 200 OK)."""

    content_type: str

    def chunks(self) -> AsyncIterator[bytes]: ...
    async def aclose(self) -> None: ...


class PlaybackServer(Protocol):
    async def open(self, path: str, start: datetime, duration_s: float, fmt: str) -> RecordingStream: ...


class StoragePolicies(Protocol):
    async def get(self) -> StoragePolicy: ...
    async def save(self, policy: StoragePolicy) -> None: ...


class DiskProbe(Protocol):
    def usage(self, path: Path) -> DiskUsage: ...


@dataclass(frozen=True)
class FolderEntry:
    name: str
    path: Path  # resolvido


@dataclass(frozen=True)
class FolderCheck:
    """O que o sistema de arquivos diz sobre um candidato a local de gravação."""

    path: Path  # resolvido (links seguidos)
    exists: bool
    is_dir: bool
    creatable: bool  # não existe, mas o pai existe e aceita escrita
    writable: bool  # escrita real testada (arquivo temporário criado, gravado e apagado)
    write_error: str
    foreign_entries: int  # itens que não são pastas de câmera do NVR
    system_volume: bool  # mesma partição do sistema operacional


class StorageFilesystem(Protocol):
    """Navegação e verificação de pastas para escolher o local de gravação."""

    def resolve(self, path: str) -> Path:
        """Caminho absoluto normalizado, com links seguidos. ValueError se não for absoluto."""
        ...

    def subfolders(self, path: Path) -> list[FolderEntry]: ...
    def check(self, path: Path) -> FolderCheck: ...
    def same_volume(self, a: Path, b: Path) -> bool: ...
    def make_dir(self, path: Path) -> None: ...

    def permission_hint(self, path: Path) -> str:
        """Comando sugerido para dar permissão de escrita ao serviço."""
        ...


class StorageMigrations(Protocol):
    async def latest(self) -> StorageMigration | None: ...
    async def add(self, migration: StorageMigration) -> None: ...
    async def save(self, migration: StorageMigration) -> None: ...
