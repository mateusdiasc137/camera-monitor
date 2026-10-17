"""Câmeras: cadastro, credenciais (por referência) e endereço RTSP.

As credenciais da câmera nunca ficam no cadastro: só `secret_ref`, a chave no cofre (ADR-003).
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from urllib.parse import quote


class CameraDriver(StrEnum):
    GENERIC_RTSP = "generic_rtsp"
    XIONGMAI = "xiongmai"  # iCSee/XMEye; controles via DVRIP nas próximas fases


class IngestMode(StrEnum):
    RELAY = "relay"  # FFmpeg -c copy com timestamps do servidor (ADR-002, padrão)
    DIRECT = "direct"  # MediaMTX lê a câmera diretamente


class StreamProfile(StrEnum):
    MAIN = "main"
    SUB = "sub"


class LensLayout(StrEnum):
    """Como a imagem chega no stream. Câmeras de duas lentes (ex.: X6C-WEQ) empilham as imagens."""

    SINGLE = "single"
    STACKED2 = "stacked2"  # duas imagens, uma acima da outra


@dataclass(frozen=True)
class CameraCredentials:
    username: str
    password: str

    def __repr__(self) -> str:  # nunca expor a senha em logs/tracebacks
        return f"CameraCredentials(username={self.username!r}, password='***')"

    __str__ = __repr__


@dataclass(frozen=True)
class Camera:
    id: str
    name: str
    driver: CameraDriver
    host: str
    rtsp_port: int
    main_path: str
    sub_path: str | None
    secret_ref: str
    ingest: IngestMode
    enabled: bool
    recording_enabled: bool
    created_at: datetime
    updated_at: datetime
    lens_layout: LensLayout = LensLayout.SINGLE

    def path_for(self, profile: StreamProfile) -> str | None:
        return self.main_path if profile is StreamProfile.MAIN else self.sub_path


# ------------------------------------------------------------------ validação

_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)
_INLINE_SECRET = re.compile(
    r"(?:^|[/?&_;])(?:user|username|password|pwd|pass)=(?!\{(?:user|password)\})", re.IGNORECASE
)


def validate_name(name: str) -> str | None:
    if not name.strip():
        return "informe um nome"
    if len(name.strip()) > 80:
        return "o nome pode ter no máximo 80 caracteres"
    return None


def validate_host(host: str) -> str | None:
    host = host.strip()
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        pass
    if not _HOSTNAME.fullmatch(host):
        return "informe só o IP ou o nome do host (sem rtsp://, porta, usuário ou caminho)"
    return None


def validate_port(port: int) -> str | None:
    return None if 1 <= port <= 65535 else "a porta deve estar entre 1 e 65535"


def validate_rtsp_path(path: str) -> str | None:
    """O caminho vem depois de host:porta. Credenciais só como marcadores {user}/{password}."""
    if not path.startswith("/"):
        return "o caminho deve começar com '/'"
    if any(c.isspace() for c in path):
        return "o caminho não pode ter espaços"
    if "@" in path.split("?")[0] and "://" in path:
        return "informe só o caminho, sem rtsp:// nem usuário/senha"
    if _INLINE_SECRET.search(path):
        return "não coloque usuário/senha no caminho: use os marcadores {user} e {password}"
    if len(path) > 512:
        return "caminho longo demais"
    return None


def validate_credentials(credentials: CameraCredentials) -> str | None:
    if not credentials.username:
        return "informe o usuário da câmera"
    if len(credentials.username) > 128 or len(credentials.password) > 256:
        return "usuário ou senha longos demais"
    return None


# ----------------------------------------------------------------- URL RTSP


def rtsp_url(camera: Camera, profile: StreamProfile, credentials: CameraCredentials) -> str:
    """Monta a URL RTSP com credenciais. O resultado é SECRETO: só para adaptadores de mídia/sonda.

    Caminhos com {user}/{password} (Xiongmai) recebem as credenciais no próprio caminho;
    os demais usam `rtsp://usuario:senha@host:porta/caminho`.
    """
    path = camera.path_for(profile)
    if path is None:
        raise ValueError(f"a câmera não tem o perfil {profile.value}")
    host = f"[{camera.host}]" if ":" in camera.host else camera.host
    if "{user}" in path or "{password}" in path:
        filled = path.replace("{user}", credentials.username).replace("{password}", credentials.password)
        return f"rtsp://{host}:{camera.rtsp_port}{filled}"
    userinfo = f"{quote(credentials.username, safe='')}:{quote(credentials.password, safe='')}"
    return f"rtsp://{userinfo}@{host}:{camera.rtsp_port}{path}"


# ------------------------------------------------------- resultado de sonda


class ProbeOutcome(StrEnum):
    ONLINE = "online"
    AUTH_ERROR = "auth_error"
    NOT_FOUND = "not_found"  # câmera responde, mas o caminho não existe
    OFFLINE = "offline"  # sem conexão TCP / tempo esgotado
    PROTOCOL_ERROR = "protocol_error"  # respondeu algo que não é RTSP válido


@dataclass(frozen=True)
class MediaTrack:
    kind: str  # video | audio | application
    codec: str  # ex.: H265, PCMA
    clock_rate: int | None


@dataclass(frozen=True)
class ProbeResult:
    profile: StreamProfile
    outcome: ProbeOutcome
    detail: str
    tracks: tuple[MediaTrack, ...] = ()
    latency_ms: int | None = None
