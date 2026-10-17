from dataclasses import dataclass

from fastapi.testclient import TestClient

from nvr.http.app import create_app
from nvr.system.application import GetSystemHealth


@dataclass
class FakeUseCases:
    get_system_health: GetSystemHealth


def test_health_endpoint_contract() -> None:
    client = TestClient(create_app(FakeUseCases(GetSystemHealth("0.0.1", probes=[]))))
    response = client.get("/api/v1/system/health")
    assert response.status_code == 200
    assert response.json() == {"version": "0.0.1", "state": "ok", "components": []}


def test_openapi_is_published_under_api_v1() -> None:
    client = TestClient(create_app(FakeUseCases(GetSystemHealth("0.0.1", probes=[]))))
    schema = client.get("/api/v1/openapi.json").json()
    assert "/api/v1/system/health" in schema["paths"]
