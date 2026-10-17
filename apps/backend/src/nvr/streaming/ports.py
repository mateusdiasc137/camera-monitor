from __future__ import annotations

from typing import Protocol

from nvr.streaming.domain import PathSpec, PathState, RelaySpec, RelayStatus


class MediaServer(Protocol):
    """Servidor de mídia (MediaMTX). Configuração aplicada pela API de controle, em memória."""

    async def list_configured(self) -> dict[str, PathSpec]:
        """Paths configurados que o NVR gerencia (prefixo `cam-`)."""
        ...

    async def put_path(self, spec: PathSpec) -> None:
        """Cria ou substitui o path."""
        ...

    async def remove_path(self, name: str) -> None: ...
    async def states(self) -> dict[str, PathState]: ...


class RelaySupervisor(Protocol):
    """Mantém um processo de relay por path, reiniciando-o se cair."""

    def ensure(self, spec: RelaySpec) -> None:
        """Inicia o relay, ou reinicia se a URL de origem mudou. Idempotente."""
        ...

    async def stop(self, name: str) -> None: ...
    def names(self) -> set[str]: ...
    def statuses(self) -> dict[str, RelayStatus]: ...
    async def stop_all(self) -> None: ...
