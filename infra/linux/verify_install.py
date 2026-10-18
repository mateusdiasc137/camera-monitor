"""Confere uma instalação feita por infra/linux/install.sh (usado no CI e no servidor de verdade).

sudo /opt/nvr/app/.venv/bin/python infra/linux/verify_install.py
    serviços, usuários, portas, HTTPS/CA, permissões e nota de segurança do systemd (só leitura)
... --user admin --password-env VAR
    + login pelo HTTPS e saúde do sistema
... --user admin --password-env VAR --camera 127.0.0.1:8654
    + cadastra a câmera falsa (scripts/fake_camera.py), confere gravação, permissões entre os serviços,
      download recortado (FFmpeg no sandbox) e a volta após reiniciar o MediaMTX. Só para teste!
"""

from __future__ import annotations

import argparse
import os
import pwd
import re
import ssl
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx

CA = Path("/var/lib/nvr-caddy/caddy/pki/authorities/local/root.crt")
UNITS = {"nvr-mediamtx": "nvr-media", "nvr-api": "nvr", "nvr-caddy": "nvr-caddy"}
LOOPBACK_ONLY = {
    8000: "nvr-api",
    9997: "MediaMTX API",
    8554: "RTSP",
    8889: "WebRTC HTTP",
    8888: "HLS",
    9996: "playback",
}
MAX_EXPOSURE = 5.0  # systemd-analyze security: abaixo de 5 = "MEDIUM" ou melhor
results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: object = "") -> bool:
    results.append((name, ok))
    print(f"{'OK   ' if ok else 'FALHA'} {name}{f' — {detail}' if detail != '' else ''}", flush=True)
    return ok


def run(*cmd: str) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout


