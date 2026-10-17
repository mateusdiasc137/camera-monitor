from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta


class MemoryLoginThrottle:
    """Bloqueia uma chave após `max_failures` falhas dentro de `window`, por `lockout`.

    Em memória: reinicia junto com o processo, o que é aceitável para um único servidor doméstico.
    """

    def __init__(
        self,
        max_failures: int = 5,
        window: timedelta = timedelta(minutes=5),
        lockout: timedelta = timedelta(minutes=5),
    ) -> None:
        self._max, self._window, self._lockout = max_failures, window, lockout
        self._failures: defaultdict[str, deque[datetime]] = defaultdict(deque)
        self._blocked_until: dict[str, datetime] = {}

    def is_blocked(self, key: str, now: datetime) -> bool:
        until = self._blocked_until.get(key)
        if until is None:
            return False
        if now >= until:
            del self._blocked_until[key]
            return False
        return True

    def register_failure(self, key: str, now: datetime) -> None:
        failures = self._failures[key]
        failures.append(now)
        while failures and now - failures[0] > self._window:
            failures.popleft()
        if len(failures) >= self._max:
            self._blocked_until[key] = now + self._lockout
            failures.clear()

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
        self._blocked_until.pop(key, None)
