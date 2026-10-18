from __future__ import annotations

from collections.abc import Callable

from nvr.cameras.ports import CameraRepository
from nvr.recordings.ports import SegmentIndex
from nvr.streaming.application import MonitorCameraHealth
from nvr.system.domain import CameraSummary


class MonitoredCameraSummaries:
    """Junta o cadastro, o estado do monitor de câmeras e o último trecho do índice de gravações."""

    def __init__(
        self,
        cameras: CameraRepository,
        monitor: MonitorCameraHealth,
        index: SegmentIndex,
        recording_allowed: Callable[[], bool],
    ) -> None:
        self._cameras, self._monitor, self._index = cameras, monitor, index
        self._recording_allowed = recording_allowed

    async def __call__(self) -> list[CameraSummary]:
        current = self._monitor.current()
        allowed = self._recording_allowed()
        out: list[CameraSummary] = []
        for camera in await self._cameras.list():
            view = current.get(camera.id)
            last = await self._index.latest(camera.id)
            if not camera.enabled:
                state = "disabled"
            else:
                state = view.health.state.value if view is not None else "starting"
            out.append(
                CameraSummary(
                    camera_id=camera.id,
                    name=camera.name,
                    state=state,
                    detail=view.health.detail if view is not None else "",
                    since=view.since if view is not None else None,
                    recording_expected=camera.enabled and camera.recording_enabled and allowed,
                    recording=view is not None and view.health.recording,
                    last_recording_at=None if last is None else last.end_at,
                )
            )
        return out
