import asyncio
import sys
from datetime import UTC, datetime, timedelta

import pytest

from nvr.cameras.domain import Camera, CameraDriver, IngestMode
from nvr.shared.errors import MediaServerUnavailable
from nvr.shared.events import Event, EventBus
from nvr.streaming.adapters.ffmpeg_relay import FfmpegRelaySupervisor, classify_failure
from nvr.streaming.application import MonitorCameraHealth
from nvr.streaming.domain import (
    CameraHealth,
    CameraState,
    PathState,
    ProfileSignals,
    RelayFailure,
    RelaySpec,
    RelayStatus,
    derive_health,
)
from tests.fakes import FakeClock

CAM = "0b6f1c2e-1111-4a3b-9c55-123456789abc"
MAIN, SUB = f"cam-{CAM}-main", f"cam-{CAM}-sub"


def sig(**kw: object) -> ProfileSignals:
    values: dict[str, object] = {
        "path_ready": True,
        "bytes_growing": True,
        "relay_running": True,
        "relay_age_s": 120.0,
        "relay_failure": None,
        "recent_restarts": 0,
    }
    values.update(kw)
    return ProfileSignals(**values)  # type: ignore[arg-type]


# ------------------------------------------------------------- derive_health


@pytest.mark.parametrize(
    ("main", "sub", "has_sub", "expected"),
    [
        (sig(), sig(), True, CameraState.ONLINE),
        (sig(), None, False, CameraState.ONLINE),
        (sig(bytes_growing=False), sig(), True, CameraState.DEGRADED),
        (sig(), sig(path_ready=False), True, CameraState.DEGRADED),
        (sig(recent_restarts=3), sig(), True, CameraState.DEGRADED),
        (sig(path_ready=False, relay_age_s=3.0), None, False, CameraState.STARTING),
        (sig(path_ready=False, relay_failure=RelayFailure.AUTH), None, False, CameraState.AUTH_ERROR),
        (sig(path_ready=False, relay_failure=RelayFailure.NOT_FOUND), None, False, CameraState.NOT_FOUND),
        (sig(path_ready=False, relay_failure=RelayFailure.UNREACHABLE), None, False, CameraState.OFFLINE),
        # recém-reiniciado, mas a falha anterior continua valendo: não "pisca" para conectando
        (sig(path_ready=False, relay_age_s=2.0, relay_failure=RelayFailure.UNREACHABLE), None, False,
         CameraState.OFFLINE),
        (sig(path_ready=False, relay_running=False, relay_age_s=None), None, False, CameraState.OFFLINE),
        (None, None, False, CameraState.STARTING),
    ],
)  # fmt: skip
def test_derive_health(main: ProfileSignals | None, sub: ProfileSignals | None, has_sub: bool,
                       expected: CameraState) -> None:  # fmt: skip
    health = derive_health(CAM, enabled=True, record_enabled=True, main=main, sub=sub, has_sub=has_sub)
    assert health.state is expected


def test_disabled_camera_and_recording_flag() -> None:
    assert derive_health(
        CAM, enabled=False, record_enabled=True, main=sig(), sub=None, has_sub=False
    ).state is (CameraState.DISABLED)
    online = derive_health(CAM, enabled=True, record_enabled=False, main=sig(), sub=None, has_sub=False)
    assert online.recording is False
    assert derive_health(
        CAM, enabled=True, record_enabled=True, main=sig(), sub=None, has_sub=False
    ).recording


# ------------------------------------------------- classificação das falhas


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        # Mensagens reais observadas (X6C-WEQ com senha errada / porta fechada no Windows).
        (["[rtsp @ 0x1] method DESCRIBE failed: 401 Unauthorized",
          "rtsp://***@10.0.0.210/x: Server returned 401 Unauthorized (authorization failed)"],
         RelayFailure.AUTH),
        (["[in#0 @ 0x1] Error opening input: Error number -138 occurred"], RelayFailure.UNREACHABLE),
        (["[rtsp @ 0x1] method DESCRIBE failed: 404 Not Found"], RelayFailure.NOT_FOUND),
        (["Connection to tcp://10.0.0.210:554 failed: Connection refused"], RelayFailure.UNREACHABLE),
        # 401 na SAÍDA (MediaMTX recusou a publicação) não é culpa da câmera.
        (["[rtsp @ 0x2] method ANNOUNCE failed: 401 Unauthorized",
          "Could not write header for output file #0"], RelayFailure.PUBLISH),
        (["algo inesperado"], RelayFailure.OTHER),
        ([], None),
    ],
)  # fmt: skip
def test_classify_failure(lines: list[str], expected: RelayFailure | None) -> None:
    assert classify_failure(lines) is expected


