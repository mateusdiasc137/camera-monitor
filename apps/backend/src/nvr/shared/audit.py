from __future__ import annotations

from typing import Any, Protocol


class AuditLog(Protocol):
    """Registro de ações administrativas. `detail` nunca deve conter segredos."""

    async def record(
        self, actor: str, action: str, target: str | None = None, detail: dict[str, Any] | None = None
    ) -> None: ...
