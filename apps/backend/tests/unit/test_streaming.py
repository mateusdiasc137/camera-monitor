from dataclasses import replace
from datetime import UTC, datetime

import pytest

from nvr.cameras.adapters.secret_stores import MemorySecretStore
from nvr.cameras.domain import Camera, CameraCredentials, CameraDriver, IngestMode, StreamProfile
from nvr.shared.redact import redact
from nvr.streaming.application import AuthorizeMediaAccess, MediaAccessRequest, ReconcileMedia
from nvr.streaming.domain import PathSpec, PathState, RelaySpec, RelayStatus, parse_path_name, path_name

NOW = datetime(2026, 10, 17, tzinfo=UTC)
CAM_ID = "0b6f1c2e-1111-4a3b-9c55-123456789abc"
OTHER_ID = "9c0d1e2f-2222-4b4c-8d66-abcdefabcdef"
RECORD_ROOT = "/srv/nvr/recordings"
RECORD_TEMPLATE = f"{RECORD_ROOT}/%path/%Y-%m-%d_%H-%M-%S-%f"


def camera(camera_id: str = CAM_ID, **overrides: object) -> Camera:
    values: dict[str, object] = {
        "id": camera_id,
        "name": f"cam {camera_id[:4]}",
        "driver": CameraDriver.XIONGMAI,
        "host": "10.0.0.210",
        "rtsp_port": 554,
        "main_path": "/user={user}&password={password}&channel=1&stream=0.sdp?",
        "sub_path": "/user={user}&password={password}&channel=1&stream=1.sdp?",
        "secret_ref": f"camera:{camera_id}",
        "ingest": IngestMode.RELAY,
        "enabled": True,
        "recording_enabled": True,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return Camera(**values)  # type: ignore[arg-type]


class FakeCameras:
    def __init__(self, *cameras: Camera) -> None:
        self.items = {c.id: c for c in cameras}

    async def list(self) -> list[Camera]:
        return list(self.items.values())


class FakeMedia:
    def __init__(self, initial: dict[str, PathSpec] | None = None) -> None:
        self.paths: dict[str, PathSpec] = dict(initial or {})
        self.calls: list[str] = []

    async def list_configured(self) -> dict[str, PathSpec]:
        return dict(self.paths)

    async def put_path(self, spec: PathSpec) -> None:
        self.calls.append(f"put {spec.name}")
        self.paths[spec.name] = spec

    async def remove_path(self, name: str) -> None:
        self.calls.append(f"remove {name}")
        self.paths.pop(name, None)

    async def states(self) -> dict[str, PathState]:
        return {}


class FakeRelays:
    def __init__(self) -> None:
        self.running: dict[str, RelaySpec] = {}

    def ensure(self, spec: RelaySpec) -> None:
        self.running[spec.name] = spec

    async def stop(self, name: str) -> None:
        self.running.pop(name, None)

    def names(self) -> set[str]:
        return set(self.running)

    def statuses(self) -> dict[str, RelayStatus]:
        return {}

    async def stop_all(self) -> None:
        self.running.clear()


def setup(
    *cameras: Camera, media: FakeMedia | None = None
) -> tuple[ReconcileMedia, FakeMedia, FakeRelays, FakeCameras]:
    repo, secrets, media, relays = (
        FakeCameras(*cameras),
        MemorySecretStore(),
        media or FakeMedia(),
        FakeRelays(),
    )
    for c in cameras:
        secrets.put(c.secret_ref, CameraCredentials("admin", "pw$&'1"))
    return ReconcileMedia(repo, secrets, media, relays, RECORD_TEMPLATE), media, relays, repo  # type: ignore[arg-type]


def test_path_names_round_trip() -> None:
    name = path_name(CAM_ID, StreamProfile.SUB)
    assert name == f"cam-{CAM_ID}-sub"
    assert parse_path_name(name) == (CAM_ID, StreamProfile.SUB)
    assert parse_path_name("outro-path") is None
    assert parse_path_name(f"cam-{CAM_ID}-main/../x") is None


async def test_creates_paths_and_relays_only_main_records() -> None:
    reconcile, media, relays, _ = setup(camera())
    report = await reconcile()
    main, sub = path_name(CAM_ID, StreamProfile.MAIN), path_name(CAM_ID, StreamProfile.SUB)
    assert sorted(report.added) == sorted([main, sub])
    assert media.paths[main] == PathSpec(main, record=True, record_path=RECORD_TEMPLATE)
    assert media.paths[sub].record is False
    assert relays.running[main].source_url == (
        "rtsp://10.0.0.210:554/user=admin&password=pw$&'1&channel=1&stream=0.sdp?"
    )
    assert "pw$" not in repr(relays.running[main])


async def test_is_idempotent() -> None:
    reconcile, media, _, _ = setup(camera())
    await reconcile()
    media.calls.clear()
    report = await reconcile()
    assert not report.changed
    assert media.calls == []


async def test_recording_toggle_updates_only_main_path() -> None:
    reconcile, media, _, repo = setup(camera())
    await reconcile()
    repo.items[CAM_ID] = replace(repo.items[CAM_ID], recording_enabled=False)
    report = await reconcile()
    assert report.updated == [path_name(CAM_ID, StreamProfile.MAIN)]
    assert media.paths[path_name(CAM_ID, StreamProfile.MAIN)].record is False


async def test_disabled_or_removed_camera_is_cleaned_up_but_foreign_paths_stay() -> None:
    foreign = PathSpec("meu-path-manual", record=False, record_path="x")
    stale = PathSpec(path_name(OTHER_ID, StreamProfile.MAIN), record=True, record_path="x")
    media = FakeMedia({"meu-path-manual": foreign, stale.name: stale})
    reconcile, media, relays, repo = setup(camera(), media=media)
    await reconcile()
    assert stale.name not in media.paths  # câmera que não existe mais
    assert "meu-path-manual" in media.paths  # nunca toca paths de terceiros

    repo.items[CAM_ID] = replace(repo.items[CAM_ID], enabled=False)
    await reconcile()
    assert not any(n.startswith("cam-") for n in media.paths)
    assert relays.running == {}


async def test_camera_without_credentials_is_skipped() -> None:
    reconcile, media, _relays, _ = setup()
    reconcile._cameras = FakeCameras(camera())  # type: ignore[assignment]  # sem segredo no cofre
    report = await reconcile()
    assert report.skipped == {f"cam {CAM_ID[:4]}": "credenciais ausentes no cofre"}
    assert media.paths == {}


async def test_camera_without_substream_has_single_path() -> None:
    reconcile, media, _, _ = setup(camera(sub_path=None))
    await reconcile()
    assert list(media.paths) == [path_name(CAM_ID, StreamProfile.MAIN)]


# ---------------------------------------------------------------- autorização

MAIN = path_name(CAM_ID, StreamProfile.MAIN)


@pytest.mark.parametrize(
    ("req", "allowed"),
    [
        (MediaAccessRequest("publish", MAIN, "127.0.0.1", "relay", "segredo"), True),
        (MediaAccessRequest("publish", MAIN, "::1", "relay", "segredo"), True),
        (MediaAccessRequest("publish", MAIN, "10.0.0.5", "relay", "segredo"), False),  # de fora
        (MediaAccessRequest("publish", MAIN, "127.0.0.1", "relay", "errado"), False),
        (MediaAccessRequest("publish", MAIN, "127.0.0.1", "admin", "segredo"), False),
        (
            MediaAccessRequest("publish", "outro", "127.0.0.1", "relay", "segredo"),
            False,
        ),  # path não gerenciado
        # Leitura: só o backend (usuário reader, segredo próprio, loopback). O relay não lê.
        (MediaAccessRequest("read", MAIN, "127.0.0.1", "reader", "leitura"), True),
        (MediaAccessRequest("playback", MAIN, "127.0.0.1", "reader", "leitura"), True),
        (MediaAccessRequest("read", MAIN, "127.0.0.1", "relay", "segredo"), False),
        (MediaAccessRequest("read", MAIN, "127.0.0.1", "reader", "segredo"), False),
        (MediaAccessRequest("read", MAIN, "10.0.0.5", "reader", "leitura"), False),  # navegador direto: não
        (MediaAccessRequest("read", "outro", "127.0.0.1", "reader", "leitura"), False),
        (MediaAccessRequest("publish", MAIN, "127.0.0.1", "reader", "leitura"), False),  # leitor não publica
        (MediaAccessRequest("playback", MAIN, "10.0.0.5", "", ""), False),
        (MediaAccessRequest("api", "", "127.0.0.1", "", ""), True),
        (MediaAccessRequest("api", "", "10.0.0.5", "", ""), False),
        (MediaAccessRequest("pprof", "", "127.0.0.1", "", ""), False),
    ],
)
def test_media_authorization(req: MediaAccessRequest, allowed: bool) -> None:
    assert AuthorizeMediaAccess("segredo", "leitura")(req) is allowed


def test_redact_covers_known_secrets_and_url_formats() -> None:
    text = "falha em rtsp://admin:pw@10.0.0.210/x e /user=admin&password=abc$1&channel=1 (segredo=Zz9)"
    out = redact(text, "Zz9")
    assert "pw@" not in out
    assert "abc$1" not in out
    assert "Zz9" not in out
