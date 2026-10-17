"""Prova de conceito do NVR (F0-05): MediaMTX gravando e servindo ao vivo uma câmera.

Uso:
  python scripts/poc_nvr.py                 # câmera falsa (rode scripts/fake_camera.py antes)
  python scripts/poc_nvr.py --source real   # câmera real, lida do .env (NVR_CAM_*)
  python scripts/poc_nvr.py --segment 1m    # duração dos segmentos (padrão 1m na PoC)

A URL com credenciais da câmera é enviada só pela API local do MediaMTX (memória), nunca gravada
em disco nem impressa. Ao vivo/gravações exigem o usuário "viewer" com senha aleatória gerada a
cada execução, exibida no terminal e salva em data/poc/viewer.secret (fora do Git).
Ctrl+C encerra.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from _common import (DATA_DIR, ROOT, camera_rtsp_url, load_dotenv, mask, mediamtx_binary, render_template,
                     require_tool)

TEMPLATE = ROOT / "integrations" / "mediamtx" / "mediamtx.poc.yml.tmpl"
POC_DIR = DATA_DIR / "poc"
API = "http://127.0.0.1:9997"
FAKE_SOURCES = {
    "cam": "rtsp://test:test@127.0.0.1:8654/main",
    "camsub": "rtsp://test:test@127.0.0.1:8654/sub",
}


class Relay:
    """FFmpeg que lê a câmera e republica no MediaMTX local sem recodificar.

    `-use_wallclock_as_timestamps 1` substitui os timestamps RTP da câmera (inconsistentes na
    X6C-WEQ) pelo relógio do servidor, eliminando os resets por deriva do gravador.
    A saída de erro do FFmpeg é mascarada antes de ser exibida (pode conter a URL da câmera).
    """

    def __init__(self, name: str, source_url: str, relay_pass: str, cam_password: str) -> None:
        self.name, self.source_url, self.cam_password = name, source_url, cam_password
        self.target = f"rtsp://relay:{relay_pass}@127.0.0.1:8554/{name}"
        self.secrets = (cam_password, relay_pass)
        self.proc: subprocess.Popen[str] | None = None
        self.next_start = 0.0

    def supervise(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        if self.proc is not None:
            print(f"relay '{self.name}' terminou (código {self.proc.returncode}); nova tentativa em 5 s", flush=True)
            self.proc = None
            self.next_start = time.monotonic() + 5
        if time.monotonic() < self.next_start:
            return
        cmd = [require_tool("ffmpeg"), "-hide_banner", "-loglevel", "warning", "-nostdin",
               "-rtsp_transport", "tcp", "-timeout", "10000000", "-use_wallclock_as_timestamps", "1",
               "-i", self.source_url, "-map", "0", "-c", "copy",
               "-f", "rtsp", "-rtsp_transport", "tcp", self.target]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        threading.Thread(target=self._pump_stderr, args=(self.proc,), daemon=True).start()

    def _pump_stderr(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            print(f"[relay {self.name}] {mask(line.rstrip(), *self.secrets)}", flush=True)

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def real_sources() -> tuple[dict[str, str], str]:
    env = load_dotenv()
    sources = {"cam": camera_rtsp_url(env)}
    if env.get("NVR_CAM_RTSP_SUBPATH"):
        sources["camsub"] = camera_rtsp_url(env, "NVR_CAM_RTSP_SUBPATH")
    return sources, env["NVR_CAM_PASSWORD"]


def sha256_hash(password: str) -> str:
    return "sha256:" + base64.b64encode(hashlib.sha256(password.encode()).digest()).decode()


def api_call(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        # Nunca repassar o corpo bruto: pode ecoar a URL de origem com credenciais.
        raise SystemExit(f"API MediaMTX {method} {path}: HTTP {exc.code} {mask(exc.read().decode(errors='replace'))}")


def wait_api(timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            api_call("GET", "/v3/paths/list")
            return
        except (OSError, SystemExit):
            time.sleep(0.3)
    raise SystemExit("API do MediaMTX não respondeu.")


def lan_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))  # não envia pacotes; só escolhe a interface de saída
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=["fake", "real"], default="fake")
    parser.add_argument("--segment", default="1m", help="recordSegmentDuration (ex.: 1m, 5m)")
    parser.add_argument("--relay", action="store_true",
                        help="ler a câmera com FFmpeg (-c copy) e republicar com timestamps do relógio do servidor")
    args = parser.parse_args()

    sources, cam_password = (FAKE_SOURCES, "") if args.source == "fake" else real_sources()
    viewer_pass = secrets.token_urlsafe(12)
    relay_pass = secrets.token_urlsafe(18)
    # Pasta separada por fonte, para nunca misturar gravações sintéticas com as da câmera real.
    variant = args.source + ("-relay" if args.relay else "")
    record_dir = (POC_DIR / "recordings" / variant).as_posix()
    (POC_DIR / "active_source").parent.mkdir(parents=True, exist_ok=True)
    (POC_DIR / "active_source").write_text(variant, encoding="utf-8")
    config = POC_DIR / "mediamtx.yml"
    render_template(TEMPLATE, config, {
        "VIEWER_USER": "viewer",
        "VIEWER_PASS_HASH": sha256_hash(viewer_pass),
        "RELAY_PASS_HASH": sha256_hash(relay_pass),
        "RECORD_PATH": f"{record_dir}/%path/%Y-%m-%d_%H-%M-%S-%f",
        "SEGMENT_DURATION": args.segment,
    })
    secret_file = POC_DIR / "viewer.secret"
    secret_file.write_text(viewer_pass, encoding="utf-8")
    if sys.platform != "win32":
        secret_file.chmod(0o600)

    server = subprocess.Popen([str(mediamtx_binary()), str(config)], cwd=POC_DIR)
    relays: dict[str, Relay] = {}
    try:
        wait_api()
        for name, url in sources.items():
            if args.relay:
                api_call("POST", f"/v3/config/paths/add/{name}", {"source": "publisher", "record": name == "cam"})
                relays[name] = Relay(name, url, relay_pass, cam_password)
            else:
                api_call("POST", f"/v3/config/paths/add/{name}", {
                    "source": url,
                    "rtspTransport": "tcp",
                    "sourceOnDemand": False,
                    "record": name == "cam",
                })
            mode = "via relay FFmpeg" if args.relay else "direto"
            print(f"path '{name}' cadastrado ({mode}): {mask(url, cam_password)}", flush=True)

        ip = lan_ip()
        print(f"""
=== PoC NVR em execução (Ctrl+C para sair) ===
Usuário: viewer   Senha: {viewer_pass}
Ao vivo WebRTC (principal):  http://{ip}:8889/cam
Ao vivo WebRTC (substream):  http://{ip}:8889/camsub
Ao vivo HLS:                 http://{ip}:8888/cam
Gravações (lista):           http://{ip}:9996/list?path=cam
Pasta das gravações:         {record_dir}
""")
        while server.poll() is None:
            for relay in relays.values():
                relay.supervise()
            time.sleep(1.0)
        print("MediaMTX terminou.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        for relay in relays.values():
            relay.stop()
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    sys.exit(main())
