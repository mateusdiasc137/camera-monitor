"""Cota e substituição com disco e SQLite reais (o medidor de disco é falso para simular cenários)."""

import asyncio
import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from nvr.bootstrap.cli import build_app
from nvr.bootstrap.container import Container, build_container
from nvr.bootstrap.settings import Settings
from nvr.identity.domain import Role
from nvr.persistence.database import migrate
from nvr.persistence.tables import audit_log
from nvr.recordings.adapters.fs_store import FileSegmentStore
from nvr.recordings.domain import GIB, DiskUsage, ReplacementPolicy
from tests.fmp4_builder import Fmp4Spec, build

PASSWORD = "senha-forte-123"
ORIGIN = "http://testserver"
T0 = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
MB = 1024 * 1024


class FakeDisk:
    def __init__(self, free_gb: float = 500, total_gb: float = 1000) -> None:
        self.free, self.total = int(free_gb * GIB), int(total_gb * GIB)

    def usage(self, path: Path) -> DiskUsage:
        return DiskUsage(self.total, self.free)


def write(folder: Path, start: datetime, size_mb: int, *, old: bool = True) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (start.strftime("%Y-%m-%d_%H-%M-%S-%f") + "Z.mp4")
    data = build(Fmp4Spec(start=start, finalized_ms=300_000, parts=1))
    path.write_bytes(data + b"\x00" * (size_mb * MB - len(data)))
    if old:  # arquivos antigos: não podem parecer "em gravação"
        past = time.time() - 3600
        os.utime(path, (past, past))
    return path


