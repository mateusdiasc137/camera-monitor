from __future__ import annotations

import asyncio
import hmac
import ipaddress
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from nvr.cameras.domain import Camera, StreamProfile, rtsp_url
from nvr.cameras.ports import CameraRepository, SecretStore
from nvr.shared.clock import Clock
from nvr.shared.errors import AppError, Conflict, NotFound, ValidationFailed
from nvr.shared.events import Event, EventPublisher
from nvr.streaming.domain import (
    CameraHealth,
    CameraState,
    LiveAnswer,
    PathSpec,
    PathState,
    ProfileSignals,
    RelaySpec,
    RelayStatus,
    derive_health,
    parse_path_name,
    path_name,
)
from nvr.streaming.ports import CameraStatusLog, LiveSignaling, MediaServer, RelaySupervisor

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
        record_path: str | Callable[[], str],
        recording_allowed: Callable[[], bool] | None = None,
    ) -> None:
        """`record_path`: recordPath completo do MediaMTX (local de gravação + padrão de nome), ou função
        que o devolve — o local pode mudar pela UI e a próxima reconciliação aplica (F1-11b)."""
        self._cameras, self._secrets, self._media, self._relays = cameras, secrets, media, relays
        self._record_path: Callable[[], str] = record_path if callable(record_path) else (lambda: record_path)
        # Trava global (armazenamento cheio com a política "parar quando encher").
        self._recording_allowed = recording_allowed or (lambda: True)
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
        record_path = self._record_path()

        for camera in await self._cameras.list():
            if not camera.enabled:
                continue
            credentials = await asyncio.to_thread(self._secrets.get, camera.secret_ref)
            if credentials is None:
                report.skipped[camera.name] = "credenciais ausentes no cofre"
                continue
            for profile in self._profiles(camera):
                name = path_name(camera.id, profile)
                record = (
                    profile is StreamProfile.MAIN and camera.recording_enabled and self._recording_allowed()
                )
                desired_paths[name] = PathSpec(name, record, record_path)
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
    """Decide quem pode publicar/assistir no MediaMTX. O navegador nunca fala direto com o MediaMTX:
    o backend autentica a sessão do painel e repassa a negociação com a credencial `reader` (ADR-003).

    - publish: só o relay local (loopback + usuário `relay` + segredo da inicialização), em paths gerenciados.
    - read/playback: só o próprio backend (loopback + usuário `reader` + segredo da inicialização).
    - api/metrics: só loopback (a API de controle nem escuta fora do loopback).
    """

    RELAY_USER = "relay"
    READER_USER = "reader"

    def __init__(self, relay_secret: str, reader_secret: str) -> None:
        self._secrets = {self.RELAY_USER: relay_secret, self.READER_USER: reader_secret}

    def _is(self, req: MediaAccessRequest, user: str) -> bool:
        return (
            _is_loopback(req.ip)
            and req.user == user
            and hmac.compare_digest(req.password.encode(), self._secrets[user].encode())
            and parse_path_name(req.path) is not None
        )

    def __call__(self, req: MediaAccessRequest) -> bool:
        if req.action == "publish":
            return self._is(req, self.RELAY_USER)
        if req.action in ("read", "playback"):
            return self._is(req, self.READER_USER)
        if req.action in ("api", "metrics"):
            return _is_loopback(req.ip)
        return False


class LiveViewing:
    """Sessões ao vivo (WebRTC). A autorização do usuário é a sessão do painel, checada na camada HTTP."""

    def __init__(self, cameras: CameraRepository, signaling: LiveSignaling) -> None:
        self._cameras, self._signaling = cameras, signaling

    async def _path(self, camera_id: str, profile: StreamProfile) -> str:
        camera = await self._cameras.get(camera_id)
        if camera is None:
            raise NotFound("câmera não encontrada")
        if not camera.enabled:
            raise Conflict("a câmera está desativada")
        if camera.path_for(profile) is None:
            raise ValidationFailed("esta câmera não tem substream", {"profile": "perfil indisponível"})
        return path_name(camera.id, profile)

    async def start(self, camera_id: str, profile: StreamProfile, offer_sdp: str) -> LiveAnswer:
        return await self._signaling.offer(await self._path(camera_id, profile), offer_sdp)

    async def add_candidates(
        self, camera_id: str, profile: StreamProfile, session_id: str, fragment: str
    ) -> None:
        await self._signaling.patch(await self._path(camera_id, profile), _session(session_id), fragment)

    async def stop(self, camera_id: str, profile: StreamProfile, session_id: str) -> None:
        await self._signaling.close(await self._path(camera_id, profile), _session(session_id))


