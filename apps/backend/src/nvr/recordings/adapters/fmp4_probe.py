"""Leitura mínima de segmentos fMP4 gravados pelo MediaMTX (v1.21), sem FFmpeg.

Estrutura (conferida em arquivo real da X6C-WEQ):
  ftyp | moov[mvhd, trak*(tkhd, mdia/mdhd), mvex, udta/mtxi] | (moof[mfhd, traf*(tfhd, tfdt, trun)] mdat)*

- Início absoluto: `udta/mtxi.NTP` (nanossegundos Unix, UTC), gravado pelo MediaMTX.
- Duração: `mvhd.duration / mvhd.timescale` (o MediaMTX reescreve ao fechar o segmento). Se for 0
  (gravação interrompida por queda de energia, por exemplo), soma o último fragmento completo:
  max por trilha de (tfdt + Σ durações do trun) / timescale da trilha — a mesma regra do MediaMTX.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO

MAX_MOOV = 4 * 1024 * 1024
MAX_MOOF = 1024 * 1024


class Fmp4Error(ValueError):
    pass


@dataclass(frozen=True)
class Fmp4Info:
    start_at: datetime | None  # da caixa mtxi (UTC); None se ausente
    duration_s: float
    duration_from_fragments: bool  # True = arquivo não finalizado (mvhd sem duração)


def _header(fh: BinaryIO, limit: int) -> tuple[bytes, int, int] | None:
    """Lê o cabeçalho de uma caixa. Retorna (tipo, tamanho total, tamanho do cabeçalho) ou None no fim."""
    start = fh.tell()
    raw = fh.read(8)
    if len(raw) < 8:
        return None
    size, kind = struct.unpack(">I4s", raw)
    header = 8
    if size == 1:
        ext = fh.read(8)
        if len(ext) < 8:
            return None
        size = struct.unpack(">Q", ext)[0]
        header = 16
    elif size == 0:
        size = limit - start  # caixa vai até o fim do arquivo
    if size < header:
        raise Fmp4Error(f"caixa {kind!r} com tamanho inválido ({size})")
    return kind, size, header


def _children(data: bytes) -> list[tuple[bytes, bytes]]:
    """Caixas filhas de um contêiner já lido em memória: [(tipo, corpo)]."""
    out: list[tuple[bytes, bytes]] = []
    pos = 0
    while pos + 8 <= len(data):
        size, kind = struct.unpack(">I4s", data[pos : pos + 8])
        header = 8
        if size == 1:
            size = struct.unpack(">Q", data[pos + 8 : pos + 16])[0]
            header = 16
        elif size == 0:
            size = len(data) - pos
        if size < header or pos + size > len(data):
            raise Fmp4Error(f"caixa {kind!r} truncada")
        out.append((kind, data[pos + header : pos + size]))
        pos += size
    return out


def _find(boxes: list[tuple[bytes, bytes]], kind: bytes) -> bytes | None:
    return next((body for k, body in boxes if k == kind), None)


def _parse_mvhd(body: bytes) -> tuple[int, int]:
    if body[0] == 1:
        timescale, duration = struct.unpack(">IQ", body[20:32])
    else:
        timescale, duration = struct.unpack(">II", body[12:20])
    return timescale, duration


def _parse_moov(data: bytes) -> tuple[int, int, dict[int, int], datetime | None]:
    """→ (timescale do mvhd, duração do mvhd, {track_id: timescale}, início da mtxi)."""
    boxes = _children(data)
    mvhd = _find(boxes, b"mvhd")
    if mvhd is None:
        raise Fmp4Error("mvhd ausente")
    movie_timescale, movie_duration = _parse_mvhd(mvhd)

    track_timescales: dict[int, int] = {}
    for kind, trak in boxes:
        if kind != b"trak":
            continue
        trak_boxes = _children(trak)
        tkhd, mdia = _find(trak_boxes, b"tkhd"), _find(trak_boxes, b"mdia")
        if tkhd is None or mdia is None:
            continue
        track_id = struct.unpack(">I", tkhd[20:24] if tkhd[0] == 1 else tkhd[12:16])[0]
        mdhd = _find(_children(mdia), b"mdhd")
        if mdhd is None:
            continue
        timescale = struct.unpack(">I", mdhd[20:24] if mdhd[0] == 1 else mdhd[12:16])[0]
        track_timescales[track_id] = timescale

    start_at = None
    udta = _find(boxes, b"udta")
    if udta is not None:
        mtxi = _find(_children(udta), b"mtxi")
        # FullBox(4) + StreamID(16) + SegmentNumber(8) + DTS(8) + NTP(8)
        if mtxi is not None and len(mtxi) >= 44:
            ntp_ns = struct.unpack(">q", mtxi[36:44])[0]
            if ntp_ns > 0:
                start_at = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=ntp_ns // 1000)
    return movie_timescale, movie_duration, track_timescales, start_at


def _fragment_end(moof: bytes, track_timescales: dict[int, int]) -> float:
    """Maior instante final (s, desde o início do segmento) entre as trilhas de um moof."""
    best = 0.0
    for kind, traf in _children(moof):
        if kind != b"traf":
            continue
        boxes = _children(traf)
        tfhd, tfdt, trun = _find(boxes, b"tfhd"), _find(boxes, b"tfdt"), _find(boxes, b"trun")
        if tfhd is None or tfdt is None or trun is None:
            continue
        tf_flags = int.from_bytes(tfhd[1:4], "big")
        track_id = struct.unpack(">I", tfhd[4:8])[0]
        timescale = track_timescales.get(track_id)
        if not timescale:
            continue
        # Duração padrão do tfhd (flag 0x08), após base-data-offset (0x01) e sample-description (0x02).
        offset = 8 + (8 if tf_flags & 0x01 else 0) + (4 if tf_flags & 0x02 else 0)
        default_duration = struct.unpack(">I", tfhd[offset : offset + 4])[0] if tf_flags & 0x08 else 0

        base = struct.unpack(">Q", tfdt[4:12])[0] if tfdt[0] == 1 else struct.unpack(">I", tfdt[4:8])[0]

        tr_flags = int.from_bytes(trun[1:4], "big")
        count = struct.unpack(">I", trun[4:8])[0]
        pos = 8 + (4 if tr_flags & 0x01 else 0) + (4 if tr_flags & 0x04 else 0)
        per_sample = sum(4 for bit in (0x100, 0x200, 0x400, 0x800) if tr_flags & bit)
        total = 0
        for _ in range(count):
            if tr_flags & 0x100:
                total += struct.unpack(">I", trun[pos : pos + 4])[0]
            else:
                total += default_duration
            pos += per_sample
        best = max(best, (base + total) / timescale)
    return best


def read_fmp4(fh: BinaryIO, file_size: int) -> Fmp4Info:
    first = _header(fh, file_size)
    if first is None or first[0] != b"ftyp":
        raise Fmp4Error("não é um MP4 (ftyp ausente)")
    fh.seek(first[1])

    moov_header = _header(fh, file_size)
    if moov_header is None or moov_header[0] != b"moov":
        raise Fmp4Error("moov ausente")
    _, moov_size, moov_hdr = moov_header
    if moov_size > MAX_MOOV:
        raise Fmp4Error("moov grande demais")
    moov = fh.read(moov_size - moov_hdr)
    if len(moov) < moov_size - moov_hdr:
        raise Fmp4Error("moov truncado")
    movie_timescale, movie_duration, track_timescales, start_at = _parse_moov(moov)

    if movie_duration > 0 and movie_timescale > 0:
        return Fmp4Info(start_at, movie_duration / movie_timescale, False)

    # Segmento não finalizado: usar o último par moof+mdat completo.
    duration = 0.0
    while True:
        box_start = fh.tell()
        header = _header(fh, file_size)
        if header is None:
            break
        kind, size, hdr = header
        if box_start + size > file_size:
            break  # último fragmento cortado: ignorar
        if kind == b"moof":
            if size > MAX_MOOF:
                raise Fmp4Error("moof grande demais")
            moof = fh.read(size - hdr)
            mdat_start = fh.tell()
            mdat = _header(fh, file_size)
            if mdat is None or mdat[0] != b"mdat" or mdat_start + mdat[1] > file_size:
                break  # mídia do fragmento incompleta
            duration = max(duration, _fragment_end(moof, track_timescales))
            fh.seek(mdat_start + mdat[1])
        else:
            fh.seek(box_start + size)
    return Fmp4Info(start_at, duration, True)


def probe_file(path: Path) -> Fmp4Info:
    with path.open("rb") as fh:
        return read_fmp4(fh, path.stat().st_size)
