"""Usuários do painel e sessões. Não confundir com credenciais das câmeras (módulo cameras)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
MIN_PASSWORD_LENGTH = 10
SESSION_TTL = timedelta(days=7)
SESSION_IDLE_TIMEOUT = timedelta(days=1)


class Role(StrEnum):
    ADMIN = "admin"  # tudo, inclusive câmeras, armazenamento e usuários
    VIEWER = "viewer"  # assistir ao vivo e gravações


@dataclass(frozen=True)
class User:
    id: str
    username: str
    role: Role
    disabled: bool
    created_at: datetime

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN


@dataclass(frozen=True)
class Session:
    """Sessão do painel. O token em si nunca é guardado: só o seu hash (`token_hash`)."""

    token_hash: str
    user_id: str
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime

    def is_valid_at(self, now: datetime) -> bool:
        return now < self.expires_at and now - self.last_seen_at < SESSION_IDLE_TIMEOUT


def validate_username(username: str) -> str | None:
    """Retorna a mensagem de erro, ou None se válido."""
    if not USERNAME_RE.fullmatch(username):
        return "use de 3 a 64 caracteres: letras minúsculas, números, ponto, hífen ou sublinhado"
    return None


def validate_password(password: str, username: str) -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"a senha precisa ter pelo menos {MIN_PASSWORD_LENGTH} caracteres"
    if username and username.lower() in password.lower():
        return "a senha não pode conter o nome de usuário"
    return None