def _session(session_id: str) -> str:
    try:
        return str(uuid.UUID(session_id))
    except ValueError as exc:
        raise NotFound("sessão ao vivo não encontrada") from exc


@dataclass(frozen=True)
class CameraHealthView:
    health: CameraHealth
    since: datetime  # desde quando está nesse estado


class MonitorCameraHealth:
    """Amostra os sinais de mídia e mantém o estado de cada câmera; publica e grava só as transições."""

    def __init__(
        self,
        cameras: CameraRepository,
        media: MediaServer,
        relays: RelaySupervisor,
        status_log: CameraStatusLog,
        events: EventPublisher,
        clock: Clock,
    ) -> None:
        self._cameras, self._media, self._relays = cameras, media, relays
        self._log, self._events, self._clock = status_log, events, clock
        self._current: dict[str, CameraHealthView] = {}
        self._last_bytes: dict[str, int] = {}
        self._loaded = False
        self._media_down = False
        # Última consulta ao servidor de mídia (para a saúde do sistema, sem consultá-lo de novo).
        self.media_checked_at: datetime | None = None
        self.media_error: str | None = None
        # O MediaMTX guarda os paths só em memória: ao voltar, é preciso reconciliar já (não em até 15 s).
        self.on_media_recovered: Callable[[], None] | None = None

    def current(self) -> dict[str, CameraHealthView]:
        return dict(self._current)

    async def __call__(self) -> None:
        if not self._loaded:
            self._current = {
                cid: CameraHealthView(health, at) for cid, (at, health) in (await self._log.latest()).items()
            }
            self._loaded = True

        cameras = await self._cameras.list()
        relay_statuses = self._relays.statuses()
        try:
            states = await self._media.states()
            media_error: str | None = None
        except AppError as exc:
            states, media_error = {}, exc.message
        self.media_checked_at, self.media_error = self._clock.now(), media_error
        if self._media_down and media_error is None and self.on_media_recovered is not None:
            log.info("servidor de mídia voltou; reconciliando imediatamente")
            self.on_media_recovered()
        self._media_down = media_error is not None

        now = self._clock.now()
        seen: set[str] = set()
        for camera in cameras:
            seen.add(camera.id)
            if media_error is not None and camera.enabled:
                health = CameraHealth(
                    camera.id, CameraState.OFFLINE, "servidor de mídia (MediaMTX) indisponível", False
                )
            else:
                health = derive_health(
                    camera.id,
                    enabled=camera.enabled,
                    record_enabled=camera.recording_enabled,
                    main=self._signals(path_name(camera.id, StreamProfile.MAIN), states, relay_statuses),
                    sub=self._signals(path_name(camera.id, StreamProfile.SUB), states, relay_statuses),
                    has_sub=camera.sub_path is not None,
                )
            previous = self._current.get(camera.id)
            if health.same_condition(previous.health if previous else None):
                continue
            self._current[camera.id] = CameraHealthView(health, now)
            await self._log.record(camera.id, now, health)
            self._events.publish(Event("camera.status", _health_payload(health, now)))
            log.info("câmera %s: %s (%s)", camera.name, health.state.value, health.detail)

        for removed in self._current.keys() - seen:
            del self._current[removed]

    def _signals(
        self, name: str, states: dict[str, PathState], relays: dict[str, RelayStatus]
    ) -> ProfileSignals | None:
        state, relay = states.get(name), relays.get(name)
        if state is None and relay is None:
            return None
        received = state.bytes_received if state else 0
        previous = self._last_bytes.get(name)
        self._last_bytes[name] = received
        growing = previous is None or received > previous  # 1ª amostra: sem base de comparação
        return ProfileSignals(
            path_ready=bool(state and state.ready),
            bytes_growing=growing,
            relay_running=bool(relay and relay.running),
            relay_age_s=relay.running_for_s if relay else None,
            relay_failure=relay.failure if relay else None,
            recent_restarts=relay.recent_restarts if relay else 0,
        )


def _health_payload(health: CameraHealth, since: datetime) -> dict[str, object]:
    return {
        "camera_id": health.camera_id,
        "state": health.state.value,
        "detail": health.detail,
        "recording": health.recording,
        "since": since.isoformat(),
    }


class GetCameraStatusHistory:
    def __init__(self, status_log: CameraStatusLog) -> None:
        self._log = status_log

    async def __call__(self, camera_id: str, limit: int = 50) -> list[tuple[datetime, CameraHealth]]:
        return await self._log.history(camera_id, max(1, min(limit, 500)))
