from dataclasses import dataclass

from fastapi.testclient import TestClient

from nvr.http.app import create_app
from nvr.streaming.application import AuthorizeMediaAccess

PATH = "cam-0b6f1c2e-1111-4a3b-9c55-123456789abc-main"


@dataclass
class FakeUseCases:
    authorize_media_access: AuthorizeMediaAccess


def client(peer: str) -> TestClient:
    app = create_app(FakeUseCases(AuthorizeMediaAccess("segredo", "leitura")))  # type: ignore[arg-type]
    return TestClient(app, client=(peer, 50000))


BODY = {
    "ip": "127.0.0.1",
    "user": "relay",
    "password": "segredo",
    "action": "publish",
    "path": PATH,
    "protocol": "rtsp",
    "token": "",
    "query": "",
    "id": None,
    "userAgent": "",
}


def test_mediamtx_auth_allows_local_relay() -> None:
    assert client("127.0.0.1").post("/internal/mediamtx/auth", json=BODY).status_code == 200


def test_mediamtx_auth_denies_wrong_secret_and_reading() -> None:
    c = client("127.0.0.1")
    assert c.post("/internal/mediamtx/auth", json=BODY | {"password": "x"}).status_code == 401
    assert c.post("/internal/mediamtx/auth", json=BODY | {"action": "read"}).status_code == 401


def test_internal_endpoint_rejects_non_loopback_callers() -> None:
    assert client("10.0.0.5").post("/internal/mediamtx/auth", json=BODY).status_code == 403


def test_internal_endpoint_is_not_in_public_schema() -> None:
    schema = client("127.0.0.1").get("/api/v1/openapi.json").json()
    assert not any(p.startswith("/internal") for p in schema["paths"])
