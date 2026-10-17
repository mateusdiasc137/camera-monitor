from nvr.system.application import GetSystemHealth
from nvr.system.domain import ComponentHealth, HealthState, SystemHealth


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
