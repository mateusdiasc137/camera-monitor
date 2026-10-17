import io

import pytest

from nvr.recordings.adapters.fmp4_probe import Fmp4Error, read_fmp4
from tests.fmp4_builder import Fmp4Spec, Track, build, expected_fragment_duration


def probe(data: bytes):  # type: ignore[no-untyped-def]
    return read_fmp4(io.BytesIO(data), len(data))


def test_finalized_segment_uses_mvhd_duration_and_mtxi_start() -> None:
    spec = Fmp4Spec(finalized_ms=77217)
    info = probe(build(spec))
    assert info.duration_s == pytest.approx(77.217)
    assert info.duration_from_fragments is False
    assert info.start_at == spec.start


def test_unfinished_segment_uses_fragments() -> None:
    spec = Fmp4Spec(finalized_ms=None, parts=4)
    info = probe(build(spec))
    assert info.duration_from_fragments is True
    assert info.duration_s == pytest.approx(expected_fragment_duration(spec, 4))  # 4 partes de ~0,96-1 s


def test_truncated_last_fragment_is_ignored() -> None:
    """Queda de energia no meio de uma parte: só os fragmentos completos contam."""
    spec = Fmp4Spec(finalized_ms=None, parts=4, truncate_last_bytes=20)
    info = probe(build(spec))
    assert info.duration_s == pytest.approx(expected_fragment_duration(spec, 3))


def test_default_sample_duration_and_tfdt_v0() -> None:
    spec = Fmp4Spec(finalized_ms=None, parts=2, per_sample_durations=False, tfdt_version=0)
    assert probe(build(spec)).duration_s == pytest.approx(expected_fragment_duration(spec, 2))


def test_longest_track_wins() -> None:
    spec = Fmp4Spec(finalized_ms=None, parts=1, tracks=[Track(1, 90000, 9000, 10), Track(2, 8000, 800, 12)])
    assert probe(build(spec)).duration_s == pytest.approx(1.2)  # áudio 12×0,1 s > vídeo 10×0,1 s


def test_missing_mtxi_gives_no_start() -> None:
    assert probe(build(Fmp4Spec(start=None, finalized_ms=1000))).start_at is None


def test_only_header_written_yet() -> None:
    spec = Fmp4Spec(finalized_ms=None, parts=0)
    assert probe(build(spec)).duration_s == 0


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"lixo qualquer que nao e mp4",
        build(Fmp4Spec())[:40],
        b"\x00\x00\x00\x10ftypisom\x00\x00\x00\x01",
    ],
)
def test_invalid_files_raise(data: bytes) -> None:
    with pytest.raises(Fmp4Error):
        probe(data)
