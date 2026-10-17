"""Gera segmentos fMP4 sintéticos com a mesma estrutura dos gravados pelo MediaMTX v1.21.

ftyp | moov[mvhd, trak(tkhd, mdia/mdhd)×N, udta/mtxi] | (moof[mfhd, traf(tfhd, tfdt, trun)×N] mdat)*
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import UTC, datetime


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def full(kind: bytes, version: int, flags: int, payload: bytes) -> bytes:
    return box(kind, struct.pack(">I", (version << 24) | flags) + payload)


@dataclass
class Track:
    track_id: int
    timescale: int
    sample_duration: int  # em unidades da trilha
    samples_per_part: int


@dataclass
class Fmp4Spec:
    start: datetime | None = datetime(2026, 10, 17, 21, 0, 0, 123456, tzinfo=UTC)
    finalized_ms: int | None = None  # duração no mvhd (ms); None = não finalizado (0)
    parts: int = 3
    tracks: list[Track] = field(
        default_factory=lambda: [Track(1, 90000, 7200, 12), Track(2, 8000, 320, 25)]  # ~1 s por parte
    )
    per_sample_durations: bool = True  # False: usa a duração padrão do tfhd
    tfdt_version: int = 1
    truncate_last_bytes: int = 0


def build(spec: Fmp4Spec) -> bytes:
    ftyp = box(b"ftyp", b"iso5" + struct.pack(">I", 1) + b"iso5dashmp42")
    mvhd = full(b"mvhd", 0, 0, struct.pack(">IIII", 0, 0, 1000, spec.finalized_ms or 0) + b"\x00" * 80)
    traks = b""
    for t in spec.tracks:
        tkhd = full(b"tkhd", 0, 3, struct.pack(">III", 0, 0, t.track_id) + b"\x00" * 68)
        mdhd = full(b"mdhd", 0, 0, struct.pack(">IIII", 0, 0, t.timescale, 0) + b"\x00" * 4)
        traks += box(b"trak", tkhd + box(b"mdia", mdhd))
    moov_children = mvhd + traks
    if spec.start is not None:
        ntp_ns = int(spec.start.timestamp() * 1_000_000) * 1000
        mtxi = full(b"mtxi", 0, 0, b"\x11" * 16 + struct.pack(">Qqq", 0, 0, ntp_ns))
        moov_children += box(b"udta", mtxi)
    out = ftyp + box(b"moov", moov_children)

    for part in range(spec.parts):
        trafs = b""
        for t in spec.tracks:
            if spec.per_sample_durations:
                tfhd = full(b"tfhd", 0, 0x020000, struct.pack(">I", t.track_id))
                entries = b"".join(
                    struct.pack(">II", t.sample_duration, 100) for _ in range(t.samples_per_part)
                )
                trun = full(b"trun", 0, 0x000301, struct.pack(">Ii", t.samples_per_part, 0) + entries)
            else:
                tfhd = full(b"tfhd", 0, 0x020008, struct.pack(">II", t.track_id, t.sample_duration))
                entries = b"".join(struct.pack(">I", 100) for _ in range(t.samples_per_part))
                trun = full(b"trun", 0, 0x000201, struct.pack(">Ii", t.samples_per_part, 0) + entries)
            base = part * t.sample_duration * t.samples_per_part
            tfdt = (
                full(b"tfdt", 1, 0, struct.pack(">Q", base))
                if spec.tfdt_version == 1
                else full(b"tfdt", 0, 0, struct.pack(">I", base))
            )
            trafs += box(b"traf", tfhd + tfdt + trun)
        moof = box(b"moof", full(b"mfhd", 0, 0, struct.pack(">I", part + 1)) + trafs)
        mdat = box(b"mdat", b"\x00" * 64)
        out += moof + mdat
    if spec.truncate_last_bytes:
        out = out[: -spec.truncate_last_bytes]
    return out


def expected_fragment_duration(spec: Fmp4Spec, complete_parts: int) -> float:
    return max(t.sample_duration * t.samples_per_part * complete_parts / t.timescale for t in spec.tracks)
