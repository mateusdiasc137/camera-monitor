"""Verifica automaticamente a PoC (F0-05) em execução (scripts/poc_nvr.py).

Uso: python scripts/poc_check.py [--host 127.0.0.1] [--wait-segment 150]

Checagens:
  1. API local: paths prontos e com faixas.
  2. Sem credencial: HLS, WebRTC e playback recusam (401).
  3. Com credencial: HLS reproduzível (ffprobe), página WebRTC acessível.
  4. Gravação: existe ao menos um segmento finalizado, válido no ffprobe.
  5. Playback: /list retorna intervalos e /get entrega um MP4 válido de 10 s.
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from _common import DATA_DIR, require_tool

POC_DIR = DATA_DIR / "poc"
results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'OK ' if ok else 'FALHA'}] {name}{' — ' + detail if detail else ''}")


def http_get(url: str, auth: tuple[str, str] | None = None, timeout: float = 10,
             method: str = "GET", body: bytes | None = None, content_type: str | None = None) -> tuple[int, bytes]:
    headers = {}
    if auth:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, headers=headers, method=method, data=body)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""


def ffprobe(target: str, extra: list[str] | None = None) -> dict | None:
    cmd = [require_tool("ffprobe"), "-v", "error", *(extra or []), "-show_entries",
           "format=duration:stream=codec_type,codec_name", "-of", "json", target]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return json.loads(proc.stdout) if proc.returncode == 0 else None


def summary(info: dict) -> str:
    streams = ", ".join(f"{s['codec_type']}:{s['codec_name']}" for s in info.get("streams", []))
    duration = info.get("format", {}).get("duration")
    return f"{streams}; duração={float(duration):.1f}s" if duration else streams


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--wait-segment", type=int, default=150, help="segundos máx. aguardando 1º segmento finalizado")
    args = parser.parse_args()
    host = args.host
    password = (POC_DIR / "viewer.secret").read_text(encoding="utf-8").strip()
    auth = ("viewer", password)

    # 1. API
    status, body = http_get("http://127.0.0.1:9997/v3/paths/list")
    items = {i["name"]: i for i in json.loads(body)["items"]} if status == 200 else {}
    for name in ("cam", "camsub"):
        item = items.get(name)
        record(f"path '{name}' pronto", bool(item and item["ready"]),
               ", ".join(item.get("tracks", [])) if item else "ausente")

    # 2. Sem credencial
    for label, url in [("HLS", f"http://{host}:8888/cam/index.m3u8"),
                       ("playback", f"http://{host}:9996/list?path=cam")]:
        status, _ = http_get(url)
        record(f"{label} sem credencial é recusado", status == 401, f"HTTP {status}")
    # WHEP só aceita POST com oferta SDP; a autenticação é checada antes de interpretar a oferta.
    status, _ = http_get(f"http://{host}:8889/cam/whep", method="POST",
                         body=b"v=0\r\n", content_type="application/sdp")
    record("WebRTC (WHEP) sem credencial é recusado", status == 401, f"HTTP {status}")

    # 3. Com credencial
    status, _ = http_get(f"http://{host}:8889/cam", auth)
    record("página WebRTC com credencial", status == 200, f"HTTP {status}")
    hls_url = f"http://viewer:{urllib.parse.quote(password)}@{host}:8888/cam/index.m3u8"
    info = ffprobe(hls_url)
    record("HLS com credencial reproduzível", info is not None, summary(info) if info else "ffprobe falhou")

    # 4. Gravação em disco
    source = (POC_DIR / "active_source").read_text(encoding="utf-8").strip()
    cam_dir = POC_DIR / "recordings" / source / "cam"
    deadline = time.monotonic() + args.wait_segment
    segments: list[Path] = []
    while time.monotonic() < deadline:
        segments = sorted(cam_dir.glob("*.mp4"))
        if len(segments) >= 2:  # o penúltimo já foi finalizado
            break
        time.sleep(5)
    if len(segments) >= 2:
        info = ffprobe(str(segments[-2]))
        record("segmento finalizado válido", info is not None,
               f"{segments[-2].name}: {summary(info)}" if info else segments[-2].name)
    else:
        record("segmento finalizado válido", False, f"{len(segments)} arquivo(s) após {args.wait_segment}s")

    # 5. Playback
    status, body = http_get(f"http://{host}:9996/list?path=cam", auth)
    entries = json.loads(body) if status == 200 else []
    record("playback /list", status == 200 and len(entries) > 0, f"HTTP {status}, {len(entries)} intervalo(s)")
    if entries:
        start = datetime.fromisoformat(entries[-1]["start"])  # o intervalo mais recente
        query = urllib.parse.urlencode({"path": "cam", "start": (start + timedelta(seconds=2)).isoformat(),
                                        "duration": "10", "format": "mp4"})
        if entries[-1]["duration"] < 13:
            record("playback /get (10 s, mp4)", False, "intervalo mais recente ainda curto; rode de novo em instantes")
            entries = []
    if entries:
        status, data = http_get(f"http://{host}:9996/get?{query}", auth, timeout=30)
        ok = status == 200 and len(data) > 0
        detail = f"HTTP {status}, {len(data)} bytes"
        if ok:
            with tempfile.TemporaryDirectory() as tmp:
                clip = Path(tmp) / "clip.mp4"
                clip.write_bytes(data)
                info = ffprobe(str(clip))
                ok = info is not None
                detail += f"; {summary(info)}" if info else "; ffprobe falhou"
        record("playback /get (10 s, mp4)", ok, detail)

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checagens OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
