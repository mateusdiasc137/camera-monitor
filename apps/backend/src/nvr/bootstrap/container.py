"""Composition root: o único lugar que instancia adaptadores e liga casos de uso (ADR-001)."""

from __future__ import annotations

from dataclasses import dataclass

from nvr import __version__
from nvr.bootstrap.settings import Settings
from nvr.system.application import GetSystemHealth


@dataclass(frozen=True)
class Container:
    settings: Settings
    get_system_health: GetSystemHealth


def build_container(settings: Settings) -> Container:
    return Container(
        settings=settings,
        get_system_health=GetSystemHealth(__version__, probes=[]),
    )