def env_value(path: Path, key: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip("'\"")
    return ""


def wait(predicate, timeout: float, step: float = 2.0):  # type: ignore[no-untyped-def]
    end = time.monotonic() + timeout
    while True:
        value = predicate()
        if value or time.monotonic() > end:
            return value
        time.sleep(step)


def check_services() -> None:
    for unit, user in UNITS.items():
        active = run("systemctl", "is-active", unit).strip()
        enabled = run("systemctl", "is-enabled", unit).strip()
        check(
            f"{unit}: ativo e habilitado no boot",
            (active, enabled) == ("active", "enabled"),
            f"{active}/{enabled}",
        )
        pid = run("systemctl", "show", "-p", "MainPID", "--value", unit).strip()
        if pid and pid != "0":
            uid = int(re.search(r"^Uid:\s+(\d+)", Path(f"/proc/{pid}/status").read_text(), re.M).group(1))  # type: ignore[union-attr]
            check(f"{unit}: roda como {user}", pwd.getpwuid(uid).pw_name == user, pwd.getpwuid(uid).pw_name)
        score = re.search(
            r"Overall exposure level for \S+: ([\d.]+)", run("systemd-analyze", "security", unit)
        )
        if score:
            check(
                f"{unit}: isolamento do systemd",
                float(score.group(1)) < MAX_EXPOSURE,
                f"exposição {score.group(1)}",
            )


def check_ports() -> None:
    listening = run("ss", "-H", "-lntup")
    sockets = []
    for line in listening.splitlines():
        parts = line.split()
        if len(parts) >= 5:
            address, _, port = parts[4].rpartition(":")
            sockets.append((parts[0], address.strip("[]"), int(port), line))
    for port, name in LOOPBACK_ONLY.items():
        bound = [s for s in sockets if s[0] == "tcp" and s[2] == port]
        check(
            f"porta {port} ({name}) só no loopback",
            bool(bound) and all(s[1] in ("127.0.0.1", "::1") for s in bound),
            ", ".join(s[1] for s in bound) or "não escuta",
        )
    for port in (80, 443):
        bound = [s for s in sockets if s[0] == "tcp" and s[2] == port]
        check(f"porta {port}/tcp na rede pelo Caddy", bool(bound) and all("caddy" in s[3] for s in bound))
    check("porta 8189/udp (WebRTC) aberta", any(s[0] == "udp" and s[2] == 8189 for s in sockets))


def check_files() -> list[Path]:
    key = Path("/etc/nvr/secrets.key")
    info = key.stat()
    check(
        "chave do cofre: 0600, só do usuário nvr",
        stat.S_IMODE(info.st_mode) == 0o600 and pwd.getpwuid(info.st_uid).pw_name == "nvr",
    )
    roots = [
        Path(p) for p in re.findall(r'"([^"]+)"', env_value(Path("/etc/nvr/nvr.env"), "NVR_STORAGE_ROOTS"))
    ]
    for root in roots:
        info = root.stat()
        ok = root.group() == "nvr" and stat.S_IMODE(info.st_mode) & 0o2070 == 0o2070
        check(
            f"raiz {root}: grupo nvr com setgid",
            ok,
            f"{root.owner()}:{root.group()} {oct(stat.S_IMODE(info.st_mode))}",
        )
    return roots


def https_client(origin: str = "https://127.0.0.1") -> httpx.Client:
    return httpx.Client(
        base_url="https://127.0.0.1",
        verify=ssl.create_default_context(cafile=str(CA)),
        headers={"Origin": origin},
        timeout=60,
    )


def check_https() -> None:
    check("CA local criada", CA.is_file())
    with https_client() as c:
        check("HTTPS validado pela CA local", c.get("/api/v1/system/ping").json() == {"status": "ok"})
    ca = httpx.get("http://127.0.0.1/nvr-ca.crt", timeout=10)
    check("CA servida por HTTP para os aparelhos", ca.content == CA.read_bytes())
    check("resto do HTTP vai para HTTPS", httpx.get("http://127.0.0.1/x", timeout=10).status_code == 308)
    with https_client() as c:
        check("rotas internas fora do proxy", c.post("/internal/mediamtx/auth", json={}).status_code == 404)


def components(c: httpx.Client) -> dict[str, dict]:  # type: ignore[type-arg]
    health = c.get("/api/v1/system/health").json()
    return {comp["name"]: comp for comp in health["components"]} | {"_cameras": health.get("cameras", [])}


def check_login(user: str, password: str) -> httpx.Client:
    c = https_client()
    login = c.post("/api/v1/auth/login", json={"username": user, "password": password})
    check("login pelo HTTPS", login.status_code == 200, login.status_code)

    def media_sampled():  # type: ignore[no-untyped-def]
        current = components(c)
        return current if current["media_server"]["detail"].startswith("respondendo") else None

    comps = wait(media_sampled, 60) or components(c)
    for name in ("database", "media_server", "storage"):
        check(f"saúde: {name}", comps[name]["state"] == "ok", comps[name]["detail"])
    return c


def check_camera(c: httpx.Client, address: str, roots: list[Path]) -> None:
    host, _, port = address.partition(":")
    camera = c.post(
        "/api/v1/cameras",
        json={
            "name": "Câmera falsa",
            "host": host,
            "rtsp_port": int(port or 554),
            "main_path": "/main",
            "sub_path": "/sub",
            "username": "test",
            "password": "test",
            "lens_layout": "stacked2",
        },
    )
    check("câmera cadastrada", camera.status_code == 201, camera.status_code)
    cam_id = camera.json()["id"]

    def recording_ok():  # type: ignore[no-untyped-def]
        comps = components(c)
        summary = next((s for s in comps["_cameras"] if s["camera_id"] == cam_id), None)
        if (
            summary
            and summary["state"] == "online"
            and summary["last_recording_at"]
            and comps["recording"]["state"] == "ok"
        ):
            return comps
        return None

    comps = wait(recording_ok, 120)
    check(
        "câmera online e gravando (índice com trecho recente)",
        bool(comps),
        (comps or components(c))["recording"]["detail"],
    )

    folders = [p for root in roots for p in root.rglob(f"cam-{cam_id}-main") if p.is_dir()]
    files = sorted(f for folder in folders for f in folder.glob("*.mp4"))
    if check("arquivos de gravação no disco", bool(files), f"{len(files)} arquivo(s)"):
        info = files[0].stat()
        owner = pwd.getpwuid(info.st_uid).pw_name
        check(
            "gravação: dono nvr-media, grupo nvr com leitura e escrita",
            owner == "nvr-media"
            and files[0].group() == "nvr"
            and stat.S_IMODE(info.st_mode) & 0o060 == 0o060,
            f"{owner}:{files[0].group()} {oct(stat.S_IMODE(info.st_mode))}",
        )
        writable = subprocess.run(["runuser", "-u", "nvr", "--", "test", "-w", str(folders[0])], check=False)
        check("backend pode apagar/mover o que o MediaMTX gravou", writable.returncode == 0)

    start = datetime.now().astimezone() - timedelta(seconds=40)
    clip = c.get(
        f"/api/v1/recordings/{cam_id}/video",
        params={
            "start": start.isoformat(),
            "duration": 20,
            "format": "mp4",
            "download": "true",
            "lens": "top",
        },
    )
    if check("download recortado pelo HTTPS (FFmpeg no sandbox)", clip.status_code == 200, clip.status_code):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(clip.content)
        dims = run(
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            tmp.name,
        ).strip()
        os.unlink(tmp.name)
        check("lente superior recortada (metade da altura)", dims == "1280,360", dims)

    subprocess.run(["systemctl", "restart", "nvr-mediamtx"], check=True)
    time.sleep(5)
    check("volta sozinho após reiniciar o MediaMTX", bool(wait(recording_ok, 120)))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--user")
    parser.add_argument("--password-env", help="variável de ambiente com a senha (nunca na linha de comando)")
    parser.add_argument("--camera", help="host:porta da câmera FALSA de teste (cadastra uma câmera!)")
    args = parser.parse_args()
    if os.geteuid() != 0:
        print("rode como root (sudo)", file=sys.stderr)
        return 2
    check_services()
    check_ports()
    roots = check_files()
    check_https()
    if args.user and args.password_env:
        client = check_login(args.user, os.environ[args.password_env])
        if args.camera:
            check_camera(client, args.camera, roots)
    failed = [name for name, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} verificações OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
