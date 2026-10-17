"""Registra mudanças de estado dos paths do MediaMTX (para testes de queda/retomada, F0-07).

Uso: python scripts/watch_paths.py [--minutes 20] [--interval 2]
Imprime uma linha a cada mudança de 'ready' e o total de bytes recebidos por path.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime


def snapshot() -> dict[str, tuple[bool, int]]:
    with urllib.request.urlopen("http://127.0.0.1:9997/v3/paths/list", timeout=3) as resp:
        items = json.load(resp)["items"]
    return {i["name"]: (bool(i["ready"]), int(i.get("bytesReceived", 0))) for i in items}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=float, default=20)
    parser.add_argument("--interval", type=float, default=2)
    args = parser.parse_args()
    last: dict[str, bool] = {}
    end = time.monotonic() + args.minutes * 60
    while time.monotonic() < end:
        try:
            state = snapshot()
        except OSError as exc:
            print(f"{datetime.now():%H:%M:%S} API indisponível: {exc}", flush=True)
            time.sleep(args.interval)
            continue
        for name, (ready, received) in state.items():
            if last.get(name) != ready:
                print(f"{datetime.now():%H:%M:%S} {name}: ready={ready} bytes={received}", flush=True)
                last[name] = ready
        time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
