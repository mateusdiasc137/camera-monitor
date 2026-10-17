from datetime import timedelta

import pytest

from nvr.identity.adapters.memory_throttle import MemoryLoginThrottle
from nvr.identity.application import (
    LOGIN_BLOCKED,
    LOGIN_FAILED,
    Authenticate,
    CreateUser,
    Login,
    Logout,
    hash_token,
)
from nvr.identity.domain import SESSION_IDLE_TIMEOUT, SESSION_TTL, Role
from nvr.shared.errors import Conflict, Unauthorized, ValidationFailed
from tests.fakes import FakeClock, MemorySessions, MemoryUsers, PlainHasher

PASSWORD = "senha-forte-123"


@pytest.fixture
def env() -> dict:  # type: ignore[type-arg]
    clock, users, sessions, hasher = FakeClock(), MemoryUsers(), MemorySessions(), PlainHasher()
    throttle = MemoryLoginThrottle(max_failures=3, window=timedelta(minutes=5), lockout=timedelta(minutes=5))
    return {
        "clock": clock,
        "users": users,
        "sessions": sessions,
        "hasher": hasher,
        "create": CreateUser(users, hasher, clock),
        "login": Login(users, sessions, hasher, throttle, clock),
        "auth": Authenticate(users, sessions, clock),
        "logout": Logout(sessions),
    }


# ------------------------------------------------------------------ CreateUser


async def test_create_user_normalizes_username(env: dict) -> None:  # type: ignore[type-arg]
    user = await env["create"]("  Admin ", PASSWORD, Role.ADMIN)
    assert user.username == "admin"
    assert await env["users"].count() == 1
    assert env["users"].rows[user.id][1] != PASSWORD  # só o hash é guardado


@pytest.mark.parametrize(
    ("username", "password"),
    [("ab", PASSWORD), ("com espaço", PASSWORD), ("admin", "curta"), ("admin", "xxadminxxyy")],
)
async def test_create_user_rejects_invalid_input(env: dict, username: str, password: str) -> None:  # type: ignore[type-arg]
    with pytest.raises(ValidationFailed):
        await env["create"](username, password, Role.ADMIN)


async def test_create_user_rejects_duplicates(env: dict) -> None:  # type: ignore[type-arg]
    await env["create"]("admin", PASSWORD, Role.ADMIN)
    with pytest.raises(Conflict):
        await env["create"]("ADMIN", PASSWORD, Role.VIEWER)


# ----------------------------------------------------------------------- Login


async def test_login_creates_session_storing_only_token_hash(env: dict) -> None:  # type: ignore[type-arg]
    await env["create"]("admin", PASSWORD, Role.ADMIN)
    result = await env["login"]("admin", PASSWORD, "10.0.0.5")
    assert result.user.username == "admin"
    assert list(env["sessions"].rows) == [hash_token(result.token)]
    session = env["sessions"].rows[hash_token(result.token)]
    assert session.expires_at - session.created_at == SESSION_TTL


async def test_wrong_password_and_unknown_user_give_same_error(env: dict) -> None:  # type: ignore[type-arg]
    await env["create"]("admin", PASSWORD, Role.ADMIN)
    with pytest.raises(Unauthorized, match=LOGIN_FAILED):
        await env["login"]("admin", "errada-123456", "10.0.0.5")
    env["hasher"].verified.clear()
    with pytest.raises(Unauthorized, match=LOGIN_FAILED):
        await env["login"]("fantasma", PASSWORD, "10.0.0.6")
    # Usuário inexistente também passa pela verificação de hash (tempo de resposta equivalente).
    assert len(env["hasher"].verified) == 1


async def test_disabled_user_cannot_login(env: dict) -> None:  # type: ignore[type-arg]
    user = await env["create"]("admin", PASSWORD, Role.ADMIN)
    env["users"].disable(user.id)
    with pytest.raises(Unauthorized, match=LOGIN_FAILED):
        await env["login"]("admin", PASSWORD, "10.0.0.5")


