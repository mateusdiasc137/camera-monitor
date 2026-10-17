"""Servidor de playback do MediaMTX (v1.21): GET /get?path&start&duration&format. Só no loopback."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

import httpx

from nvr.shared.errors import MediaServerUnavailable, NotFound, ValidationFailed
from nvr.shared.redact import redact

CHUNK = 64 * 1024


class _HttpxRecordingStream:
    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self.content_type = response.headers.get("content-type", "video/mp4")

    async def chunks(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self._response.aiter_bytes(CHUNK):
                yield chunk
        finally:
            await self._response.aclose()

    async def aclose(self) -> None:
        await self._response.aclose()


class MediaMtxPlayback:
    def __init__(self, base_url: str, user: str, secret: str, timeout: float = 30.0) -> None:
        self._secret = secret
        # O MediaMTX gera o trecho enquanto envia, mas só a partir de dados já gravados (não espera o
        # "ao vivo"): um bloco que demore mais que `read` indica servidor travado — não prender a conexão.
        self._client = httpx.AsyncClient(
            base_url=base_url, auth=(user, secret), timeout=httpx.Timeout(timeout, read=60.0)
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def open(self, path: str, start: datetime, duration_s: float, fmt: str) -> _HttpxRecordingStream:
        params = {"path": path, "start": start.isoformat(), "duration": f"{duration_s:.3f}", "format": fmt}
        request = self._client.build_request("GET", "/get", params=params)
        try:
            response = await self._client.send(request, stream=True)
        except httpx.HTTPError as exc:
            raise MediaServerUnavailable(
                f"servidor de playback não respondeu ({type(exc).__name__})"
            ) from exc
        if response.status_code == 200:
            return _HttpxRecordingStream(response)
        body = (await response.aread()).decode("utf-8", errors="replace")[:300]
        await response.aclose()
        if response.status_code == 404:
            raise NotFound("não há gravação nesse momento")
        if response.status_code == 400:
            raise ValidationFailed(f"pedido de reprodução inválido: {redact(body, self._secret)}")
        raise MediaServerUnavailable(f"servidor de playback respondeu {response.status_code}")
