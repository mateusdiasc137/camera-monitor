"""Estado das câmeras: snapshot, histórico e fluxo SSE."""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nvr.bootstrap.cli import build_app
from nvr.bootstrap.container import build_container
from nvr.bootstrap.settings import Settings
from nvr.http.routers.status import sse_stream
from nvr.identity.domain import Role
from nvr.persistence.database import migrate
from nvr.shared.events import Event, EventBus
from nvr.streaming.adapters.sql_status_log import SqlCameraStatusLog
from nvr.streaming.domain import CameraHealth, CameraState

PASSWORD = "senha-forte-123"
ORIGIN = "http://testserver"


@pytest.fixture
def env(tmp_path: Path) -> Iterator[dict]:  # type: ignore[type-arg]
    migrate(tmp_path)
    container = build_container(Settings(data_dir=tmp_path, cookie_secure=False))
    asyncio.run(container.create_user("admin", PASSWORD, Role.ADMIN))
    with TestClient(build_app(container), base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
        client.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
        yield {"client": client, "container": container, "data_dir": tmp_path}


def test_status_snapshot_and_history(env: dict) -> None:  # type: ignore[type-arg]
    client = env["client"]
    camera_id = client.post("/api/v1/cameras", json={
        "name": "Quintal", "host": "192.0.2.10", "main_path": "/a", "username": "u", "password": "p",
    }).json()["id"]  # fmt: skip
    assert client.get("/api/v1/cameras/status").json() == []  # monitor ainda não rodou

    log = SqlCameraStatusLog(env["container"].engine)
    t1, t2 = datetime(2026, 10, 17, 12, 0, tzinfo=UTC), datetime(2026, 10, 17, 12, 5, tzinfo=UTC)

    async def seed() -> None:
        await log.record(camera_id, t1, CameraHealth(camera_id, CameraState.ONLINE, "recebendo vídeo", True))
        await log.record(camera_id, t2, CameraHealth(camera_id, CameraState.OFFLINE, "sem conexão", False))
        await env["container"].monitor_camera_health()  # carrega o último estado do histórico

    asyncio.run(seed())

    # Nos testes o MediaMTX está inacessível: o monitor registra a nova causa (servidor de mídia fora).
    current = client.get("/api/v1/cameras/status").json()
    assert [(c["camera_id"], c["state"]) for c in current] == [(camera_id, "offline")]
    assert "MediaMTX" in current[0]["detail"]
    history = client.get(f"/api/v1/cameras/{camera_id}/status-history").json()
    assert [h["state"] for h in history] == ["offline", "offline", "online"]  # mais recente primeiro
    assert history[1]["detail"] == "sem conexão"
    assert client.get("/api/v1/cameras/inexistente/status-history").status_code == 404


def test_status_routes_require_login(env: dict) -> None:  # type: ignore[type-arg]
    client = env["client"]
    client.cookies.clear()
    assert client.get("/api/v1/cameras/status").status_code == 401
    assert client.get("/api/v1/events/stream").status_code == 401


class FakeRequest:
    def __init__(self) -> None:
        self.disconnected = False

    async def is_disconnected(self) -> bool:
        return self.disconnected


async def test_sse_stream_formats_events_and_keepalives() -> None:
    bus, request = EventBus(), FakeRequest()
    stream = sse_stream(bus, request, keepalive=0.05)  # type: ignore[arg-type]
    assert await anext(stream) == ": conectado\n\n"
    bus.publish(Event("camera.status", {"camera_id": "c1", "state": "online", "detail": "recebendo vídeo"}))
    chunk = await anext(stream)
    assert chunk.startswith("event: camera.status\ndata: ")
    assert '"detail": "recebendo vídeo"' in chunk
    assert await anext(stream) == ": keepalive\n\n"
    request.disconnected = True
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert bus.subscriber_count == 0
