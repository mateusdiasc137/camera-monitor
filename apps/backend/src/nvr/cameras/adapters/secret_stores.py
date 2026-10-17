"""Cofres de credenciais de câmeras (ADR-003). Nenhuma implementação registra valores em log."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from nvr.cameras.domain import CameraCredentials

log = logging.getLogger(__name__)
KEYRING_SERVICE = "nvr-camera-monitor"


def _encode(credentials: CameraCredentials) -> str:
    return json.dumps({"u": credentials.username, "p": credentials.password})


def _decode(raw: str) -> CameraCredentials:
    data = json.loads(raw)
    return CameraCredentials(data["u"], data["p"])


class KeyringSecretStore:
    """Cofre do sistema: Windows Credential Manager, Secret Service (Linux) ou Keychain (macOS)."""

    def __init__(self, service: str = KEYRING_SERVICE) -> None:
        import keyring

        self._keyring = keyring
        self._service = service

    def put(self, ref: str, credentials: CameraCredentials) -> None:
        self._keyring.set_password(self._service, ref, _encode(credentials))

    def get(self, ref: str) -> CameraCredentials | None:
        raw = self._keyring.get_password(self._service, ref)
        return None if raw is None else _decode(raw)

    def delete(self, ref: str) -> None:
        import keyring.errors

        with contextlib.suppress(keyring.errors.PasswordDeleteError):
            self._keyring.delete_password(self._service, ref)

    @staticmethod
    def available() -> bool:
        """Há um backend real? (servidores Linux sem sessão gráfica normalmente não têm)."""
        try:
            import keyring
            from keyring.backends import fail

            backend = keyring.get_keyring()
            return not isinstance(backend, fail.Keyring) and backend.priority > 0
        except Exception:
            return False


class FileSecretStore:
    """Arquivo cifrado (Fernet: AES-128-CBC + HMAC), com a chave em arquivo separado, fora do data_dir.

    Alternativa para servidores sem cofre do sistema. Risco aceito e documentado: quem ler a chave e o
    arquivo obtém as credenciais; ambos ficam com permissão 0600 para o usuário do serviço.
    """

    def __init__(self, path: Path, key_file: Path) -> None:
        self._path, self._key_file = path, key_file
        self._lock = threading.Lock()
        self._fernet = Fernet(self._load_or_create_key())

    def _load_or_create_key(self) -> bytes:
        if self._key_file.exists():
            return self._key_file.read_bytes().strip()
        self._key_file.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        _write_private(self._key_file, key)
        log.info("chave do cofre de credenciais criada em %s", self._key_file)
        return key

    def _read_all(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            data: dict[str, str] = json.loads(self._fernet.decrypt(self._path.read_bytes()))
        except InvalidToken as exc:
            raise RuntimeError(
                f"não foi possível decifrar {self._path}: chave errada ou arquivo corrompido"
            ) from exc
        return data

    def _write_all(self, data: dict[str, str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        _write_private(tmp, self._fernet.encrypt(json.dumps(data).encode()))
        os.replace(tmp, self._path)

    def put(self, ref: str, credentials: CameraCredentials) -> None:
        with self._lock:
            data = self._read_all()
            data[ref] = _encode(credentials)
            self._write_all(data)

    def get(self, ref: str) -> CameraCredentials | None:
        with self._lock:
            raw = self._read_all().get(ref)
        return None if raw is None else _decode(raw)

    def delete(self, ref: str) -> None:
        with self._lock:
            data = self._read_all()
            if data.pop(ref, None) is not None:
                self._write_all(data)


class MemorySecretStore:
    """Só para testes e demonstrações: perde tudo ao reiniciar."""

    def __init__(self) -> None:
        self._data: dict[str, CameraCredentials] = {}

    def put(self, ref: str, credentials: CameraCredentials) -> None:
        self._data[ref] = credentials

    def get(self, ref: str) -> CameraCredentials | None:
        return self._data.get(ref)

    def delete(self, ref: str) -> None:
        self._data.pop(ref, None)


def _write_private(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(content)
    if sys.platform != "win32":
        path.chmod(0o600)
