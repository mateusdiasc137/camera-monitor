"""Saúde agregada do sistema."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class HealthState(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass(frozen=True)
class ComponentHealth:
    name: str
    state: HealthState
    detail: str = ""


@dataclass(frozen=True)
class SystemHealth:
    version: str
    components: tuple[ComponentHealth, ...]

    @property
    def state(self) -> HealthState:
        """O pior estado entre os componentes; sem componentes, o sistema está ok."""
        states = {c.state for c in self.components}
        if HealthState.DOWN in states:
            return HealthState.DOWN
        if HealthState.DEGRADED in states:
            return HealthState.DEGRADED
        return HealthState.OK
