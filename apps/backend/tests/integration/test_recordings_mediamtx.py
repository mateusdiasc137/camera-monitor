"""O índice contra gravações reais do MediaMTX (binário de tools/) alimentado pelo FFmpeg."""

import asyncio
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nvr.bootstrap.container import build_container
from nvr.bootstrap.settings import Settings
from nvr.cameras.application import CameraDraft
from nvr.cameras.domain import CameraCredentials
from nvr.persistence.database import migrate
from nvr.recordings.domain import RECORD_FILENAME_TEMPLATE

REPO = Path(__file__).resolve().parents[4]
API_PORT, RTSP_PORT, PLAYBACK_PORT = 19995, 18556, 19994
SECONDS = 7


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


def ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return float(out.strip())


def test_index_matches_real_mediamtx_recordings(tmp_path: Path) -> None:
    data, recordings = tmp_path / "data", tmp_path / "recordings"
    migrate(data)
    container = build_container(
        Settings(
            data_dir=data,
            recordings_dir=recordings,
            mediamtx_playback_url=f"http://127.0.0.1:{PLAYBACK_PORT}",
        )
    )

    async def register() -> str:
        draft = CameraDraft("Sintética", "127.0.0.1", "/x", CameraCredentials("u", "p"))
        return (await container.register_camera(draft, "teste")).id

    camera_id = asyncio.run(register())
    path = f"cam-{camera_id}-main"
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
playback: true
playbackAddress: 127.0.0.1:{PLAYBACK_PORT}
authMethod: internal
authInternalUsers:
  - user: any
    ips: ["127.0.0.1", "::1"]
    permissions: [{{action: api}}, {{action: publish}}, {{action: playback}}]
paths:
  {path}:
    source: publisher
    record: yes
    recordPath: {recordings.as_posix()}/%path/{RECORD_FILENAME_TEMPLATE}
    recordFormat: fmp4
    recordSegmentDuration: 2s
    recordPartDuration: 200ms
    recordDeleteAfter: 0s
""",
        encoding="utf-8",
    )
    started = datetime.now(UTC)
    server = subprocess.Popen([str(mediamtx_binary()), str(config)], cwd=tmp_path)
    try:
        time.sleep(1.0)
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-re", "-f", "lavfi",
             "-i", "testsrc2=size=320x240:rate=10", "-t", str(SECONDS), "-c:v", "libx264", "-g", "10",
             "-pix_fmt", "yuv420p", "-f", "rtsp", "-rtsp_transport", "tcp",
             f"rtsp://127.0.0.1:{RTSP_PORT}/{path}"],
            check=True, timeout=60,
        )  # fmt: skip
        time.sleep(1.0)
    finally:
        server.kill()  # encerramento abrupto: o último segmento pode ficar sem duração no mvhd
        server.wait(timeout=10)

    files = sorted((recordings / path).glob("*.mp4"))
    assert len(files) >= 3, files
    assert all(re.search(r"(Z|[+-]\d{4})\.mp4$", f.name) for f in files)  # nome com fuso (%z)

    async def index_and_query():  # type: ignore[no-untyped-def]
        await container.ensure_storage_location()
        report = await container.index_segments()
        segments = await container.segment_index.in_range(
            camera_id, started - timedelta(minutes=1), datetime.now(UTC) + timedelta(minutes=1)
        )
        return report, segments

    report, segments = asyncio.run(index_and_query())
    assert report.added == len(files)
    starts = [s.start_at for s in segments]
    assert starts == sorted(starts)
    assert abs(starts[0] - started) < timedelta(seconds=5)
    total = sum(s.duration_s for s in segments)
    assert total == pytest.approx(SECONDS, abs=1.5)
    for segment in segments:
        on_disk = recordings / segment.rel_path
        assert segment.duration_s == pytest.approx(ffprobe_duration(on_disk), abs=0.35), segment.rel_path

    # Reprodução pelo backend: MediaMTX religado (sem publicador) servindo os arquivos gravados.
    server = subprocess.Popen([str(mediamtx_binary()), str(config)], cwd=tmp_path)
    try:
        time.sleep(1.0)

        async def fetch_clip() -> bytes:
            clip = await container.open_recording(
                camera_id, segments[0].start_at + timedelta(seconds=0.5), 3.0, "mp4"
            )
            data = b"".join([chunk async for chunk in clip.stream.chunks()])
            await container.aclose()
            return data

        clip_bytes = asyncio.run(fetch_clip())
    finally:
        server.kill()
        server.wait(timeout=10)
    clip_file = tmp_path / "trecho.mp4"
    clip_file.write_bytes(clip_bytes)
    assert ffprobe_duration(clip_file) == pytest.approx(3.0, abs=1.0)
