"""Ambiente do Alembic: migrações síncronas sobre SQLite, com batch mode (ALTER TABLE limitado)."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine

from nvr.persistence.tables import metadata

config = context.config
url = config.get_main_option("sqlalchemy.url")
if url is None:
    raise RuntimeError("sqlalchemy.url não configurada (use nvr.persistence.database.migrate)")

engine = create_engine(url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
