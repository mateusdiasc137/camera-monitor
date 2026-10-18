from __future__ import annotations

import asyncio
from collections.abc import Sequence

from nvr.shared.clock import Clock
from nvr.system.domain import SystemHealth, cameras_component, recording_component
from nvr.system.ports import CameraSummaries, HealthProbe


class GetSystemHealth:
    """Componentes (banco, servidor de mídia, armazenamento...) e, se houver, câmeras e gravação."""

    def __init__(
        self,
        version: str,
        probes: Sequence[HealthProbe],
        cameras: CameraSummaries | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._version = version
        self._probes = tuple(probes)
        self._cameras, self._clock = cameras, clock

    async def __call__(self) -> SystemHealth:
        components = list(await asyncio.gather(*(probe.check() for probe in self._probes)))
        if self._cameras is None or self._clock is None:
            return SystemHealth(version=self._version, components=tuple(components))
        now = self._clock.now()
        cameras = await self._cameras()
        components += [cameras_component(cameras), recording_component(cameras, now)]
        return SystemHealth(self._version, tuple(components), tuple(cameras), now)
