"""Cliente da API de controle do MediaMTX (v1.21, /v3). Só fala com o loopback."""

from __future__ import annotations

from typing import Any

import httpx

from nvr.shared.errors import MediaServerUnavailable
from nvr.streaming.domain import MANAGED_PREFIX, PathSpec, PathState


class MediaMtxApi:
    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, url: str, json: dict[str, Any] | None = None) -> httpx.Response:
        try:
            response = await self._client.request(method, url, json=json)
        except httpx.HTTPError as exc:
            raise MediaServerUnavailable(f"MediaMTX não respondeu ({type(exc).__name__})") from exc
        return response

    async def _paged(self, url: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 0
        while True:
            response = await self._request("GET", f"{url}?itemsPerPage=200&page={page}")
            if response.status_code != 200:
                raise MediaServerUnavailable(f"MediaMTX respondeu {response.status_code} em {url}")
            body = response.json()
            items += body.get("items") or []
            page += 1
            if page >= int(body.get("pageCount") or 1):
                return items

    async def list_configured(self) -> dict[str, PathSpec]:
        items = await self._paged("/v3/config/paths/list")
        return {
            item["name"]: PathSpec(item["name"], bool(item.get("record")), str(item.get("recordPath", "")))
            for item in items
            if str(item.get("name", "")).startswith(MANAGED_PREFIX)
        }

    async def put_path(self, spec: PathSpec) -> None:
        body = {"source": "publisher", "record": spec.record, "recordPath": spec.record_path}
        response = await self._request("POST", f"/v3/config/paths/replace/{spec.name}", body)
        if response.status_code == 404:
            response = await self._request("POST", f"/v3/config/paths/add/{spec.name}", body)
        if response.status_code != 200:
            raise MediaServerUnavailable(f"MediaMTX recusou o path {spec.name}: {response.status_code}")

    async def remove_path(self, name: str) -> None:
        response = await self._request("DELETE", f"/v3/config/paths/delete/{name}")
        if response.status_code not in (200, 404):
            raise MediaServerUnavailable(f"MediaMTX não removeu o path {name}: {response.status_code}")

    async def states(self) -> dict[str, PathState]:
        items = await self._paged("/v3/paths/list")
        return {
            item["name"]: PathState(
                name=item["name"],
                ready=bool(item.get("ready")),
                bytes_received=int(item.get("bytesReceived") or 0),
                readers=len(item.get("readers") or []),
                tracks=tuple(item.get("tracks") or ()),
            )
            for item in items
            if str(item.get("name", "")).startswith(MANAGED_PREFIX)
        }
