"""Paths de mídia no servidor de streaming e relays de ingestão (ADR-002)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from nvr.cameras.domain import StreamProfile

MANAGED_PREFIX = "cam-"
_PATH_RE = re.compile(rf"^{MANAGED_PREFIX}(?P<camera_id>[0-9a-f-]{{36}})-(?P<profile>main|sub)$")


def path_name(camera_id: str, profile: StreamProfile) -> str:
    """Nome do path no MediaMTX. Só paths com este formato são gerenciados (e removidos) pelo NVR."""
    return f"{MANAGED_PREFIX}{camera_id}-{profile.value}"


def parse_path_name(name: str) -> tuple[str, StreamProfile] | None:
    match = _PATH_RE.fullmatch(name)
    if match is None:
        return None
    return match["camera_id"], StreamProfile(match["profile"])


@dataclass(frozen=True)
class PathSpec:
    """Configuração desejada de um path. A fonte é sempre um publicador local (o relay)."""

    name: str
    record: bool
    record_path: str


@dataclass(frozen=True)
class PathState:
    name: str
    ready: bool
    bytes_received: int
    readers: int
    tracks: tuple[str, ...]


@dataclass(frozen=True)
class RelaySpec:
    """O que o relay deve fazer. `source_url` é SECRETO (contém credenciais da câmera)."""

    name: str
    source_url: str

    def __repr__(self) -> str:
        return f"RelaySpec(name={self.name!r}, source_url='***')"


@dataclass(frozen=True)
class RelayStatus:
    name: str
    running: bool
    restarts: int
    last_exit_code: int | None
    last_error: str  # já mascarada
