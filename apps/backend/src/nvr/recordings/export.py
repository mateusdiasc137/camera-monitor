"""Download de um intervalo escolhido na linha do tempo, inteiro ou só uma lente (ADR-006).

O servidor de playback do MediaMTX encerra o trecho no primeiro buraco. Sem buracos e com o quadro
inteiro, o MP4 vem direto dele. Com buracos (queda da câmera, pausa...) ou recorte de lente, o intervalo
é buscado em partes contínuas e montado num único MP4 pelo FFmpeg, sem recodificar.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

from nvr.cameras.domain import LensLayout, StreamProfile
from nvr.cameras.ports import CameraRepository
from nvr.recordings.application import MAX_CLIP, RecordingClip
from nvr.recordings.domain import ALIGN_MARGIN, Lens, Segment, align_to_recording, is_continuous, lens_crop
from nvr.recordings.ports import ExportTools, ExportWorkspace, PlaybackServer, SegmentIndex
from nvr.shared.errors import NotFound, ResourceBusy, ValidationFailed
from nvr.streaming.domain import path_name

log = logging.getLogger(__name__)

MAX_PARALLEL_EXPORTS = 2
MAX_PIECES = 500
MIN_PIECE_S = 0.2
STEP = timedelta(milliseconds=50)


class ExportRecording:
    def __init__(
        self,
        cameras: CameraRepository,
        index: SegmentIndex,
        playback: PlaybackServer,
        tools: ExportTools,
    ) -> None:
        self._cameras, self._index, self._playback, self._tools = cameras, index, playback, tools
        self._slots = asyncio.Semaphore(MAX_PARALLEL_EXPORTS)

    async def __call__(self, camera_id: str, start: datetime, duration_s: float, lens: Lens) -> RecordingClip:
        if start.tzinfo is None:
            raise ValidationFailed("informe o início com fuso horário (ISO 8601)")
        if not 0 < duration_s <= MAX_CLIP.total_seconds():
            raise ValidationFailed("duração deve ser maior que zero e de no máximo 2 h")
        camera = await self._cameras.get(camera_id)
        if camera is None:
            raise NotFound("câmera não encontrada")
        if lens is not Lens.BOTH and camera.lens_layout is not LensLayout.STACKED2:
            raise ValidationFailed("esta câmera tem uma lente só", {"lens": "use as duas (quadro inteiro)"})

        end = start + timedelta(seconds=duration_s)
        segments = await self._index.in_range(camera.id, start, end)
        aligned = align_to_recording(segments, start, end)
        if aligned is None:
            raise NotFound("não há gravação nesse trecho")
        path = path_name(camera.id, StreamProfile.MAIN)

        if lens is Lens.BOTH and is_continuous(segments, start, end):
            stream = await self._playback.open(path, aligned[0], aligned[1], "mp4")
            return RecordingClip(stream, camera.name, aligned[0], aligned[1], "mp4")

        if self._slots.locked():
            raise ResourceBusy("já há downloads sendo preparados; tente de novo em instantes")
        async with self._slots:
            workspace = self._tools.workspace()
            try:
                pieces, first_start, total = await self._fetch_pieces(workspace, path, segments, start, end)
                crop = None
                codec = "hevc"
                if lens is not Lens.BOTH:
                    geometry = await self._tools.geometry(pieces[0])
                    codec, crop = geometry.codec, lens_crop(lens, geometry.height)
                out = workspace.path("trecho.mp4")
                await self._tools.mux(pieces, out, codec, crop)
                result = await workspace.open_result(out)
            except BaseException:
                await workspace.discard()
                raise
        log.info("exportação: %s, %d parte(s), %.0f s, lente %s", camera.name, len(pieces), total, lens.value)
        return RecordingClip(result, camera.name, first_start, total, "mp4")

    async def _fetch_pieces(
        self, ws: ExportWorkspace, path: str, segments: list[Segment], start: datetime, end: datetime
    ) -> tuple[list[Path], datetime, float]:
        """Busca as partes contínuas do intervalo, uma por pedido, pulando os buracos pelo índice."""
        pieces: list[Path] = []
        first_start: datetime | None = None
        total = 0.0
        cursor = start
        for _ in range(MAX_PIECES):
            following = sorted((s for s in segments if s.end_at > cursor + STEP), key=lambda s: s.start_at)
            if not following:
                break
            nxt = following[0]
            piece_start = cursor if nxt.start_at <= cursor else nxt.start_at + ALIGN_MARGIN
            remaining = (end - piece_start).total_seconds()
            if remaining < MIN_PIECE_S:
                break
            try:
                stream = await self._playback.open(path, piece_start, remaining, "fmp4")
            except NotFound:
                cursor = nxt.end_at  # o servidor não achou: pular este segmento
                continue
            saved = await ws.save(stream, f"parte-{len(pieces):03d}.mp4")
            if saved.duration_s < MIN_PIECE_S:
                cursor = nxt.end_at
                continue
            pieces.append(saved.path)
            first_start = first_start or piece_start
            total += saved.duration_s
            cursor = piece_start + timedelta(seconds=saved.duration_s)
        if not pieces or first_start is None:
            raise NotFound("não há gravação nesse trecho")
        return pieces, first_start, total
