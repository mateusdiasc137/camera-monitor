"""Saúde agregada do sistema."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum


class HealthState(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass(frozen=True)
class ComponentHealth:
    name: str
    state: HealthState
    detail: str = ""


@dataclass(frozen=True)
class SystemHealth:
    version: str
    components: tuple[ComponentHealth, ...]
    cameras: tuple[CameraSummary, ...] = field(default=())
    checked_at: datetime | None = None

    @property
    def state(self) -> HealthState:
        """O pior estado entre os componentes; sem componentes, o sistema está ok."""
        states = {c.state for c in self.components}
        if HealthState.DOWN in states:
            return HealthState.DOWN
        if HealthState.DEGRADED in states:
            return HealthState.DEGRADED
        return HealthState.OK


# ------------------------------------------------------------- câmeras e gravação (F1-12)

# Com a câmera online e a gravação ligada, o índice (varredura a cada 30 s) mostra o segmento aberto
# crescendo: passar disso sem arquivo novo indica falha na escrita (permissão, disco, servidor de mídia).
RECORDING_LATE = timedelta(minutes=3)

STATE_TEXT = {
    "online": "online",
    "degraded": "com problemas",
    "starting": "conectando",
    "offline": "offline",
    "auth_error": "senha recusada",
    "not_found": "caminho não encontrado",
    "disabled": "desativada",
}
_PROBLEM = {"offline", "auth_error", "not_found", "degraded"}


@dataclass(frozen=True)
class CameraSummary:
    camera_id: str
    name: str
    state: str  # um de STATE_TEXT
    detail: str
    since: datetime | None  # desde quando está nesse estado
    recording_expected: bool  # ativa, gravação ligada e não pausada por falta de espaço
    recording: bool  # o servidor de mídia está gravando o principal agora
    last_recording_at: datetime | None  # fim do último trecho indexado


def recording_late(camera: CameraSummary, now: datetime) -> timedelta | None:
    """Há quanto tempo falta gravação de uma câmera online que deveria gravar (None = em dia)."""
    if not camera.recording_expected or camera.state != "online":
        return None
    reference = max((t for t in (camera.last_recording_at, camera.since) if t is not None), default=None)
    if reference is None:
        return None
    late = now - reference
    return late if late > RECORDING_LATE else None


def _ago(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    if seconds < 120:
        return f"{seconds} s"
    if seconds < 7200:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h"


def cameras_component(cameras: Sequence[CameraSummary]) -> ComponentHealth:
    if not cameras:
        return ComponentHealth("cameras", HealthState.OK, "nenhuma câmera cadastrada")
    counts = Counter(c.state for c in cameras)
    parts = [f"{n} {STATE_TEXT.get(state, state)}" for state, n in counts.most_common()]
    problems = [c for c in cameras if c.state in _PROBLEM]
    detail = ", ".join(parts)
    if problems:
        names = ", ".join(f"{c.name} ({STATE_TEXT.get(c.state, c.state)})" for c in problems)
        return ComponentHealth("cameras", HealthState.DEGRADED, f"{detail} — {names}")
    return ComponentHealth("cameras", HealthState.OK, detail)


def recording_component(cameras: Sequence[CameraSummary], now: datetime) -> ComponentHealth:
    expected = [c for c in cameras if c.recording_expected and c.state != "disabled"]
    if not expected:
        return ComponentHealth("recording", HealthState.OK, "nenhuma câmera com gravação ligada")
    # Deveriam gravar e não estão: câmera com problema ("conectando" é passageiro) ou sem arquivo novo.
    missing: list[str] = []
    for c in expected:
        if c.state in _PROBLEM:
            missing.append(f"{c.name} ({STATE_TEXT[c.state]})")
        elif (late := recording_late(c, now)) is not None:
            missing.append(f"{c.name} (sem arquivo novo há {_ago(late)})")
    if missing:
        return ComponentHealth("recording", HealthState.DEGRADED, f"sem gravar: {', '.join(missing)}")
    recent = [c.last_recording_at for c in expected if c.last_recording_at is not None]
    last = f"; último trecho há {_ago(now - max(recent))}" if recent else ""
    return ComponentHealth("recording", HealthState.OK, f"{len(expected)} câmera(s) gravando{last}")
