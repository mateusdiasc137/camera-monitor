from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert, inspect, select
from sqlalchemy.exc import IntegrityError, StatementError

from nvr.persistence import tables
from nvr.persistence.database import create_engine as create_async_engine
from nvr.persistence.database import downgrade, migrate, sqlite_path
from nvr.system.adapters.database_probe import DatabaseProbe
from nvr.system.domain import HealthState

EXPECTED_TABLES = {"users", "cameras", "storage_locations", "storage_policy", "segments", "audit_log"}


def sync_engine(data_dir: Path):  # type: ignore[no-untyped-def]
    return create_engine(f"sqlite:///{sqlite_path(data_dir).as_posix()}")


def test_migration_up_and_down(tmp_path: Path) -> None:
    migrate(tmp_path)
    engine = sync_engine(tmp_path)
    assert set(inspect(engine).get_table_names()) >= EXPECTED_TABLES
    engine.dispose()

    downgrade(tmp_path, "base")
    engine = sync_engine(tmp_path)
    assert EXPECTED_TABLES.isdisjoint(inspect(engine).get_table_names())
    engine.dispose()


def test_migration_is_idempotent(tmp_path: Path) -> None:
    migrate(tmp_path)
    migrate(tmp_path)


def test_default_storage_policy(tmp_path: Path) -> None:
    migrate(tmp_path)
    engine = sync_engine(tmp_path)
    with engine.connect() as conn:
        row = conn.execute(select(tables.storage_policy)).one()
    engine.dispose()
    assert row.replacement == "overwrite_oldest"
    assert row.quota_bytes is None
    assert (row.high_watermark, row.low_watermark) == (0.95, 0.90)
    assert row.min_free_bytes == 2 * 1024**3


def _camera(**overrides: object) -> dict[str, object]:
    now = datetime.now(UTC)
    values: dict[str, object] = {
        "id": "cam-1",
        "name": "Quintal",
        "driver": "xiongmai",
        "host": "192.0.2.10",
        "rtsp_port": 554,
        "main_path": "/user={user}&password={password}&channel=1&stream=0.sdp?",
        "sub_path": None,
        "secret_ref": "camera:cam-1",
        "ingest": "relay",
        "enabled": True,
        "recording_enabled": True,
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return values


def test_datetimes_round_trip_in_utc(tmp_path: Path) -> None:
    migrate(tmp_path)
    engine = sync_engine(tmp_path)
    brt = timezone(timedelta(hours=-3))
    local = datetime(2026, 10, 17, 16, 0, tzinfo=brt)
    with engine.begin() as conn:
        conn.execute(insert(tables.cameras).values(**_camera(created_at=local, updated_at=local)))
        stored = conn.execute(select(tables.cameras.c.created_at)).scalar_one()
    engine.dispose()
    assert stored == local
    assert stored.utcoffset() == timedelta(0)


def test_naive_datetime_is_rejected(tmp_path: Path) -> None:
    migrate(tmp_path)
    engine = sync_engine(tmp_path)
    statement = insert(tables.cameras).values(**_camera(created_at=datetime(2026, 10, 17, 16, 0)))
    with engine.begin() as conn, pytest.raises(StatementError, match="sem fuso"):
        conn.execute(statement)
    engine.dispose()


def test_constraints_are_enforced(tmp_path: Path) -> None:
    migrate(tmp_path)
    engine = sync_engine(tmp_path)
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert(tables.cameras).values(**_camera(ingest="magic")))
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert(tables.cameras).values(**_camera(rtsp_port=70000)))
    engine.dispose()


def test_only_one_active_storage_location(tmp_path: Path) -> None:
    migrate(tmp_path)
    engine = sync_engine(tmp_path)
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(insert(tables.storage_locations).values(id="a", path="/a", active=True, created_at=now))
        conn.execute(insert(tables.storage_locations).values(id="b", path="/b", active=False, created_at=now))
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert(tables.storage_locations).values(id="c", path="/c", active=True, created_at=now))
    engine.dispose()


def test_segments_require_existing_camera(tmp_path: Path) -> None:
    """Chaves estrangeiras precisam estar ligadas (PRAGMA foreign_keys)."""
    migrate(tmp_path)
    engine = sync_engine(tmp_path)
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(insert(tables.storage_locations).values(id="a", path="/a", active=True, created_at=now))
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            insert(tables.segments).values(
                camera_id="inexistente",
                location_id="a",
                rel_path="x.mp4",
                start_at=now,
                duration_s=1.0,
                size_bytes=1,
                indexed_at=now,
            )
        )
    engine.dispose()


async def test_database_probe(tmp_path: Path) -> None:
    migrate(tmp_path)
    engine = create_async_engine(tmp_path)
    try:
        health = await DatabaseProbe(engine).check()
    finally:
        await engine.dispose()
    assert health.state is HealthState.OK
