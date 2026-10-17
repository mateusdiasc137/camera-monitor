from __future__ import annotations

from argon2 import PasswordHasher as _Argon2
from argon2.exceptions import InvalidHashError, VerificationError


class Argon2PasswordHasher:
    """argon2id com os parâmetros padrão da biblioteca (RFC 9106, perfil de baixa memória)."""

    def __init__(self) -> None:
        self._ph = _Argon2()

    def hash(self, password: str) -> str:
        return self._ph.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._ph.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        try:
            return self._ph.check_needs_rehash(password_hash)
        except InvalidHashError:
            return True
