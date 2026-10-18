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
from nvr.cameras.domain import CameraCredentials, LensLayout
from nvr.persistence.database import migrate
from nvr.recordings.domain import RECORD_FILENAME_TEMPLATE, Lens, LocationMode, MigrationState, Segment
from nvr.streaming.domain import PathSpec

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


def test_switch_location_while_recording_then_migrate_and_play(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F1-11b: troca de pasta a quente (sem derrubar o publicador), migração e reprodução contínua."""
    monkeypatch.setattr("nvr.recordings.location.PROTECT_RECENT_S", 1.0)  # segmentos de 2 s
    api_port, rtsp_port, playback_port, seconds = 19985, 18546, 19984, 12
    data, roots = tmp_path / "data", tmp_path / "discos"
    a, b = roots / "a", (roots / "b").resolve()
    migrate(data)
    container = build_container(
        Settings(
            data_dir=data,
            recordings_dir=a,
            storage_roots=[roots],
            mediamtx_api_url=f"http://127.0.0.1:{api_port}",
            mediamtx_playback_url=f"http://127.0.0.1:{playback_port}",
        )
    )
    container.migration_runner._poll = 0.2  # type: ignore[attr-defined]
    config = tmp_path / "mediamtx.yml"
    config.write_text(
        f"""
logLevel: warn
api: true
apiAddress: 127.0.0.1:{api_port}
rtsp: true
rtspAddress: 127.0.0.1:{rtsp_port}
rtspTransports: [tcp]
rtmp: false
hls: false
webrtc: false
srt: false
moq: false
playback: true
playbackAddress: 127.0.0.1:{playback_port}
authMethod: internal
authInternalUsers:
  - user: any
    ips: ["127.0.0.1", "::1"]
    permissions: [{{action: api}}, {{action: publish}}, {{action: playback}}]
pathDefaults:
  recordFormat: fmp4
  recordSegmentDuration: 2s
  recordPartDuration: 200ms
  recordDeleteAfter: 0s
""",
        encoding="utf-8",
    )
    server = subprocess.Popen([str(mediamtx_binary()), str(config)], cwd=tmp_path)

    async def scenario() -> tuple[str, datetime, list[Segment], dict[str, bytes]]:
        draft = CameraDraft("Sintética", "127.0.0.1", "/x", CameraCredentials("u", "p"))
        camera_id = (await container.register_camera(draft, "teste")).id
        name = f"cam-{camera_id}-main"
        await container.ensure_storage_location()
        await asyncio.sleep(1.0)
        await container.media_api.put_path(PathSpec(name, True, container.record_dir.record_path()))
        started = datetime.now(UTC)
        publisher = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-re", "-f", "lavfi",
             "-i", "testsrc2=size=320x240:rate=10", "-t", str(seconds), "-c:v", "libx264", "-g", "10",
             "-pix_fmt", "yuv420p", "-f", "rtsp", "-rtsp_transport", "tcp",
             f"rtsp://127.0.0.1:{rtsp_port}/{name}"],
        )  # fmt: skip
        await asyncio.sleep(5)
        switched = datetime.now(UTC)
        await container.change_storage_location(str(b), LocationMode.MIGRATE, "teste")
        # O que a reconciliação faria em seguida: novo recordPath no MediaMTX.
        await container.media_api.put_path(PathSpec(name, True, container.record_dir.record_path()))
        assert await asyncio.to_thread(publisher.wait, 60) == 0  # o publicador não caiu na troca
        for _ in range(150):
            latest = await container.get_storage_migration()
            if latest is not None and latest.migration.state is not MigrationState.RUNNING:
                break
            await asyncio.sleep(0.2)
        assert latest is not None
        assert latest.migration.state is MigrationState.DONE, latest.migration.error
        await container.index_segments()
        segments = await container.segment_index.in_range(
            camera_id, started - timedelta(minutes=1), datetime.now(UTC) + timedelta(minutes=1)
        )
        # A troca reinicia o gravador: o novo segmento só começa no próximo quadro-chave (buraco de até
        # ~1 GOP). O MediaMTX não emenda através dele, mas um pedido que começa no buraco avança até o
        # próximo segmento — é o que a interface faz ao continuar a reprodução.
        gap_start = max(s.end_at for s in segments if s.start_at < switched)
        clips: dict[str, bytes] = {}
        for label, start, duration in (
            ("antes", segments[0].start_at, 2.0),  # gravado no local antigo, servido do novo
            ("buraco", gap_start + timedelta(seconds=0.05), 4.0),
        ):
            clip = await container.open_recording(camera_id, start, duration, "mp4")
            clips[label] = b"".join([chunk async for chunk in clip.stream.chunks()])
        await container.aclose()
        return camera_id, switched, segments, clips

    try:
        camera_id, switched, segments, clips = asyncio.run(scenario())
    finally:
        server.kill()
        server.wait(timeout=10)

    folder = f"cam-{camera_id}-main"
    assert not list(a.glob("*/*.mp4"))  # tudo migrou
    assert len(list((b / folder).glob("*.mp4"))) == len(segments)
    assert len({s.location_id for s in segments}) == 1
    assert any(s.start_at < switched for s in segments)  # gravado antes da troca, agora no novo local
    assert sum(s.duration_s for s in segments) == pytest.approx(seconds, abs=2.5)
    before = [s for s in segments if s.start_at < switched]
    after = [s for s in segments if s.start_at >= switched]
    gap = (after[0].start_at - before[-1].end_at).total_seconds()
    assert gap < 1.5  # no máximo ~1 GOP (1 s neste teste) sem gravação na troca
    for label, expected in (("antes", 2.0), ("buraco", 4.0)):
        clip_file = tmp_path / f"{label}.mp4"
        clip_file.write_bytes(clips[label])
        assert ffprobe_duration(clip_file) == pytest.approx(expected, abs=0.8), label


def has_encoder(name: str) -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return f" {name} " in out


def chroma_u(path: Path) -> float:
    """Média do Cb (U) de um quadro: azul puro ≈ 240; o padrão de teste fica perto de 128."""
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", "0.5", "-i", str(path), "-frames:v", "1",
         "-vf", "signalstats,metadata=print:file=-", "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return float(next(line.split("=")[1] for line in out.splitlines() if "signalstats.UAVG" in line))


def ffprobe_video(path: Path) -> tuple[str, int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip
    codec, width, height = out.split(",")
    return codec, int(width), int(height)


@pytest.mark.skipif(not has_encoder("libx265"), reason="FFmpeg sem libx265")
def test_export_joins_pieces_across_a_gap_and_crops_each_lens(tmp_path: Path) -> None:
    """Download do intervalo inteiro apesar do buraco (o playback do MediaMTX para nele) e de cada lente."""
    api_port, rtsp_port, playback_port = 19975, 18536, 19974
    data, recordings = tmp_path / "data", tmp_path / "recordings"
    migrate(data)
    container = build_container(
        Settings(
            data_dir=data,
            recordings_dir=recordings,
            ffmpeg_path="ffmpeg",  # o conftest desliga o FFmpeg por isolamento; aqui ele monta o download
            mediamtx_api_url=f"http://127.0.0.1:{api_port}",
            mediamtx_playback_url=f"http://127.0.0.1:{playback_port}",
        )
    )
    config = tmp_path / "mediamtx.yml"
    config.write_text(
        f"""
logLevel: warn
api: true
apiAddress: 127.0.0.1:{api_port}
rtsp: true
rtspAddress: 127.0.0.1:{rtsp_port}
rtspTransports: [tcp]
rtmp: false
hls: false
webrtc: false
srt: false
moq: false
playback: true
playbackAddress: 127.0.0.1:{playback_port}
authMethod: internal
authInternalUsers:
  - user: any
    ips: ["127.0.0.1", "::1"]
    permissions: [{{action: api}}, {{action: publish}}, {{action: playback}}]
pathDefaults:
  recordFormat: fmp4
  recordSegmentDuration: 2s
  recordPartDuration: 200ms
  recordDeleteAfter: 0s
""",
        encoding="utf-8",
    )
    server = subprocess.Popen([str(mediamtx_binary()), str(config)], cwd=tmp_path)

    def publish(name: str, seconds: int) -> None:
        # "Duas lentes" empilhadas: em cima o padrão de teste, embaixo azul (320x480, H.265).
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-re",
             "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=10:duration={seconds}",
             "-f", "lavfi", "-i", f"color=c=blue:size=320x240:rate=10:duration={seconds}",
             "-filter_complex", "[0][1]vstack", "-c:v", "libx265", "-x265-params", "log-level=error",
             "-g", "10", "-pix_fmt", "yuv420p", "-f", "rtsp", "-rtsp_transport", "tcp",
             f"rtsp://127.0.0.1:{rtsp_port}/{name}"],
            check=True, timeout=120,
        )  # fmt: skip

    async def scenario() -> dict[str, tuple[bytes, float]]:
        draft = CameraDraft(
            "Duas Lentes", "127.0.0.1", "/x", CameraCredentials("u", "p"), lens_layout=LensLayout.STACKED2
        )
        camera_id = (await container.register_camera(draft, "teste")).id
        name = f"cam-{camera_id}-main"
        await container.ensure_storage_location()
        await asyncio.sleep(1.0)
        await container.media_api.put_path(PathSpec(name, True, container.record_dir.record_path()))
        started = datetime.now(UTC)
        await asyncio.to_thread(publish, name, 4)
        await asyncio.sleep(3)  # câmera "fora do ar": buraco de ~3 s
        await asyncio.to_thread(publish, name, 4)
        await asyncio.sleep(1)
        ended = datetime.now(UTC)
        await container.index_segments()
        results: dict[str, tuple[bytes, float]] = {}
        for lens in (Lens.BOTH, Lens.TOP, Lens.BOTTOM):
            clip = await container.export_recording(
                camera_id, started - timedelta(seconds=1), (ended - started).total_seconds() + 2, lens
            )
            assert getattr(clip.stream, "size", 0) > 0
            results[lens.value] = (b"".join([c async for c in clip.stream.chunks()]), clip.duration_s)
        await container.aclose()
        return results

    try:
        results = asyncio.run(scenario())
    finally:
        server.kill()
        server.wait(timeout=10)

    files = {}
    for lens, (content, _reported) in results.items():
        files[lens] = tmp_path / f"{lens}.mp4"
        files[lens].write_bytes(content)
    assert ffprobe_duration(files["both"]) == pytest.approx(8.0, abs=1.5)  # as duas partes, sem o buraco
    assert results["both"][1] == pytest.approx(ffprobe_duration(files["both"]), abs=1.0)
    assert ffprobe_video(files["both"]) == ("hevc", 320, 480)
    assert ffprobe_video(files["top"]) == ("hevc", 320, 240)
    assert ffprobe_video(files["bottom"]) == ("hevc", 320, 240)
    assert ffprobe_duration(files["top"]) == pytest.approx(8.0, abs=1.5)
    assert chroma_u(files["bottom"]) > 200  # a lente de baixo é a azul
    assert chroma_u(files["top"]) < 170
    assert not list((data / "exportacoes").glob("exportacao-*"))  # temporários apagados após o envio
