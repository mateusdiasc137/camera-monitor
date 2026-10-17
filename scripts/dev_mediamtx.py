"""Sobe o MediaMTX de desenvolvimento com integrations/mediamtx/mediamtx.yml.

Uso: python scripts/dev_mediamtx.py
Precisa do backend rodando (`uv run nvr serve`) para autorizar os relays. Ctrl+C encerra.
"""

from __future__ import annotations

import subprocess
import sys

from _common import ROOT, mediamtx_binary

CONFIG = ROOT / "integrations" / "mediamtx" / "mediamtx.yml"


def main() -> int:
    proc = subprocess.Popen([str(mediamtx_binary()), str(CONFIG)], cwd=ROOT)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        return 0


if __name__ == "__main__":
    sys.exit(main())
