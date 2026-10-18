"""Linha de comando: `nvr serve`, `nvr migrate`, `nvr create-user` e `nvr openapi`."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from nvr.bootstrap.container import Container, build_container
from nvr.bootstrap.settings import Settings
from nvr.http.app import create_app
from nvr.identity.domain import Role
from nvr.jobs.periodic import PeriodicTask
from nvr.persistence.database import migrate
from nvr.shared.errors import AppError


def build_app(container: Container) -> FastAPI:
    settings = container.settings
    media_sync = PeriodicTask(
        "reconciliação de mídia", settings.media_sync_interval, container.reconcile_media
    )
    health_monitor = PeriodicTask(
        "estado das câmeras", settings.status_interval, container.monitor_camera_health
    )
    container.monitor_camera_health.on_media_recovered = media_sync.trigger
    indexer = PeriodicTask("índice de gravações", settings.index_interval, container.index_segments)
    storage = PeriodicTask(
        "cota de armazenamento", settings.storage_interval, container.enforce_storage_policy
    )
    container.enforce_storage_policy.on_gate_change = media_sync.trigger
    container.update_storage_policy.on_change = storage.trigger

    def location_changed() -> None:  # novo local: recordPath no MediaMTX, índice e cota já
        media_sync.trigger()
        indexer.trigger()
        storage.trigger()

    container.change_storage_location.on_change = location_changed
    container.migration_runner.on_finished = location_changed

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await container.ensure_storage_location()
        container.migration_runner.start()  # retoma a migração interrompida por um reinício, se houver
        if settings.media_enabled:
            media_sync.start()
            health_monitor.start()
            indexer.start()
            storage.start()
            app.state.media_sync = media_sync
        try:
            yield
        finally:
            await storage.stop()
            await indexer.stop()
            await health_monitor.stop()
            await media_sync.stop()
            await container.aclose()

    return create_app(
        container,
        cookie_secure=settings.cookie_secure,
        trusted_proxies=settings.trusted_proxies,
        lifespan=lifespan,
        event_bus=container.event_bus,
    )


async def _create_user(settings: Settings, username: str, role: Role) -> int:
    password = getpass.getpass("Senha: ")
    if password != getpass.getpass("Repita a senha: "):
        print("As senhas não conferem.", file=sys.stderr)
        return 1
    container = build_container(settings)
    try:
        user = await container.create_user(username, password, role)
    except AppError as exc:
        print(f"Erro: {exc.message}", file=sys.stderr)
        return 1
    finally:
        await container.aclose()
    print(f"Usuário '{user.username}' ({user.role.value}) criado.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nvr")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="aplica as migrações e inicia a API HTTP")
    sub.add_parser("migrate", help="aplica as migrações do banco")
    create = sub.add_parser("create-user", help="cria um usuário do painel (a senha é pedida no terminal)")
    create.add_argument("username")
    create.add_argument("--role", choices=[r.value for r in Role], default=Role.ADMIN.value)
    openapi = sub.add_parser("openapi", help="exporta o esquema OpenAPI (para gerar o cliente da UI)")
    openapi.add_argument("--output", type=Path, help="arquivo de saída (padrão: stdout)")
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):  # logs em UTF-8 também no Windows (cp1252 corrompe acentos)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # a reconciliação consulta o MediaMTX a cada ciclo
    settings = Settings()
    if args.cmd == "migrate":
        migrate(settings.data_dir)
    elif args.cmd == "serve":
        migrate(settings.data_dir)
        uvicorn.run(build_app(build_container(settings)), host=settings.bind_host, port=settings.bind_port)
    elif args.cmd == "create-user":
        migrate(settings.data_dir)
        return asyncio.run(_create_user(settings, args.username, Role(args.role)))
    elif args.cmd == "openapi":
        app = create_app(build_container(settings))
        text = json.dumps(app.openapi(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        if args.output:
            args.output.write_text(text, encoding="utf-8", newline="\n")  # idêntico no Windows e Linux
        else:
            sys.stdout.write(text)
    return 0
