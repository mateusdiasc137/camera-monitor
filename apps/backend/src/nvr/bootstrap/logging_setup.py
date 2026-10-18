"""Logs do serviço: texto (desenvolvimento) ou JSON por linha (produção), sempre com segredos mascarados.

A máscara é a última barreira (ADR-003): além dos segredos desta execução (publicador/leitor do MediaMTX),
troca por *** qualquer credencial em URL (`rtsp://u:s@h`) ou em caminho (`password=...`), inclusive nos
tracebacks e nos logs do uvicorn.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from nvr.shared.redact import redact

# Atributos padrão de LogRecord: o que sobrar veio de `extra=` e vai para o JSON.
_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime", "taskName"}


class RedactingFilter(logging.Filter):
    def __init__(self) -> None:
        super().__init__()
        self.secrets: set[str] = set()

    def add_secrets(self, *secrets: str) -> None:
        self.secrets.update(s for s in secrets if s)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        clean = redact(message, *self.secrets)
        if clean != message:
            record.msg, record.args = clean, None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text, *self.secrets)
        if record.stack_info:
            record.stack_info = redact(record.stack_info, *self.secrets)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD and not key.startswith("_"):
                data[key] = value if isinstance(value, (str, int, float, bool, type(None))) else str(value)
        if record.exc_text:
            data["exc"] = record.exc_text
        if record.stack_info:
            data["stack"] = record.stack_info
        return json.dumps(data, ensure_ascii=False)


def configure_logging(fmt: str = "text", level: str = "INFO", stream: Any = None) -> RedactingFilter:
    """Um único handler na raiz (o uvicorn sobe com `log_config=None` e propaga para cá)."""
    handler = logging.StreamHandler(stream or sys.stderr)
    redactor = RedactingFilter()
    handler.addFilter(redactor)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    for old in list(root.handlers):
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(level.upper())
    logging.getLogger("httpx").setLevel(logging.WARNING)  # a reconciliação consulta o MediaMTX a cada ciclo
    return redactor
