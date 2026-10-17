"""Cadastro de câmeras ponta a ponta (container real, SQLite temporário, cofre em memória, sonda falsa)."""

import asyncio
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nvr.bootstrap.cli import build_app
from nvr.bootstrap.container import build_container
from nvr.bootstrap.settings import Settings
from nvr.cameras.adapters.secret_stores import MemorySecretStore
from nvr.cameras.domain import MediaTrack, ProbeOutcome, ProbeResult, StreamProfile
from nvr.identity.domain import Role
from nvr.persistence.database import migrate, sqlite_path

PASSWORD = "senha-forte-123"
CAMERA_PASSWORD = "Segredo-Da-Camera-42"
ORIGIN = "http://testserver"


class FakeProbe:
    def __init__(self) -> None:
        self.urls: list[str] = []

    async def probe(self, url: str, profile: StreamProfile) -> ProbeResult:
        self.urls.append(url)
        if "errada" in url:
            return ProbeResult(profile, ProbeOutcome.AUTH_ERROR, "a câmera recusou usuário/senha")
        return ProbeResult(
            profile, ProbeOutcome.ONLINE, "video H265", (MediaTrack("video", "H265", 90000),), 12
        )


@pytest.fixture
def env(tmp_path: Path) -> Iterator[dict]:  # type: ignore[type-arg]
    migrate(tmp_path)
    secrets, probe = MemorySecretStore(), FakeProbe()
    container = build_container(
        Settings(data_dir=tmp_path, cookie_secure=False), secrets=secrets, probe=probe
    )
    asyncio.run(container.create_user("admin", PASSWORD, Role.ADMIN))
    asyncio.run(container.create_user("visitante", PASSWORD, Role.VIEWER))
    with TestClient(build_app(container), base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
        yield {"client": client, "secrets": secrets, "probe": probe, "data_dir": tmp_path}


def as_user(client: TestClient, username: str) -> TestClient:
    client.cookies.clear()
    assert (
        client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD}).status_code
        == 200
    )
    return client


XIONGMAI = {
    "name": "Quintal",
    "host": "10.0.0.210",
    "main_path": "/user={user}&password={password}&channel=1&stream=0.sdp?",
    "sub_path": "/user={user}&password={password}&channel=1&stream=1.sdp?",
    "username": "admin",
    "password": CAMERA_PASSWORD,
    "driver": "xiongmai",
}


def test_admin_registers_camera_and_secret_goes_to_vault(env: dict) -> None:  # type: ignore[type-arg]
    client = as_user(env["client"], "admin")
    response = client.post("/api/v1/cameras", json=XIONGMAI)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Quintal"
    assert body["ingest"] == "relay"
    assert body["recording_enabled"] is True
    assert "password" not in body
    assert "username" not in body
    stored = env["secrets"].get(f"camera:{body['id']}")
    assert stored.password == CAMERA_PASSWORD

    listed = client.get("/api/v1/cameras").json()
    assert [c["id"] for c in listed] == [body["id"]]


def test_viewer_can_list_but_not_change(env: dict) -> None:  # type: ignore[type-arg]
    camera_id = as_user(env["client"], "admin").post("/api/v1/cameras", json=XIONGMAI).json()["id"]
    client = as_user(env["client"], "visitante")
    assert client.get("/api/v1/cameras").status_code == 200
    assert client.post("/api/v1/cameras", json=XIONGMAI | {"name": "Outra"}).status_code == 403
    assert client.patch(f"/api/v1/cameras/{camera_id}", json={"name": "X"}).status_code == 403
    assert client.delete(f"/api/v1/cameras/{camera_id}").status_code == 403
    assert client.post(f"/api/v1/cameras/{camera_id}/test").status_code == 403


def test_validation_reports_every_field(env: dict) -> None:  # type: ignore[type-arg]
    client = as_user(env["client"], "admin")
    response = client.post(
        "/api/v1/cameras",
        json=XIONGMAI
        | {"name": " ", "host": "rtsp://10.0.0.210", "main_path": "/user=admin&password=123", "rtsp_port": 0},
    )
    assert response.status_code == 422
    fields = response.json()["fields"]
    assert set(fields) == {"name", "host", "main_path", "rtsp_port"}
    assert "marcadores" in fields["main_path"]


