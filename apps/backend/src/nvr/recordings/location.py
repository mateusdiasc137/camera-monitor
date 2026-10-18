"""Local de gravação escolhido pela UI (D7, F1-11b): navegar, validar, trocar e migrar.

- Só pastas dentro das raízes permitidas na instalação (`NVR_STORAGE_ROOTS`), com links resolvidos.
- A troca é imediata: o MediaMTX passa a gravar no novo local sem derrubar o publicador.
- `migrate`: as gravações existentes são movidas em segundo plano (retomável após reinício);
  `fresh`: elas ficam na pasta antiga, fora do índice e da cota (voltam se o local for reativado).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any

from nvr.recordings.domain import (
    PROTECT_RECENT_S,
    RECORD_FILENAME_TEMPLATE,
    DiskUsage,
    LocationMode,
    MigrationState,
    StorageMigration,
    overlapping,
    parse_segment_filename,
    path_within,
    record_dir_problem,
)
from nvr.recordings.ports import (
    DiskProbe,
    FolderEntry,
    SegmentFile,
    SegmentIndex,
    SegmentStore,
    StorageFilesystem,
    StorageLocation,
    StorageLocations,
    StorageMigrations,
    StoragePolicies,
)
from nvr.recordings.storage import gb_text
from nvr.shared.audit import AuditLog
from nvr.shared.clock import Clock
from nvr.shared.errors import Conflict, NotFound, ValidationFailed
from nvr.shared.events import Event, EventPublisher
from nvr.streaming.domain import parse_path_name

log = logging.getLogger(__name__)
PROGRESS_EVERY_S = 1.0


class ActiveRecordDir:
    """Pasta do local ativo, em memória: a reconciliação de mídia monta o `recordPath` a partir dela."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def record_path(self) -> str:
        # %z no nome: início sem ambiguidade de fuso/horário de verão (o MediaMTX entende ao reproduzir).
        return f"{self.path.as_posix()}/%path/{RECORD_FILENAME_TEMPLATE}"

    def ensure_folder(self, path_name: str) -> None:
        """Pasta de gravação do path (`%path` do recordPath), criada pelo backend: herda o grupo e a
        permissão de escrita do grupo do local (setgid), que o MediaMTX não daria ao criá-la."""
        if "/" in path_name or "\\" in path_name or path_name in ("", ".", ".."):
            raise ValueError(f"nome de path inválido: {path_name}")
        (self.path / path_name).mkdir(exist_ok=True)


def managed_files(files: Sequence[SegmentFile]) -> list[SegmentFile]:
    """Só os segmentos em pastas de câmera do NVR (o resto da pasta nunca é tocado)."""
    return [
        f
        for f in files
        if parse_path_name(PurePosixPath(f.rel_path).parent.name) is not None
        and parse_segment_filename(PurePosixPath(f.rel_path).name) is not None
    ]


def _confine(fs: StorageFilesystem, roots: Sequence[Path], raw: str) -> Path:
    try:
        path = fs.resolve(raw)
    except (ValueError, OSError):
        raise ValidationFailed("caminho inválido", {"path": "informe um caminho absoluto"}) from None
    if not path_within(path, roots):
        allowed = ", ".join(str(r) for r in roots)
        raise ValidationFailed(
            "pasta fora das permitidas", {"path": f"escolha uma pasta dentro de: {allowed}"}
        )
    return path


# ------------------------------------------------------------------ navegar


@dataclass(frozen=True)
class FolderListing:
    path: Path | None  # None: lista das raízes permitidas
    parent: Path | None  # None: subir volta à lista das raízes
    folders: list[FolderEntry]


