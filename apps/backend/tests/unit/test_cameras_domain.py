from datetime import UTC, datetime

import pytest

from nvr.cameras.domain import (
    Camera,
    CameraCredentials,
    CameraDriver,
    IngestMode,
    StreamProfile,
    rtsp_url,
    validate_host,
    validate_rtsp_path,
)

NOW = datetime(2026, 10, 17, tzinfo=UTC)
CREDS = CameraCredentials("admin", "s3nh@&forte")


def camera(**overrides: object) -> Camera:
    values: dict[str, object] = {
        "id": "c1",
        "name": "Quintal",
        "driver": CameraDriver.XIONGMAI,
        "host": "192.0.2.10",
        "rtsp_port": 554,
        "main_path": "/stream1",
        "sub_path": None,
        "secret_ref": "camera:c1",
        "ingest": IngestMode.RELAY,
        "enabled": True,
        "recording_enabled": True,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return Camera(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "path",
    [
        "/stream1",
        "/user={user}&password={password}&channel=1&stream=0.sdp?",
        "/user={user}_password={password}_channel=1_stream=0.sdp?real_stream",
        "/cam/realmonitor?channel=1&subtype=0",
    ],
)
def test_valid_paths(path: str) -> None:
    assert validate_rtsp_path(path) is None


@pytest.mark.parametrize(
    ("path", "fragment"),
    [
        ("stream1", "começar com '/'"),
        ("/user=admin&password=123456&channel=1&stream=0.sdp?", "marcadores"),
        ("/user={user}&password=123456&channel=1", "marcadores"),
        ("/user={user}_password=segredo_channel=1", "marcadores"),
        ("/live stream", "espaços"),
    ],
)
def test_invalid_paths(path: str, fragment: str) -> None:
    error = validate_rtsp_path(path)
    assert error is not None
    assert fragment in error


@pytest.mark.parametrize("host", ["10.0.0.210", "camera.local", "nvr-camera-monitor.home.arpa", "fe80::1"])
def test_valid_hosts(host: str) -> None:
    assert validate_host(host) is None


@pytest.mark.parametrize("host", ["rtsp://10.0.0.210", "10.0.0.210:554", "admin@10.0.0.210", "", "a b"])
def test_invalid_hosts(host: str) -> None:
    assert validate_host(host) is not None


def test_url_with_userinfo_is_percent_encoded() -> None:
    url = rtsp_url(camera(), StreamProfile.MAIN, CREDS)
    assert url == "rtsp://admin:s3nh%40%26forte@192.0.2.10:554/stream1"


def test_url_with_placeholders_puts_credentials_in_path() -> None:
    cam = camera(main_path="/user={user}&password={password}&channel=1&stream=0.sdp?")
    url = rtsp_url(cam, StreamProfile.MAIN, CameraCredentials("admin", "abc123"))
    assert url == "rtsp://192.0.2.10:554/user=admin&password=abc123&channel=1&stream=0.sdp?"


def test_url_for_ipv6_host() -> None:
    assert rtsp_url(camera(host="fe80::1"), StreamProfile.MAIN, CREDS).startswith("rtsp://admin:")
    assert "@[fe80::1]:554/" in rtsp_url(camera(host="fe80::1"), StreamProfile.MAIN, CREDS)


def test_missing_sub_profile_is_an_error() -> None:
    with pytest.raises(ValueError, match="sub"):
        rtsp_url(camera(), StreamProfile.SUB, CREDS)


def test_credentials_never_show_password() -> None:
    assert "s3nh" not in repr(CREDS)
    assert "s3nh" not in str(CREDS)
    assert "s3nh" not in f"{[CREDS]}"
