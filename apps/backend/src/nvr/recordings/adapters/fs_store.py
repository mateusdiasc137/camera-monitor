from __future__ import annotations

import os
import shutil
from pathlib import Path, PurePosixPath

from nvr.recordings.adapters.fmp4_probe import probe_file
from nvr.recordings.domain import DiskUsage
from nvr.recordings.ports import ProbedSegment, SegmentFile


class FileSegmentStore:
    """Arquivos fMP4 do MediaMTX: <raiz>/<path>/<arquivo>.mp4 (um nível de subpasta)."""

    def scan(self, root: Path) -> list[SegmentFile]:
        files: list[SegmentFile] = []
        if not root.is_dir():
            return files
        with os.scandir(root) as folders:
            for folder in folders:
                if not folder.is_dir(follow_symlinks=False):
                    continue
                with os.scandir(folder.path) as entries:
                    for entry in entries:
                        if not entry.name.endswith(".mp4") or not entry.is_file(follow_symlinks=False):
                            continue
                        try:
                            stat = entry.stat(follow_symlinks=False)
                        except FileNotFoundError:
                            continue  # removido entre a listagem e o stat
                        files.append(SegmentFile(f"{folder.name}/{entry.name}", stat.st_size, stat.st_mtime))
        return files

    @staticmethod
    def _resolve(root: Path, rel_path: str) -> Path:
        """Caminho do segmento, garantidamente dentro da raiz (sem '..', sem links para fora)."""
        parts = PurePosixPath(rel_path).parts
        if len(parts) != 2 or any(p in ("", ".", "..") for p in parts) or not parts[1].endswith(".mp4"):
            raise ValueError(f"caminho de segmento inválido: {rel_path}")
        path = root / parts[0] / parts[1]
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"segmento fora do local de gravação: {rel_path}")
        return path

    def probe(self, root: Path, rel_path: str) -> ProbedSegment:
        info = probe_file(self._resolve(root, rel_path))
        return ProbedSegment(info.start_at, info.duration_s, info.duration_from_fragments)

    def delete(self, root: Path, rel_path: str) -> None:
        path = self._resolve(root, rel_path)
        if path.is_symlink():
            raise ValueError(f"não apago links: {rel_path}")
        path.unlink(missing_ok=True)


class ShutilDiskProbe:
    def usage(self, path: Path) -> DiskUsage:
        target = path
        while not target.exists() and target.parent != target:
            target = target.parent  # pasta ainda não criada: mede o volume que a conterá
        total, _used, free = shutil.disk_usage(target)
        return DiskUsage(total, free)
