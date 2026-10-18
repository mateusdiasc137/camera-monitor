from __future__ import annotations

from nvr.streaming.application import MonitorCameraHealth
from nvr.streaming.ports import RelaySupervisor
from nvr.system.domain import ComponentHealth, HealthState


class MediaServerProbe:
    """Servidor de mídia (MediaMTX) pela última amostra do monitor de câmeras, sem nova consulta."""

    NAME = "media_server"

    def __init__(self, monitor: MonitorCameraHealth, relays: RelaySupervisor, enabled: bool) -> None:
        self._monitor, self._relays, self._enabled = monitor, relays, enabled

    async def check(self) -> ComponentHealth:
        if not self._enabled:
            return ComponentHealth(
                self.NAME, HealthState.OK, "desativado nesta instalação (NVR_MEDIA_ENABLED)"
            )
        if self._monitor.media_checked_at is None:
            return ComponentHealth(self.NAME, HealthState.OK, "aguardando a primeira verificação")
        if self._monitor.media_error is not None:
            return ComponentHealth(self.NAME, HealthState.DOWN, f"sem resposta: {self._monitor.media_error}")
        relays = len(self._relays.statuses())
        return ComponentHealth(self.NAME, HealthState.OK, f"respondendo; {relays} relay(s) de câmera ativos")
