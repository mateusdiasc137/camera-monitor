from __future__ import annotations

from typing import Protocol

from nvr.system.domain import ComponentHealth


class HealthProbe(Protocol):
    """Verifica um componente (banco, MediaMTX, disco...). Não deve lançar exceções."""

    async def check(self) -> ComponentHealth: ...
