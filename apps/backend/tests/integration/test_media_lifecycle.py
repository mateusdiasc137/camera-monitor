"""Ciclo de vida da mídia com processos reais: MediaMTX (portas alternativas) + relays FFmpeg.

A "câmera" é uma porta local fechada: os relays falham e são reiniciados. O que se verifica é que a
aplicação cria os paths, mantém os relays e, ao desligar, encerra tudo sem travar nem deixar órfãos.
"""

import asyncio
import json
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nvr.bootstrap.cli import build_app
from nvr.bootstrap.container import build_container
from nvr.bootstrap.settings import Settings
from nvr.identity.domain import Role
from nvr.persistence.database import migrate

REPO = Path(__file__).resolve().parents[4]
API_PORT, RTSP_PORT = 19998, 18555
PASSWORD = "senha-forte-123"


def mediamtx_binary() -> Path | None:
    manifest = json.loads((REPO / "tools" / "manifest.json").read_text(encoding="utf-8"))
    name = "mediamtx.exe" if sys.platform == "win32" else "mediamtx"
    binary = REPO / "tools" / "mediamtx" / manifest["mediamtx"]["version"] / name
    return binary if binary.exists() else None


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(mediamtx_binary() is None, reason="MediaMTX não baixado"),
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg não instalado"),
]


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def ffmpeg_pids() -> set[int]:
    if sys.platform == "win32":
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq ffmpeg.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
        ).stdout
        return {int(line.split('","')[1]) for line in out.splitlines() if line.startswith('"ffmpeg.exe"')}
    out = subprocess.run(["pgrep", "-x", "ffmpeg"], capture_output=True, text=True).stdout
    return {int(p) for p in out.split()}


@pytest.fixture
def mediamtx(tmp_path: Path) -> Iterator[None]:
    config = tmp_path / "mediamtx.yml"
    config.write_text(
        f"""
logLevel: warn
api: true
apiAddress: 127.0.0.1:{API_PORT}
rtsp: true
rtspAddress: 127.0.0.1:{RTSP_PORT}
rtspTransports: [tcp]
rtmp: false
hls: false
webrtc: false
srt: false
moq: false
playback: false
authMethod: internal
authInternalUsers:
  - user: any
    ips: ["127.0.0.1", "::1"]
    permissions: [{{action: api}}, {{action: publish}}]
paths: {{}}
""",
        encoding="utf-8",
    )
    process = subprocess.Popen([str(mediamtx_binary()), str(config)], cwd=tmp_path)
    time.sleep(1.0)
    try:
        yield
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_relays_start_and_shutdown_cleanly(
    tmp_path: Path, mediamtx: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in ("NVR_MEDIA_ENABLED", "NVR_MEDIAMTX_API_URL", "NVR_FFMPEG_PATH"):
        monkeypatch.delenv(key)
    data = tmp_path / "data"
    migrate(data)
    settings = Settings(
        data_dir=data,
        cookie_secure=False,
        media_enabled=True,
        media_sync_interval=1.0,
        mediamtx_api_url=f"http://127.0.0.1:{API_PORT}",
        mediamtx_rtsp_address=f"127.0.0.1:{RTSP_PORT}",
    )
    container = build_container(settings)
    asyncio.run(container.create_user("admin", PASSWORD, Role.ADMIN))
    before = ffmpeg_pids()

    started = time.monotonic()
    with TestClient(build_app(container), base_url="http://t", headers={"Origin": "http://t"}) as client:
        client.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
        response = client.post("/api/v1/cameras", json={
            "name": "Fora do ar", "host": "127.0.0.1", "rtsp_port": closed_port(), "main_path": "/stream",
            "username": "u", "password": "p",
        })  # fmt: skip
        assert response.status_code == 201
        camera_id = response.json()["id"]
        main = f"cam-{camera_id}-main"

        # No Windows, a conexão recusada só aparece após o -timeout do FFmpeg (10 s).
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if main in container.relays.statuses() and container.relays.statuses()[main].restarts >= 1:
                break
            time.sleep(0.2)
        status = container.relays.statuses()[main]
        assert status.restarts >= 1  # FFmpeg real rodou, falhou (câmera fora do ar) e foi reiniciado
        assert "u:p@" not in status.last_error  # saída do FFmpeg mascarada
    shutdown_took = time.monotonic() - started

    assert shutdown_took < 30, f"desligamento demorou {shutdown_took:.1f}s"
    time.sleep(0.5)
    assert ffmpeg_pids() - before == set(), "relays FFmpeg órfãos após desligar"
