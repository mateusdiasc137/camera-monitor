from datetime import UTC, datetime, timedelta

from nvr.system.application import GetSystemHealth
from nvr.system.domain import (
    CameraSummary,
    ComponentHealth,
    HealthState,
    SystemHealth,
    cameras_component,
    recording_component,
    recording_late,
)


class FixedProbe:
    def __init__(self, name: str, state: HealthState) -> None:
        self._health = ComponentHealth(name, state)

    async def check(self) -> ComponentHealth:
        return self._health


def test_without_components_is_ok() -> None:
    assert SystemHealth("1", ()).state is HealthState.OK


def test_worst_component_wins() -> None:
    health = SystemHealth(
        "1", (ComponentHealth("db", HealthState.OK), ComponentHealth("media", HealthState.DEGRADED))
    )
    assert health.state is HealthState.DEGRADED
    down = SystemHealth("1", (*health.components, ComponentHealth("disk", HealthState.DOWN)))
    assert down.state is HealthState.DOWN


async def test_use_case_aggregates_probes() -> None:
    use_case = GetSystemHealth(
        "9.9", [FixedProbe("db", HealthState.OK), FixedProbe("media", HealthState.DOWN)]
    )
    health = await use_case()
    assert health.version == "9.9"
    assert [c.name for c in health.components] == ["db", "media"]
    assert health.state is HealthState.DOWN


NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def cam(name: str, state: str = "online", *, last_min: float | None = 0.5, since_min: float = 60,
        expected: bool = True) -> CameraSummary:  # fmt: skip
    return CameraSummary(
        camera_id=name,
        name=name,
        state=state,
        detail="",
        since=NOW - timedelta(minutes=since_min),
        recording_expected=expected,
        recording=expected,
        last_recording_at=None if last_min is None else NOW - timedelta(minutes=last_min),
    )


def test_recording_late_only_for_online_cameras_that_should_record() -> None:
    assert recording_late(cam("a", last_min=1), NOW) is None
    assert recording_late(cam("a", last_min=10), NOW) == timedelta(minutes=10)
    assert recording_late(cam("a", last_min=10, expected=False), NOW) is None  # gravação desligada
    assert recording_late(cam("a", "offline", last_min=10), NOW) is None  # o problema é a câmera
    assert recording_late(cam("a", last_min=60, since_min=1), NOW) is None  # acabou de reconectar
    assert recording_late(cam("a", last_min=None, since_min=1), NOW) is None  # recém-cadastrada
    assert recording_late(cam("a", last_min=None, since_min=10), NOW) == timedelta(minutes=10)


def test_cameras_component_counts_and_names_problems() -> None:
    assert cameras_component([]).detail == "nenhuma câmera cadastrada"
    ok = cameras_component([cam("Quintal"), cam("Frente")])
    assert (ok.state, ok.detail) == (HealthState.OK, "2 online")
    bad = cameras_component([cam("Quintal"), cam("Garagem", "auth_error"), cam("Rua", "disabled")])
    assert bad.state is HealthState.DEGRADED
    assert "Garagem (senha recusada)" in bad.detail
    assert "1 desativada" in bad.detail
    assert "Rua" not in bad.detail.split("—")[1]  # desativada não é problema


def test_recording_component_flags_cameras_without_new_files() -> None:
    fine = recording_component([cam("Quintal", last_min=0.5)], NOW)
    assert (fine.state, fine.detail) == (HealthState.OK, "1 câmera(s) gravando; último trecho há 30 s")
    late = recording_component([cam("Quintal", last_min=12), cam("Frente")], NOW)
    assert late.state is HealthState.DEGRADED
    assert late.detail == "sem gravar: Quintal (sem arquivo novo há 12 min)"
    offline = recording_component([cam("Quintal", "offline"), cam("Rua", "starting")], NOW)
    assert offline.detail == "sem gravar: Quintal (offline)"  # "conectando" é passageiro
    off = recording_component([cam("Quintal", expected=False)], NOW)
    assert off.detail == "nenhuma câmera com gravação ligada"


async def test_use_case_adds_camera_components() -> None:
    class Summaries:
        async def __call__(self) -> list[CameraSummary]:
            return [cam("Quintal", last_min=20)]

    class Clock:
        def now(self) -> datetime:
            return NOW

    health = await GetSystemHealth("1", [FixedProbe("db", HealthState.OK)], Summaries(), Clock())()  # type: ignore[arg-type]
    assert [c.name for c in health.components] == ["db", "cameras", "recording"]
    assert health.state is HealthState.DEGRADED
    assert health.checked_at == NOW
    assert health.cameras[0].name == "Quintal"
