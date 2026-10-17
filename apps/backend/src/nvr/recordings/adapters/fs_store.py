from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from nvr.recordings.adapters.fmp4_probe import probe_file
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

    def probe(self, root: Path, rel_path: str) -> ProbedSegment:
        parts = PurePosixPath(rel_path).parts
        if len(parts) != 2 or any(p in ("", ".", "..") for p in parts):
            raise ValueError(f"caminho de segmento inválido: {rel_path}")
        info = probe_file(root / parts[0] / parts[1])
        return ProbedSegment(info.start_at, info.duration_s, info.duration_from_fragments)
