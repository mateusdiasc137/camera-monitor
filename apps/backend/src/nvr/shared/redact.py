"""Mascaramento de credenciais em textos que podem ir para logs ou respostas (ADR-003)."""

from __future__ import annotations

import re

_URL_USERINFO = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^/@\s]+@", re.IGNORECASE)
# Formato Xiongmai: /user=admin&password=xyz&channel=1 (ou separado por "_").
_PATH_CREDENTIALS = re.compile(
    r"(?P<key>\b(?:user|username|password|pwd|pass)=)[^&_;\s/?\"'<>,]*", re.IGNORECASE
)


def redact(text: str, *secrets: str) -> str:
    """Troca por *** os segredos conhecidos e qualquer credencial em URL ou caminho."""
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(secret, "***")
    text = _URL_USERINFO.sub(r"\g<scheme>***@", text)
    return _PATH_CREDENTIALS.sub(r"\g<key>***", text)