def test_refused_connection_to_media_server_is_not_blamed_on_camera() -> None:
    lines = ["[tcp @ 0x1] Connection to tcp://127.0.0.1:18554?timeout=0 failed: Connection refused"]
    assert classify_failure(lines, "127.0.0.1:18554") is RelayFailure.PUBLISH
    camera_side = ["[tcp @ 0x1] Connection to tcp://10.0.0.210:554?timeout=0 failed: Connection refused"]
    assert classify_failure(camera_side, "127.0.0.1:18554") is RelayFailure.UNREACHABLE


# Saídas reais do FFmpeg 9 (Windows) no teste ponta a ponta com a X6C-WEQ, 17/10/2026.
MEDIAMTX_DIED_MID_STREAM = [
    "[rtsp @ 00000296500701c0] Failed reading RTSP data: Error number -10054 occurred",
    "[vost#0:0/copy @ 000002964e58de00] Error submitting a packet to the muxer: Broken pipe",
    "Last message repeated 1 times",
    "[out#0/rtsp @ 000002964e54ec40] Error muxing a packet",
    "[out#0/rtsp @ 000002964e54ec40] Task finished with error code: -32 (Broken pipe)",
    "[out#0/rtsp @ 000002964e54ec40] Terminating thread with return code -32 (Broken pipe)",
    "[out#0/rtsp @ 000002964e54ec40] Error writing trailer: Broken pipe",
]
MEDIAMTX_WITHOUT_PATH_YET = [
    "[out#0/rtsp @ 00000217d2e2d0c0] Could not write header (incorrect codec parameters ?): "
    "Server returned 400 Bad Request",
]
CAMERA_WRONG_PASSWORD = [
    "[in#0 @ 0000019a8cbae7c0] method DESCRIBE failed: 401 (Unauthorized)",
    "[in#0 @ 0000019a8cbae1c0] Error opening input: Server returned 401 Unauthorized (authorization failed)",
    "Error opening input file ***.",
    "Error opening input files: Server returned 401 Unauthorized (authorization failed)",
]


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        (MEDIAMTX_DIED_MID_STREAM, RelayFailure.PUBLISH),
        (MEDIAMTX_WITHOUT_PATH_YET, RelayFailure.PUBLISH),
        (CAMERA_WRONG_PASSWORD, RelayFailure.AUTH),
        (["[in#0/rtsp @ 0x1] Error during demuxing: Error number -10054 occurred"], RelayFailure.UNREACHABLE),
    ],
)
def test_classify_real_ffmpeg_output(lines: list[str], expected: RelayFailure) -> None:
    assert classify_failure(lines, "127.0.0.1:18554") is expected


async def test_auth_failure_slows_retries_to_protect_camera_account() -> None:
    script = "import sys; print('method DESCRIBE failed: 401 Unauthorized', file=sys.stderr); sys.exit(1)"
    sup = FfmpegRelaySupervisor(
        "ffmpeg", "127.0.0.1:8554", "relay", "s", command=[sys.executable, "-c", script],
        backoff=(0.05, 0.1), auth_backoff=30.0,
    )  # fmt: skip
    sup.ensure(RelaySpec(MAIN, "rtsp://x"))
    for _ in range(100):
        if sup.statuses()[MAIN].restarts >= 1:
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(1.0)  # com backoff normal haveria ~10 reinícios aqui
    status = sup.statuses()[MAIN]
    await sup.stop_all()
    assert status.failure is RelayFailure.AUTH
    assert status.restarts == 1


# ------------------------------------------------------------------ monitor


class FakeCameras:
    def __init__(self, *cameras: Camera) -> None:
        self.items = {c.id: c for c in cameras}

    async def list(self) -> list[Camera]:
        return list(self.items.values())


class FakeMedia:
    def __init__(self) -> None:
        self.states_map: dict[str, PathState] = {}
        self.down = False

    async def states(self) -> dict[str, PathState]:
        if self.down:
            raise MediaServerUnavailable("MediaMTX não respondeu")
        return dict(self.states_map)


class FakeRelays:
    def __init__(self) -> None:
        self.map: dict[str, RelayStatus] = {}

    def statuses(self) -> dict[str, RelayStatus]:
        return dict(self.map)


