from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from nvr.system.domain import ComponentHealth, HealthState


class DatabaseProbe:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def check(self) -> ComponentHealth:
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            return ComponentHealth("database", HealthState.DOWN, type(exc).__name__)
        return ComponentHealth("database", HealthState.OK)
