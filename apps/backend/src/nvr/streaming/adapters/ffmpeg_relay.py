"""Relays FFmpeg supervisionados pelo backend (ADR-002).

Cada relay lê a câmera e republica no MediaMTX local sem recodificar, com timestamps do relógio do
servidor. Os argumentos vão direto para o processo (sem shell, sem expansão de variáveis), então
senhas com `$`, `&` ou aspas funcionam. Toda saída do FFmpeg é mascarada antes de ir para o log.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field

from nvr.shared.redact import redact
from nvr.streaming.domain import RelayFailure, RelaySpec, RelayStatus

log = logging.getLogger(__name__)

STABLE_AFTER = 60.0  # rodando por mais que isso, o backoff volta ao mínimo
HEALTHY_AFTER = 30.0  # rodando por mais que isso sem cair, a causa da última falha é esquecida
BACKOFF_MIN, BACKOFF_MAX = 2.0, 30.0
AUTH_BACKOFF = 300.0  # senha recusada: tentar devagar para não bloquear a conta da câmera
RESTART_WINDOW = 600.0

_PUBLISH_FAILED = re.compile(r"(ANNOUNCE|RECORD|SETUP) failed|Could not write header", re.IGNORECASE)
_AUTH = re.compile(r"\b401\b|Unauthorized|authorization failed", re.IGNORECASE)
_NOT_FOUND = re.compile(r"\b404\b|Not Found", re.IGNORECASE)
_UNREACHABLE = re.compile(
    r"Connection refused|timed out|Network is unreachable|No route to host|Connection reset|Broken pipe|"
    r"Error number -(138|110|111|113|10053|10054|10060|10061)|End of file",
    re.IGNORECASE,
)
# O FFmpeg marca a origem das linhas: [in#0...] = entrada (câmera); [out#0...]/[vost#0...] = saída (MediaMTX).
_INPUT_LINE = re.compile(r"\[in#\d|Error opening input|Error during demuxing", re.IGNORECASE)
_OUTPUT_LINE = re.compile(r"\[(out|vost|aost)#\d|Error muxing|Error writing trailer", re.IGNORECASE)
_ERRORISH = re.compile(r"error|failed|broken pipe|refused", re.IGNORECASE)


def classify_failure(lines: Sequence[str], publish_host: str | None = None) -> RelayFailure | None:
    """Causa provável a partir das últimas linhas do FFmpeg (já mascaradas).

    A mesma mensagem de rede ("Connection refused", "-10054") aparece para os dois lados do relay; o
    lado é decidido pelas marcações de origem do FFmpeg e pelo endereço de publicação (`publish_host`).
    Exemplos reais (Windows, FFmpeg 9): ver tests/unit/test_camera_health.py.
    """
    text = "\n".join(lines)
    if not text:
        return None
    input_error = any(_INPUT_LINE.search(line) and _ERRORISH.search(line) for line in lines)
    output_error = any(_OUTPUT_LINE.search(line) and _ERRORISH.search(line) for line in lines)
    if _PUBLISH_FAILED.search(text):
        return RelayFailure.PUBLISH  # a saída (MediaMTX) recusou, não a câmera
    if publish_host and any(publish_host in line and _UNREACHABLE.search(line) for line in lines):
        return RelayFailure.PUBLISH  # o MediaMTX não está aceitando conexões
    if _AUTH.search(text):
        return RelayFailure.AUTH
    if _NOT_FOUND.search(text):
        return RelayFailure.NOT_FOUND
    if output_error and not input_error:
        return RelayFailure.PUBLISH  # ex.: MediaMTX caiu no meio da transmissão (Broken pipe na saída)
    if _UNREACHABLE.search(text):
        return RelayFailure.UNREACHABLE
    return RelayFailure.OTHER


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
    stderr_tail: deque[str] = field(default_factory=lambda: deque(maxlen=8))
    failure: RelayFailure | None = None
    started_at: float | None = None  # monotonic do processo atual
    restart_times: deque[float] = field(default_factory=lambda: deque(maxlen=50))


class FfmpegRelaySupervisor:
    def __init__(
        self,
        ffmpeg: str,
        publish_host: str,
        publish_user: str,
        publish_secret: str,
        command: Sequence[str] | None = None,
        backoff: tuple[float, float] = (BACKOFF_MIN, BACKOFF_MAX),
        auth_backoff: float = AUTH_BACKOFF,
    ) -> None:
        """`command` substitui o FFmpeg (testes); recebe `{source}` e `{target}` como argumentos."""
        self._ffmpeg = ffmpeg
        self._publish_host = publish_host
        self._publish = f"rtsp://{publish_user}:{publish_secret}@{publish_host}"
        self._secret = publish_secret
        self._command = list(command) if command else None
        self._relays: dict[str, _Relay] = {}
        self._backoff_min, self._backoff_max = backoff
        self._auth_backoff = auth_backoff
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
            relay.stderr_tail.clear()
            try:
                relay.process = await asyncio.create_subprocess_exec(
                    *self._args(relay),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
            except OSError as exc:
                relay.stderr_tail.append(f"não foi possível iniciar o FFmpeg: {type(exc).__name__}")
                relay.failure = RelayFailure.OTHER
                log.error("relay %s: não foi possível iniciar o FFmpeg (%s)", relay.spec.name, exc)
            else:
                relay.started_at = started
                log.info("relay %s iniciado (pid %s)", relay.spec.name, relay.process.pid)
                try:
                    await self._pump(relay, relay.process)
                    relay.last_exit_code = await relay.process.wait()
                finally:
                    await self._terminate(relay.process)
                    relay.started_at = None
                relay.failure = classify_failure(relay.stderr_tail, self._publish_host) or RelayFailure.OTHER
                log.warning(
                    "relay %s terminou (código %s): %s",
                    relay.spec.name, relay.last_exit_code, " | ".join(relay.stderr_tail) or "sem mensagens",
                )  # fmt: skip
            relay.restarts += 1
            relay.restart_times.append(time.monotonic())
            stable = time.monotonic() - started > STABLE_AFTER
            backoff = self._backoff_min if stable else min(backoff * 2, self._backoff_max)
            wait = max(backoff, self._auth_backoff) if relay.failure is RelayFailure.AUTH else backoff
            if relay.failure is RelayFailure.AUTH:
                log.warning("relay %s: senha recusada; nova tentativa em %.0f s", relay.spec.name, wait)
            await asyncio.sleep(wait)

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
        now = time.monotonic()
        result: dict[str, RelayStatus] = {}
        for name, r in self._relays.items():
            running = r.process is not None and r.process.returncode is None and r.started_at is not None
            running_for = now - r.started_at if running and r.started_at is not None else None
            if running_for is not None and running_for > HEALTHY_AFTER:
                r.failure = None  # estável: a falha anterior não descreve mais o relay
            result[name] = RelayStatus(
                name=name,
                running=running,
                restarts=r.restarts,
                last_exit_code=r.last_exit_code,
                last_error=r.stderr_tail[-1] if r.stderr_tail else "",
                failure=r.failure,
                running_for_s=running_for,
                recent_restarts=sum(1 for t in r.restart_times if now - t < RESTART_WINDOW),
            )
        return result

    async def stop_all(self) -> None:
        for name in list(self._relays):
            await self.stop(name)
        if self._retiring:
            await asyncio.gather(*self._retiring, return_exceptions=True)
