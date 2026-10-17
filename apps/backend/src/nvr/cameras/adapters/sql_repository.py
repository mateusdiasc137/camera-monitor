from __future__ import annotations

from sqlalchemy import RowMapping, delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from nvr.cameras.domain import Camera, CameraDriver, IngestMode
from nvr.persistence.tables import cameras

_FIELDS = (
    "name",
    "driver",
    "host",
    "rtsp_port",
    "main_path",
    "sub_path",
    "secret_ref",
    "ingest",
    "enabled",
    "recording_enabled",
    "created_at",
    "updated_at",
)


def _camera(row: RowMapping) -> Camera:
    return Camera(
        id=row["id"],
        name=row["name"],
        driver=CameraDriver(row["driver"]),
        host=row["host"],
        rtsp_port=row["rtsp_port"],
        main_path=row["main_path"],
        sub_path=row["sub_path"],
        secret_ref=row["secret_ref"],
        ingest=IngestMode(row["ingest"]),
        enabled=row["enabled"],
        recording_enabled=row["recording_enabled"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _values(camera: Camera) -> dict[str, object]:
    values = {field: getattr(camera, field) for field in _FIELDS}
    values["driver"] = camera.driver.value
    values["ingest"] = camera.ingest.value
    return values


class SqlCameraRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def list(self) -> list[Camera]:
        async with self._engine.connect() as conn:
            rows = (await conn.execute(select(cameras))).mappings().all()
        return [_camera(r) for r in rows]

    async def get(self, camera_id: str) -> Camera | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(select(cameras).where(cameras.c.id == camera_id))).mappings().first()
        return None if row is None else _camera(row)

    async def get_by_name(self, name: str) -> Camera | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(select(cameras).where(cameras.c.name == name))).mappings().first()
        return None if row is None else _camera(row)

    async def add(self, camera: Camera) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(insert(cameras).values(id=camera.id, **_values(camera)))

    async def update(self, camera: Camera) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(update(cameras).where(cameras.c.id == camera.id).values(**_values(camera)))

    async def delete(self, camera_id: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(delete(cameras).where(cameras.c.id == camera_id))
