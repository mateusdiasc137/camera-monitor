"""Gravações: segmentos em disco, intervalos contínuos e buracos (ADR-006).

O banco guarda só metadados dos segmentos; o vídeo fica nos arquivos fMP4 do MediaMTX.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from enum import StrEnum
from zoneinfo import ZoneInfo

# Arquivos: <raiz>/cam-<id>-main/<AAAA-MM-DD_HH-MM-SS-ffffff>[<±HHMM|Z>].mp4 (recordPath com %z)
_FILENAME = re.compile(
    r"^(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})_(?P<h>\d{2})-(?P<mi>\d{2})-(?P<s>\d{2})-(?P<f>\d{6})"
    r"(?P<z>Z|[+-]\d{4})?\.mp4$"
)
RECORD_FILENAME_TEMPLATE = "%Y-%m-%d_%H-%M-%S-%f%z"  # para o recordPath do MediaMTX
MERGE_TOLERANCE = timedelta(seconds=2)
# Atraso máximo esperado entre o fato (ex.: gravação desligada) e o registro no histórico de estado:
# amostragem do monitor (5 s) + falha do relay detectada pelo FFmpeg (até ~11 s com câmera fora do ar).
STATUS_LAG = timedelta(seconds=15)


@dataclass(frozen=True)
class Segment:
    camera_id: str
    location_id: str
    rel_path: str  # relativo à raiz do local de gravação, com "/"
    start_at: datetime  # UTC
    duration_s: float
    size_bytes: int

    @property
    def end_at(self) -> datetime:
        return self.start_at + timedelta(seconds=self.duration_s)


@dataclass(frozen=True)
class RecordingInterval:
    start: datetime
    end: datetime

    @property
    def duration_s(self) -> float:
        return (self.end - self.start).total_seconds()


class GapReason(StrEnum):
    PAUSED = "paused"  # gravação desligada pelo usuário
    CAMERA_UNAVAILABLE = "camera_unavailable"  # câmera offline, senha recusada, servidor de mídia fora...
    DISABLED = "disabled"  # câmera desativada
    UNKNOWN = "unknown"  # sem registro do motivo (ex.: backend parado)


@dataclass(frozen=True)
class Gap:
    start: datetime
    end: datetime
    reason: GapReason


@dataclass(frozen=True)
class StatusPoint:
    """Mudança de estado da câmera relevante para explicar buracos (vem do histórico de estado)."""

    at: datetime
    state: str  # valores de CameraState
    recording: bool


def parse_segment_filename(name: str, local_tz: timezone | ZoneInfo | None = None) -> datetime | None:
    """Início do segmento pelo nome do arquivo (UTC). Nomes sem fuso usam o fuso local do servidor."""
    match = _FILENAME.fullmatch(name)
    if match is None:
        return None
    naive = datetime(
        int(match["y"]), int(match["mo"]), int(match["d"]),
        int(match["h"]), int(match["mi"]), int(match["s"]), int(match["f"]),
    )  # fmt: skip
    zone = match["z"]
    if zone == "Z":
        aware = naive.replace(tzinfo=UTC)
    elif zone:
        sign = 1 if zone[0] == "+" else -1
        aware = naive.replace(tzinfo=timezone(sign * timedelta(hours=int(zone[1:3]), minutes=int(zone[3:5]))))
    else:
        aware = naive.replace(tzinfo=local_tz) if local_tz else naive.astimezone()
    return aware.astimezone(UTC)


def merge_intervals(
    segments: Iterable[Segment], tolerance: timedelta = MERGE_TOLERANCE
) -> list[RecordingInterval]:
    """Une segmentos contíguos (ou sobrepostos) em intervalos.

    Separações maiores que `tolerance` viram buracos.
    """
    ordered = sorted(segments, key=lambda s: s.start_at)
    intervals: list[RecordingInterval] = []
    for segment in ordered:
        if segment.duration_s <= 0:
            continue
        if intervals and segment.start_at <= intervals[-1].end + tolerance:
            last = intervals[-1]
            intervals[-1] = RecordingInterval(last.start, max(last.end, segment.end_at))
        else:
            intervals.append(RecordingInterval(segment.start_at, segment.end_at))
    return intervals


def clip(intervals: Sequence[RecordingInterval], start: datetime, end: datetime) -> list[RecordingInterval]:
    return [
        RecordingInterval(max(i.start, start), min(i.end, end))
        for i in intervals
        if i.end > start and i.start < end
    ]


def _reason_at(moment: datetime, history: Sequence[StatusPoint]) -> GapReason:
    """Motivo pelo último estado conhecido no instante (o histórico vem ordenado por data)."""
    current: StatusPoint | None = None
    for point in history:
        if point.at <= moment:
            current = point
        else:
            break
    if current is None:
        return GapReason.UNKNOWN
    if current.state == "disabled":
        return GapReason.DISABLED
    if current.state in ("online", "degraded") and not current.recording:
        return GapReason.PAUSED
    if current.state in ("offline", "auth_error", "not_found", "starting"):
        return GapReason.CAMERA_UNAVAILABLE
    return GapReason.UNKNOWN


def find_gaps(
    intervals: Sequence[RecordingInterval],
    start: datetime,
    end: datetime,
    history: Sequence[StatusPoint] = (),
    min_gap: timedelta = MERGE_TOLERANCE,
) -> list[Gap]:
    """Buracos dentro de [start, end) entre os intervalos gravados, com o motivo provável.

    O motivo é o estado vigente um pouco DEPOIS do início do buraco: o monitor de estado amostra a
    cada poucos segundos, então a transição (ex.: gravação desligada) é registrada com atraso.
    """

    def split(gap_start: datetime, gap_end: datetime) -> list[Gap]:
        """Um buraco pode ter várias causas em sequência: divide nas mudanças de estado."""
        probe = gap_start + min(STATUS_LAG, (gap_end - gap_start) / 2)
        pieces = [Gap(gap_start, gap_end, _reason_at(probe, history))]
        for point in history:
            if probe < point.at < gap_end:
                reason = _reason_at(point.at, history)
                last = pieces[-1]
                if reason is last.reason:
                    continue
                pieces[-1] = Gap(last.start, point.at, last.reason)
                pieces.append(Gap(point.at, gap_end, reason))
        return pieces

    gaps: list[Gap] = []
    cursor = start
    for interval in clip(intervals, start, end):
        if interval.start - cursor > min_gap:
            gaps.extend(split(cursor, interval.start))
        cursor = max(cursor, interval.end)
    if end - cursor > min_gap:
        gaps.extend(split(cursor, end))
    return gaps
