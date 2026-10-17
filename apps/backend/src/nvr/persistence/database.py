"""Engine assíncrona e migrações (Alembic configurado por código, sem alembic.ini)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def sqlite_path(data_dir: Path) -> Path:
    return data_dir / "nvr.db"


def _alembic_config(db_file: Path) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", MIGRATIONS_DIR.as_posix())
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_file.as_posix()}")
    return cfg


def migrate(data_dir: Path, revision: str = "head") -> None:
    """Aplica as migrações (síncrono; roda antes de a aplicação aceitar requisições)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    command.upgrade(_alembic_config(sqlite_path(data_dir)), revision)


def downgrade(data_dir: Path, revision: str) -> None:
    command.downgrade(_alembic_config(sqlite_path(data_dir)), revision)


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
    """Chaves estrangeiras ligadas e WAL (leituras concorrentes com o job de indexação)."""
    module = type(dbapi_connection).__module__
    if "sqlite" not in module:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def create_engine(data_dir: Path) -> AsyncEngine:
    return create_async_engine(f"sqlite+aiosqlite:///{sqlite_path(data_dir).as_posix()}")
