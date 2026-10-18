"""Saúde do sistema com o container real: câmeras por estado, gravação atrasada e ping público."""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nvr.bootstrap.cli import build_app
from nvr.bootstrap.container import build_container
from nvr.bootstrap.settings import Settings
from nvr.identity.domain import Role
from nvr.persistence.database import migrate
from nvr.streaming.application import CameraHealthView
from nvr.streaming.domain import CameraHealth, CameraState
from tests.integration.test_storage_enforcement import write

PASSWORD = "senha-forte-123"
ORIGIN = "http://testserver"


@pytest.fixture
def env(tmp_path: Path) -> Iterator[dict[str, Any]]:
    data = tmp_path / "data"
    migrate(data)
    container = build_container(Settings(data_dir=data, cookie_secure=False))
    asyncio.run(container.create_user("admin", PASSWORD, Role.ADMIN))
    with TestClient(build_app(container), base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
        assert client.get("/api/v1/system/ping").json() == {"status": "ok"}  # sem login
        assert client.get("/api/v1/system/health").status_code == 401
        client.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
        camera = client.post("/api/v1/cameras", json={
            "name": "Quintal", "host": "192.0.2.10", "main_path": "/a", "username": "u", "password": "p",
        }).json()  # fmt: skip
        yield {"client": client, "container": container, "camera": camera}


def component(health: dict[str, Any], name: str) -> dict[str, Any]:
    return next(c for c in health["components"] if c["name"] == name)  # type: ignore[no-any-return]


def test_health_reports_cameras_and_late_recording(env: dict[str, Any]) -> None:
    client, container, cam = env["client"], env["container"], env["camera"]
    health = client.get("/api/v1/system/health").json()
    assert [c["name"] for c in health["components"]] == [
        "database", "media_server", "storage", "cameras", "recording",
    ]  # fmt: skip
    assert "desativado" in component(health, "media_server")["detail"]  # testes rodam sem mídia
    assert health["cameras"][0]["state"] == "starting"  # o monitor ainda não amostrou

    now = datetime.now(UTC)
    container.monitor_camera_health._current[cam["id"]] = CameraHealthView(
        CameraHealth(cam["id"], CameraState.ONLINE, "", True), now - timedelta(minutes=30)
    )
    root = container.settings.resolved_recordings_dir() / f"cam-{cam['id']}-main"
    write(root, now - timedelta(minutes=20), 1)  # trecho de 5 min que terminou há 15 min
    asyncio.run(container.index_segments())

    health = client.get("/api/v1/system/health").json()
    assert health["state"] == "degraded"
    assert component(health, "cameras")["state"] == "ok"
    recording = component(health, "recording")
    assert recording["state"] == "degraded"
    assert recording["detail"].startswith("sem gravar: Quintal (sem arquivo novo há 15 min")
    summary = health["cameras"][0]
    assert (summary["name"], summary["state"], summary["recording_expected"]) == ("Quintal", "online", True)
    last = datetime.fromisoformat(summary["last_recording_at"])
    assert abs((last - (now - timedelta(minutes=15))).total_seconds()) < 2

    write(root, now - timedelta(minutes=4), 1)  # trecho aberto agora
    asyncio.run(container.index_segments())
    health = client.get("/api/v1/system/health").json()
    assert component(health, "recording")["state"] == "ok"
    assert health["state"] == "ok"

    client.patch(f"/api/v1/cameras/{cam['id']}", json={"recording_enabled": False})
    health = client.get("/api/v1/system/health").json()
    assert health["cameras"][0]["recording_expected"] is False
    assert component(health, "recording")["detail"] == "nenhuma câmera com gravação ligada"


def test_media_server_down_is_reported(env: dict[str, Any]) -> None:
    container = env["container"]
    probe = next(p for p in container.get_system_health._probes if type(p).__name__ == "MediaServerProbe")
    probe._enabled = True  # type: ignore[attr-defined]
    container.monitor_camera_health.media_checked_at = datetime.now(UTC)
    container.monitor_camera_health.media_error = "MediaMTX não respondeu"
    health = env["client"].get("/api/v1/system/health").json()
    assert health["state"] == "down"
    assert component(health, "recording")["state"] == "ok"  # o monitor ainda não viu a câmera cair
    assert component(health, "media_server") == {
        "name": "media_server", "state": "down", "detail": "sem resposta: MediaMTX não respondeu",
    }  # fmt: skip
