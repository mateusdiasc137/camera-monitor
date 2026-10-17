from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, insert, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from nvr.persistence.tables import segments, storage_locations
from nvr.recordings.domain import Segment
from nvr.recordings.ports import StorageLocation
from nvr.shared.clock import Clock

# Segmentos têm no máximo alguns minutos (recordSegmentDuration); a margem cobre configurações maiores.
MAX_SEGMENT = timedelta(hours=2)


class SqlStorageLocations:
    def __init__(self, engine: AsyncEngine, clock: Clock) -> None:
        self._engine, self._clock = engine, clock

    async def active(self) -> StorageLocation | None:
        query = select(storage_locations).where(storage_locations.c.active.is_(True))
        async with self._engine.connect() as conn:
            row = (await conn.execute(query)).mappings().first()
        return None if row is None else StorageLocation(row["id"], Path(row["path"]), True)

    async def add(self, location: StorageLocation) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(storage_locations).values(
                    id=location.id,
                    path=str(location.path),
                    active=location.active,
                    created_at=self._clock.now(),
                )
            )


class SqlSegmentIndex:
    def __init__(self, engine: AsyncEngine, clock: Clock) -> None:
        self._engine, self._clock = engine, clock

    async def known(self, location_id: str) -> dict[str, int]:
        query = select(segments.c.rel_path, segments.c.size_bytes).where(
            segments.c.location_id == location_id
        )
        async with self._engine.connect() as conn:
            return {row.rel_path: row.size_bytes for row in await conn.execute(query)}

    async def upsert(self, items: list[Segment]) -> None:
        now = self._clock.now()
        async with self._engine.begin() as conn:
            for s in items:
                stmt = sqlite_insert(segments).values(
                    camera_id=s.camera_id,
                    location_id=s.location_id,
                    rel_path=s.rel_path,
                    start_at=s.start_at,
                    duration_s=s.duration_s,
                    size_bytes=s.size_bytes,
                    indexed_at=now,
                )
                await conn.execute(
                    stmt.on_conflict_do_update(
                        index_elements=[segments.c.location_id, segments.c.rel_path],
                        set_={
                            "start_at": stmt.excluded.start_at,
                            "duration_s": stmt.excluded.duration_s,
                            "size_bytes": stmt.excluded.size_bytes,
                            "indexed_at": stmt.excluded.indexed_at,
                        },
                    )
                )

    async def remove(self, location_id: str, rel_paths: list[str]) -> None:
        async with self._engine.begin() as conn:
            for chunk in (rel_paths[i : i + 500] for i in range(0, len(rel_paths), 500)):
                await conn.execute(
                    delete(segments).where(
                        segments.c.location_id == location_id, segments.c.rel_path.in_(chunk)
                    )
                )

    async def in_range(self, camera_id: str, start: datetime, end: datetime) -> list[Segment]:
        query = (
            select(segments)
            .where(
                segments.c.camera_id == camera_id,
                segments.c.start_at < end,
                segments.c.start_at >= start - MAX_SEGMENT,
            )
            .order_by(segments.c.start_at)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        found = [
            Segment(
                r["camera_id"],
                r["location_id"],
                r["rel_path"],
                r["start_at"],
                r["duration_s"],
                r["size_bytes"],
            )
            for r in rows
        ]
        return [s for s in found if s.end_at > start]
