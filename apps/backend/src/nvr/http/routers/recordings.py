"""Consulta de gravações: intervalos gravados e buracos (com motivo) numa janela de tempo."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import AwareDatetime, BaseModel

from nvr.recordings.application import RecordingsView
from nvr.recordings.domain import Lens
from nvr.shared.errors import ValidationFailed

router = APIRouter(prefix="/recordings", tags=["recordings"])


class IntervalOut(BaseModel):
    start: datetime
    end: datetime


class GapOut(BaseModel):
    start: datetime
    end: datetime
    reason: Literal["paused", "camera_unavailable", "disabled", "unknown"]


class RecordingsOut(BaseModel):
    camera_id: str
    start: datetime
    end: datetime
    intervals: list[IntervalOut]
    gaps: list[GapOut]
    segments: int
    recorded_s: float
    recording_now: bool

    @classmethod
    def from_view(cls, v: RecordingsView) -> RecordingsOut:
        return cls(
            camera_id=v.camera_id,
            start=v.start,
            end=v.end,
            intervals=[IntervalOut(start=i.start, end=i.end) for i in v.intervals],
            gaps=[GapOut(start=g.start, end=g.end, reason=g.reason.value) for g in v.gaps],
            segments=v.segments,
            recorded_s=round(v.recorded_s, 3),
            recording_now=v.recording_now,
        )


@router.get("", response_model=RecordingsOut)
async def list_recordings(
    request: Request,
    camera_id: str,
    start: Annotated[AwareDatetime | None, Query(description="padrão: 24 h antes do fim")] = None,
    end: Annotated[AwareDatetime | None, Query(description="padrão: agora")] = None,
) -> RecordingsOut:
    end = end or datetime.now().astimezone()
    start = start or end - timedelta(hours=24)
    view = await request.app.state.use_cases.list_recordings(camera_id, start, end)
    return RecordingsOut.from_view(view)


LENS_SUFFIX = {Lens.BOTH: "", Lens.TOP: "_superior", Lens.BOTTOM: "_inferior"}


def _download_name(camera_name: str, start: datetime, lens: Lens = Lens.BOTH) -> str:
    """Nome ASCII seguro para o cabeçalho: "camera-da-frente_2026-10-17_18-04-57_superior.mp4"."""
    ascii_name = unicodedata.normalize("NFKD", camera_name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ascii_name).strip("-").lower() or "camera"
    return f"{slug}_{start.astimezone().strftime('%Y-%m-%d_%H-%M-%S')}{LENS_SUFFIX[lens]}.mp4"


@router.get(
    "/{camera_id}/video",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"video/mp4": {}}, "description": "trecho gravado (MP4 fragmentado ou comum)"}
    },
)
async def recording_video(
    request: Request,
    camera_id: str,
    start: AwareDatetime,
    duration: Annotated[float, Query(gt=0, le=7200, description="segundos")],
    format: Literal["fmp4", "mp4"] = "fmp4",
    download: bool = False,
    lens: Annotated[
        Literal["both", "top", "bottom"],
        Query(description="download: quadro inteiro ou só a lente de cima/de baixo (câmeras de 2 lentes)"),
    ] = "both",
) -> StreamingResponse:
    """Reprodução (fmp4, progressivo) ou download (mp4 comum) de um trecho.

    O download cobre o intervalo inteiro, juntando os trechos gravados se houver buracos, e pode trazer
    só uma lente (recorte sem recodificar). Sem suporte a Range: para pular, peça outro início.
    """
    use_cases = request.app.state.use_cases
    chosen = Lens(lens)
    headers = {"Cache-Control": "no-store"}
    if download:
        clip = await use_cases.export_recording(camera_id, start, duration, chosen)
        name = _download_name(clip.camera_name, clip.start, chosen)
        headers["Content-Disposition"] = f'attachment; filename="{name}"'
    elif chosen is not Lens.BOTH:
        raise ValidationFailed("o recorte de lente vale só para download", {"lens": "use download=true"})
    else:
        clip = await use_cases.open_recording(camera_id, start, duration, format)
    size = getattr(clip.stream, "size", None)
    if isinstance(size, int):
        headers["Content-Length"] = str(size)
    return StreamingResponse(clip.stream.chunks(), media_type="video/mp4", headers=headers)
