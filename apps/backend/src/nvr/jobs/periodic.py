"""Tarefas periódicas em segundo plano (asyncio), iniciadas e paradas junto com a aplicação."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from nvr.shared.errors import AppError

log = logging.getLogger(__name__)


class PeriodicTask:
    def __init__(self, name: str, interval: float, func: Callable[[], Awaitable[object]]) -> None:
        self._name, self._interval, self._func = name, interval, func
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._loop(), name=f"job:{self._name}")

    def trigger(self) -> None:
        """Roda o quanto antes (ex.: logo após alterar uma câmera)."""
        self._wake.set()

    async def _loop(self) -> None:
        while True:
            try:
                await self._func()
            except AppError as exc:
                log.warning("%s: %s", self._name, exc.message)
            except Exception:
                log.exception("%s falhou", self._name)
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self._interval)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
