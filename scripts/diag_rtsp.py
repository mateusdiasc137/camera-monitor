"""Diagnóstico RTSP da câmera real (F0-06). Somente leitura: não altera nada na câmera.

Uso: python scripts/diag_rtsp.py [--seconds 30]

Lê NVR_CAM_* do .env, identifica codecs/resolução/fps dos perfis principal e secundário e mede o
bitrate real gravando N segundos em arquivo temporário (apagado em seguida). Toda saída passa
pelo mascaramento de credenciais. O relatório JSON vai para docs/compatibility/raw/ (fora do Git).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from _common import ROOT, camera_rtsp_url, load_dotenv, mask, require_tool

RAW_DIR = ROOT / "docs" / "compatibility" / "raw"


def probe(url: str, secret: str) -> dict:
    cmd = [require_tool("ffprobe"), "-v", "error", "-rtsp_transport", "tcp", "-timeout", "10000000",
           "-show_entries",
           "stream=codec_type,codec_name,profile,width,height,r_frame_rate,avg_frame_rate,sample_rate,channels",
           "-of", "json", url]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
    if proc.returncode != 0:
        return {"ok": False, "error": mask(proc.stderr.strip(), secret)[-500:]}
    return {"ok": True, "streams": json.loads(proc.stdout).get("streams", [])}


def measure(url: str, secret: str, seconds: int) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "sample.mkv"
        cmd = [require_tool("ffmpeg"), "-v", "error", "-rtsp_transport", "tcp", "-timeout", "10000000",
               "-i", url, "-t", str(seconds), "-c", "copy", "-y", str(out)]
        start = time.monotonic()
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=seconds + 60)
        elapsed = time.monotonic() - start
        if proc.returncode != 0 or not out.exists():
            return {"ok": False, "error": mask(proc.stderr.strip(), secret)[-500:]}
        size = out.stat().st_size
        gop = keyframe_interval(out)
    mbps = size * 8 / seconds / 1_000_000
    return {"ok": True, "seconds": seconds, "bytes": size, "mbps": round(mbps, 3),
            "gb_per_day": round(mbps * 10.8, 1), "keyframe_interval_s": gop,
            "wall_time_s": round(elapsed, 1),
            "warnings": mask(proc.stderr.strip(), secret)[-500:] or None}


def keyframe_interval(path: Path) -> float | None:
    cmd = [require_tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
           "-show_entries", "frame=pts_time", "-of", "csv=p=0", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    times = [float(t) for t in proc.stdout.split() if t.strip().replace(".", "", 1).isdigit()]
    if len(times) < 2:
        return None
    return round((times[-1] - times[0]) / (len(times) - 1), 2)


def describe(streams: list[dict]) -> str:
    parts = []
    for s in streams:
        if s.get("codec_type") == "video":
            parts.append(f"vídeo {s.get('codec_name')} {s.get('profile', '')} {s.get('width')}x{s.get('height')} "
                         f"@ {s.get('avg_frame_rate') or s.get('r_frame_rate')}")
        elif s.get("codec_type") == "audio":
            parts.append(f"áudio {s.get('codec_name')} {s.get('sample_rate')} Hz {s.get('channels')} canal(is)")
    return "; ".join(parts) or "nenhuma faixa"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seconds", type=int, default=30, help="duração da medição de bitrate")
    args = parser.parse_args()

    env = load_dotenv()
    secret = env.get("NVR_CAM_PASSWORD", "")
    profiles = {"main": camera_rtsp_url(env)}
    if env.get("NVR_CAM_RTSP_SUBPATH"):
        profiles["sub"] = camera_rtsp_url(env, "NVR_CAM_RTSP_SUBPATH")

    report: dict = {"timestamp": datetime.now().astimezone().isoformat(), "host": env["NVR_CAM_HOST"],
                    "profiles": {}}
    for name, url in profiles.items():
        print(f"\n== perfil {name}: {mask(url, secret)}")
        info = probe(url, secret)
        if not info["ok"]:
            print(f"  FALHA ao abrir: {info['error']}")
            if "401" in info["error"]:
                print("  → câmera recusou usuário/senha (auth_error). Confira NVR_CAM_USER/NVR_CAM_PASSWORD.")
            report["profiles"][name] = {"probe": info}
            continue
        print(f"  {describe(info['streams'])}")
        print(f"  medindo bitrate por {args.seconds} s...")
        rate = measure(url, secret, args.seconds)
        if rate["ok"]:
            print(f"  bitrate médio: {rate['mbps']} Mbps (≈ {rate['gb_per_day']} GB/dia); "
                  f"intervalo entre keyframes: {rate['keyframe_interval_s']} s")
        else:
            print(f"  FALHA na medição: {rate['error']}")
        report["profiles"][name] = {"probe": info, "bitrate": rate}

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out = RAW_DIR / f"rtsp_probe_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(mask(json.dumps(report, indent=2, ensure_ascii=False), secret), encoding="utf-8")
    print(f"\nRelatório: {out.relative_to(ROOT)}")
    return 0 if all(p["probe"]["ok"] for p in report["profiles"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
