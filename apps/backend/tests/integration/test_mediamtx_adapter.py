"""Adaptador da API do MediaMTX contra o binário real (tools/, baixado por scripts/fetch_tools.py).

Usa portas alternativas para não colidir com instâncias de desenvolvimento.
"""

import asyncio
import json
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from nvr.streaming.adapters.mediamtx_api import MediaMtxApi
from nvr.streaming.domain import PathSpec

REPO = Path(__file__).resolve().parents[4]
API_PORT, RTSP_PORT = 19997, 18554


def mediamtx_binary() -> Path | None:
    manifest = json.loads((REPO / "tools" / "manifest.json").read_text(encoding="utf-8"))
    name = "mediamtx.exe" if sys.platform == "win32" else "mediamtx"
    binary = REPO / "tools" / "mediamtx" / manifest["mediamtx"]["version"] / name
    return binary if binary.exists() else None


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        mediamtx_binary() is None, reason="MediaMTX não baixado (python scripts/fetch_tools.py)"
    ),
]


@pytest.fixture
async def api(tmp_path: Path) -> AsyncIterator[MediaMtxApi]:
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
    permissions: [{{action: api}}]
paths: {{}}
""",
        encoding="utf-8",
    )
    process = subprocess.Popen([str(mediamtx_binary()), str(config)], cwd=tmp_path)
    client = MediaMtxApi(f"http://127.0.0.1:{API_PORT}")
    try:
        for _ in range(50):
            try:
                await client.states()
                break
            except Exception:
                await asyncio.sleep(0.1)
        yield client
    finally:
        await client.aclose()
        process.terminate()
        process.wait(timeout=10)


async def test_add_replace_list_remove(api: MediaMtxApi) -> None:
    name = "cam-0b6f1c2e-1111-4a3b-9c55-123456789abc-main"
    spec = PathSpec(name, record=False, record_path="rec/%path/%Y-%m-%d_%H-%M-%S-%f")
    await api.put_path(spec)
    assert (await api.list_configured())[name] == spec

    await api.put_path(PathSpec(name, record=True, record_path=spec.record_path))  # replace
    assert (await api.list_configured())[name].record is True

    states = await api.states()
    assert states[name].ready is False  # ninguém publicando

    await api.remove_path(name)
    assert name not in await api.list_configured()
    await api.remove_path(name)  # idempotente


async def test_unmanaged_paths_are_not_listed(api: MediaMtxApi) -> None:
    import httpx

    async with httpx.AsyncClient() as raw:
        response = await raw.post(
            f"http://127.0.0.1:{API_PORT}/v3/config/paths/add/manual", json={"source": "publisher"}
        )
    assert response.status_code == 200
    assert "manual" not in await api.list_configured()
