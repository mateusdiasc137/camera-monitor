"""Negociação WHEP com o servidor WebRTC do MediaMTX (v1.21), em nome do navegador.

O MediaMTX responde 201 com o SDP de resposta e `Location: /{path}/whep/{segredo}`; PATCH/DELETE da
sessão usam esse segredo (uuid). O backend usa a credencial `reader`, aceita só pelo próprio backend.
"""

from __future__ import annotations

import httpx

from nvr.shared.errors import CameraOffline, MediaServerUnavailable, UnsupportedFeature, ValidationFailed
from nvr.shared.redact import redact
from nvr.streaming.domain import LiveAnswer

MAX_SDP = 64 * 1024


class MediaMtxWhep:
    def __init__(self, base_url: str, user: str, secret: str, timeout: float = 10.0) -> None:
        self._secret = secret
        self._client = httpx.AsyncClient(base_url=base_url, auth=(user, secret), timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _send(
        self, method: str, url: str, content: str | None, content_type: str | None
    ) -> httpx.Response:
        headers = {"Content-Type": content_type} if content_type else {}
        if method == "PATCH":
            headers["If-Match"] = "*"
        try:
            return await self._client.request(method, url, content=content, headers=headers)
        except httpx.HTTPError as exc:
            raise MediaServerUnavailable(f"servidor de mídia não respondeu ({type(exc).__name__})") from exc

    def _error(self, response: httpx.Response) -> str:
        try:
            message = str(response.json().get("error", ""))
        except ValueError:
            message = response.text[:200]
        return redact(message, self._secret)

    async def offer(self, path: str, sdp: str) -> LiveAnswer:
        response = await self._send("POST", f"/{path}/whep", sdp, "application/sdp")
        if response.status_code == 201:
            location = response.headers.get("location", "")
            session_id = location.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
            if not session_id or len(response.content) > MAX_SDP:
                raise MediaServerUnavailable("resposta WHEP inválida do servidor de mídia")
            return LiveAnswer(response.text, session_id)
        if response.status_code == 404:
            raise CameraOffline("a câmera não está transmitindo agora")
        if response.status_code == 400:
            detail = self._error(response)
            if "codec" in detail.lower():
                raise UnsupportedFeature(
                    "o navegador não aceita os codecs desta câmera (H.265); use o Google Chrome atualizado"
                )
            raise ValidationFailed(f"oferta WebRTC recusada: {detail}")
        if response.status_code == 401:
            raise MediaServerUnavailable("o servidor de mídia recusou o backend (autorização)")
        raise MediaServerUnavailable(f"servidor de mídia respondeu {response.status_code}")

    async def patch(self, path: str, session_id: str, fragment: str) -> None:
        response = await self._send(
            "PATCH", f"/{path}/whep/{session_id}", fragment, "application/trickle-ice-sdpfrag"
        )
        if response.status_code == 404:
            raise CameraOffline("sessão ao vivo encerrada")
        if response.status_code not in (200, 204):
            raise MediaServerUnavailable(f"servidor de mídia respondeu {response.status_code}")

    async def close(self, path: str, session_id: str) -> None:
        response = await self._send("DELETE", f"/{path}/whep/{session_id}", None, None)
        if response.status_code not in (200, 204, 404):
            raise MediaServerUnavailable(f"servidor de mídia respondeu {response.status_code}")
