from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass

from nvr.identity.domain import (
    SESSION_TTL,
    Role,
    Session,
    User,
    validate_password,
    validate_username,
)
from nvr.identity.ports import Clock, LoginThrottle, PasswordHasher, SessionRepository, UserRepository
from nvr.shared.errors import Conflict, Unauthorized, ValidationFailed

LOGIN_FAILED = "usuário ou senha incorretos"
LOGIN_BLOCKED = "muitas tentativas; aguarde alguns minutos e tente de novo"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class LoginResult:
    user: User
    token: str  # entregue ao cliente uma única vez (cookie); o servidor guarda só o hash


class CreateUser:
    def __init__(self, users: UserRepository, hasher: PasswordHasher, clock: Clock) -> None:
        self._users, self._hasher, self._clock = users, hasher, clock

    async def __call__(self, username: str, password: str, role: Role) -> User:
        username = username.strip().lower()
        if error := validate_username(username):
            raise ValidationFailed(f"nome de usuário inválido: {error}")
        if error := validate_password(password, username):
            raise ValidationFailed(error)
        if await self._users.get_by_username(username) is not None:
            raise Conflict(f"o usuário '{username}' já existe")
        user = User(str(uuid.uuid4()), username, role, disabled=False, created_at=self._clock.now())
        await self._users.add(user, self._hasher.hash(password))
        return user


class Login:
    def __init__(
        self,
        users: UserRepository,
        sessions: SessionRepository,
        hasher: PasswordHasher,
        throttle: LoginThrottle,
        clock: Clock,
    ) -> None:
        self._users, self._sessions, self._hasher = users, sessions, hasher
        self._throttle, self._clock = throttle, clock
        # Hash de referência para gastar o mesmo tempo quando o usuário não existe.
        self._dummy_hash = hasher.hash(secrets.token_urlsafe(16))

    async def __call__(self, username: str, password: str, client: str) -> LoginResult:
        now = self._clock.now()
        username = username.strip().lower()
        keys = (f"user:{username}", f"client:{client}")
        if any(self._throttle.is_blocked(k, now) for k in keys):
            raise Unauthorized(LOGIN_BLOCKED)

        found = await self._users.get_by_username(username)
        password_hash = found[1] if found else self._dummy_hash
        valid = self._hasher.verify(password_hash, password)
        if found is None or not valid or found[0].disabled:
            for k in keys:
                self._throttle.register_failure(k, now)
            raise Unauthorized(LOGIN_FAILED)

        user = found[0]
        for k in keys:
            self._throttle.reset(k)
        if self._hasher.needs_rehash(password_hash):
            await self._users.update_password_hash(user.id, self._hasher.hash(password))

        token = secrets.token_urlsafe(32)
        await self._sessions.add(
            Session(
                hash_token(token), user.id, created_at=now, expires_at=now + SESSION_TTL, last_seen_at=now
            )
        )
        return LoginResult(user, token)


class Authenticate:
    """Resolve o token do cookie em um usuário ativo, renovando o 'visto por último'."""

    def __init__(self, users: UserRepository, sessions: SessionRepository, clock: Clock) -> None:
        self._users, self._sessions, self._clock = users, sessions, clock

    async def __call__(self, token: str | None) -> User:
        if not token:
            raise Unauthorized("faça login para continuar")
        now = self._clock.now()
        token_hash = hash_token(token)
        session = await self._sessions.get(token_hash)
        if session is None or not session.is_valid_at(now):
            if session is not None:
                await self._sessions.delete(token_hash)
            raise Unauthorized("sessão expirada; faça login novamente")
        user = await self._users.get_by_id(session.user_id)
        if user is None or user.disabled:
            await self._sessions.delete(token_hash)
            raise Unauthorized("sessão inválida")
        await self._sessions.touch(token_hash, now)
        return user


class Logout:
    def __init__(self, sessions: SessionRepository) -> None:
        self._sessions = sessions

    async def __call__(self, token: str | None) -> None:
        if token:
            await self._sessions.delete(hash_token(token))
