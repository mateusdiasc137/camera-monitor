"""Sobe uma câmera RTSP falsa: MediaMTX dedicado (porta 8654, com senha) + FFmpeg gerando vídeo/áudio sintéticos.

Uso: python scripts/fake_camera.py
Leitura: rtsp://test:test@127.0.0.1:8654/main  e  /sub
Ctrl+C encerra tudo.

Perfis (aproximam a câmera real; ajustar após o diagnóstico da Fase 0):
  main: 1280x720, 15 fps, H.264, GOP 2 s + áudio G.711 A-law (PCMA) 8 kHz
  sub:   640x360, 15 fps, H.264, GOP 2 s + áudio G.711 A-law (PCMA) 8 kHz
"""

from __future__ import annotations

import subprocess
import sys
import time

from _common import ROOT, mediamtx_binary, require_tool

CONFIG = ROOT / "integrations" / "fixtures" / "fake-camera" / "mediamtx.fake-camera.yml"
PUBLISH_BASE = "rtsp://fakepublisher:fakepublisher@127.0.0.1:8654"


def ffmpeg_command(ffmpeg: str) -> list[str]:
    video_opts = ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
                  "-profile:v", "main", "-pix_fmt", "yuv420p", "-g", "30", "-bf", "0"]
    audio_opts = ["-c:a", "pcm_alaw", "-ar", "8000", "-ac", "1"]
    rtsp_out = ["-f", "rtsp", "-rtsp_transport", "tcp"]
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-re",
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=15",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=8000",
        "-filter_complex", "[0:v]split=2[vmain][vsub0];[vsub0]scale=640:360[vsub]",
        "-map", "[vmain]", "-map", "1:a", *video_opts, "-b:v", "2M", *audio_opts,
        *rtsp_out, f"{PUBLISH_BASE}/main",
        "-map", "[vsub]", "-map", "1:a", *video_opts, "-b:v", "500k", *audio_opts,
        *rtsp_out, f"{PUBLISH_BASE}/sub",
    ]


def main() -> int:
    ffmpeg = require_tool("ffmpeg")
    server = subprocess.Popen([str(mediamtx_binary()), str(CONFIG)])
    publisher: subprocess.Popen[bytes] | None = None
    print("Câmera falsa: rtsp://test:test@127.0.0.1:8654/main  (e /sub). Ctrl+C para sair.")
    try:
        time.sleep(1.0)
        while True:
            if server.poll() is not None:
                print("MediaMTX da câmera falsa terminou.", file=sys.stderr)
                return 1
            if publisher is None or publisher.poll() is not None:
                if publisher is not None:
                    print("FFmpeg terminou; reiniciando em 2 s...", file=sys.stderr)
                    time.sleep(2.0)
                publisher = subprocess.Popen(ffmpeg_command(ffmpeg), stdin=subprocess.DEVNULL)
            time.sleep(1.0)
    except KeyboardInterrupt:
        return 0
    finally:
        for proc in (publisher, server):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()


if __name__ == "__main__":
    sys.exit(main())
