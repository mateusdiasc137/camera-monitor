"""Linha de comando: `nvr serve`, `nvr migrate`, `nvr create-user` e `nvr openapi`."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
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
from nvr.persistence.database import migrate
from nvr.shared.errors import AppError


def build_app(container: Container) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        await container.aclose()

    settings = container.settings
    return create_app(
        container,
        cookie_secure=settings.cookie_secure,
        trusted_proxies=settings.trusted_proxies,
        lifespan=lifespan,
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
