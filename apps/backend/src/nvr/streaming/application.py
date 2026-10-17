from __future__ import annotations

import asyncio
import hmac
import ipaddress
import logging
from dataclasses import dataclass, field

from nvr.cameras.domain import Camera, StreamProfile, rtsp_url
from nvr.cameras.ports import CameraRepository, SecretStore
from nvr.streaming.domain import PathSpec, RelaySpec, parse_path_name, path_name
from nvr.streaming.ports import MediaServer, RelaySupervisor

log = logging.getLogger(__name__)


@dataclass
class ReconcileReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)  # câmera -> motivo

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated or self.removed)


class ReconcileMedia:
    """Leva o MediaMTX e os relays ao estado desejado a partir das câmeras cadastradas.

    - Câmera ativa → path `cam-<id>-main` (+ `-sub` se houver) com fonte = relay local.
    - Só o perfil principal grava, e só se `recording_enabled` (ADR-006).
    - Paths gerenciados que não correspondem a nenhuma câmera ativa são removidos; paths de terceiros
      (sem o prefixo `cam-`) nunca são tocados.
    Idempotente: pode rodar periodicamente e após reinício do MediaMTX.
    """

    def __init__(
        self,
        cameras: CameraRepository,
        secrets: SecretStore,
        media: MediaServer,
        relays: RelaySupervisor,
        record_root: str,
    ) -> None:
        self._cameras, self._secrets, self._media, self._relays = cameras, secrets, media, relays
        self._record_template = f"{record_root.rstrip('/')}/%path/%Y-%m-%d_%H-%M-%S-%f"
        self._lock = asyncio.Lock()

    def _profiles(self, camera: Camera) -> list[StreamProfile]:
        return [StreamProfile.MAIN] + ([StreamProfile.SUB] if camera.sub_path else [])

    async def __call__(self) -> ReconcileReport:
        async with self._lock:
            return await self._reconcile()

    async def _reconcile(self) -> ReconcileReport:
        report = ReconcileReport()
        desired_paths: dict[str, PathSpec] = {}
        desired_relays: dict[str, RelaySpec] = {}

        for camera in await self._cameras.list():
            if not camera.enabled:
                continue
            credentials = await asyncio.to_thread(self._secrets.get, camera.secret_ref)
            if credentials is None:
                report.skipped[camera.name] = "credenciais ausentes no cofre"
                continue
            for profile in self._profiles(camera):
                name = path_name(camera.id, profile)
                record = profile is StreamProfile.MAIN and camera.recording_enabled
                desired_paths[name] = PathSpec(name, record, self._record_template)
                desired_relays[name] = RelaySpec(name, rtsp_url(camera, profile, credentials))

        # Defesa em profundidade: nunca alterar paths fora do padrão gerenciado,
        # mesmo que o adaptador os liste.
        current = {n: s for n, s in (await self._media.list_configured()).items() if parse_path_name(n)}
        for name, spec in desired_paths.items():
            if name not in current:
                await self._media.put_path(spec)
                report.added.append(name)
            elif current[name] != spec:
                await self._media.put_path(spec)
                report.updated.append(name)
        for name in current.keys() - desired_paths.keys():
            await self._media.remove_path(name)
            report.removed.append(name)

        for relay_spec in desired_relays.values():
            self._relays.ensure(relay_spec)
        for name in self._relays.names() - desired_relays.keys():
            await self._relays.stop(name)

        if report.changed:
            log.info("mídia reconciliada: +%s ~%s -%s", report.added, report.updated, report.removed)
        for camera_name, reason in report.skipped.items():
            log.warning("câmera '%s' ignorada: %s", camera_name, reason)
        return report


@dataclass(frozen=True)
class MediaAccessRequest:
    """Pedido de autorização enviado pelo MediaMTX (`authMethod: http`)."""

    action: str  # publish | read | playback | api | metrics | pprof
    path: str
    ip: str
    user: str
    password: str
    protocol: str = ""


def _is_loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


class AuthorizeMediaAccess:
    """Decide quem pode publicar/assistir no MediaMTX.

    - publish: só o relay local (loopback + usuário `relay` + segredo da inicialização), em paths gerenciados.
    - api/metrics: só loopback (a API de controle nem escuta fora do loopback).
    - read/playback: negado até a F1-08 (tokens de sessão do player).
    """

    RELAY_USER = "relay"

    def __init__(self, relay_secret: str) -> None:
        self._relay_secret = relay_secret

    def __call__(self, req: MediaAccessRequest) -> bool:
        if req.action == "publish":
            return (
                _is_loopback(req.ip)
                and req.user == self.RELAY_USER
                and hmac.compare_digest(req.password.encode(), self._relay_secret.encode())
                and parse_path_name(req.path) is not None
            )
        if req.action in ("api", "metrics"):
            return _is_loopback(req.ip)
        return False
