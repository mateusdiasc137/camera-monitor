"""Sistema de arquivos local para escolher o local de gravação (navegar, testar escrita, volumes)."""

from __future__ import annotations

import getpass
import importlib
import os
import uuid
from pathlib import Path
from typing import Any

from nvr.recordings.ports import FolderCheck, FolderEntry
from nvr.streaming.domain import parse_path_name

# Pastas de sistema que só confundem a navegação.
_HIDDEN = {"$recycle.bin", "system volume information", "lost+found", "recovery", "config.msi"}


def _existing_ancestor(path: Path) -> Path:
    target = path
    while not target.exists() and target.parent != target:
        target = target.parent
    return target


class LocalStorageFilesystem:
    def resolve(self, path: str) -> Path:
        raw = path.strip()
        if raw.startswith(("\\\\?\\", "\\\\.\\")):
            raise ValueError("caminho de dispositivo não é aceito")
        candidate = Path(raw)
        if not candidate.is_absolute():
            raise ValueError("o caminho precisa ser absoluto")
        return candidate.resolve()  # normaliza '..' e segue links (inclusive junções no Windows)

    def subfolders(self, path: Path) -> list[FolderEntry]:
        entries: list[FolderEntry] = []
        with os.scandir(path) as items:
            for item in items:
                if item.name.startswith(".") or item.name.casefold() in _HIDDEN:
                    continue
                try:
                    if not item.is_dir():  # segue links: o destino é conferido contra as raízes
                        continue
                    resolved = Path(item.path).resolve()
                except OSError:
                    continue
                entries.append(FolderEntry(item.name, resolved))
        return sorted(entries, key=lambda e: e.name.casefold())

    @staticmethod
    def _try_write(folder: Path) -> tuple[bool, str]:
        probe = folder / f".nvr-teste-de-escrita-{uuid.uuid4().hex}"
        try:
            with probe.open("wb") as handle:
                handle.write(b"nvr")
                handle.flush()
                os.fsync(handle.fileno())
            probe.unlink()
        except OSError as exc:
            return False, exc.strerror or type(exc).__name__
        return True, ""

    @staticmethod
    def _system_volume(path: Path) -> bool:
        if os.name == "nt":
            system_drive = os.environ.get("SYSTEMDRIVE", "C:").casefold()
            return path.drive.casefold() == system_drive
        try:
            return os.stat(_existing_ancestor(path)).st_dev == os.stat("/").st_dev
        except OSError:
            return False

    def check(self, path: Path) -> FolderCheck:
        exists, is_dir = path.exists(), path.is_dir()
        writable, error, creatable, foreign = False, "", False, 0
        if is_dir:
            writable, error = self._try_write(path)
            try:
                with os.scandir(path) as items:
                    foreign = sum(1 for i in items if not (i.is_dir() and parse_path_name(i.name)))
            except OSError:
                foreign = 0
        elif not exists:
            ancestor = _existing_ancestor(path)
            if ancestor.is_dir():
                creatable, error = self._try_write(ancestor)
        return FolderCheck(
            path, exists, is_dir, creatable, writable, error, foreign, self._system_volume(path)
        )

    def same_volume(self, a: Path, b: Path) -> bool:
        try:
            return os.stat(_existing_ancestor(a)).st_dev == os.stat(_existing_ancestor(b)).st_dev
        except OSError:
            return False

    def make_dir(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)

    def permission_hint(self, path: Path) -> str:
        user = getpass.getuser()
        if os.name == "nt":
            return f'icacls "{path}" /grant "{user}:(OI)(CI)M"'
        # Servidor: grupo compartilhado com o MediaMTX e setgid, para as gravações herdarem o grupo.
        # (grp/getgid só existem fora do Windows: carregados aqui para a análise de tipos no Windows.)
        posix: Any = os
        group = importlib.import_module("grp").getgrgid(posix.getgid()).gr_name
        return f"sudo chown {user}:{group} '{path}' && sudo chmod 2770 '{path}'"
