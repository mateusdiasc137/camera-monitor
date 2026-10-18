from datetime import UTC, datetime, timedelta, timezone

import pytest

from nvr.recordings.domain import (
    ALIGN_MARGIN,
    Crop,
    Gap,
    GapReason,
    Lens,
    RecordingInterval,
    Segment,
    StatusPoint,
    align_to_recording,
    find_gaps,
    is_continuous,
    lens_crop,
    merge_intervals,
    parse_segment_filename,
)

T0 = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
BRT = timezone(timedelta(hours=-3))


def seg(start_min: float, dur_s: float) -> Segment:
    return Segment("c", "l", f"x/{start_min}.mp4", T0 + timedelta(minutes=start_min), dur_s, 1)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("2026-10-17_18-04-57-207889-0300.mp4", datetime(2026, 10, 17, 21, 4, 57, 207889, tzinfo=UTC)),
        ("2026-10-17_21-04-57-207889Z.mp4", datetime(2026, 10, 17, 21, 4, 57, 207889, tzinfo=UTC)),
        ("2026-10-17_23-04-57-000001+0200.mp4", datetime(2026, 10, 17, 21, 4, 57, 1, tzinfo=UTC)),
    ],
)
def test_parse_filename_with_zone(name: str, expected: datetime) -> None:
    assert parse_segment_filename(name) == expected


def test_parse_legacy_filename_uses_local_zone() -> None:
    parsed = parse_segment_filename("2026-10-17_18-04-57-207889.mp4", local_tz=BRT)
    assert parsed == datetime(2026, 10, 17, 21, 4, 57, 207889, tzinfo=UTC)


@pytest.mark.parametrize("name", ["foto.jpg", "2026-10-17_18-04-57.mp4", "../2026-10-17_18-04-57-207889.mp4"])
def test_parse_rejects_other_names(name: str) -> None:
    assert parse_segment_filename(name) is None


def test_merge_contiguous_and_overlapping_segments() -> None:
    intervals = merge_intervals([seg(5, 300), seg(0, 300), seg(10, 299.5), seg(9.99, 30)])
    assert intervals == [RecordingInterval(T0, T0 + timedelta(minutes=10, seconds=299.5))]


def test_merge_splits_on_real_gaps_and_ignores_empty() -> None:
    intervals = merge_intervals([seg(0, 300), seg(6, 60), seg(20, 0)])
    assert [(i.start, i.end) for i in intervals] == [
        (T0, T0 + timedelta(minutes=5)),
        (T0 + timedelta(minutes=6), T0 + timedelta(minutes=7)),
    ]


def test_gaps_with_reasons_from_status_history() -> None:
    intervals = [
        RecordingInterval(T0, T0 + timedelta(minutes=10)),
        RecordingInterval(T0 + timedelta(minutes=20), T0 + timedelta(minutes=30)),
        RecordingInterval(T0 + timedelta(minutes=40), T0 + timedelta(minutes=50)),
    ]
    history = [
        StatusPoint(T0 - timedelta(hours=1), "online", True),
        StatusPoint(T0 + timedelta(minutes=10), "online", False),  # usuário desligou a gravação
        StatusPoint(T0 + timedelta(minutes=20), "online", True),
        StatusPoint(T0 + timedelta(minutes=30, seconds=1), "offline", False),  # câmera caiu
        StatusPoint(T0 + timedelta(minutes=40), "online", True),
    ]
    gaps = find_gaps(intervals, T0 - timedelta(minutes=5), T0 + timedelta(minutes=50), history)
    assert gaps == [
        Gap(
            T0 - timedelta(minutes=5), T0, GapReason.UNKNOWN
        ),  # antes: online e gravando → sem motivo conhecido
        Gap(T0 + timedelta(minutes=10), T0 + timedelta(minutes=20), GapReason.PAUSED),
        Gap(T0 + timedelta(minutes=30), T0 + timedelta(minutes=40), GapReason.CAMERA_UNAVAILABLE),
    ]


def test_reason_accounts_for_status_sampling_delay() -> None:
    """Caso real (17/10, 19:37): gravação desligada às :19, estado registrado só às :23."""
    stop = T0 + timedelta(minutes=37, seconds=19)
    intervals = [RecordingInterval(T0 + timedelta(minutes=36), stop)]
    history = [
        StatusPoint(T0 + timedelta(minutes=36, seconds=17), "online", True),
        StatusPoint(stop + timedelta(seconds=4), "online", False),
    ]
    gaps = find_gaps(intervals, T0 + timedelta(minutes=36), T0 + timedelta(minutes=43), history)
    assert gaps == [Gap(stop, T0 + timedelta(minutes=43), GapReason.PAUSED)]


