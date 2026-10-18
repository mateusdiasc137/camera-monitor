from __future__ import annotations

from nvr.recordings.domain import StorageState
from nvr.recordings.storage import EnforceStoragePolicy, gb_text
from nvr.system.domain import ComponentHealth, HealthState


class StorageProbe:
    """Saúde do armazenamento a partir da última aplicação da política (não varre o disco de novo)."""

    def __init__(self, enforcer: EnforceStoragePolicy) -> None:
        self._enforcer = enforcer

    async def check(self) -> ComponentHealth:
        last = self._enforcer.last
        if last is None:
            return ComponentHealth("storage", HealthState.OK, "aguardando a primeira verificação")
        free = f"{gb_text(last.disk.free_bytes)} GB livres" if last.disk else ""
        if last.recording_paused:
            return ComponentHealth(
                "storage", HealthState.DOWN, f"gravação pausada: armazenamento cheio; {free}"
            )
        if last.state is StorageState.FULL:
            return ComponentHealth(
                "storage",
                HealthState.DEGRADED,
                f"limite de {last.reason} atingido e nada mais a apagar; {free}",
            )
        return ComponentHealth(
            "storage", HealthState.OK, f"{gb_text(last.used_bytes)} GB em gravações; {free}"
        )
