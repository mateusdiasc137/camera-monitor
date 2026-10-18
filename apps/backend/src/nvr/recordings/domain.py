"""Gravações: segmentos em disco, intervalos contínuos e buracos (ADR-006).

O banco guarda só metadados dos segmentos; o vídeo fica nos arquivos fMP4 do MediaMTX.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from enum import StrEnum
from itertools import pairwise
from pathlib import PurePath
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


# ------------------------------------------------------------- armazenamento

GIB = 1024**3
PROTECT_RECENT_S = 30.0  # arquivo mexido há menos que isso pode estar em gravação: nunca apagar


class ReplacementPolicy(StrEnum):
    OVERWRITE_OLDEST = "overwrite_oldest"  # apaga as gravações mais antigas (padrão)
    STOP_WHEN_FULL = "stop_when_full"  # pausa a gravação e avisa; não apaga nada


@dataclass(frozen=True)
class StoragePolicy:
    quota_bytes: int | None  # None: sem cota (só o piso de espaço livre vale)
    replacement: ReplacementPolicy
    high_watermark: float  # fração da cota que dispara a limpeza
    low_watermark: float  # fração da cota até onde a limpeza vai
    min_free_bytes: int  # piso físico de espaço livre no disco


@dataclass(frozen=True)
class DiskUsage:
    total_bytes: int
    free_bytes: int


@dataclass(frozen=True)
class StoredFile:
    """Arquivo de gravação em disco, candidato (ou não) à substituição."""

    rel_path: str
    folder: str
    start_at: datetime
    size_bytes: int
    modified_at: float  # epoch (s)


class StorageState(StrEnum):
    OK = "ok"
    CLEANING = "cleaning"  # acima do limite: apagando os mais antigos
    FULL = "full"  # limite atingido e nada (mais) pode ser apagado, ou política "parar"


@dataclass(frozen=True)
class CleanupPlan:
    state: StorageState
    reason: str  # "cota", "espaço livre" ou ""
    delete: tuple[StoredFile, ...]
    bytes_to_free: int
    stop_recording: bool


def protected_files(
    files: Sequence[StoredFile], now_epoch: float, active_folders: set[str] | None = None
) -> set[str]:
    """Os mexidos há pouco e o mais recente de cada pasta ativa (pode estar em gravação).

    Pastas de câmeras removidas (`active_folders` não as inclui) não gravam mais: o último arquivo delas
    pode ser apagado — senão gravações órfãs ocupariam a cota para sempre.
    """
    newest: dict[str, StoredFile] = {}
    for f in files:
        if active_folders is not None and f.folder not in active_folders:
            continue
        current = newest.get(f.folder)
        if current is None or f.start_at > current.start_at:
            newest[f.folder] = f
    recent = {f.rel_path for f in files if now_epoch - f.modified_at < PROTECT_RECENT_S}
    return {f.rel_path for f in newest.values()} | recent


def plan_cleanup(
    policy: StoragePolicy,
    files: Sequence[StoredFile],
    disk: DiskUsage,
    now_epoch: float,
    active_folders: set[str] | None = None,
) -> CleanupPlan:
    """Decide o que apagar (ou se a gravação deve parar). Função pura; ver ADR-006.

    Histerese: dispara acima de `high_watermark` da cota e limpa até `low_watermark`; o piso de espaço
    livre dispara abaixo de `min_free_bytes` e limpa até 125% dele.
    """
    used = sum(f.size_bytes for f in files)
    need = 0
    reasons: list[str] = []
    if policy.quota_bytes is not None and used > policy.quota_bytes * policy.high_watermark:
        need = max(need, used - int(policy.quota_bytes * policy.low_watermark))
        reasons.append("cota")
    if disk.free_bytes < policy.min_free_bytes:
        need = max(need, int(policy.min_free_bytes * 1.25) - disk.free_bytes)
        reasons.append("espaço livre")
    if need <= 0:
        return CleanupPlan(StorageState.OK, "", (), 0, False)

    reason = " e ".join(reasons)
    if policy.replacement is ReplacementPolicy.STOP_WHEN_FULL:
        return CleanupPlan(StorageState.FULL, reason, (), need, True)

    keep = protected_files(files, now_epoch, active_folders)
    chosen: list[StoredFile] = []
    freed = 0
    for f in sorted(files, key=lambda x: x.start_at):
        if freed >= need:
            break
        if f.rel_path in keep:
            continue
        chosen.append(f)
        freed += f.size_bytes
    state = StorageState.CLEANING if freed >= need else StorageState.FULL
    return CleanupPlan(state, reason, tuple(chosen), need, False)


def should_resume_recording(policy: StoragePolicy, used_bytes: int, disk: DiskUsage) -> bool:
    """Política "parar quando encher": só volta a gravar abaixo do limite inferior (histerese)."""
    below_quota = policy.quota_bytes is None or used_bytes <= policy.quota_bytes * policy.low_watermark
    return below_quota and disk.free_bytes >= policy.min_free_bytes * 1.25


# ------------------------------------------------------------- local de gravação (D7)


class LocationMode(StrEnum):
    MIGRATE = "migrate"  # move as gravações existentes para o novo local
    FRESH = "fresh"  # começa vazio: as antigas ficam na pasta anterior, fora do índice e da cota


class MigrationState(StrEnum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"  # parou com erro: retomar ou abandonar
    ABANDONED = "abandoned"  # o que faltava ficou no local antigo, fora do índice


@dataclass(frozen=True)
class StorageMigration:
    id: str
    from_location_id: str
    to_location_id: str
    state: MigrationState
    total_files: int
    total_bytes: int
    moved_files: int
    moved_bytes: int
    error: str
    started_at: datetime
    finished_at: datetime | None


def path_within(path: PurePath, roots: Iterable[PurePath]) -> bool:
    """`path` é uma das raízes ou está dentro de uma delas. Caminhos já resolvidos (sem links, sem '..')."""
    return any(path == root or path.is_relative_to(root) for root in roots)


def overlapping(a: PurePath, b: PurePath) -> bool:
    """Um local dentro do outro (ou o mesmo): a varredura de um enxergaria as pastas do outro."""
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


# `%` inicia variáveis no recordPath do MediaMTX; controle/aspas quebrariam a configuração.
_FORBIDDEN_IN_RECORD_DIR = re.compile(r"[%\x00-\x1f\"]")


def record_dir_problem(path: str) -> str | None:
    """Motivo pelo qual a pasta não pode ir para o `recordPath` do MediaMTX, ou None."""
    found = _FORBIDDEN_IN_RECORD_DIR.search(path)
    if found is None:
        return None
    char = found.group()
    shown = "%" if char == "%" else "aspas" if char == '"' else "caractere de controle"
    return f"o caminho não pode conter {shown} (o servidor de mídia não aceita)"


# ------------------------------------------------------------- exportação (download de trechos)

# Buraco maior que isso entre segmentos: o servidor de playback do MediaMTX encerra o trecho ali.
# Medido (v1.21.2): ~0,1 s entre segmentos normais é emendado; 0,6 s (gravador reiniciado) não.
JOIN_GAP = timedelta(seconds=0.3)
ALIGN_MARGIN = timedelta(milliseconds=1)  # o índice guarda µs; o MediaMTX, ns


class Lens(StrEnum):
    BOTH = "both"  # quadro inteiro (as duas lentes empilhadas, ou a única lente)
    TOP = "top"
    BOTTOM = "bottom"


@dataclass(frozen=True)
class Crop:
    """Linhas cortadas em cima e embaixo (janela de exibição do H.264/H.265, sem recodificar)."""

    top: int
    bottom: int


def lens_crop(lens: Lens, height: int) -> Crop | None:
    """Recorte de uma lente num quadro com duas lentes empilhadas. Metade par (croma 4:2:0)."""
    if lens is Lens.BOTH:
        return None
    half = (height // 4) * 2
    return Crop(0, height - half) if lens is Lens.TOP else Crop(half, 0)


def align_to_recording(
    segments: Sequence[Segment], start: datetime, end: datetime
) -> tuple[datetime, float] | None:
    """Início dentro de um buraco (o MediaMTX responde 404): avança até o próximo segmento, mantendo o
    fim. None se não houver gravação no intervalo."""
    if any(s.start_at <= start < s.end_at for s in segments):
        return start, (end - start).total_seconds()
    following = [s.start_at for s in segments if start < s.start_at < end]
    if not following:
        return None
    aligned = min(following) + ALIGN_MARGIN
    return aligned, (end - aligned).total_seconds()


def is_continuous(segments: Sequence[Segment], start: datetime, end: datetime) -> bool:
    """O intervalo está gravado sem buracos que o MediaMTX não emende (um único pedido basta)."""
    inside = sorted((s for s in segments if s.end_at > start and s.start_at < end), key=lambda s: s.start_at)
    if not inside or inside[0].start_at - start > JOIN_GAP or end - inside[-1].end_at > JOIN_GAP:
        return False
    return all(b.start_at - a.end_at <= JOIN_GAP for a, b in pairwise(inside))
