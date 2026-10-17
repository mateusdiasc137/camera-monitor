"""Barramento de eventos em memória (um processo). Alimenta o SSE da interface.

Cada assinante tem uma fila limitada; se um cliente lento não consumir, os eventos mais antigos
são descartados (o cliente recarrega o estado completo ao reconectar).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Event:
    type: str  # ex.: camera.status
    data: dict[str, Any] = field(default_factory=dict)


class EventPublisher(Protocol):
    def publish(self, event: Event) -> None: ...


class EventBus:
    def __init__(self, queue_size: int = 100) -> None:
        self._queue_size = queue_size
        self._subscribers: set[asyncio.Queue[Event]] = set()

    def publish(self, event: Event) -> None:
        for queue in list(self._subscribers):
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(event)

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[Event]]:
        queue: asyncio.Queue[Event] = asyncio.Queue(self._queue_size)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
