from __future__ import annotations

from datetime import datetime

from sqlalchemy import RowMapping, delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from nvr.identity.domain import Role, Session, User
from nvr.persistence.tables import sessions, users


def _user(row: RowMapping) -> User:
    return User(row["id"], row["username"], Role(row["role"]), row["disabled"], row["created_at"])


class SqlUserRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get_by_username(self, username: str) -> tuple[User, str] | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(select(users).where(users.c.username == username))).mappings().first()
        return None if row is None else (_user(row), row["password_hash"])

    async def get_by_id(self, user_id: str) -> User | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(select(users).where(users.c.id == user_id))).mappings().first()
        return None if row is None else _user(row)

    async def add(self, user: User, password_hash: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(users).values(
                    id=user.id,
                    username=user.username,
                    password_hash=password_hash,
                    role=user.role.value,
                    disabled=user.disabled,
                    created_at=user.created_at,
                )
            )

    async def update_password_hash(self, user_id: str, password_hash: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(update(users).where(users.c.id == user_id).values(password_hash=password_hash))

    async def count(self) -> int:
        async with self._engine.connect() as conn:
            return int((await conn.execute(select(func.count()).select_from(users))).scalar_one())


class SqlSessionRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def add(self, session: Session) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(sessions).values(
                    token_hash=session.token_hash,
                    user_id=session.user_id,
                    created_at=session.created_at,
                    expires_at=session.expires_at,
                    last_seen_at=session.last_seen_at,
                )
            )

    async def get(self, token_hash: str) -> Session | None:
        async with self._engine.connect() as conn:
            query = select(sessions).where(sessions.c.token_hash == token_hash)
            row = (await conn.execute(query)).mappings().first()
        if row is None:
            return None
        return Session(
            row["token_hash"], row["user_id"], row["created_at"], row["expires_at"], row["last_seen_at"]
        )

    async def touch(self, token_hash: str, last_seen_at: datetime) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                update(sessions).where(sessions.c.token_hash == token_hash).values(last_seen_at=last_seen_at)
            )

    async def delete(self, token_hash: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(delete(sessions).where(sessions.c.token_hash == token_hash))

    async def delete_expired(self, now: datetime) -> int:
        async with self._engine.begin() as conn:
            result = await conn.execute(delete(sessions).where(sessions.c.expires_at <= now))
        return int(result.rowcount or 0)
