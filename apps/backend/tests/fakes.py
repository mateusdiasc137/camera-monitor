"""Dublês de teste para as portas da aplicação."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from nvr.identity.domain import Session, User


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.current = start or datetime(2026, 10, 17, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


class PlainHasher:
    """Hasher trivial e rápido (só para testes de caso de uso)."""

    def __init__(self, outdated_prefix: str | None = None) -> None:
        self.verified: list[str] = []
        self.outdated_prefix = outdated_prefix

    def hash(self, password: str) -> str:
        return f"plain${password}"

    def verify(self, password_hash: str, password: str) -> bool:
        self.verified.append(password_hash)
        return password_hash.split("$", 1)[-1] == password

    def needs_rehash(self, password_hash: str) -> bool:
        return self.outdated_prefix is not None and password_hash.startswith(self.outdated_prefix)


class MemoryUsers:
    def __init__(self) -> None:
        self.rows: dict[str, tuple[User, str]] = {}

    async def get_by_username(self, username: str) -> tuple[User, str] | None:
        return next((r for r in self.rows.values() if r[0].username == username), None)

    async def get_by_id(self, user_id: str) -> User | None:
        row = self.rows.get(user_id)
        return row[0] if row else None

    async def add(self, user: User, password_hash: str) -> None:
        self.rows[user.id] = (user, password_hash)

    async def update_password_hash(self, user_id: str, password_hash: str) -> None:
        self.rows[user_id] = (self.rows[user_id][0], password_hash)

    async def count(self) -> int:
        return len(self.rows)

    def disable(self, user_id: str) -> None:
        user, password_hash = self.rows[user_id]
        self.rows[user_id] = (replace(user, disabled=True), password_hash)


class MemorySessions:
    def __init__(self) -> None:
        self.rows: dict[str, Session] = {}

    async def add(self, session: Session) -> None:
        self.rows[session.token_hash] = session

    async def get(self, token_hash: str) -> Session | None:
        return self.rows.get(token_hash)

    async def touch(self, token_hash: str, last_seen_at: datetime) -> None:
        if token_hash in self.rows:
            self.rows[token_hash] = replace(self.rows[token_hash], last_seen_at=last_seen_at)

    async def delete(self, token_hash: str) -> None:
        self.rows.pop(token_hash, None)

    async def delete_expired(self, now: datetime) -> int:
        expired = [k for k, s in self.rows.items() if s.expires_at <= now]
        for k in expired:
            del self.rows[k]
        return len(expired)
