"""Composition root: o único lugar que instancia adaptadores e liga casos de uso (ADR-001)."""

from __future__ import annotations

import logging
import secrets as secrets_module
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from nvr import __version__
from nvr.bootstrap.settings import Settings
from nvr.cameras.adapters.rtsp_probe import RtspDescribeProbe
from nvr.cameras.adapters.secret_stores import FileSecretStore, KeyringSecretStore, MemorySecretStore
from nvr.cameras.adapters.sql_repository import SqlCameraRepository
from nvr.cameras.application import (
    DeleteCamera,
    GetCamera,
    ListCameras,
    RegisterCamera,
    TestCameraConnection,
    UpdateCamera,
)
from nvr.cameras.ports import SecretStore, StreamProbe
from nvr.identity.adapters.argon2_hasher import Argon2PasswordHasher
from nvr.identity.adapters.memory_throttle import MemoryLoginThrottle
from nvr.identity.adapters.sql_repositories import SqlSessionRepository, SqlUserRepository
from nvr.identity.application import Authenticate, CreateUser, Login, Logout
from nvr.persistence.database import create_engine
from nvr.shared.clock import SystemClock
from nvr.streaming.adapters.ffmpeg_relay import FfmpegRelaySupervisor
from nvr.streaming.adapters.mediamtx_api import MediaMtxApi
from nvr.streaming.application import AuthorizeMediaAccess, ReconcileMedia
from nvr.system.adapters.database_probe import DatabaseProbe
from nvr.system.adapters.sql_audit import SqlAuditLog
from nvr.system.application import GetSystemHealth

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Container:
    settings: Settings
    engine: AsyncEngine
    get_system_health: GetSystemHealth
    create_user: CreateUser
    login: Login
    logout: Logout
    authenticate: Authenticate
    list_cameras: ListCameras
    get_camera: GetCamera
    register_camera: RegisterCamera
    update_camera: UpdateCamera
    delete_camera: DeleteCamera
    test_camera_connection: TestCameraConnection
    reconcile_media: ReconcileMedia
    authorize_media_access: AuthorizeMediaAccess
    media_api: MediaMtxApi
    relays: FfmpegRelaySupervisor

    async def aclose(self) -> None:
        await self.relays.stop_all()
        await self.media_api.aclose()
        await self.engine.dispose()


def build_secret_store(settings: Settings) -> SecretStore:
    backend = settings.secret_backend
    if backend == "memory":
        return MemorySecretStore()
    if backend == "keyring" or (backend == "auto" and KeyringSecretStore.available()):
        return KeyringSecretStore()
    log.info("cofre do sistema indisponível; usando arquivo cifrado")
    return FileSecretStore(settings.data_dir / "camera-secrets.enc", settings.resolved_secret_key_file())


def build_container(
    settings: Settings, *, secrets: SecretStore | None = None, probe: StreamProbe | None = None
) -> Container:
    engine = create_engine(settings.data_dir)
    clock = SystemClock()
    hasher = Argon2PasswordHasher()
    users = SqlUserRepository(engine)
    sessions = SqlSessionRepository(engine)
    audit = SqlAuditLog(engine, clock)
    cameras = SqlCameraRepository(engine)
    secrets = secrets if secrets is not None else build_secret_store(settings)
    camera_deps = (cameras, secrets, audit, clock)
    # Segredo do publicador local, novo a cada inicialização (ADR-002/003):
    # só os relays deste processo conseguem publicar no MediaMTX.
    relay_secret = secrets_module.token_urlsafe(24)
    media_api = MediaMtxApi(settings.mediamtx_api_url)
    relays = FfmpegRelaySupervisor(
        settings.ffmpeg_path, settings.mediamtx_rtsp_address, AuthorizeMediaAccess.RELAY_USER, relay_secret
    )
    return Container(
        settings=settings,
        engine=engine,
        get_system_health=GetSystemHealth(__version__, probes=[DatabaseProbe(engine)]),
        create_user=CreateUser(users, hasher, clock),
        login=Login(users, sessions, hasher, MemoryLoginThrottle(), clock),
        logout=Logout(sessions),
        authenticate=Authenticate(users, sessions, clock),
        list_cameras=ListCameras(*camera_deps),
        get_camera=GetCamera(*camera_deps),
        register_camera=RegisterCamera(*camera_deps),
        update_camera=UpdateCamera(*camera_deps),
        delete_camera=DeleteCamera(*camera_deps),
        test_camera_connection=TestCameraConnection(*camera_deps, probe=probe or RtspDescribeProbe()),
        reconcile_media=ReconcileMedia(
            cameras, secrets, media_api, relays, settings.resolved_recordings_dir().as_posix()
        ),
        authorize_media_access=AuthorizeMediaAccess(relay_secret),
        media_api=media_api,
        relays=relays,
    )
