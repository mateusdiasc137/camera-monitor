"""Composition root: o único lugar que instancia adaptadores e liga casos de uso (ADR-001)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from nvr import __version__
from nvr.bootstrap.settings import Settings
from nvr.identity.adapters.argon2_hasher import Argon2PasswordHasher
from nvr.identity.adapters.memory_throttle import MemoryLoginThrottle
from nvr.identity.adapters.sql_repositories import SqlSessionRepository, SqlUserRepository
from nvr.identity.application import Authenticate, CreateUser, Login, Logout
from nvr.persistence.database import create_engine
from nvr.shared.clock import SystemClock
from nvr.system.adapters.database_probe import DatabaseProbe
from nvr.system.application import GetSystemHealth


@dataclass(frozen=True)
class Container:
    settings: Settings
    engine: AsyncEngine
    get_system_health: GetSystemHealth
    create_user: CreateUser
    login: Login
    logout: Logout
    authenticate: Authenticate

    async def aclose(self) -> None:
        await self.engine.dispose()


def build_container(settings: Settings) -> Container:
    engine = create_engine(settings.data_dir)
    clock = SystemClock()
    hasher = Argon2PasswordHasher()
    users = SqlUserRepository(engine)
    sessions = SqlSessionRepository(engine)
    return Container(
        settings=settings,
        engine=engine,
        get_system_health=GetSystemHealth(__version__, probes=[DatabaseProbe(engine)]),
        create_user=CreateUser(users, hasher, clock),
        login=Login(users, sessions, hasher, MemoryLoginThrottle(), clock),
        logout=Logout(sessions),
        authenticate=Authenticate(users, sessions, clock),
    )
