"""Isolamento dos testes: nenhum teste fala com o MediaMTX de desenvolvimento nem com câmeras reais.

Testes que precisam de mídia sobem a própria instância (portas alternativas) e sobrescrevem estes valores.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_media(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVR_MEDIA_ENABLED", "false")
    monkeypatch.setenv("NVR_MEDIAMTX_API_URL", "http://127.0.0.1:1")  # porta que ninguém escuta
    monkeypatch.setenv("NVR_FFMPEG_PATH", "ffmpeg-desativado-nos-testes")
    monkeypatch.setenv("NVR_SECRET_BACKEND", "memory")  # nunca grava no cofre real do SO
