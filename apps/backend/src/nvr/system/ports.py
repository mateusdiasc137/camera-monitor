from __future__ import annotations

from typing import Protocol

from nvr.system.domain import CameraSummary, ComponentHealth


class HealthProbe(Protocol):
    """Verifica um componente (banco, MediaMTX, disco...). Não deve lançar exceções."""

    async def check(self) -> ComponentHealth: ...


class CameraSummaries(Protocol):
    """Estado atual, gravação esperada e último trecho gravado de cada câmera."""

    async def __call__(self) -> list[CameraSummary]: ...
