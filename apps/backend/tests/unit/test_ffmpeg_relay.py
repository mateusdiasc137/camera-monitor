"""Supervisor de relays com um processo falso (Python) no lugar do FFmpeg."""

import asyncio
import logging
import sys

import pytest

from nvr.streaming.adapters.ffmpeg_relay import FfmpegRelaySupervisor, ffmpeg_relay_args
from nvr.streaming.domain import RelaySpec

SOURCE = "rtsp://10.0.0.210:554/user=admin&password=Segredo$1&channel=1&stream=0.sdp?"


def supervisor(script: str) -> FfmpegRelaySupervisor:
    command = [sys.executable, "-c", script, "{source}", "{target}"]
    return FfmpegRelaySupervisor(
        "ffmpeg", "127.0.0.1:8554", "relay", "pub-secret", command=command, backoff=(0.05, 0.1)
    )


async def wait_for(condition, limit: float = 5.0) -> None:  # type: ignore[no-untyped-def]
    deadline = asyncio.get_running_loop().time() + limit
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condição não atingida")
        await asyncio.sleep(0.02)


def test_ffmpeg_args_are_a_list_without_shell() -> None:
    args = ffmpeg_relay_args("ffmpeg", SOURCE, "rtsp://relay:x@127.0.0.1:8554/cam")
    assert args[args.index("-i") + 1] == SOURCE  # intacto: $, & e ? sem interpretação
    assert args[args.index("-c") + 1] == "copy"
    assert "-use_wallclock_as_timestamps" in args


async def test_crashing_relay_is_restarted_and_logs_are_masked(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    script = (
        "import sys; print('erro abrindo', sys.argv[1], 'para', sys.argv[2], file=sys.stderr); sys.exit(3)"
    )
    sup = supervisor(script)
    sup.ensure(RelaySpec("cam-x-main", SOURCE))
    await wait_for(lambda: sup.statuses()["cam-x-main"].restarts >= 2)
    status = sup.statuses()["cam-x-main"]
    await sup.stop_all()
    assert status.last_exit_code == 3
    assert "erro abrindo" in status.last_error
    for text in (status.last_error, caplog.text):
        assert "Segredo$1" not in text
        assert "pub-secret" not in text


async def test_running_relay_receives_exact_arguments_and_stops() -> None:
    marker = []
    script = (
        "import sys, time; sys.stderr.write('ARGS ' + repr(sys.argv[1:]) + '\\n'); "
        "sys.stderr.flush(); time.sleep(30)"
    )
    sup = supervisor(script)
    sup.ensure(RelaySpec("cam-x-main", SOURCE))
    await wait_for(lambda: sup.statuses()["cam-x-main"].running)
    await wait_for(lambda: "ARGS" in sup.statuses()["cam-x-main"].last_error)
    marker.append(sup.statuses()["cam-x-main"].last_error)
    await sup.stop("cam-x-main")
    assert sup.names() == set()
    # Argumentos chegaram ao processo (aqui mascarados, mas presentes e com o alvo de publicação certo).
    assert "127.0.0.1:8554/cam-x-main" in marker[0]


async def test_ensure_is_idempotent_and_restarts_on_url_change() -> None:
    sup = supervisor("import time; time.sleep(30)")
    spec = RelaySpec("cam-x-main", SOURCE)
    sup.ensure(spec)
    await wait_for(lambda: sup.statuses()["cam-x-main"].running)
    first_task = sup._relays["cam-x-main"].task
    sup.ensure(spec)
    assert sup._relays["cam-x-main"].task is first_task
    sup.ensure(RelaySpec("cam-x-main", SOURCE.replace("10.0.0.210", "10.0.0.211")))
    assert sup._relays["cam-x-main"].task is not first_task
    await sup.stop_all()
