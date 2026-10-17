"""Linha de comando: `nvr serve`, `nvr migrate` e `nvr openapi`."""

from __future__ import annotations

import argparse
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
from nvr.persistence.database import migrate


def build_app(container: Container) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        await container.aclose()

    return create_app(container, lifespan=lifespan)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nvr")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="aplica as migrações e inicia a API HTTP")
    sub.add_parser("migrate", help="aplica as migrações do banco")
    openapi = sub.add_parser("openapi", help="exporta o esquema OpenAPI (para gerar o cliente da UI)")
    openapi.add_argument("--output", type=Path, help="arquivo de saída (padrão: stdout)")
    args = parser.parse_args(argv)

    settings = Settings()
    if args.cmd == "migrate":
        migrate(settings.data_dir)
    elif args.cmd == "serve":
        migrate(settings.data_dir)
        uvicorn.run(build_app(build_container(settings)), host=settings.bind_host, port=settings.bind_port)
    elif args.cmd == "openapi":
        app = create_app(build_container(settings))
        text = json.dumps(app.openapi(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        if args.output:
            args.output.write_text(text, encoding="utf-8", newline="\n")  # idêntico no Windows e Linux
        else:
            sys.stdout.write(text)
    return 0