class MemoryLog:
    def __init__(self) -> None:
        self.rows: list[tuple[str, datetime, CameraHealth]] = []

    async def record(self, camera_id: str, at: datetime, health: CameraHealth) -> None:
        self.rows.append((camera_id, at, health))

    async def latest(self) -> dict[str, tuple[datetime, CameraHealth]]:
        return {cid: (at, h) for cid, at, h in self.rows}

    async def history(self, camera_id: str, limit: int) -> list[tuple[datetime, CameraHealth]]:
        return [(at, h) for cid, at, h in reversed(self.rows) if cid == camera_id][:limit]


def camera() -> Camera:
    now = datetime(2026, 10, 17, tzinfo=UTC)
    return Camera(CAM, "Quintal", CameraDriver.XIONGMAI, "10.0.0.210", 554, "/a", None, f"camera:{CAM}",
                  IngestMode.RELAY, True, True, now, now)  # fmt: skip


class Recorder:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def publish(self, event: Event) -> None:
        self.events.append(event)


MonitorParts = tuple[MonitorCameraHealth, FakeMedia, FakeRelays, MemoryLog, Recorder, FakeClock]


def monitor(log: MemoryLog | None = None) -> MonitorParts:
    media, relays, log, events, clock = FakeMedia(), FakeRelays(), log or MemoryLog(), Recorder(), FakeClock()
    relays.map[MAIN] = RelayStatus(MAIN, True, 0, None, "", None, 60.0, 0)
    mon = MonitorCameraHealth(FakeCameras(camera()), media, relays, log, events, clock)  # type: ignore[arg-type]
    return mon, media, relays, log, events, clock


async def test_records_and_publishes_only_transitions() -> None:
    mon, media, _, log, events, clock = monitor()
    media.states_map[MAIN] = PathState(MAIN, True, 1000, 0, ("H265", "G711"))
    await mon()
    for step in range(3):
        clock.advance(timedelta(seconds=5))
        media.states_map[MAIN] = PathState(MAIN, True, 2000 + step * 1000, 0, ("H265",))
        await mon()
    assert [h.state for _, _, h in log.rows] == [CameraState.ONLINE]
    assert len(events.events) == 1
    assert events.events[0].type == "camera.status"
    assert events.events[0].data["state"] == "online"
    assert mon.current()[CAM].health.recording is True


async def test_stalled_bytes_then_media_server_down() -> None:
    mon, media, _, log, _, clock = monitor()
    media.states_map[MAIN] = PathState(MAIN, True, 1000, 0, ())
    await mon()
    clock.advance(timedelta(seconds=5))
    await mon()  # mesmos bytes: parou de chegar dado
    media.down = True
    clock.advance(timedelta(seconds=5))
    await mon()
    assert [h.state for _, _, h in log.rows] == [
        CameraState.ONLINE,
        CameraState.DEGRADED,
        CameraState.OFFLINE,
    ]
    assert "MediaMTX" in log.rows[-1][2].detail
    assert mon.current()[CAM].since == clock.now()


async def test_media_server_recovery_triggers_immediate_reconcile() -> None:
    mon, media, *_ = monitor()
    triggered: list[bool] = []
    mon.on_media_recovered = lambda: triggered.append(True)
    media.states_map[MAIN] = PathState(MAIN, True, 1000, 0, ())
    await mon()
    media.down = True
    await mon()
    await mon()
    assert triggered == []
    media.down = False
    await mon()
    await mon()
    assert triggered == [True]  # uma vez, na volta


async def test_restart_does_not_duplicate_last_known_state() -> None:
    log = MemoryLog()
    first, media, *_ = monitor(log)
    media.states_map[MAIN] = PathState(MAIN, True, 1000, 0, ())
    await first()
    second, media2, *_ = monitor(log)  # backend reiniciado: carrega o último estado do histórico
    media2.states_map[MAIN] = PathState(MAIN, True, 5000, 0, ())
    await second()
    assert len(log.rows) == 1


async def test_event_bus_drops_oldest_for_slow_subscribers() -> None:
    bus = EventBus(queue_size=2)
    async with bus.subscribe() as queue:
        for i in range(5):
            bus.publish(Event("x", {"i": i}))
        assert [queue.get_nowait().data["i"] for _ in range(2)] == [3, 4]
    assert bus.subscriber_count == 0
