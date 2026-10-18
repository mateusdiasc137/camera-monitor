from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path, PurePosixPath

from nvr.recordings.adapters.fmp4_probe import probe_file
from nvr.recordings.domain import DiskUsage
from nvr.recordings.ports import ProbedSegment, SegmentFile
from nvr.streaming.domain import parse_path_name

ERROR_NOT_SAME_DEVICE = 17  # Windows: rename entre volumes


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

    def move(self, src_root: Path, dst_root: Path, rel_path: str) -> None:
        src = self._resolve(src_root, rel_path)
        dst = self._resolve(dst_root, rel_path)
        if src.is_symlink():
            raise ValueError(f"não movo links: {rel_path}")
        if not src.exists():
            if dst.exists():
                return  # já movido (retomada após queda)
            raise FileNotFoundError(errno.ENOENT, "arquivo sumiu da origem", str(src))
        dst.parent.mkdir(parents=True, exist_ok=True)
        size = src.stat().st_size
        if dst.exists():
            if dst.stat().st_size == size:
                src.unlink()  # cópia completa de uma tentativa anterior: falta só apagar a origem
                return
            raise FileExistsError(errno.EEXIST, "já existe outro arquivo com este nome no destino", str(dst))
        try:
            os.rename(src, dst)  # mesmo volume: instantâneo e atômico
            return
        except OSError as exc:
            if exc.errno != errno.EXDEV and getattr(exc, "winerror", None) != ERROR_NOT_SAME_DEVICE:
                raise
        # Outro volume: copia para .part (ignorado pela varredura), garante no disco e só então troca.
        part = dst.with_name(dst.name + ".part")
        try:
            shutil.copyfile(src, part)
            with part.open("rb+") as handle:
                os.fsync(handle.fileno())
            if part.stat().st_size != size:
                raise OSError(errno.EIO, "cópia incompleta", str(dst))
            os.replace(part, dst)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        src.unlink()

    def remove_empty_folders(self, root: Path) -> None:
        if not root.is_dir():
            return
        with os.scandir(root) as folders:
            for folder in folders:
                if parse_path_name(folder.name) is None or not folder.is_dir(follow_symlinks=False):
                    continue
                try:
                    os.rmdir(folder.path)  # só some se estiver vazia
                except OSError:
                    continue


class ShutilDiskProbe:
    def usage(self, path: Path) -> DiskUsage:
        target = path
        while not target.exists() and target.parent != target:
            target = target.parent  # pasta ainda não criada: mede o volume que a conterá
        total, _used, free = shutil.disk_usage(target)
        return DiskUsage(total, free)
