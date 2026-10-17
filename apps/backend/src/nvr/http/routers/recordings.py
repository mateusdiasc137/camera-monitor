"""Consulta de gravações: intervalos gravados e buracos (com motivo) numa janela de tempo."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from pydantic import AwareDatetime, BaseModel

from nvr.recordings.application import RecordingsView

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