def test_duplicate_name_is_rejected(env: dict) -> None:  # type: ignore[type-arg]
    client = as_user(env["client"], "admin")
    client.post("/api/v1/cameras", json=XIONGMAI)
    response = client.post("/api/v1/cameras", json=XIONGMAI | {"host": "10.0.0.211"})
    assert response.status_code == 422
    assert "name" in response.json()["fields"]


def test_partial_update_and_credential_rotation(env: dict) -> None:  # type: ignore[type-arg]
    client = as_user(env["client"], "admin")
    camera_id = client.post("/api/v1/cameras", json=XIONGMAI).json()["id"]
    updated = client.patch(
        f"/api/v1/cameras/{camera_id}", json={"host": "10.0.0.99", "recording_enabled": False}
    )
    assert updated.status_code == 200
    assert updated.json()["host"] == "10.0.0.99"
    assert updated.json()["recording_enabled"] is False
    assert updated.json()["name"] == "Quintal"
    assert env["secrets"].get(f"camera:{camera_id}").password == CAMERA_PASSWORD  # senha mantida

    client.patch(f"/api/v1/cameras/{camera_id}", json={"username": "admin", "password": "nova-senha-1"})
    assert env["secrets"].get(f"camera:{camera_id}").password == "nova-senha-1"

    cleared = client.patch(f"/api/v1/cameras/{camera_id}", json={"sub_path": ""})
    assert cleared.json()["sub_path"] is None


def test_delete_removes_secret(env: dict) -> None:  # type: ignore[type-arg]
    client = as_user(env["client"], "admin")
    camera_id = client.post("/api/v1/cameras", json=XIONGMAI).json()["id"]
    assert client.delete(f"/api/v1/cameras/{camera_id}").status_code == 204
    assert client.get(f"/api/v1/cameras/{camera_id}").status_code == 404
    assert env["secrets"].get(f"camera:{camera_id}") is None


def test_connection_test_probes_both_profiles(env: dict) -> None:  # type: ignore[type-arg]
    client = as_user(env["client"], "admin")
    camera_id = client.post("/api/v1/cameras", json=XIONGMAI).json()["id"]
    results = client.post(f"/api/v1/cameras/{camera_id}/test").json()
    assert [(r["profile"], r["outcome"]) for r in results] == [("main", "online"), ("sub", "online")]
    assert results[0]["tracks"] == [{"kind": "video", "codec": "H265", "clock_rate": 90000}]
    # A sonda recebeu a URL completa (com a senha no caminho, formato Xiongmai)...
    assert env["probe"].urls[0] == (
        f"rtsp://10.0.0.210:554/user=admin&password={CAMERA_PASSWORD}&channel=1&stream=0.sdp?"
    )


def test_camera_password_never_leaks(env: dict, caplog: pytest.LogCaptureFixture) -> None:  # type: ignore[type-arg]
    """Critério do MVP: segredos fora de respostas da API, banco, auditoria e logs."""
    caplog.set_level(logging.DEBUG)
    client = as_user(env["client"], "admin")
    responses = [client.post("/api/v1/cameras", json=XIONGMAI)]
    camera_id = responses[0].json()["id"]
    responses += [
        client.get("/api/v1/cameras"),
        client.get(f"/api/v1/cameras/{camera_id}"),
        client.patch(f"/api/v1/cameras/{camera_id}", json={"name": "Quintal 2"}),
        client.post(f"/api/v1/cameras/{camera_id}/test"),
        client.post("/api/v1/cameras", json=XIONGMAI | {"name": " "}),  # erro de validação
        client.get("/api/v1/openapi.json"),
    ]
    for response in responses:
        assert CAMERA_PASSWORD not in response.text, response.request.url
    db_bytes = b"".join(p.read_bytes() for p in env["data_dir"].glob("nvr.db*"))
    assert CAMERA_PASSWORD.encode() not in db_bytes
    assert sqlite_path(env["data_dir"]).exists()
    assert CAMERA_PASSWORD not in caplog.text