@pytest.fixture
def env(tmp_path: Path) -> Iterator[dict]:  # type: ignore[type-arg]
    data = tmp_path / "data"
    migrate(data)
    container = build_container(Settings(data_dir=data, cookie_secure=False))
    disk = FakeDisk()
    for component in (
        container.enforce_storage_policy,
        container.get_storage_overview,
        container.update_storage_policy,
    ):
        component._disk = disk  # type: ignore[attr-defined]
    asyncio.run(container.create_user("admin", PASSWORD, Role.ADMIN))
    asyncio.run(container.create_user("visitante", PASSWORD, Role.VIEWER))
    with TestClient(build_app(container), base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
        client.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
        camera = client.post("/api/v1/cameras", json={
            "name": "Quintal", "host": "192.0.2.10", "main_path": "/a", "username": "u", "password": "p",
        }).json()  # fmt: skip
        root = container.settings.resolved_recordings_dir()
        yield {"client": client, "container": container, "camera": camera, "root": root, "disk": disk}


async def set_quota(container: Container, quota_mb: int, replacement: ReplacementPolicy) -> None:
    policy = await container.update_storage_policy._policies.get()
    from dataclasses import replace

    await container.update_storage_policy._policies.save(
        replace(policy, quota_bytes=quota_mb * MB, replacement=replacement)
    )


def test_overwrite_oldest_deletes_files_index_rows_and_audits(env: dict) -> None:  # type: ignore[type-arg]
    container, root, cam = env["container"], env["root"], env["camera"]
    main = root / f"cam-{cam['id']}-main"
    paths = [write(main, T0 + timedelta(minutes=5 * i), 10) for i in range(10)]  # 100 MB
    orphan = write(
        root / "cam-00000000-0000-0000-0000-000000000000-main", T0 - timedelta(days=1), 10
    )  # câmera removida
    asyncio.run(container.index_segments())
    asyncio.run(set_quota(container, 100, ReplacementPolicy.OVERWRITE_OLDEST))  # 110 MB > 95 MB

    result = asyncio.run(container.enforce_storage_policy())
    assert result is not None
    assert result.deleted_files == 2  # 110 → 90 MB: a órfã (mais antiga de todas) e o 1º da câmera
    assert not orphan.exists()
    assert not paths[0].exists()
    assert all(p.exists() for p in paths[1:])

    async def check_db() -> tuple[int, list[str]]:
        known = await container.segment_index.known((await container.ensure_storage_location()).id)
        async with container.engine.connect() as conn:
            actions = [r.action for r in await conn.execute(select(audit_log.c.action))]
        return len(known), actions

    known, actions = asyncio.run(check_db())
    assert known == 9
    assert "storage.cleanup" in actions
    assert asyncio.run(container.enforce_storage_policy()).deleted_files == 0  # estável abaixo do limite
    overview = asyncio.run(container.get_storage_overview())
    assert overview is not None
    assert overview.last_cleanup is not None
    assert overview.last_cleanup.deleted_files == 2  # a verificação seguinte, sem exclusões, não a esconde


def test_stop_when_full_pauses_recording_and_resumes(env: dict) -> None:  # type: ignore[type-arg]
    container, root, cam = env["container"], env["root"], env["camera"]
    main = root / f"cam-{cam['id']}-main"
    for i in range(10):
        write(main, T0 + timedelta(minutes=5 * i), 10)
    changes: list[bool] = []
    container.enforce_storage_policy.on_gate_change = lambda: changes.append(container.recording_gate.allowed)
    asyncio.run(set_quota(container, 100, ReplacementPolicy.STOP_WHEN_FULL))

    result = asyncio.run(container.enforce_storage_policy())
    assert result is not None
    assert result.recording_paused
    assert container.recording_gate() is False
    assert len(list(main.glob("*.mp4"))) == 10  # nada apagado
    health = asyncio.run(container.get_system_health())
    assert any(c.name == "storage" and c.state.value == "down" for c in health.components)

    asyncio.run(set_quota(container, 200, ReplacementPolicy.STOP_WHEN_FULL))  # 100 MB ≤ 90% de 200
    asyncio.run(container.enforce_storage_policy())
    assert container.recording_gate() is True
    assert changes == [False, True]


def test_storage_api(env: dict) -> None:  # type: ignore[type-arg]
    client, root, cam, disk = env["client"], env["root"], env["camera"], env["disk"]
    main = root / f"cam-{cam['id']}-main"
    for i in range(3):
        write(main, T0 + timedelta(minutes=5 * i), 10)

    body = client.get("/api/v1/storage").json()
    assert body["used_bytes"] >= 30 * MB
    assert body["policy"] == {"quota_gb": None, "replacement": "overwrite_oldest", "high_watermark": 0.95,
                              "low_watermark": 0.9, "min_free_gb": 2.0}  # fmt: skip
    assert body["cameras"][0]["camera_name"] == "Quintal"

    assert client.put("/api/v1/storage/policy", json={"quota_gb": 0.5}).status_code == 422  # mínimo 1 GB
    disk.free = 10 * GIB
    too_big = client.put("/api/v1/storage/policy", json={"quota_gb": 50})
    assert too_big.status_code == 422
    assert "máximo possível" in too_big.json()["fields"]["quota_gb"]
    ok = client.put("/api/v1/storage/policy", json={"quota_gb": 5, "replacement": "stop_when_full"})
    assert ok.status_code == 200
    assert ok.json()["quota_gb"] == 5.0

    client.cookies.clear()
    client.post("/api/v1/auth/login", json={"username": "visitante", "password": PASSWORD})
    assert client.get("/api/v1/storage").status_code == 200
    assert client.put("/api/v1/storage/policy", json={"quota_gb": 5}).status_code == 403


def test_shrinking_quota_requires_confirmation(env: dict) -> None:  # type: ignore[type-arg]
    container, client = env["container"], env["client"]

    async def fake_used() -> None:  # simula 3 GB já gravados sem criar 3 GB de arquivos
        container.update_storage_policy._store = BigStore(3 * GIB)  # type: ignore[attr-defined]

    class BigStore(FileSegmentStore):
        def __init__(self, size: int) -> None:
            self.size = size

        def scan(self, root: Path):  # type: ignore[no-untyped-def]
            from nvr.recordings.ports import SegmentFile

            return [
                SegmentFile(f"cam-{env['camera']['id']}-main/2026-10-10_12-00-00-000000Z.mp4", self.size, 0.0)
            ]

    asyncio.run(fake_used())
    response = client.put("/api/v1/storage/policy", json={"quota_gb": 2})
    assert response.status_code == 409
    assert response.json()["error"] == "confirmation_required"
    assert response.json()["details"]["would_delete_bytes"] == 3 * GIB - int(2 * GIB * 0.9)
    assert client.put("/api/v1/storage/policy", json={"quota_gb": 2, "confirm": True}).status_code == 200


def test_delete_refuses_paths_outside_root(tmp_path: Path) -> None:
    store = FileSegmentStore()
    for bad in ("../fora.mp4", "a/../../fora.mp4", "a/b/c.mp4", "a/nao-video.txt", "/abs/x.mp4"):
        with pytest.raises(ValueError, match="segmento"):
            store.delete(tmp_path, bad)