class BrowseStorage:
    def __init__(self, roots: Sequence[Path], fs: StorageFilesystem) -> None:
        self._roots, self._fs = list(roots), fs

    async def __call__(self, path: str | None) -> FolderListing:
        if not path:
            return FolderListing(None, None, [FolderEntry(str(r), r) for r in self._roots])
        target = _confine(self._fs, self._roots, path)
        try:
            entries = await asyncio.to_thread(self._fs.subfolders, target)
        except FileNotFoundError:
            raise NotFound("pasta não encontrada") from None
        except NotADirectoryError:
            raise ValidationFailed("não é uma pasta", {"path": "não é uma pasta"}) from None
        except PermissionError:
            raise ValidationFailed(
                "sem permissão para abrir a pasta",
                {"path": f"sem permissão para listar. Para liberar: {self._fs.permission_hint(target)}"},
            ) from None
        # Links que apontam para fora das raízes não aparecem (nem podem ser escolhidos).
        visible = [e for e in entries if path_within(e.path, self._roots)]
        parent = None if target in self._roots else target.parent
        return FolderListing(target, parent, visible)


# ------------------------------------------------------------------ validar


@dataclass(frozen=True)
class LocationCheck:
    path: Path
    errors: list[str]
    warnings: list[str]
    will_create: bool
    disk: DiskUsage | None
    current_path: Path | None
    current_bytes: int  # gravações do NVR no local atual (o que a migração moveria)
    existing_bytes: int  # gravações do NVR que já estão no novo local
    same_volume: bool
    migrate_problem: str  # vazio = dá para migrar

    @property
    def ok(self) -> bool:
        return not self.errors


class ValidateStorageLocation:
    def __init__(
        self,
        roots: Sequence[Path],
        fs: StorageFilesystem,
        locations: StorageLocations,
        migrations: StorageMigrations,
        store: SegmentStore,
        disk: DiskProbe,
        policies: StoragePolicies,
    ) -> None:
        self._roots, self._fs, self._locations, self._migrations = list(roots), fs, locations, migrations
        self._store, self._disk, self._policies = store, disk, policies

    async def __call__(self, raw_path: str) -> LocationCheck:
        target = _confine(self._fs, self._roots, raw_path)
        errors: list[str] = []
        warnings: list[str] = []
        if problem := record_dir_problem(str(target)):
            errors.append(problem)

        current = await self._locations.active()
        if current is not None and overlapping(target, current.path):
            errors.append(
                "este já é o local atual"
                if target == current.path
                else f"um local não pode ficar dentro do outro (atual: {current.path})"
            )
        latest = await self._migrations.latest()
        if latest is not None and latest.state is MigrationState.RUNNING:
            errors.append("há uma migração de gravações em andamento; aguarde terminar")
        elif latest is not None and latest.state is MigrationState.FAILED:
            errors.append("há uma migração interrompida: retome-a ou abandone-a antes de trocar de novo")

        check = await asyncio.to_thread(self._fs.check, target)
        will_create = False
        if check.exists and not check.is_dir:
            errors.append("o caminho é um arquivo, não uma pasta")
        elif not check.exists and not check.creatable:
            errors.append(
                "a pasta não existe e não pode ser criada aqui. Para liberar: "
                + self._fs.permission_hint(target.parent)
            )
        elif not check.exists:
            will_create = True
        elif not check.writable:
            errors.append(
                f"sem permissão de escrita ({check.write_error}). Para liberar: "
                + self._fs.permission_hint(target)
            )
        if check.system_volume:
            warnings.append(
                "fica no mesmo disco do sistema operacional: se encher, o servidor inteiro pode parar; "
                "prefira um disco só para as gravações"
            )
        if check.foreign_entries:
            warnings.append(
                f"a pasta já tem {check.foreign_entries} item(ns) que não são gravações do NVR "
                "(não serão tocados); prefira uma pasta exclusiva"
            )

        disk = await asyncio.to_thread(self._disk.usage, target)
        current_bytes = 0
        same_volume = False
        if current is not None:
            current_files = managed_files(await asyncio.to_thread(self._store.scan, current.path))
            current_bytes = sum(f.size_bytes for f in current_files)
            same_volume = await asyncio.to_thread(self._fs.same_volume, current.path, target)
        existing = managed_files(await asyncio.to_thread(self._store.scan, target)) if check.is_dir else []
        existing_bytes = sum(f.size_bytes for f in existing)
        if existing_bytes:
            warnings.append(
                f"a pasta já tem {gb_text(existing_bytes)} GB de gravações do NVR: "
                "elas voltam para a linha do tempo"
            )

        policy = await self._policies.get()
        migrate_problem = ""
        room = disk.free_bytes - policy.min_free_bytes
        if not same_volume and current_bytes > room:
            migrate_problem = (
                f"as gravações atuais ({gb_text(current_bytes)} GB) não cabem no novo disco "
                f"(livre: {gb_text(max(room, 0))} GB além da reserva)"
            )
        if policy.quota_bytes is not None:
            capacity = room + existing_bytes + (current_bytes if same_volume else 0)
            if policy.quota_bytes > capacity:
                warnings.append(
                    f"a cota atual ({gb_text(policy.quota_bytes)} GB) não cabe neste disco "
                    f"(máximo ≈ {gb_text(max(capacity, 0))} GB): a reserva de espaço livre passa a mandar"
                )
        return LocationCheck(
            target,
            errors,
            warnings,
            will_create,
            disk,
            None if current is None else current.path,
            current_bytes,
            existing_bytes,
            same_volume,
            migrate_problem,
        )


