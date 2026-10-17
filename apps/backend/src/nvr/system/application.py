from __future__ import annotations

import asyncio
from collections.abc import Sequence

from nvr.system.domain import SystemHealth
from nvr.system.ports import HealthProbe


class GetSystemHealth:
    def __init__(self, version: str, probes: Sequence[HealthProbe]) -> None:
        self._version = version
        self._probes = tuple(probes)

    async def __call__(self) -> SystemHealth:
        components = await asyncio.gather(*(probe.check() for probe in self._probes))
        return SystemHealth(version=self._version, components=tuple(components))
