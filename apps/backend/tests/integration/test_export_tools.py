"""Montagem de downloads com o FFmpeg real: junção de partes e recorte de lente em H.264, sem recodificar."""

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from nvr.recordings.adapters.ffmpeg_export import FfmpegExportTools
from nvr.recordings.domain import Lens, lens_crop

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg não instalado"),
]


def make_piece(path: Path, seconds: int) -> None:
    """fMP4 como o do MediaMTX: duas "lentes" empilhadas (320x480), H.264, fragmentado."""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=10:duration={seconds}",
         "-f", "lavfi", "-i", f"color=c=blue:size=320x240:rate=10:duration={seconds}",
         "-filter_complex", "[0][1]vstack", "-c:v", "libx264", "-g", "10", "-pix_fmt", "yuv420p",
         "-movflags", "frag_keyframe+empty_moov+default_base_moof", "-f", "mp4", "-y", str(path)],
        check=True,
    )  # fmt: skip


def probe(path: Path) -> list[str]:
    return subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height:format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.split()  # fmt: skip


def test_mux_joins_pieces_and_crops_h264(tmp_path: Path) -> None:
    tools = FfmpegExportTools("ffmpeg", tmp_path / "exportacoes")
    workspace = tools.workspace()
    pieces = [workspace.path("a.mp4"), workspace.path("b.mp4")]
    make_piece(pieces[0], 2)
    make_piece(pieces[1], 3)

    async def run() -> tuple[str, int, int, bytes]:
        geometry = await tools.geometry(pieces[0])
        out = workspace.path("trecho.mp4")
        await tools.mux(pieces, out, geometry.codec, lens_crop(Lens.BOTTOM, geometry.height))
        result = await workspace.open_result(out)
        data = b"".join([chunk async for chunk in result.chunks()])  # apaga a pasta ao terminar
        return geometry.codec, geometry.width, geometry.height, data

    codec, width, height, data = asyncio.run(run())
    assert (codec, width, height) == ("h264", 320, 480)
    final = tmp_path / "final.mp4"
    final.write_bytes(data)
    stream, duration = probe(final)
    assert stream == "h264,320,240"
    assert float(duration) == pytest.approx(5.0, abs=0.5)
    assert not list((tmp_path / "exportacoes").iterdir())


def test_mux_failure_is_reported_without_leaking_files(tmp_path: Path) -> None:
    tools = FfmpegExportTools("ffmpeg", tmp_path / "exportacoes")
    workspace = tools.workspace()
    broken = workspace.path("quebrado.mp4")
    broken.write_bytes(b"isto nao e um video")

    async def run() -> None:
        try:
            await tools.mux([broken], workspace.path("trecho.mp4"), "hevc", None)
        finally:
            await workspace.discard()

    with pytest.raises(Exception, match="não foi possível montar"):
        asyncio.run(run())
    assert not list((tmp_path / "exportacoes").iterdir())
