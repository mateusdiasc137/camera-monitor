"""Ao vivo via WHEP intermediado pelo backend (sinalização falsa no lugar do MediaMTX)."""

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nvr.bootstrap.cli import build_app
from nvr.bootstrap.container import build_container
from nvr.bootstrap.settings import Settings
from nvr.identity.domain import Role
from nvr.persistence.database import migrate
from nvr.shared.errors import CameraOffline
from nvr.streaming.application import LiveViewing
from nvr.streaming.domain import LiveAnswer

PASSWORD = "senha-forte-123"
ORIGIN = "http://testserver"
SESSION = "5f0c2a8e-3a52-4b0c-9a7e-0123456789ab"
OFFER = "v=0\r\no=- 1 1 IN IP4 0.0.0.0\r\ns=-\r\nt=0 0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n"


class FakeSignaling:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.offline = False

    async def offer(self, path: str, sdp: str) -> LiveAnswer:
        self.calls.append(("offer", path, sdp))
        if self.offline:
            raise CameraOffline("a câmera não está transmitindo agora")
        return LiveAnswer("v=0\r\ns=resposta\r\n", SESSION)

    async def patch(self, path: str, session_id: str, fragment: str) -> None:
        self.calls.append(("patch", path, session_id, fragment))

    async def close(self, path: str, session_id: str) -> None:
        self.calls.append(("close", path, session_id))


@pytest.fixture
def env(tmp_path: Path) -> Iterator[dict]:  # type: ignore[type-arg]
    migrate(tmp_path)
    container = build_container(Settings(data_dir=tmp_path, cookie_secure=False))
    signaling = FakeSignaling()
    object.__setattr__(container, "live_viewing", LiveViewing(container.live_viewing._cameras, signaling))
    asyncio.run(container.create_user("admin", PASSWORD, Role.ADMIN))
    asyncio.run(container.create_user("visitante", PASSWORD, Role.VIEWER))
    with TestClient(build_app(container), base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
        client.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
        camera = client.post("/api/v1/cameras", json={
            "name": "Quintal", "host": "192.0.2.10", "main_path": "/a", "sub_path": "/b",
            "username": "u", "password": "p", "lens_layout": "stacked2",
        }).json()  # fmt: skip
        yield {"client": client, "camera": camera, "signaling": signaling}


def login(client: TestClient, user: str) -> None:
    client.cookies.clear()
    client.post("/api/v1/auth/login", json={"username": user, "password": PASSWORD})


def test_viewer_negotiates_live_through_backend(env: dict) -> None:  # type: ignore[type-arg]
    client, camera = env["client"], env["camera"]
    assert camera["lens_layout"] == "stacked2"
    login(client, "visitante")  # visitante pode assistir
    response = client.post(
        f"/api/v1/cameras/{camera['id']}/whep?profile=sub",
        content=OFFER,
        headers={"Content-Type": "application/sdp"},
    )
    assert response.status_code == 201
    assert response.headers["content-type"].startswith("application/sdp")
    assert response.text == "v=0\r\ns=resposta\r\n"
    assert response.headers["location"] == f"/api/v1/cameras/{camera['id']}/whep/{SESSION}?profile=sub"
    assert env["signaling"].calls[0] == ("offer", f"cam-{camera['id']}-sub", OFFER)

    patch = client.patch(
        response.headers["location"],
        content="a=candidate:1",
        headers={"Content-Type": "application/trickle-ice-sdpfrag"},
    )
    assert patch.status_code == 204
    assert client.delete(response.headers["location"]).status_code == 204
    assert [c[0] for c in env["signaling"].calls] == ["offer", "patch", "close"]


def test_live_requires_session_and_same_origin(env: dict) -> None:  # type: ignore[type-arg]
    client, camera = env["client"], env["camera"]
    url = f"/api/v1/cameras/{camera['id']}/whep"
    assert (
        client.post(
            url, content=OFFER, headers={"Content-Type": "application/sdp", "Origin": "http://evil.example"}
        ).status_code
        == 403
    )
    client.cookies.clear()
    assert client.post(url, content=OFFER, headers={"Content-Type": "application/sdp"}).status_code == 401
    assert env["signaling"].calls == []


def test_live_validates_input(env: dict) -> None:  # type: ignore[type-arg]
    client, camera = env["client"], env["camera"]
    url = f"/api/v1/cameras/{camera['id']}/whep"
    assert client.post(url, content=OFFER, headers={"Content-Type": "text/plain"}).status_code == 422
    assert (
        client.post(url, content="x" * 70_000, headers={"Content-Type": "application/sdp"}).status_code == 422
    )
    assert (
        client.post(
            url + "?profile=outro", content=OFFER, headers={"Content-Type": "application/sdp"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/cameras/inexistente/whep", content=OFFER, headers={"Content-Type": "application/sdp"}
        ).status_code
        == 404
    )
    assert client.delete(f"{url}/../../outra").status_code in (404, 405)
    assert client.delete(f"{url}/nao-e-uuid").status_code == 404


def test_offline_camera_and_disabled_camera(env: dict) -> None:  # type: ignore[type-arg]
    client, camera = env["client"], env["camera"]
    url = f"/api/v1/cameras/{camera['id']}/whep"
    env["signaling"].offline = True
    response = client.post(url, content=OFFER, headers={"Content-Type": "application/sdp"})
    assert response.status_code == 503
    assert "não está transmitindo" in response.json()["message"]
    client.patch(f"/api/v1/cameras/{camera['id']}", json={"enabled": False})
    assert client.post(url, content=OFFER, headers={"Content-Type": "application/sdp"}).status_code == 409
