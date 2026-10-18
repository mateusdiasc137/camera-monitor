"""Logs com máscara de segredos (texto e JSON), inclusive em exceções."""

import io
import json
import logging
from collections.abc import Iterator

import pytest

from nvr.bootstrap.logging_setup import configure_logging

SECRET = "pub-secret"


@pytest.fixture
def restore_root() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in handlers:
        root.addHandler(h)
    root.setLevel(level)


def emit(fmt: str) -> str:
    out = io.StringIO()
    redactor = configure_logging(fmt, "INFO", stream=out)
    redactor.add_secrets(SECRET)
    log = logging.getLogger("nvr.teste")
    log.info("relay %s caiu", "rtsp://admin:Segredo$1@10.0.0.210:554/user=admin&password=Segredo$1&channel=1")
    log.warning("token do publicador: %s", SECRET, extra={"camera_id": "c1"})
    try:
        raise RuntimeError(f"falha com rtsp://u:{SECRET}@127.0.0.1/x")
    except RuntimeError:
        log.exception("erro no relay")
    return out.getvalue()


@pytest.mark.usefixtures("restore_root")
def test_text_logs_are_redacted() -> None:
    text = emit("text")
    assert SECRET not in text
    assert "Segredo$1" not in text
    assert "rtsp://***@10.0.0.210" in text
    assert "password=***" in text
    assert "RuntimeError" in text  # o traceback continua lá, só sem o segredo


@pytest.mark.usefixtures("restore_root")
def test_json_logs_one_object_per_line_with_extras() -> None:
    lines = [json.loads(line) for line in emit("json").splitlines() if line.startswith("{")]
    assert [r["level"] for r in lines] == ["INFO", "WARNING", "ERROR"]
    assert all(r["logger"] == "nvr.teste" for r in lines)
    assert lines[1]["camera_id"] == "c1"
    assert lines[1]["msg"] == "token do publicador: ***"
    assert "RuntimeError" in lines[2]["exc"]
    dump = json.dumps(lines)
    assert SECRET not in dump
    assert "Segredo$1" not in dump
