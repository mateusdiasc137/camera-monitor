"""O Caddyfile de produção (infra/caddy) na frente do backend real: TLS da CA local, rotas e cabeçalhos."""

import asyncio
import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn

from nvr.bootstrap.cli import build_app
from nvr.bootstrap.container import build_container
from nvr.bootstrap.settings import Settings
from nvr.identity.domain import Role
from nvr.persistence.database import migrate

REPO = Path(__file__).resolve().parents[4]
CADDYFILE = REPO / "infra" / "caddy" / "Caddyfile"
PASSWORD = "senha-forte-123"
NAME = "nvr-camera-monitor.local"


def caddy_binary() -> Path | None:
    manifest = json.loads((REPO / "tools" / "manifest.json").read_text(encoding="utf-8"))
    binary = (
        REPO
        / "tools"
        / "caddy"
        / manifest["caddy"]["version"]
        / ("caddy.exe" if sys.platform == "win32" else "caddy")
    )
    return binary if binary.exists() else None


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(caddy_binary() is None, reason="Caddy não baixado (scripts/fetch_tools.py)"),
]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_port(port: int, timeout: float = 30) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise TimeoutError(f"porta {port} não abriu")


@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    tmp = tmp_path_factory.mktemp("caddy")
    data, web = tmp / "data", tmp / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><title>NVR</title><div id=root></div>", encoding="utf-8")
    (web / "assets" / "app-1a2b3c.js").write_text("console.log('nvr')", encoding="utf-8")

    migrate(data)
    container = build_container(Settings(data_dir=data))  # cookie Secure: só funciona atrás do HTTPS
    asyncio.run(container.create_user("admin", PASSWORD, Role.ADMIN))
    backend_port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(build_app(container), host="127.0.0.1", port=backend_port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    wait_port(backend_port)

    https_port, http_port = free_port(), free_port()
    caddy_data = tmp / "caddy-dados"
    ca_dir = caddy_data / "caddy" / "pki" / "authorities" / "local"
    env = dict(
        os.environ,
        XDG_DATA_HOME=str(caddy_data),
        XDG_CONFIG_HOME=str(tmp / "caddy-config"),
        NVR_SITE_ADDRESSES=f"{NAME}, 127.0.0.1",
        NVR_BACKEND=f"127.0.0.1:{backend_port}",
        NVR_WEB_ROOT=str(web),
        NVR_CA_DIR=str(ca_dir),
        NVR_HTTPS_PORT=str(https_port),
        NVR_HTTP_PORT=str(http_port),
        NVR_HTTPS_SUFFIX=f":{https_port}",
        NVR_CADDY_ADMIN="off",
    )
    log = (tmp / "caddy.log").open("w", encoding="utf-8")
    caddy = subprocess.Popen(
        [str(caddy_binary()), "run", "--config", str(CADDYFILE), "--adapter", "caddyfile"],
        env=env, stdout=log, stderr=subprocess.STDOUT,
    )  # fmt: skip
    try:
        wait_port(https_port)
        wait_port(http_port)
        for _ in range(100):
            if (ca_dir / "root.crt").exists():
                break
            time.sleep(0.1)
        ctx = ssl.create_default_context(cafile=str(ca_dir / "root.crt"))
        yield {
            "https": https_port,
            "http": http_port,
            "ctx": ctx,
            "ca": ca_dir / "root.crt",
            "log": tmp / "caddy.log",
        }
    finally:
        caddy.terminate()
        caddy.wait(timeout=10)
        log.close()
        server.should_exit = True
        thread.join(timeout=10)


def client(stack: dict[str, Any]) -> httpx.Client:
    base = f"https://127.0.0.1:{stack['https']}"
    return httpx.Client(base_url=base, verify=stack["ctx"], headers={"Origin": base}, timeout=10)


def test_certificate_is_valid_only_with_the_local_ca(stack: dict[str, Any]) -> None:
    for name in (NAME, "127.0.0.1"):
        for _ in range(50):  # o certificado de cada nome é emitido logo após subir
            try:
                with (
                    socket.create_connection(("127.0.0.1", stack["https"])) as raw,
                    stack["ctx"].wrap_socket(raw, server_hostname=name) as tls,
                ):
                    cert = tls.getpeercert()
                break
            except (ssl.SSLError, ConnectionError):
                time.sleep(0.2)
        else:
            raise AssertionError(f"sem certificado válido para {name}")
        sans = {value for _kind, value in cert["subjectAltName"]}
        assert name in sans
    untrusted = ssl.create_default_context()  # só as CAs do sistema: precisa recusar
    with (
        socket.create_connection(("127.0.0.1", stack["https"])) as raw,
        pytest.raises(ssl.SSLCertVerificationError),
    ):
        untrusted.wrap_socket(raw, server_hostname=NAME)


def test_static_interface_with_security_headers(stack: dict[str, Any]) -> None:
    with client(stack) as c:
        page = c.get("/gravacoes")  # rota da interface: cai no index.html
        assert page.status_code == 200
        assert "<title>NVR</title>" in page.text
        assert page.headers["cache-control"] == "no-cache"
        assert page.headers["strict-transport-security"].startswith("max-age=")
        assert page.headers["x-content-type-options"] == "nosniff"
        assert page.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert "server" not in page.headers
        asset = c.get("/assets/app-1a2b3c.js")
        assert asset.status_code == 200
        assert "immutable" in asset.headers["cache-control"]


def test_api_through_proxy_with_secure_session(stack: dict[str, Any]) -> None:
    with client(stack) as c:
        assert c.get("/api/v1/system/ping").json() == {"status": "ok"}
        assert c.get("/api/v1/auth/me").status_code == 401
        login = c.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
        assert login.status_code == 200
        cookie = login.headers["set-cookie"]
        for flag in ("Secure", "HttpOnly", "SameSite=strict"):
            assert flag in cookie
        assert c.get("/api/v1/auth/me").json()["username"] == "admin"

        foreign = c.post(
            "/api/v1/auth/logout", headers={"Origin": "https://outro-site.example"}
        )  # origem diferente
        assert foreign.status_code == 403

        # SSE passa em fluxo (sem acumular no proxy): o comentário inicial chega na hora.
        with c.stream("GET", "/api/v1/events/stream") as events:
            started = time.monotonic()
            first = next(events.iter_text())
            assert ": conectado" in first
            assert time.monotonic() - started < 3


def test_internal_routes_are_not_exposed(stack: dict[str, Any]) -> None:
    with client(stack) as c:
        assert c.post("/internal/mediamtx/auth", json={}).status_code == 404
        assert c.get("/internal/mediamtx/auth").status_code == 404


def test_http_redirects_and_serves_only_the_ca_certificate(stack: dict[str, Any]) -> None:
    base = f"http://127.0.0.1:{stack['http']}"
    with httpx.Client(base_url=base, timeout=10) as c:
        moved = c.get("/gravacoes?dia=hoje")
        assert moved.status_code == 308
        assert moved.headers["location"] == f"https://127.0.0.1:{stack['https']}/gravacoes?dia=hoje"
        ca = c.get("/nvr-ca.crt")
        assert ca.status_code == 200
        assert ca.headers["content-type"] == "application/x-x509-ca-cert"
        assert ca.content == stack["ca"].read_bytes()
        assert b"PRIVATE KEY" not in ca.content
        for probe in ("/root.key", "/nvr-ca.crt/../root.key", "/root.crt"):
            response = c.get(probe)
            assert response.status_code == 308, probe  # nada além do certificado é servido por HTTP