def test_long_gap_is_split_by_status_changes() -> None:
    """Caso real (17/10, 18:41→19:36): pausa, depois servidor de mídia fora, depois pausa de novo."""
    stop = T0 + timedelta(minutes=41, seconds=48)
    resume = T0 + timedelta(hours=1, minutes=36)
    intervals = [
        RecordingInterval(T0 + timedelta(minutes=41), stop),
        RecordingInterval(resume, resume + timedelta(minutes=1)),
    ]
    history = [
        StatusPoint(T0 + timedelta(minutes=41, seconds=18), "online", True),
        StatusPoint(stop + timedelta(seconds=5), "online", False),
        StatusPoint(T0 + timedelta(minutes=45, seconds=33), "offline", False),
        StatusPoint(T0 + timedelta(minutes=45, seconds=40), "offline", False),  # mesma causa: não divide
        StatusPoint(T0 + timedelta(hours=1, minutes=25, seconds=40), "online", False),
        StatusPoint(resume + timedelta(seconds=4), "online", True),
    ]
    gaps = find_gaps(intervals, T0 + timedelta(minutes=41), resume + timedelta(minutes=1), history)
    assert gaps == [
        Gap(stop, T0 + timedelta(minutes=45, seconds=33), GapReason.PAUSED),
        Gap(T0 + timedelta(minutes=45, seconds=33), T0 + timedelta(hours=1, minutes=25, seconds=40),
            GapReason.CAMERA_UNAVAILABLE),
        Gap(T0 + timedelta(hours=1, minutes=25, seconds=40), resume, GapReason.PAUSED),
    ]  # fmt: skip


def test_no_history_means_unknown_and_tiny_gaps_are_ignored() -> None:
    intervals = [
        RecordingInterval(T0, T0 + timedelta(minutes=5)),
        RecordingInterval(T0 + timedelta(minutes=5, seconds=1), T0 + timedelta(minutes=10)),
    ]
    assert find_gaps(intervals, T0, T0 + timedelta(minutes=10)) == []
    assert find_gaps([], T0, T0 + timedelta(minutes=1)) == [
        Gap(T0, T0 + timedelta(minutes=1), GapReason.UNKNOWN)
    ]


# ---------------------------------------------------------------- exportação


@pytest.mark.parametrize(
    ("lens", "height", "crop"),
    [
        (Lens.BOTH, 2592, None),
        (Lens.TOP, 2592, Crop(0, 1296)),  # X6C-WEQ: 2304x2592 = duas lentes de 2304x1296
        (Lens.BOTTOM, 2592, Crop(1296, 0)),
        (Lens.TOP, 1082, Crop(0, 542)),  # metade ímpar: a de cima fica par (croma 4:2:0)
        (Lens.BOTTOM, 1082, Crop(540, 0)),
    ],
)
def test_lens_crop(lens: Lens, height: int, crop: Crop | None) -> None:
    assert lens_crop(lens, height) == crop


def test_align_to_recording_moves_start_out_of_a_gap() -> None:
    segments = [seg(0, 60), seg(1.01, 60)]  # buraco de 0,6 s às 12:01:00
    end = T0 + timedelta(minutes=2)
    inside = T0 + timedelta(seconds=30)
    assert align_to_recording(segments, inside, end) == (inside, 90.0)
    in_gap = T0 + timedelta(seconds=60.2)
    start, duration = align_to_recording(segments, in_gap, end)  # type: ignore[misc]
    assert start == segments[1].start_at + ALIGN_MARGIN
    assert duration == pytest.approx((end - start).total_seconds())
    assert align_to_recording(segments, T0 + timedelta(minutes=5), T0 + timedelta(minutes=6)) is None


def test_is_continuous_tolerates_only_tiny_gaps() -> None:
    normal = [seg(0, 299.9), seg(5, 299.9), seg(10, 299.9)]  # 0,1 s entre segmentos: emendados
    assert is_continuous(normal, T0 + timedelta(minutes=1), T0 + timedelta(minutes=12))
    restarted = [seg(0, 59.4), seg(1, 60)]  # 0,6 s: o servidor de playback para aqui
    assert not is_continuous(restarted, T0, T0 + timedelta(minutes=2))
    assert not is_continuous(normal, T0 - timedelta(minutes=1), T0 + timedelta(minutes=2))  # começa antes
    assert not is_continuous(normal, T0, T0 + timedelta(minutes=20))  # termina depois
    assert not is_continuous([], T0, T0 + timedelta(minutes=1))
