from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from nvr.persistence.tables import segments, storage_locations, storage_migrations, storage_policy
from nvr.recordings.domain import (
    MigrationState,
    ReplacementPolicy,
    Segment,
    StorageMigration,
    StoragePolicy,
)
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

    async def _one(self, condition: Any) -> StorageLocation | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(select(storage_locations).where(condition))).mappings().first()
        return None if row is None else StorageLocation(row["id"], Path(row["path"]), row["active"])

    async def get(self, location_id: str) -> StorageLocation | None:
        return await self._one(storage_locations.c.id == location_id)

    async def by_path(self, path: Path) -> StorageLocation | None:
        return await self._one(storage_locations.c.path == str(path))

    async def activate(self, location_id: str) -> None:
        async with self._engine.begin() as conn:  # um único ativo (índice parcial): desliga antes
            await conn.execute(
                update(storage_locations).where(storage_locations.c.active.is_(True)).values(active=False)
            )
            await conn.execute(
                update(storage_locations).where(storage_locations.c.id == location_id).values(active=True)
            )

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

    async def remove_location(self, location_id: str) -> int:
        async with self._engine.begin() as conn:
            result = await conn.execute(delete(segments).where(segments.c.location_id == location_id))
        return result.rowcount or 0

    async def relocate(self, from_location_id: str, to_location_id: str, rel_path: str) -> None:
        old = (segments.c.location_id == from_location_id) & (segments.c.rel_path == rel_path)
        new = (segments.c.location_id == to_location_id) & (segments.c.rel_path == rel_path)
        async with self._engine.begin() as conn:
            if (await conn.execute(select(segments.c.id).where(new))).first() is not None:
                await conn.execute(delete(segments).where(old))  # a varredura do novo local chegou antes
            else:
                await conn.execute(update(segments).where(old).values(location_id=to_location_id))

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


class SqlStoragePolicies:
    def __init__(self, engine: AsyncEngine, clock: Clock) -> None:
        self._engine, self._clock = engine, clock

    async def get(self) -> StoragePolicy:
        async with self._engine.connect() as conn:
            row = (
                (await conn.execute(select(storage_policy).where(storage_policy.c.id == 1))).mappings().one()
            )
        return StoragePolicy(
            quota_bytes=row["quota_bytes"],
            replacement=ReplacementPolicy(row["replacement"]),
            high_watermark=row["high_watermark"],
            low_watermark=row["low_watermark"],
            min_free_bytes=row["min_free_bytes"],
        )

    async def save(self, policy: StoragePolicy) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                update(storage_policy)
                .where(storage_policy.c.id == 1)
                .values(
                    quota_bytes=policy.quota_bytes,
                    replacement=policy.replacement.value,
                    high_watermark=policy.high_watermark,
                    low_watermark=policy.low_watermark,
                    min_free_bytes=policy.min_free_bytes,
                    updated_at=self._clock.now(),
                )
            )


class SqlStorageMigrations:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def latest(self) -> StorageMigration | None:
        query = select(storage_migrations).order_by(storage_migrations.c.started_at.desc()).limit(1)
        async with self._engine.connect() as conn:
            row = (await conn.execute(query)).mappings().first()
        if row is None:
            return None
        return StorageMigration(
            id=row["id"],
            from_location_id=row["from_location_id"],
            to_location_id=row["to_location_id"],
            state=MigrationState(row["state"]),
            total_files=row["total_files"],
            total_bytes=row["total_bytes"],
            moved_files=row["moved_files"],
            moved_bytes=row["moved_bytes"],
            error=row["error"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )

    @staticmethod
    def _values(m: StorageMigration) -> dict[str, Any]:
        return {
            "from_location_id": m.from_location_id,
            "to_location_id": m.to_location_id,
            "state": m.state.value,
            "total_files": m.total_files,
            "total_bytes": m.total_bytes,
            "moved_files": m.moved_files,
            "moved_bytes": m.moved_bytes,
            "error": m.error[:1024],
            "started_at": m.started_at,
            "finished_at": m.finished_at,
        }

    async def add(self, migration: StorageMigration) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(insert(storage_migrations).values(id=migration.id, **self._values(migration)))

    async def save(self, migration: StorageMigration) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                update(storage_migrations)
                .where(storage_migrations.c.id == migration.id)
                .values(**self._values(migration))
            )
