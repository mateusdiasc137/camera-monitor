from __future__ import annotations

from typing import Any

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from nvr.persistence.tables import audit_log
from nvr.shared.clock import Clock


class SqlAuditLog:
    def __init__(self, engine: AsyncEngine, clock: Clock) -> None:
        self._engine, self._clock = engine, clock

    async def record(
        self, actor: str, action: str, target: str | None = None, detail: dict[str, Any] | None = None
    ) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(audit_log).values(
                    at=self._clock.now(), actor=actor, action=action, target=target, detail=detail
                )
            )
