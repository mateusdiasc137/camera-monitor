from datetime import UTC, datetime, timedelta

import pytest

from nvr.recordings.domain import (
    GIB,
    DiskUsage,
    ReplacementPolicy,
    StoragePolicy,
    StorageState,
    StoredFile,
    plan_cleanup,
    protected_files,
    should_resume_recording,
)

T0 = datetime(2026, 10, 1, tzinfo=UTC)
NOW = T0.timestamp() + 30 * 86400
PLENTY = DiskUsage(1000 * GIB, 500 * GIB)


def policy(quota_gb: float | None = 10, replacement: ReplacementPolicy = ReplacementPolicy.OVERWRITE_OLDEST,
           min_free_gb: float = 2) -> StoragePolicy:  # fmt: skip
    quota = None if quota_gb is None else int(quota_gb * GIB)
    return StoragePolicy(quota, replacement, 0.95, 0.90, int(min_free_gb * GIB))


def files(cam: str, count: int, gb_each: float = 1.0, start_h: int = 0) -> list[StoredFile]:
    """`count` segmentos de 1 h, o primeiro em T0 + start_h horas."""
    return [
        StoredFile(
            f"{cam}/s{start_h + i:03d}.mp4", cam, T0 + timedelta(hours=start_h + i), int(gb_each * GIB), 0.0
        )
        for i in range(count)
    ]


def test_below_high_watermark_does_nothing() -> None:
    plan = plan_cleanup(policy(10), files("a", 9), PLENTY, NOW)  # 9 GB < 9,5 GB
    assert plan.state is StorageState.OK
    assert plan.delete == ()


def test_deletes_oldest_across_cameras_down_to_low_watermark() -> None:
    stored = files("cam-a", 6, start_h=0) + files("cam-b", 6, start_h=3)  # 12 GB numa cota de 10 GB
    plan = plan_cleanup(policy(10), stored, PLENTY, NOW)
    assert plan.state is StorageState.CLEANING
    assert plan.reason == "cota"
    # precisa liberar 12 - 9 = 3 GB: os 3 mais antigos de todos (cam-a 0,1,2)
    assert [f.rel_path for f in plan.delete] == ["cam-a/s000.mp4", "cam-a/s001.mp4", "cam-a/s002.mp4"]


def test_never_deletes_newest_per_folder_or_recently_modified() -> None:
    stored = files("cam-a", 3)
    recent = StoredFile(
        "cam-a/x.mp4", "cam-a", T0 - timedelta(days=1), GIB, NOW - 5
    )  # antigo, mas mexido agora
    plan = plan_cleanup(policy(1), [*stored, recent], PLENTY, NOW)
    deleted = {f.rel_path for f in plan.delete}
    assert "cam-a/s002.mp4" not in deleted  # mais novo da pasta (pode estar gravando)
    assert "cam-a/x.mp4" not in deleted
    assert deleted == {"cam-a/s000.mp4", "cam-a/s001.mp4"}
    assert plan.state is StorageState.FULL  # não deu para chegar a 0,9 GB
    assert protected_files([*stored, recent], NOW) == {"cam-a/s002.mp4", "cam-a/x.mp4"}


def test_free_space_floor_acts_even_without_quota() -> None:
    disk = DiskUsage(100 * GIB, int(1.5 * GIB))  # abaixo do piso de 2 GB
    plan = plan_cleanup(policy(None), files("a", 5), disk, NOW)
    assert plan.reason == "espaço livre"
    # precisa chegar a 2,5 GB livres: liberar 1 GB → o mais antigo
    assert [f.rel_path for f in plan.delete] == ["a/s000.mp4"]


def test_stop_when_full_never_deletes() -> None:
    plan = plan_cleanup(policy(10, ReplacementPolicy.STOP_WHEN_FULL), files("a", 12), PLENTY, NOW)
    assert plan.stop_recording is True
    assert plan.delete == ()
    assert plan.state is StorageState.FULL


@pytest.mark.parametrize(("used_gb", "resume"), [(9.5, False), (9.0, True), (5.0, True)])
def test_resume_uses_low_watermark(used_gb: float, resume: bool) -> None:
    assert should_resume_recording(policy(10), int(used_gb * GIB), PLENTY) is resume


def test_resume_needs_free_space_margin() -> None:
    assert should_resume_recording(policy(None), 0, DiskUsage(100 * GIB, int(2.2 * GIB))) is False
    assert should_resume_recording(policy(None), 0, DiskUsage(100 * GIB, 3 * GIB)) is True


def test_orphan_folder_newest_file_is_deletable() -> None:
    """Câmera removida não grava mais: o último arquivo dela não precisa de proteção."""
    stored = files("cam-removida", 2) + files("cam-ativa", 2, start_h=10)
    keep = protected_files(stored, NOW, active_folders={"cam-ativa"})
    assert keep == {"cam-ativa/s011.mp4"}
