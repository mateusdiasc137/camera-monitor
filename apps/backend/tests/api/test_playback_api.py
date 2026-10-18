"""Reprodução/download de gravações pelo backend (servidor de playback falso)."""

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nvr.bootstrap.cli import build_app
from nvr.bootstrap.container import build_container
from nvr.bootstrap.settings import Settings
from nvr.identity.domain import Role
from nvr.persistence.database import migrate
from nvr.recordings.application import OpenRecording
from nvr.recordings.domain import Segment
from nvr.recordings.export import ExportRecording
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


class CoveredIndex:
    """Tudo gravado continuamente (exceto antes de 2000): o download vai direto ao servidor de playback."""

    async def in_range(self, camera_id: str, start: datetime, end: datetime) -> list[Segment]:
        if start.year < 2000:
            return []
        span = (end - start).total_seconds() + 2
        return [Segment(camera_id, "l", "cam/x.mp4", start - timedelta(seconds=1), span, 1)]


@pytest.fixture
def env(tmp_path: Path) -> Iterator[dict]:  # type: ignore[type-arg]
    migrate(tmp_path)
    container = build_container(Settings(data_dir=tmp_path, cookie_secure=False))
    playback = FakePlayback()
    cameras = container.open_recording._cameras
    object.__setattr__(container, "open_recording", OpenRecording(cameras, playback))
    tools = container.export_recording._tools
    object.__setattr__(
        container, "export_recording", ExportRecording(cameras, CoveredIndex(), playback, tools)
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


def test_lens_is_only_for_downloads_of_two_lens_cameras(env: dict) -> None:  # type: ignore[type-arg]
    client, cam = env["client"], env["camera"]
    url = f"/api/v1/recordings/{cam['id']}/video"
    base = {"start": "2026-10-17T21:04:57+00:00", "duration": 60}
    playing = client.get(url, params=base | {"lens": "top"})
    assert playing.status_code == 422
    assert "lens" in playing.json()["fields"]
    single = client.get(url, params=base | {"lens": "top", "download": "true"})
    assert single.status_code == 422  # câmera de uma lente
    assert client.get(url, params=base | {"lens": "lateral", "download": "true"}).status_code == 422


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


class FakeIndex:
    """Dois segmentos com um buraco de 0,6 s entre eles (gravador reiniciado)."""

    def __init__(self) -> None:
        t0 = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
        self.first = Segment("c", "l", "cam-c-main/a.mp4", t0, 3.0, 1)
        self.second = Segment("c", "l", "cam-c-main/b.mp4", t0 + timedelta(seconds=3.6), 3.0, 1)

    async def in_range(self, camera_id: str, start: datetime, end: datetime) -> list[Segment]:
        return [s for s in (self.first, self.second) if s.start_at < end and s.end_at > start]


def test_start_inside_a_short_gap_moves_to_next_segment() -> None:
    index, playback = FakeIndex(), FakePlayback()

    class Cameras:
        async def get(self, camera_id: str) -> object:
            return type("Cam", (), {"id": camera_id, "name": "Quintal"})()

    use_case = OpenRecording(Cameras(), playback, index)  # type: ignore[arg-type]
    in_gap = index.first.end_at + timedelta(seconds=0.2)
    clip = asyncio.run(use_case("c", in_gap, 4.0, "fmp4"))
    _path, start, duration, _fmt = playback.calls[-1]
    assert start == index.second.start_at + timedelta(milliseconds=1)
    assert duration == pytest.approx(4.0 - 0.4 - 0.001)  # o fim pedido continua o mesmo
    assert clip.start == start

    inside = index.first.start_at + timedelta(seconds=1)
    asyncio.run(use_case("c", inside, 4.0, "fmp4"))
    assert playback.calls[-1][1] == inside  # dentro de um segmento: não mexe
