"""Linha de comando: `nvr serve` e `nvr openapi`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import uvicorn

from nvr.bootstrap.container import build_container
from nvr.bootstrap.settings import Settings
from nvr.http.app import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nvr")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="inicia a API HTTP")
    openapi = sub.add_parser("openapi", help="exporta o esquema OpenAPI (para gerar o cliente da UI)")
    openapi.add_argument("--output", type=Path, help="arquivo de saída (padrão: stdout)")
    args = parser.parse_args(argv)

    settings = Settings()
    app = create_app(build_container(settings))
    if args.cmd == "serve":
        uvicorn.run(app, host=settings.bind_host, port=settings.bind_port)
    elif args.cmd == "openapi":
        text = json.dumps(app.openapi(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        if args.output:
            args.output.write_text(text, encoding="utf-8", newline="\n")  # idêntico no Windows e Linux
        else:
            sys.stdout.write(text)
    return 0