async def test_throttle_blocks_after_repeated_failures(env: dict) -> None:  # type: ignore[type-arg]
    await env["create"]("admin", PASSWORD, Role.ADMIN)
    for _ in range(3):
        with pytest.raises(Unauthorized, match=LOGIN_FAILED):
            await env["login"]("admin", "errada-123456", "10.0.0.5")
    # Bloqueado mesmo com a senha certa, inclusive a partir de outro IP (chave por usuário).
    with pytest.raises(Unauthorized, match=LOGIN_BLOCKED):
        await env["login"]("admin", PASSWORD, "10.0.0.99")
    env["clock"].advance(timedelta(minutes=6))
    assert (await env["login"]("admin", PASSWORD, "10.0.0.5")).user.username == "admin"


async def test_outdated_hash_is_upgraded_on_login(env: dict) -> None:  # type: ignore[type-arg]
    user = await env["create"]("admin", PASSWORD, Role.ADMIN)
    env["users"].rows[user.id] = (user, f"old$plain${PASSWORD}")
    env["hasher"].outdated_prefix = "old$"
    env["hasher"].verify = lambda h, p: h.endswith(p)  # aceita o formato antigo
    await env["login"]("admin", PASSWORD, "10.0.0.5")
    assert env["users"].rows[user.id][1] == f"plain${PASSWORD}"


# --------------------------------------------------------- Authenticate/Logout


async def test_authenticate_and_logout(env: dict) -> None:  # type: ignore[type-arg]
    await env["create"]("admin", PASSWORD, Role.ADMIN)
    token = (await env["login"]("admin", PASSWORD, "10.0.0.5")).token
    assert (await env["auth"](token)).username == "admin"
    await env["logout"](token)
    with pytest.raises(Unauthorized):
        await env["auth"](token)


@pytest.mark.parametrize("token", [None, "", "token-inventado"])
async def test_authenticate_rejects_missing_or_unknown_token(env: dict, token: str | None) -> None:  # type: ignore[type-arg]
    with pytest.raises(Unauthorized):
        await env["auth"](token)


async def test_session_expires_when_idle(env: dict) -> None:  # type: ignore[type-arg]
    await env["create"]("admin", PASSWORD, Role.ADMIN)
    token = (await env["login"]("admin", PASSWORD, "10.0.0.5")).token
    env["clock"].advance(SESSION_IDLE_TIMEOUT + timedelta(seconds=1))
    with pytest.raises(Unauthorized):
        await env["auth"](token)
    assert env["sessions"].rows == {}  # sessão expirada é removida


async def test_activity_keeps_session_alive_until_absolute_ttl(env: dict) -> None:  # type: ignore[type-arg]
    await env["create"]("admin", PASSWORD, Role.ADMIN)
    token = (await env["login"]("admin", PASSWORD, "10.0.0.5")).token
    elapsed = timedelta()
    while elapsed + timedelta(hours=12) < SESSION_TTL:
        env["clock"].advance(timedelta(hours=12))
        elapsed += timedelta(hours=12)
        await env["auth"](token)
    env["clock"].advance(SESSION_TTL - elapsed)
    with pytest.raises(Unauthorized):
        await env["auth"](token)


async def test_disabling_user_invalidates_sessions(env: dict) -> None:  # type: ignore[type-arg]
    user = await env["create"]("admin", PASSWORD, Role.ADMIN)
    token = (await env["login"]("admin", PASSWORD, "10.0.0.5")).token
    env["users"].disable(user.id)
    with pytest.raises(Unauthorized):
        await env["auth"](token)


# -------------------------------------------------------------------- adapters


def test_argon2_hasher_round_trip() -> None:
    from nvr.identity.adapters.argon2_hasher import Argon2PasswordHasher

    hasher = Argon2PasswordHasher()
    hashed = hasher.hash(PASSWORD)
    assert hashed.startswith("$argon2id$")
    assert hasher.verify(hashed, PASSWORD)
    assert not hasher.verify(hashed, "outra-senha-123")
    assert not hasher.verify("lixo", PASSWORD)
    assert not hasher.needs_rehash(hashed)
