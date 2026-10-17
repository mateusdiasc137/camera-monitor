from __future__ import annotations

from datetime import datetime

from sqlalchemy import RowMapping, func, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from nvr.persistence.tables import camera_status_events as events
from nvr.streaming.domain import CameraHealth, CameraState


def _row(row: RowMapping) -> tuple[datetime, CameraHealth]:
    health = CameraHealth(row["camera_id"], CameraState(row["state"]), row["detail"], row["recording"])
    return row["at"], health


class SqlCameraStatusLog:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def record(self, camera_id: str, at: datetime, health: CameraHealth) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(events).values(
                    camera_id=camera_id,
                    at=at,
                    state=health.state.value,
                    detail=health.detail[:255],
                    recording=health.recording,
                )
            )

    async def latest(self) -> dict[str, tuple[datetime, CameraHealth]]:
        newest_ids = select(func.max(events.c.id)).group_by(events.c.camera_id)
        query = select(events).where(events.c.id.in_(newest_ids))
        async with self._engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return {row["camera_id"]: _row(row) for row in rows}

    async def history(self, camera_id: str, limit: int) -> list[tuple[datetime, CameraHealth]]:
        query = (
            select(events).where(events.c.camera_id == camera_id).order_by(events.c.id.desc()).limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [_row(row) for row in rows]
