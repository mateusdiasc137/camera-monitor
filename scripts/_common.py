"""Utilitários compartilhados pelos scripts da Fase 0 (somente biblioteca padrão)."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

_URL_CREDENTIALS = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^/@\s]+@", re.IGNORECASE)


def mask(text: str) -> str:
    """Remove usuário/senha de URLs (rtsp://user:pass@host -> rtsp://***@host)."""
    return _URL_CREDENTIALS.sub(r"\g<scheme>***@", text)


def load_dotenv(path: Path = ROOT / ".env") -> dict[str, str]:
    """Lê um .env simples (CHAVE=valor). Variáveis já definidas no ambiente têm prioridade."""
    values: dict[str, str] = {}
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    values.update({k: v for k, v in os.environ.items() if k.startswith("NVR_")})
    return values


def mediamtx_binary() -> Path:
    manifest = json.loads((ROOT / "tools" / "manifest.json").read_text(encoding="utf-8"))
    version = manifest["mediamtx"]["version"]
    name = "mediamtx.exe" if sys.platform == "win32" else "mediamtx"
    binary = ROOT / "tools" / "mediamtx" / version / name
    if not binary.exists():
        raise SystemExit(f"MediaMTX não encontrado em {binary}. Rode: python scripts/fetch_tools.py")
    return binary


def require_tool(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        raise SystemExit(f"'{name}' não encontrado no PATH.")
    return found


def render_template(template: Path, output: Path, values: dict[str, str]) -> None:
    """Substitui ${CHAVE} no template. Falha se sobrar alguma chave não resolvida."""
    text = template.read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("${" + key + "}", value)
    leftover = re.findall(r"\$\{([A-Z0-9_]+)\}", text)
    if leftover:
        raise SystemExit(f"Template {template.name}: valores ausentes para {sorted(set(leftover))}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    if sys.platform != "win32":
        output.chmod(0o600)
