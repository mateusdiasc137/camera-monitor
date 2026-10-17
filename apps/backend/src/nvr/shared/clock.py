from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    """Relógio real em UTC. Testes usam um relógio controlável com a mesma interface."""

    def now(self) -> datetime:
        return datetime.now(UTC)
