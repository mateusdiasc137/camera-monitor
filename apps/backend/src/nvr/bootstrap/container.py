"""Composition root: o único lugar que instancia adaptadores e liga casos de uso (ADR-001)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from nvr import __version__
from nvr.bootstrap.settings import Settings
from nvr.persistence.database import create_engine
from nvr.system.adapters.database_probe import DatabaseProbe
from nvr.system.application import GetSystemHealth


@dataclass(frozen=True)
class Container:
    settings: Settings
    engine: AsyncEngine
    get_system_health: GetSystemHealth

    async def aclose(self) -> None:
        await self.engine.dispose()


def build_container(settings: Settings) -> Container:
    engine = create_engine(settings.data_dir)
    return Container(
        settings=settings,
        engine=engine,
        get_system_health=GetSystemHealth(__version__, probes=[DatabaseProbe(engine)]),
    )
