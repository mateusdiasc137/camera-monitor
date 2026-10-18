"""Esquema relacional (SQLAlchemy Core). Usado apenas por adaptadores de repositório e migrações.

Cada módulo de negócio acessa só as próprias tabelas, pelo próprio repositório (ADR-001).
Datas sempre em UTC; o banco guarda só metadados, nunca vídeo ou segredos (ADR-003, ADR-006).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Dialect,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    TypeDecorator,
)

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
metadata = MetaData(naming_convention=NAMING)


class UTCDateTime(TypeDecorator[datetime]):
    """Data/hora com fuso, armazenada como texto ISO 8601 em UTC (ordenável no SQLite)."""

    impl = String(32)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("datetime sem fuso horário: use UTC explícito")
        return value.astimezone(UTC).isoformat(timespec="microseconds")

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        return None if value is None else datetime.fromisoformat(value)


users = Table(
    "users",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("username", String(64), nullable=False, unique=True),
    Column("password_hash", String(255), nullable=False),
    Column("role", String(16), nullable=False),
    Column("disabled", Boolean, nullable=False, default=False),
    Column("created_at", UTCDateTime, nullable=False),
    CheckConstraint("role in ('admin', 'viewer')", name="role"),
)

sessions = Table(
    "sessions",
    metadata,
    Column("token_hash", String(64), primary_key=True),  # SHA-256 do token; o token nunca é gravado
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    Column("expires_at", UTCDateTime, nullable=False),
    Column("last_seen_at", UTCDateTime, nullable=False),
)
Index("ix_sessions_expires_at", sessions.c.expires_at)

cameras = Table(
    "cameras",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("name", String(80), nullable=False, unique=True),
    Column("driver", String(32), nullable=False),  # ex.: generic_rtsp, xiongmai
    Column("host", String(255), nullable=False),
    Column("rtsp_port", Integer, nullable=False),
    # Caminhos RTSP podem conter {user}/{password} (formato Xiongmai); nunca as credenciais em si.
    Column("main_path", String(512), nullable=False),
    Column("sub_path", String(512), nullable=True),
    Column("secret_ref", String(128), nullable=False),  # chave no SecretStore, não o segredo
    Column("ingest", String(16), nullable=False),  # relay | direct (ADR-002)
    Column("enabled", Boolean, nullable=False),
    Column("recording_enabled", Boolean, nullable=False),
    Column("lens_layout", String(16), nullable=False, server_default="single"),
    Column("created_at", UTCDateTime, nullable=False),
    Column("updated_at", UTCDateTime, nullable=False),
    CheckConstraint("ingest in ('relay', 'direct')", name="ingest"),
    CheckConstraint("rtsp_port between 1 and 65535", name="rtsp_port"),
)

storage_locations = Table(
    "storage_locations",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("path", String(1024), nullable=False, unique=True),
    Column("active", Boolean, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
)
# No máximo um local ativo.
Index(
    "uq_storage_locations_single_active",
    storage_locations.c.active,
    unique=True,
    sqlite_where=storage_locations.c.active.is_(True),
)

storage_policy = Table(
    "storage_policy",
    metadata,
    Column("id", Integer, primary_key=True),  # linha única (id = 1)
    Column("quota_bytes", BigInteger, nullable=True),  # None = ainda não configurada
    Column("replacement", String(32), nullable=False),
    Column("high_watermark", Float, nullable=False),
    Column("low_watermark", Float, nullable=False),
    Column("min_free_bytes", BigInteger, nullable=False),
    Column("updated_at", UTCDateTime, nullable=False),
    CheckConstraint("id = 1", name="singleton"),
    CheckConstraint("replacement in ('overwrite_oldest', 'stop_when_full')", name="replacement"),
    CheckConstraint(
        "low_watermark > 0 and low_watermark < high_watermark and high_watermark <= 1", name="watermarks"
    ),
)

storage_migrations = Table(
    "storage_migrations",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("from_location_id", String(36), ForeignKey("storage_locations.id"), nullable=False),
    Column("to_location_id", String(36), ForeignKey("storage_locations.id"), nullable=False),
    Column("state", String(16), nullable=False),
    Column("total_files", Integer, nullable=False),
    Column("total_bytes", BigInteger, nullable=False),
    Column("moved_files", Integer, nullable=False),
    Column("moved_bytes", BigInteger, nullable=False),
    Column("error", String(1024), nullable=False),
    Column("started_at", UTCDateTime, nullable=False),
    Column("finished_at", UTCDateTime, nullable=True),
    CheckConstraint("state in ('running', 'done', 'failed', 'abandoned')", name="state"),
)
Index("ix_storage_migrations_started_at", storage_migrations.c.started_at)

segments = Table(
    "segments",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("camera_id", String(36), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False),
    Column("location_id", String(36), ForeignKey("storage_locations.id"), nullable=False),
    Column("rel_path", String(1024), nullable=False),
    Column("start_at", UTCDateTime, nullable=False),
    Column("duration_s", Float, nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("indexed_at", UTCDateTime, nullable=False),
)
Index("uq_segments_location_path", segments.c.location_id, segments.c.rel_path, unique=True)
Index("ix_segments_camera_start", segments.c.camera_id, segments.c.start_at)
Index("ix_segments_start", segments.c.start_at)

audit_log = Table(
    "audit_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("at", UTCDateTime, nullable=False),
    Column("actor", String(64), nullable=False),  # usuário ou "system"
    Column("action", String(64), nullable=False),
    Column("target", String(128), nullable=True),
    Column("detail", JSON, nullable=True),
)
Index("ix_audit_log_at", audit_log.c.at)

camera_status_events = Table(
    "camera_status_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("camera_id", String(36), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False),
    Column("at", UTCDateTime, nullable=False),
    Column("state", String(16), nullable=False),
    Column("detail", String(255), nullable=False),
    Column("recording", Boolean, nullable=False),
)
Index("ix_camera_status_events_camera_at", camera_status_events.c.camera_id, camera_status_events.c.at)
