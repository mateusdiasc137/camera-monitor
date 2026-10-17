"""Relays FFmpeg supervisionados pelo backend (ADR-002).

Cada relay lê a câmera e republica no MediaMTX local sem recodificar, com timestamps do relógio do
servidor. Os argumentos vão direto para o processo (sem shell, sem expansão de variáveis), então
senhas com `$`, `&` ou aspas funcionam. Toda saída do FFmpeg é mascarada antes de ir para o log.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field

from nvr.shared.redact import redact
from nvr.streaming.domain import RelaySpec, RelayStatus

log = logging.getLogger(__name__)

STABLE_AFTER = 60.0  # rodando por mais que isso, o backoff volta ao mínimo
BACKOFF_MIN, BACKOFF_MAX = 2.0, 30.0


def ffmpeg_relay_args(ffmpeg: str, source_url: str, target_url: str) -> list[str]:
    return [
        ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostdin",
        "-rtsp_transport", "tcp", "-timeout", "10000000",
        "-use_wallclock_as_timestamps", "1",
        "-i", source_url,
        "-map", "0", "-c", "copy",
        "-f", "rtsp", "-rtsp_transport", "tcp", target_url,
    ]  # fmt: skip


@dataclass
class _Relay:
    spec: RelaySpec
    task: asyncio.Task[None] | None = None
    process: asyncio.subprocess.Process | None = None
    restarts: int = 0
    last_exit_code: int | None = None
    stderr_tail: deque[str] = field(default_factory=lambda: deque(maxlen=5))


class FfmpegRelaySupervisor:
    def __init__(
        self,
        ffmpeg: str,
        publish_host: str,
        publish_user: str,
        publish_secret: str,
        command: Sequence[str] | None = None,
        backoff: tuple[float, float] = (BACKOFF_MIN, BACKOFF_MAX),
    ) -> None:
        """`command` substitui o FFmpeg (testes); recebe `{source}` e `{target}` como argumentos."""
        self._ffmpeg = ffmpeg
        self._publish = f"rtsp://{publish_user}:{publish_secret}@{publish_host}"
        self._secret = publish_secret
        self._command = list(command) if command else None
        self._relays: dict[str, _Relay] = {}
        self._backoff_min, self._backoff_max = backoff
        self._retiring: set[asyncio.Task[None]] = set()  # relays substituídos, ainda encerrando o processo

    def _args(self, relay: _Relay) -> list[str]:
        target = f"{self._publish}/{relay.spec.name}"
        if self._command:
            return [
                a.replace("{source}", relay.spec.source_url).replace("{target}", target)
                for a in self._command
            ]
        return ffmpeg_relay_args(self._ffmpeg, relay.spec.source_url, target)

    def _redact(self, text: str, relay: _Relay) -> str:
        return redact(text, relay.spec.source_url, self._secret)

    def ensure(self, spec: RelaySpec) -> None:
        current = self._relays.get(spec.name)
        if current is not None and current.spec == spec and current.task and not current.task.done():
            return
        if current is not None and current.task and not current.task.done():
            # URL mudou (ex.: senha/host): encerra o antigo sem perder a referência (stop_all espera por ele).
            current.task.cancel()
            self._retiring.add(current.task)
            current.task.add_done_callback(self._retiring.discard)
        relay = _Relay(spec)
        relay.task = asyncio.get_running_loop().create_task(self._run(relay), name=f"relay:{spec.name}")
        self._relays[spec.name] = relay

    async def _run(self, relay: _Relay) -> None:
        backoff = self._backoff_min
        while True:
            started = time.monotonic()
            try:
                relay.process = await asyncio.create_subprocess_exec(
                    *self._args(relay),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
            except OSError as exc:
                relay.stderr_tail.append(f"não foi possível iniciar o FFmpeg: {type(exc).__name__}")
                log.error("relay %s: não foi possível iniciar o FFmpeg (%s)", relay.spec.name, exc)
            else:
                log.info("relay %s iniciado (pid %s)", relay.spec.name, relay.process.pid)
                try:
                    await self._pump(relay, relay.process)
                    relay.last_exit_code = await relay.process.wait()
                finally:
                    await self._terminate(relay.process)
                log.warning(
                    "relay %s terminou (código %s): %s",
                    relay.spec.name, relay.last_exit_code, " | ".join(relay.stderr_tail) or "sem mensagens",
                )  # fmt: skip
            relay.restarts += 1
            stable = time.monotonic() - started > STABLE_AFTER
            backoff = self._backoff_min if stable else min(backoff * 2, self._backoff_max)
            await asyncio.sleep(backoff)

    async def _pump(self, relay: _Relay, process: asyncio.subprocess.Process) -> None:
        if process.stderr is None:
            return
        async for raw in process.stderr:
            line = self._redact(raw.decode("utf-8", errors="replace").strip(), relay)
            if line:
                relay.stderr_tail.append(line)
                log.debug("relay %s: %s", relay.spec.name, line)

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
        # Drena o stderr até EOF: sem isso o transporte do pipe fica aberto
        # (no Windows, vazaria a cada reinício do relay).
        if process.stderr is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(process.stderr.read(), 2)

    async def stop(self, name: str) -> None:
        relay = self._relays.pop(name, None)
        if relay is None or relay.task is None:
            return
        relay.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await relay.task
        log.info("relay %s parado", name)

    def names(self) -> set[str]:
        return set(self._relays)

    def statuses(self) -> dict[str, RelayStatus]:
        return {
            name: RelayStatus(
                name=name,
                running=r.process is not None and r.process.returncode is None,
                restarts=r.restarts,
                last_exit_code=r.last_exit_code,
                last_error=r.stderr_tail[-1] if r.stderr_tail else "",
            )
            for name, r in self._relays.items()
        }

    async def stop_all(self) -> None:
        for name in list(self._relays):
            await self.stop(name)
        if self._retiring:
            await asyncio.gather(*self._retiring, return_exceptions=True)