# ------------------------------------------------------------------ migrar


@dataclass(frozen=True)
class MigrationView:
    migration: StorageMigration
    from_path: Path | None
    to_path: Path | None

    def payload(self) -> dict[str, Any]:
        """Formato do evento SSE `storage.migration` (o mesmo de `MigrationOut` na API)."""
        m = self.migration
        return {
            "id": m.id,
            "state": m.state.value,
            "from_path": None if self.from_path is None else str(self.from_path),
            "to_path": None if self.to_path is None else str(self.to_path),
            "total_files": m.total_files,
            "total_bytes": m.total_bytes,
            "moved_files": m.moved_files,
            "moved_bytes": m.moved_bytes,
            "error": m.error,
            "started_at": m.started_at.isoformat(),
            "finished_at": None if m.finished_at is None else m.finished_at.isoformat(),
        }


async def view_of(locations: StorageLocations, migration: StorageMigration) -> MigrationView:
    src = await locations.get(migration.from_location_id)
    dst = await locations.get(migration.to_location_id)
    return MigrationView(migration, None if src is None else src.path, None if dst is None else dst.path)


class MigrationRunner:
    """Move as gravações do local antigo para o novo, em segundo plano e de forma retomável.

    Os mais recentes primeiro (ficam reproduzíveis logo). Arquivos mexidos há < 30 s podem estar abertos:
    esperam. Cada arquivo movido muda de local no índice na hora, sem duplicar na linha do tempo.
    """

    def __init__(
        self,
        locations: StorageLocations,
        migrations: StorageMigrations,
        store: SegmentStore,
        index: SegmentIndex,
        events: EventPublisher,
        audit: AuditLog,
        clock: Clock,
        poll_s: float = 5.0,
    ) -> None:
        self._locations, self._migrations, self._store, self._index = locations, migrations, store, index
        self._events, self._audit, self._clock, self._poll = events, audit, clock, poll_s
        self._task: asyncio.Task[StorageMigration | None] | None = None
        self.on_finished: Callable[[], None] | None = None  # ex.: reindexar e reaplicar a cota

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Retoma/inicia a migração pendente, se houver (idempotente)."""
        if not self.running:
            self._task = asyncio.create_task(self._guarded(), name="migração de gravações")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _guarded(self) -> StorageMigration | None:
        try:
            return await self.run()
        except asyncio.CancelledError:
            raise
        except Exception:  # a tarefa de fundo não pode morrer em silêncio
            log.exception("migração de gravações falhou")
            return None

    async def _publish(self, migration: StorageMigration) -> None:
        view = await view_of(self._locations, migration)
        self._events.publish(Event("storage.migration", view.payload()))

    async def _finish(self, m: StorageMigration, state: MigrationState, error: str = "") -> StorageMigration:
        m = replace(m, state=state, error=error, finished_at=self._clock.now())
        await self._migrations.save(m)
        await self._publish(m)
        await self._audit.record("system", f"storage.migration_{state.value}", m.to_location_id, {
            "arquivos": m.moved_files, "bytes": m.moved_bytes, "erro": error,
        })  # fmt: skip
        if self.on_finished is not None:
            self.on_finished()
        return m

    async def run(self) -> StorageMigration | None:
        m = await self._migrations.latest()
        if m is None or m.state is not MigrationState.RUNNING:
            return m
        src = await self._locations.get(m.from_location_id)
        dst = await self._locations.get(m.to_location_id)
        if src is None or dst is None:
            return await self._finish(m, MigrationState.FAILED, "local de gravação desconhecido")
        if not await asyncio.to_thread(src.path.is_dir):
            return await self._finish(m, MigrationState.FAILED, f"local antigo inacessível: {src.path}")
        log.info("migrando gravações de %s para %s", src.path, dst.path)
        last_progress = time.monotonic()
        while True:
            files = managed_files(await asyncio.to_thread(self._store.scan, src.path))
            if not files:
                break
            now = time.time()
            ready = sorted(
                (f for f in files if now - f.modified_at >= PROTECT_RECENT_S),
                key=lambda f: PurePosixPath(f.rel_path).name,
                reverse=True,
            )
            if not ready:
                await asyncio.sleep(self._poll)  # o segmento que estava aberto ainda pode estar fechando
                continue
            for f in ready:
                try:
                    await asyncio.to_thread(self._store.move, src.path, dst.path, f.rel_path)
                except (OSError, ValueError) as exc:
                    reason = getattr(exc, "strerror", None) or str(exc) or type(exc).__name__
                    log.warning("migração parou em %s: %s", f.rel_path, reason)
                    return await self._finish(m, MigrationState.FAILED, f"{f.rel_path}: {reason}")
                await self._index.relocate(src.id, dst.id, f.rel_path)
                m = replace(m, moved_files=m.moved_files + 1, moved_bytes=m.moved_bytes + f.size_bytes)
                if time.monotonic() - last_progress >= PROGRESS_EVERY_S:
                    last_progress = time.monotonic()
                    await self._migrations.save(m)
                    await self._publish(m)
        await asyncio.to_thread(self._store.remove_empty_folders, src.path)
        stale = await self._index.remove_location(src.id)  # linhas de arquivos que já não existiam
        if stale:
            log.info("migração: %d registro(s) de arquivos inexistentes saíram do índice", stale)
        log.info("migração concluída: %d arquivo(s), %s GB", m.moved_files, gb_text(m.moved_bytes))
        return await self._finish(m, MigrationState.DONE)


class GetStorageMigration:
    """A migração mais recente (em andamento, interrompida ou concluída), para a tela de armazenamento."""

    def __init__(self, locations: StorageLocations, migrations: StorageMigrations) -> None:
        self._locations, self._migrations = locations, migrations

    async def __call__(self) -> MigrationView | None:
        latest = await self._migrations.latest()
        return None if latest is None else await view_of(self._locations, latest)


# ------------------------------------------------------------------ trocar


@dataclass
class LocationChange:
    location: StorageLocation
    migration: MigrationView | None
    unindexed_segments: int = 0  # modo `fresh`: quantos segmentos saíram da linha do tempo
    warnings: list[str] = field(default_factory=list)


class ChangeStorageLocation:
    def __init__(
        self,
        validate: ValidateStorageLocation,
        fs: StorageFilesystem,
        locations: StorageLocations,
        migrations: StorageMigrations,
        store: SegmentStore,
        index: SegmentIndex,
        runner: MigrationRunner,
        record_dir: ActiveRecordDir,
        audit: AuditLog,
        clock: Clock,
    ) -> None:
        self._validate, self._fs, self._locations, self._migrations = validate, fs, locations, migrations
        self._store, self._index, self._runner, self._record_dir = store, index, runner, record_dir
        self._audit, self._clock = audit, clock
        self._lock = asyncio.Lock()
        self.on_change: Callable[[], None] | None = None  # reconciliar a mídia, reindexar, reaplicar a cota

    async def __call__(self, raw_path: str, mode: LocationMode, actor: str) -> LocationChange:
        async with self._lock:
            return await self._change(raw_path, mode, actor)

    async def _change(self, raw_path: str, mode: LocationMode, actor: str) -> LocationChange:
        check = await self._validate(raw_path)
        if check.errors:
            raise ValidationFailed("local de gravação recusado", {"path": "; ".join(check.errors)})
        if mode is LocationMode.MIGRATE and check.migrate_problem:
            raise ValidationFailed("não dá para mover as gravações", {"mode": check.migrate_problem})
        target = check.path
        if check.will_create:
            await asyncio.to_thread(self._fs.make_dir, target)
            created = await asyncio.to_thread(self._fs.check, target)
            if not created.writable:
                raise ValidationFailed(
                    "pasta criada, mas sem permissão de escrita",
                    {"path": f"{created.write_error}. Para liberar: {self._fs.permission_hint(target)}"},
                )

        previous = await self._locations.active()
        location = await self._locations.by_path(target)
        if location is None:
            location = StorageLocation(str(uuid.uuid4()), target, active=False)
            await self._locations.add(location)
        await self._locations.activate(location.id)
        self._record_dir.path = target
        log.warning("local de gravação: %s → %s (%s)", previous and previous.path, target, mode.value)

        result = LocationChange(replace(location, active=True), None, warnings=check.warnings)
        if previous is not None and mode is LocationMode.FRESH:
            result.unindexed_segments = await self._index.remove_location(previous.id)
        elif previous is not None:
            files = managed_files(await asyncio.to_thread(self._store.scan, previous.path))
            migration = StorageMigration(
                str(uuid.uuid4()), previous.id, location.id, MigrationState.RUNNING,
                len(files), sum(f.size_bytes for f in files), 0, 0, "", self._clock.now(), None,
            )  # fmt: skip
            await self._migrations.add(migration)
            result.migration = MigrationView(migration, previous.path, target)
        await self._audit.record(actor, "storage.location_changed", location.id, {
            "de": None if previous is None else str(previous.path), "para": str(target), "modo": mode.value,
        })  # fmt: skip
        if self.on_change is not None:
            self.on_change()
        if result.migration is not None:
            self._runner.start()
        return result


class ResolveStorageMigration:
    """Migração interrompida: retomar (tentar de novo) ou abandonar (o resto fica no local antigo)."""

    def __init__(
        self,
        locations: StorageLocations,
        migrations: StorageMigrations,
        index: SegmentIndex,
        runner: MigrationRunner,
        audit: AuditLog,
        clock: Clock,
    ) -> None:
        self._locations, self._migrations, self._index = locations, migrations, index
        self._runner, self._audit, self._clock = runner, audit, clock

    async def _failed(self) -> StorageMigration:
        latest = await self._migrations.latest()
        if latest is None:
            raise NotFound("nenhuma migração registrada")
        if latest.state is MigrationState.RUNNING:
            raise Conflict("a migração está em andamento")
        if latest.state is not MigrationState.FAILED:
            raise Conflict("a última migração não está interrompida")
        return latest

    async def resume(self, actor: str) -> MigrationView:
        m = replace(await self._failed(), state=MigrationState.RUNNING, error="", finished_at=None)
        await self._migrations.save(m)
        await self._audit.record(actor, "storage.migration_resumed", m.to_location_id, {})
        self._runner.start()
        return await view_of(self._locations, m)

    async def abandon(self, actor: str) -> MigrationView:
        m = await self._failed()
        removed = await self._index.remove_location(m.from_location_id)
        m = replace(m, state=MigrationState.ABANDONED, finished_at=self._clock.now())
        await self._migrations.save(m)
        await self._audit.record(actor, "storage.migration_abandoned", m.to_location_id, {
            "segmentos_fora_do_indice": removed,
        })  # fmt: skip
        return await view_of(self._locations, m)
