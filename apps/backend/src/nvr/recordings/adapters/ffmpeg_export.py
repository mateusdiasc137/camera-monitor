"""Montagem de downloads com o FFmpeg: junta partes fMP4 e recorta uma lente, sempre sem recodificar.

O recorte usa a janela de exibição do próprio H.265/H.264 (`hevc_metadata`/`h264_metadata`): o arquivo
continua com a imagem inteira codificada, mas os players (Chrome, VLC, FFmpeg) mostram só a lente.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import tempfile
import time
from collections.abc import AsyncIterator
from pathlib import Path

from nvr.recordings.adapters.fmp4_probe import Fmp4Error, probe_file, probe_geometry
from nvr.recordings.domain import Crop
from nvr.recordings.ports import RecordingStream, SavedPiece, VideoGeometry
from nvr.shared.errors import MediaServerUnavailable

log = logging.getLogger(__name__)

CHUNK = 256 * 1024
PREFIX = "exportacao-"
STALE_AFTER_S = 6 * 3600  # sobras de exportações interrompidas (queda do processo)
BSF = {"hevc": "hevc_metadata", "h264": "h264_metadata"}


class _FileStream:
    """Envia o arquivo final e apaga a pasta da exportação ao terminar (ou se o download for cancelado)."""

    content_type = "video/mp4"

    def __init__(self, path: Path, workspace: _Workspace) -> None:
        self._path, self._workspace = path, workspace
        self.size = path.stat().st_size

    async def chunks(self) -> AsyncIterator[bytes]:
        try:
            with self._path.open("rb") as fh:
                while chunk := await asyncio.to_thread(fh.read, CHUNK):
                    yield chunk
        finally:
            await self._workspace.discard()

    async def aclose(self) -> None:
        await self._workspace.discard()


class _Workspace:
    def __init__(self, root: Path) -> None:
        self._dir = Path(tempfile.mkdtemp(prefix=PREFIX, dir=root))

    def path(self, name: str) -> Path:
        return self._dir / name

    async def save(self, stream: RecordingStream, name: str) -> SavedPiece:
        target = self.path(name)
        try:
            with target.open("wb") as fh:
                async for chunk in stream.chunks():
                    await asyncio.to_thread(fh.write, chunk)
        finally:
            await stream.aclose()
        try:
            duration = (await asyncio.to_thread(probe_file, target)).duration_s
        except (Fmp4Error, OSError):
            duration = 0.0
        return SavedPiece(target, duration)

    async def open_result(self, path: Path) -> RecordingStream:
        return _FileStream(path, self)

    async def discard(self) -> None:
        await asyncio.to_thread(shutil.rmtree, self._dir, True)


class FfmpegExportTools:
    def __init__(self, ffmpeg_path: str, root: Path) -> None:
        self._ffmpeg, self._root = ffmpeg_path, root

    def _sweep(self) -> None:
        """Apaga pastas de exportações antigas que ficaram para trás (processo encerrado no meio)."""
        if not self._root.is_dir():
            return
        for folder in self._root.glob(f"{PREFIX}*"):
            with contextlib.suppress(OSError):
                if folder.is_dir() and time.time() - folder.stat().st_mtime > STALE_AFTER_S:
                    shutil.rmtree(folder, ignore_errors=True)

    def workspace(self) -> _Workspace:
        self._root.mkdir(parents=True, exist_ok=True)
        self._sweep()
        return _Workspace(self._root)

    async def geometry(self, piece: Path) -> VideoGeometry:
        try:
            codec, width, height = await asyncio.to_thread(probe_geometry, piece)
        except Fmp4Error as exc:
            raise MediaServerUnavailable(f"não foi possível ler o vídeo gravado: {exc}") from exc
        return VideoGeometry(codec, width, height)

    async def mux(self, pieces: list[Path], out: Path, codec: str, crop: Crop | None) -> None:
        listing = out.with_suffix(".txt")
        listing.write_text(
            "".join("file '{}'\n".format(p.as_posix().replace("'", "'\\''")) for p in pieces),
            encoding="utf-8",
        )
        args = [self._ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                "-f", "concat", "-safe", "0", "-i", str(listing),
                "-map", "0:v:0", "-map", "0:a?", "-c", "copy"]  # fmt: skip
        if crop is not None:
            if codec not in BSF:
                raise MediaServerUnavailable(f"recorte não suportado para {codec}")
            args += ["-bsf:v", f"{BSF[codec]}=crop_top={crop.top}:crop_bottom={crop.bottom}"]
        args += ["-movflags", "+faststart", "-f", "mp4", "-y", str(out)]
        try:
            process = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
            )
        except OSError as exc:
            raise MediaServerUnavailable(f"FFmpeg indisponível ({type(exc).__name__})") from exc
        try:
            _, stderr = await process.communicate()
        except BaseException:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
            raise
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()[-300:]
            log.warning("FFmpeg falhou ao montar o download: %s", detail)
            raise MediaServerUnavailable("não foi possível montar o vídeo para download")
