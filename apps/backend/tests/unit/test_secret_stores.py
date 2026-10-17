from pathlib import Path

import pytest

from nvr.cameras.adapters.secret_stores import FileSecretStore, MemorySecretStore
from nvr.cameras.domain import CameraCredentials

CREDS = CameraCredentials("admin", "senha-da-camera-XYZ")


def test_file_store_round_trip_and_ciphertext(tmp_path: Path) -> None:
    store = FileSecretStore(tmp_path / "data" / "secrets.enc", tmp_path / "config" / "secret.key")
    store.put("camera:1", CREDS)
    store.put("camera:2", CameraCredentials("outro", "outra-senha"))
    assert store.get("camera:1") == CREDS
    raw = (tmp_path / "data" / "secrets.enc").read_bytes()
    assert b"senha-da-camera-XYZ" not in raw
    assert b"admin" not in raw

    reopened = FileSecretStore(tmp_path / "data" / "secrets.enc", tmp_path / "config" / "secret.key")
    assert reopened.get("camera:2") == CameraCredentials("outro", "outra-senha")
    reopened.delete("camera:1")
    assert reopened.get("camera:1") is None
    assert reopened.get("inexistente") is None


def test_file_store_with_wrong_key_fails_loudly(tmp_path: Path) -> None:
    FileSecretStore(tmp_path / "secrets.enc", tmp_path / "a.key").put("camera:1", CREDS)
    other = FileSecretStore(tmp_path / "secrets.enc", tmp_path / "b.key")
    with pytest.raises(RuntimeError, match="decifrar"):
        other.get("camera:1")


def test_memory_store() -> None:
    store = MemorySecretStore()
    store.put("r", CREDS)
    assert store.get("r") == CREDS
    store.delete("r")
    store.delete("r")  # idempotente
    assert store.get("r") is None
