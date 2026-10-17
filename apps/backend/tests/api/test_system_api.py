from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from nvr.http.app import create_app
from nvr.http.security import current_user
from nvr.identity.domain import Role, User
from nvr.system.application import GetSystemHealth

VIEWER = User("u1", "visitante", Role.VIEWER, disabled=False, created_at=datetime(2026, 1, 1, tzinfo=UTC))


@dataclass
class FakeUseCases:
    get_system_health: GetSystemHealth
    login: object = None
    logout: object = None
    authenticate: object = None


def make_client() -> TestClient:
    app = create_app(FakeUseCases(GetSystemHealth("0.0.1", probes=[])))  # type: ignore[arg-type]
    app.dependency_overrides[current_user] = lambda: VIEWER
    return TestClient(app)


def test_health_endpoint_contract() -> None:
    response = make_client().get("/api/v1/system/health")
    assert response.status_code == 200
    assert response.json() == {"version": "0.0.1", "state": "ok", "components": []}


def test_openapi_is_published_under_api_v1() -> None:
    schema = make_client().get("/api/v1/openapi.json").json()
    assert "/api/v1/system/health" in schema["paths"]
    assert "/api/v1/auth/login" in schema["paths"]
