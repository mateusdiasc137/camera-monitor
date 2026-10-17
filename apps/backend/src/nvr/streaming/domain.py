"""Paths de mídia no servidor de streaming e relays de ingestão (ADR-002)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

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
    failure: RelayFailure | None = None  # causa da última falha (None = processo atual sem erro)
    running_for_s: float | None = None
    recent_restarts: int = 0  # nos últimos 10 min


# ----------------------------------------------------------- estado das câmeras


class RelayFailure(StrEnum):
    """Causa da última falha do relay, inferida da saída do FFmpeg."""

    AUTH = "auth"  # 401: usuário/senha recusados
    NOT_FOUND = "not_found"  # 404: caminho RTSP inexistente
    UNREACHABLE = "unreachable"  # recusa de conexão, tempo esgotado, rede
    PUBLISH = "publish"  # o MediaMTX recusou a publicação
    OTHER = "other"


class CameraState(StrEnum):
    ONLINE = "online"
    DEGRADED = "degraded"  # recebendo, mas com problema (sem dados, substream fora, reinícios)
    STARTING = "starting"  # relay recém-iniciado, ainda sem stream
    OFFLINE = "offline"
    AUTH_ERROR = "auth_error"
    NOT_FOUND = "not_found"
    DISABLED = "disabled"


@dataclass(frozen=True)
class CameraHealth:
    camera_id: str
    state: CameraState
    detail: str
    recording: bool  # o principal está pronto e configurado para gravar

    def same_condition(self, other: CameraHealth | None) -> bool:
        return other is not None and (self.state, self.detail, self.recording) == (
            other.state, other.detail, other.recording,
        )  # fmt: skip


@dataclass(frozen=True)
class ProfileSignals:
    """Sinais de um perfil (main/sub) num instante."""

    path_ready: bool
    bytes_growing: bool
    relay_running: bool
    relay_age_s: float | None  # há quanto tempo o processo atual roda
    relay_failure: RelayFailure | None
    recent_restarts: int


STARTING_GRACE_S = 20.0
RESTARTS_DEGRADED = 3


def derive_health(
    camera_id: str,
    *,
    enabled: bool,
    record_enabled: bool,
    main: ProfileSignals | None,
    sub: ProfileSignals | None,
    has_sub: bool,
) -> CameraHealth:
    """Função pura: sinais do MediaMTX e dos relays → estado exibido ao usuário."""
    if not enabled:
        return CameraHealth(camera_id, CameraState.DISABLED, "câmera desativada", False)
    if main is None:
        return CameraHealth(camera_id, CameraState.STARTING, "aguardando a configuração da mídia", False)

    if main.path_ready:
        recording = record_enabled
        if not main.bytes_growing:
            return CameraHealth(
                camera_id, CameraState.DEGRADED, "conectada, mas sem receber dados", recording
            )
        if has_sub and (sub is None or not sub.path_ready):
            return CameraHealth(camera_id, CameraState.DEGRADED, "substream indisponível", recording)
        if main.recent_restarts >= RESTARTS_DEGRADED:
            return CameraHealth(
                camera_id, CameraState.DEGRADED, "conexão instável (reconexões frequentes)", recording
            )
        return CameraHealth(camera_id, CameraState.ONLINE, "recebendo vídeo", recording)

    if main.relay_failure is RelayFailure.AUTH:
        return CameraHealth(camera_id, CameraState.AUTH_ERROR, "a câmera recusou usuário/senha", False)
    if main.relay_failure is RelayFailure.NOT_FOUND:
        return CameraHealth(camera_id, CameraState.NOT_FOUND, "caminho RTSP não encontrado na câmera", False)
    if main.relay_running and (main.relay_age_s or 0) < STARTING_GRACE_S and main.relay_failure is None:
        return CameraHealth(camera_id, CameraState.STARTING, "conectando…", False)
    if main.relay_failure is RelayFailure.PUBLISH:
        return CameraHealth(camera_id, CameraState.OFFLINE, "o servidor de mídia recusou o stream", False)
    return CameraHealth(camera_id, CameraState.OFFLINE, "sem conexão com a câmera", False)
