"""Fluxo de autenticação ponta a ponta com o container real e SQLite temporário."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nvr.bootstrap.cli import build_app
from nvr.bootstrap.container import build_container
from nvr.bootstrap.settings import Settings
from nvr.identity.domain import Role
from nvr.persistence.database import migrate

PASSWORD = "senha-forte-123"
ORIGIN = "http://testserver"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    import asyncio

    migrate(tmp_path)
    settings = Settings(data_dir=tmp_path, cookie_secure=False)
    container = build_container(settings)
    asyncio.run(container.create_user("admin", PASSWORD, Role.ADMIN))
    asyncio.run(container.create_user("visitante", PASSWORD, Role.VIEWER))
    with TestClient(build_app(container), base_url=ORIGIN) as test_client:
        yield test_client


def login(client: TestClient, username: str = "admin", password: str = PASSWORD):  # type: ignore[no-untyped-def]
    return client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}, headers={"Origin": ORIGIN}
    )


PROTECTED = [("GET", "/api/v1/system/health"), ("GET", "/api/v1/auth/me")]


@pytest.mark.parametrize(("method", "path"), PROTECTED)
def test_protected_routes_require_session(client: TestClient, method: str, path: str) -> None:
    response = client.request(method, path)
    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


def test_every_route_except_login_is_protected(client: TestClient) -> None:
    """Rede de segurança: qualquer rota nova sem proteção quebra este teste."""
    public = {
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/openapi.json",
        "/api/v1/docs",
        "/api/v1/docs/oauth2-redirect",
    }
    for route in client.app.routes:  # type: ignore[attr-defined]
        path = getattr(route, "path", "")
        if path in public or not path.startswith("/api/"):
            continue
        for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}:
            response = client.request(
                method, path.replace("{", "x").replace("}", ""), headers={"Origin": ORIGIN}
            )
            assert response.status_code == 401, f"{method} {path} acessível sem login"


def test_login_sets_hardened_cookie_and_me_works(client: TestClient) -> None:
    response = login(client)
    assert response.status_code == 200
    assert response.json()["username"] == "admin"
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "path=/" in cookie
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json() == {"id": me.json()["id"], "username": "admin", "role": "admin"}
    assert client.get("/api/v1/system/health").status_code == 200


def test_secure_flag_in_production_settings(tmp_path: Path) -> None:
    import asyncio

    migrate(tmp_path)
    container = build_container(Settings(data_dir=tmp_path))  # padrão: cookie_secure=True
    asyncio.run(container.create_user("admin", PASSWORD, Role.ADMIN))
    with TestClient(build_app(container), base_url="https://testserver") as https_client:
        response = https_client.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
    assert "secure" in response.headers["set-cookie"].lower()


def test_wrong_password_does_not_reveal_which_part_failed(client: TestClient) -> None:
    wrong_pass = login(client, password="errada-123456")
    unknown_user = login(client, username="ninguem")
    assert wrong_pass.status_code == unknown_user.status_code == 401
    assert wrong_pass.json() == unknown_user.json()
    assert "set-cookie" not in wrong_pass.headers


def test_logout_invalidates_session(client: TestClient) -> None:
    login(client)
    assert client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN}).status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401


def test_stolen_cookie_is_useless_after_logout(client: TestClient) -> None:
    login(client)
    token = client.cookies.get("nvr_session")
    client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN})
    client.cookies.set("nvr_session", token)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_cross_origin_mutation_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": PASSWORD},
        headers={"Origin": "http://site-malicioso.example"},
    )
    assert response.status_code == 403
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": PASSWORD},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert response.status_code == 403


def test_throttle_uses_forwarded_ip_only_from_trusted_proxy(client: TestClient) -> None:
    """O TestClient conecta como 'testclient' (não confiável): X-Forwarded-For deve ser ignorado,
    então trocar o cabeçalho não escapa do bloqueio por cliente."""
    for i in range(5):
        login(client, username=f"inexistente{i}", password="errada-123456")
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": PASSWORD},
        headers={"Origin": ORIGIN, "X-Forwarded-For": "203.0.113.50"},
    )
    assert response.status_code == 401
    assert "tentativas" in response.json()["message"]
