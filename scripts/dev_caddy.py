"""Caddy de desenvolvimento: HTTPS com a CA local na frente do backend e da interface compilada.

Usa o mesmo infra/caddy/Caddyfile da produção, em portas sem privilégio:
  https://<IP-do-PC>:8443  e  https://localhost:8443   (interface + /api → backend em 127.0.0.1:8000)
  http://<IP-do-PC>:8080/nvr-ca.crt                     (certificado raiz para instalar nos aparelhos)

A CA fica em data/caddy (fora do Git) e NÃO é instalada no sistema (skip_install_trust).
Antes: backend rodando (uv run nvr serve) e interface compilada (npm run build em apps/web).
Uso: python scripts/dev_caddy.py [--https-port 8443] [--http-port 8080]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

from _common import ROOT, cert_fingerprint, lan_ipv4, tool_binary

NAME = "nvr-camera-monitor.local"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--https-port", type=int, default=8443)
    parser.add_argument("--http-port", type=int, default=8080)
    parser.add_argument("--backend", default="127.0.0.1:8000")
    args = parser.parse_args()

    web = ROOT / "apps" / "web" / "dist"
    if not (web / "index.html").exists():
        raise SystemExit("Interface não compilada: rode 'npm run build' em apps/web")
    data = ROOT / "data" / "caddy"
    ca_dir = data / "dados" / "caddy" / "pki" / "authorities" / "local"
    ip = lan_ipv4()
    addresses = ", ".join(a for a in (NAME, "localhost", "127.0.0.1", ip) if a)
    env = dict(
        os.environ,
        XDG_DATA_HOME=str(data / "dados"),
        XDG_CONFIG_HOME=str(data / "config"),
        NVR_SITE_ADDRESSES=addresses,
        NVR_BACKEND=args.backend,
        NVR_WEB_ROOT=str(web),
        NVR_CA_DIR=str(ca_dir),
        NVR_HTTPS_PORT=str(args.https_port),
        NVR_HTTP_PORT=str(args.http_port),
        NVR_HTTPS_SUFFIX=f":{args.https_port}",
        NVR_CADDY_ADMIN="off",
    )
    caddy = subprocess.Popen(
        [str(tool_binary("caddy")), "run", "--config", str(ROOT / "infra" / "caddy" / "Caddyfile"),
         "--adapter", "caddyfile"],
        env=env,
    )  # fmt: skip
    try:
        for _ in range(100):
            if (ca_dir / "root.crt").exists() or caddy.poll() is not None:
                break
            time.sleep(0.1)
        if caddy.poll() is not None:
            return caddy.returncode or 1
        host = ip or "localhost"
        print(
            f"""
==== Caddy de desenvolvimento ====
Interface:      https://{host}:{args.https_port}   (também https://localhost:{args.https_port})
CA para baixar: http://{host}:{args.http_port}/nvr-ca.crt
Arquivo da CA:  {ca_dir / "root.crt"}
SHA-256 da CA:  {cert_fingerprint(ca_dir / "root.crt")}
Confira esse SHA-256 no aparelho antes de confiar (docs/operations/https-e-certificado.md).
No Windows, libere as portas {args.https_port}/TCP e {args.http_port}/TCP no firewall (rede Privada) para o celular.
""",
            flush=True,
        )
        return caddy.wait()
    except KeyboardInterrupt:
        return 0
    finally:
        if caddy.poll() is None:
            caddy.terminate()
            caddy.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main())
