"""Mede a deriva dos timestamps de cada faixa da câmera em relação ao relógio real (F0-07).

Uso: python scripts/diag_clock.py [--seconds 60] [--profile main|sub]

Lê os pacotes por N segundos de relógio de parede e compara, por faixa, o avanço dos timestamps
(pts) com o tempo real decorrido. Taxa 1,000 = relógio correto; 0,98 = a faixa "anda" 2% mais
devagar que o tempo real. Somente leitura.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import time

from _common import camera_rtsp_url, load_dotenv, mask, require_tool


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--profile", choices=["main", "sub"], default="main")
    parser.add_argument("--warmup", type=float, default=15, help="segundos iniciais ignorados")
    args = parser.parse_args()
    started = time.monotonic()
    env = load_dotenv()
    if args.profile == "sub":
        env["NVR_CAM_RTSP_SUBPATH"] = env.get("NVR_CAM_RTSP_SUBPATH") or env["NVR_CAM_RTSP_PATH"].replace("stream=0", "stream=1")
        url = camera_rtsp_url(env, "NVR_CAM_RTSP_SUBPATH")
    else:
        url = camera_rtsp_url(env)
    cmd = [require_tool("ffprobe"), "-v", "error", "-rtsp_transport", "tcp", "-show_entries",
           "packet=stream_index,pts_time", "-of", "csv=p=0", url]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    first: dict[str, tuple[float, float]] = {}
    last: dict[str, tuple[float, float]] = {}
    counts: dict[str, int] = {}

    def reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            parts = line.strip().split(",")
            if len(parts) < 2 or parts[1] in ("", "N/A"):
                continue
            idx, pts = parts[0], float(parts[1])
            now = time.monotonic()
            if now - started < args.warmup:  # ignora o burst inicial do buffer da câmera
                continue
            first.setdefault(idx, (now, pts))
            last[idx] = (now, pts)
            counts[idx] = counts.get(idx, 0) + 1

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    time.sleep(args.seconds)
    proc.kill()
    thread.join(timeout=5)
    err = proc.stderr.read() if proc.stderr else ""
    if not first:
        print("nenhum pacote lido:", mask(err, env.get("NVR_CAM_PASSWORD", ""))[-300:])
        return 1
    print(f"perfil {args.profile}, {args.seconds:.0f} s de relógio real")
    for idx in sorted(first):
        (w0, p0), (w1, p1) = first[idx], last[idx]
        wall, media = w1 - w0, p1 - p0
        rate = media / wall if wall > 0 else float("nan")
        print(f"  faixa {idx}: {counts[idx]} pacotes; mídia {media:.2f} s em {wall:.2f} s reais → taxa {rate:.4f} "
              f"(deriva {(rate - 1) * 3600:+.0f} s/hora)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
