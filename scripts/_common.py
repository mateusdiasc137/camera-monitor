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
# Formato Xiongmai com credenciais no caminho: /user=admin&password=xyz&channel=1 (ou separado por '_').
_PATH_CREDENTIALS = re.compile(r"(?P<key>\b(?:user|username|password|pwd|pass)=)[^&_;\s/?]*", re.IGNORECASE)


def mask(text: str, *secrets: str) -> str:
    """Remove credenciais de URLs e de caminhos, além de quaisquer segredos informados explicitamente."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    text = _URL_CREDENTIALS.sub(r"\g<scheme>***@", text)
    return _PATH_CREDENTIALS.sub(r"\g<key>***", text)


def camera_rtsp_url(env: dict[str, str], path_key: str = "NVR_CAM_RTSP_PATH") -> str:
    """Monta a URL RTSP da câmera a partir do .env.

    Se o caminho contiver {user}/{password} (formato Xiongmai com credenciais no caminho), eles são
    substituídos ali; caso contrário, as credenciais vão no formato padrão rtsp://user:pass@host.
    """
    from urllib.parse import quote

    missing = [k for k in ("NVR_CAM_HOST", path_key, "NVR_CAM_USER", "NVR_CAM_PASSWORD")
               if not env.get(k) or env[k] == "CHANGE_ME"]
    if missing:
        raise SystemExit(f"Preencha no .env: {', '.join(missing)} (veja .env.example)")
    user, password = env["NVR_CAM_USER"], env["NVR_CAM_PASSWORD"]
    host, port, path = env["NVR_CAM_HOST"], env.get("NVR_CAM_RTSP_PORT", "554"), env[path_key]
    if not path.startswith("/"):
        path = "/" + path
    if "{user}" in path or "{password}" in path:
        return f"rtsp://{host}:{port}" + path.replace("{user}", user).replace("{password}", password)
    return f"rtsp://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}{path}"


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
