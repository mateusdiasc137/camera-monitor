"""Reprodução/download de gravações pelo backend (servidor de playback falso)."""

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nvr.bootstrap.cli import build_app
from nvr.bootstrap.container import build_container
from nvr.bootstrap.settings import Settings
from nvr.identity.domain import Role
from nvr.persistence.database import migrate
from nvr.recordings.application import OpenRecording
from nvr.shared.errors import NotFound

PASSWORD = "senha-forte-123"
ORIGIN = "http://testserver"


class FakeStream:
    content_type = "video/mp4"

    def __init__(self) -> None:
        self.closed = False

    async def chunks(self) -> AsyncIterator[bytes]:
        for part in (b"ftyp-falso", b"moov-falso", b"mdat-falso"):
            yield part

    async def aclose(self) -> None:
        self.closed = True


class FakePlayback:
    def __init__(self) -> None:
        self.calls: list[tuple[str, datetime, float, str]] = []

    async def open(self, path: str, start: datetime, duration_s: float, fmt: str) -> FakeStream:
        self.calls.append((path, start, duration_s, fmt))
        if start.year < 2000:
            raise NotFound("não há gravação nesse momento")
        return FakeStream()


@pytest.fixture
def env(tmp_path: Path) -> Iterator[dict]:  # type: ignore[type-arg]
    migrate(tmp_path)
    container = build_container(Settings(data_dir=tmp_path, cookie_secure=False))
    playback = FakePlayback()
    object.__setattr__(
        container, "open_recording", OpenRecording(container.open_recording._cameras, playback)
    )
    asyncio.run(container.create_user("admin", PASSWORD, Role.ADMIN))
    with TestClient(build_app(container), base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
        client.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
        body = {"name": "Câmera da Frente", "host": "192.0.2.10", "main_path": "/a", "username": "u"}
        camera = client.post("/api/v1/cameras", json=body | {"password": "p"}).json()
        yield {"client": client, "camera": camera, "playback": playback}


def test_streams_clip_from_main_path(env: dict) -> None:  # type: ignore[type-arg]
    client, cam = env["client"], env["camera"]
    r = client.get(
        f"/api/v1/recordings/{cam['id']}/video", params={"start": "2026-10-17T21:04:57Z", "duration": 30}
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "video/mp4"
    assert r.content == b"ftyp-falsomoov-falsomdat-falso"
    assert "content-disposition" not in r.headers
    path, start, duration, fmt = env["playback"].calls[0]
    assert path == f"cam-{cam['id']}-main"
    assert start.isoformat() == "2026-10-17T21:04:57+00:00"
    assert (duration, fmt) == (30, "fmp4")


def test_download_has_safe_filename(env: dict) -> None:  # type: ignore[type-arg]
    client, cam = env["client"], env["camera"]
    params = {"start": "2026-10-17T21:04:57+00:00", "duration": 60, "format": "mp4", "download": "true"}
    r = client.get(f"/api/v1/recordings/{cam['id']}/video", params=params)
    disposition = r.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="camera-da-frente_2026-10-17_')
    assert disposition.endswith('.mp4"')
    assert env["playback"].calls[-1][3] == "mp4"


@pytest.mark.parametrize(
    ("params", "status"),
    [
        ({"start": "2026-10-17T21:00:00", "duration": 30}, 422),  # sem fuso
        ({"start": "2026-10-17T21:00:00Z", "duration": 0}, 422),
        ({"start": "2026-10-17T21:00:00Z", "duration": 7201}, 422),  # mais de 2 h
        ({"start": "2026-10-17T21:00:00Z", "duration": 30, "format": "avi"}, 422),
        ({"start": "1990-01-01T00:00:00Z", "duration": 30}, 404),  # nada gravado
    ],
)
def test_validation_and_not_found(env: dict, params: dict, status: int) -> None:  # type: ignore[type-arg]
    r = env["client"].get(f"/api/v1/recordings/{env['camera']['id']}/video", params=params)
    assert r.status_code == status


def test_requires_login_and_existing_camera(env: dict) -> None:  # type: ignore[type-arg]
    client = env["client"]
    params = {"start": "2026-10-17T21:00:00Z", "duration": 30}
    assert client.get("/api/v1/recordings/nao-existe/video", params=params).status_code == 404
    client.cookies.clear()
    assert client.get(f"/api/v1/recordings/{env['camera']['id']}/video", params=params).status_code == 401
