"""Índice de gravações com arquivos sintéticos, SQLite e sistema de arquivos reais."""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nvr.bootstrap.cli import build_app
from nvr.bootstrap.container import Container, build_container
from nvr.bootstrap.settings import Settings
from nvr.identity.domain import Role
from nvr.persistence.database import migrate
from nvr.streaming.domain import CameraHealth, CameraState
from tests.fmp4_builder import Fmp4Spec, build

PASSWORD = "senha-forte-123"
ORIGIN = "http://testserver"
T0 = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def write_segment(folder: Path, start: datetime, seconds: float, *, finalized: bool = True) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    name = start.astimezone().strftime("%Y-%m-%d_%H-%M-%S-%f") + start.astimezone().strftime("%z") + ".mp4"
    spec = Fmp4Spec(start=start, finalized_ms=int(seconds * 1000) if finalized else None, parts=2)
    path = folder / name
    path.write_bytes(build(spec))
    return path


@pytest.fixture
def env(tmp_path: Path) -> Iterator[dict]:  # type: ignore[type-arg]
    data = tmp_path / "data"
    migrate(data)
    container = build_container(Settings(data_dir=data, cookie_secure=False))
    asyncio.run(container.create_user("admin", PASSWORD, Role.ADMIN))
    with TestClient(build_app(container), base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
        client.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
        camera = client.post("/api/v1/cameras", json={
            "name": "Quintal", "host": "192.0.2.10", "main_path": "/a", "sub_path": "/b",
            "username": "u", "password": "p",
        }).json()  # fmt: skip
        root = container.settings.resolved_recordings_dir()
        yield {"client": client, "container": container, "camera": camera, "root": root}


def run(container: Container):  # type: ignore[no-untyped-def]
    return asyncio.run(container.index_segments())


def test_indexes_new_grown_and_vanished_files(env: dict) -> None:  # type: ignore[type-arg]
    root, cam, container = env["root"], env["camera"], env["container"]
    main = root / f"cam-{cam['id']}-main"
    first = write_segment(main, T0, 300)
    write_segment(main, T0 + timedelta(minutes=5), 300)
    current = write_segment(main, T0 + timedelta(minutes=10), 0, finalized=False)  # ainda gravando

    report = run(container)
    assert (report.added, report.updated, report.removed) == (3, 0, 0)
    assert run(container).added == 0  # incremental: nada mudou

    current.write_bytes(build(Fmp4Spec(start=T0 + timedelta(minutes=10), finalized_ms=None, parts=5)))
    assert run(container).updated == 1  # o segmento em gravação cresceu

    first.unlink()
    assert run(container).removed == 1


def test_skips_foreign_files(env: dict) -> None:  # type: ignore[type-arg]
    root, cam, container = env["root"], env["camera"], env["container"]
    write_segment(root / f"cam-{cam['id']}-sub", T0, 60)  # substream não é gravado
    write_segment(root / "cam-00000000-0000-0000-0000-000000000000-main", T0, 60)  # câmera removida
    write_segment(root / "outra-pasta", T0, 60)
    broken = root / f"cam-{cam['id']}-main" / "2026-10-17_12-00-00-000000Z.mp4"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_bytes(b"isto nao e um mp4")
    report = run(container)
    assert report.added == 0
    assert dict(report.skipped) == {
        "perfil não gravado": 1, "câmera removida": 1, "pasta desconhecida": 1, "ilegível": 1,
    }  # fmt: skip


def test_recordings_api_returns_intervals_and_gap_reasons(env: dict) -> None:  # type: ignore[type-arg]
    root, cam, container, client = env["root"], env["camera"], env["container"], env["client"]
    main = root / f"cam-{cam['id']}-main"
    write_segment(main, T0, 300)
    write_segment(main, T0 + timedelta(minutes=5), 300)
    write_segment(main, T0 + timedelta(minutes=20), 300)
    run(container)

    async def seed() -> None:
        log = container.monitor_camera_health._log
        await log.record(
            cam["id"], T0 - timedelta(hours=1), CameraHealth(cam["id"], CameraState.ONLINE, "x", True)
        )
        await log.record(
            cam["id"], T0 + timedelta(minutes=10), CameraHealth(cam["id"], CameraState.OFFLINE, "x", False)
        )
        await log.record(
            cam["id"], T0 + timedelta(minutes=20), CameraHealth(cam["id"], CameraState.ONLINE, "x", True)
        )

    asyncio.run(seed())
    params = {
        "camera_id": cam["id"],
        "start": T0.isoformat(),
        "end": (T0 + timedelta(minutes=30)).isoformat(),
    }
    body = client.get("/api/v1/recordings", params=params).json()
    assert [(i["start"], i["end"]) for i in body["intervals"]] == [
        ("2026-10-17T12:00:00Z", "2026-10-17T12:10:00Z"),
        ("2026-10-17T12:20:00Z", "2026-10-17T12:25:00Z"),
    ]
    assert [(g["start"], g["end"], g["reason"]) for g in body["gaps"]] == [
        ("2026-10-17T12:10:00Z", "2026-10-17T12:20:00Z", "camera_unavailable"),
        ("2026-10-17T12:25:00Z", "2026-10-17T12:30:00Z", "unknown"),
    ]
    assert body["segments"] == 3
    assert body["recorded_s"] == pytest.approx(900)


def test_recordings_api_validation(env: dict) -> None:  # type: ignore[type-arg]
    client, cam = env["client"], env["camera"]
    naive = {"camera_id": cam["id"], "start": "2026-10-17T12:00:00", "end": "2026-10-17T13:00:00"}
    assert client.get("/api/v1/recordings", params=naive).status_code == 422
    too_long = {"camera_id": cam["id"], "start": "2026-09-01T00:00:00Z", "end": "2026-10-17T00:00:00Z"}
    assert client.get("/api/v1/recordings", params=too_long).status_code == 422
    reversed_ = {"camera_id": cam["id"], "start": "2026-10-17T13:00:00Z", "end": "2026-10-17T12:00:00Z"}
    assert client.get("/api/v1/recordings", params=reversed_).status_code == 422
    assert client.get("/api/v1/recordings", params={"camera_id": "nao-existe"}).status_code == 404
    client.cookies.clear()
    assert client.get("/api/v1/recordings", params={"camera_id": cam["id"]}).status_code == 401
