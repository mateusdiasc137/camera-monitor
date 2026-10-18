"""Troca do local de gravação pela UI (F1-11b): navegação confinada, validação, `fresh` e `migrate`."""

import asyncio
import errno
import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from nvr.bootstrap.cli import build_app
from nvr.bootstrap.container import Container, build_container
from nvr.bootstrap.settings import Settings
from nvr.http.routers.storage import MigrationOut
from nvr.identity.domain import Role
from nvr.persistence.database import migrate
from nvr.persistence.tables import audit_log
from nvr.recordings.adapters import fs_store
from nvr.recordings.adapters.fs_store import FileSegmentStore
from nvr.recordings.domain import GIB, DiskUsage, MigrationState
from nvr.recordings.ports import FolderCheck
from nvr.shared.events import Event
from tests.integration.test_storage_enforcement import write

PASSWORD = "senha-forte-123"
ORIGIN = "http://testserver"
T0 = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
def env(tmp_path: Path) -> Iterator[dict[str, Any]]:
    data, roots = tmp_path / "data", tmp_path / "discos"
    hd1 = roots / "hd1" / "nvr"
    (roots / "hd2").mkdir(parents=True)
    migrate(data)
    container = build_container(
        Settings(data_dir=data, cookie_secure=False, recordings_dir=hd1, storage_roots=[roots])
    )
    container.migration_runner._poll = 0.05  # type: ignore[attr-defined]
    asyncio.run(container.create_user("admin", PASSWORD, Role.ADMIN))
    asyncio.run(container.create_user("visitante", PASSWORD, Role.VIEWER))
    with TestClient(build_app(container), base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
        client.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
        camera = client.post("/api/v1/cameras", json={
            "name": "Quintal", "host": "192.0.2.10", "main_path": "/a", "username": "u", "password": "p",
        }).json()  # fmt: skip
        yield {
            "client": client,
            "container": container,
            "camera": camera,
            "roots": roots.resolve(),
            "a": hd1.resolve(),
            "b": (roots / "hd2" / "gravacoes").resolve(),
            "outside": tmp_path.resolve() / "data",
        }


def record(container: Container, root: Path, camera_id: str, count: int, size_mb: int = 1) -> list[Path]:
    paths = [
        write(root / f"cam-{camera_id}-main", T0 + timedelta(minutes=5 * i), size_mb) for i in range(count)
    ]
    asyncio.run(container.index_segments())
    return paths


def indexed(container: Container, camera_id: str) -> list[tuple[str, str]]:
    async def query() -> list[tuple[str, str]]:
        found = await container.segment_index.in_range(
            camera_id, T0 - timedelta(days=1), T0 + timedelta(days=1)
        )
        return [(s.location_id, s.rel_path) for s in found]

    return asyncio.run(query())


def wait_migration(client: TestClient, timeout: float = 15.0) -> dict[str, Any]:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        migration = client.get("/api/v1/storage").json()["migration"]
        if migration and migration["state"] != "running":
            return migration  # type: ignore[no-any-return]
        time.sleep(0.05)
    raise AssertionError("a migração não terminou")


def actions(container: Container) -> list[str]:
    async def query() -> list[str]:
        async with container.engine.connect() as conn:
            return [r.action for r in await conn.execute(select(audit_log.c.action))]

    return asyncio.run(query())


# ---------------------------------------------------------------- navegação


def test_browse_stays_inside_roots(env: dict[str, Any]) -> None:
    client, roots = env["client"], env["roots"]
    top = client.get("/api/v1/storage/browse").json()
    assert top["path"] is None
    assert [f["path"] for f in top["folders"]] == [str(roots)]

    inside = client.get("/api/v1/storage/browse", params={"path": str(roots)}).json()
    assert [f["name"] for f in inside["folders"]] == ["hd1", "hd2"]
    assert inside["parent"] is None  # subir a partir de uma raiz volta à lista das raízes
    hd1 = client.get("/api/v1/storage/browse", params={"path": str(roots / "hd1")}).json()
    assert hd1["parent"] == str(roots)

    for bad in (str(roots / ".."), str(roots / "hd1" / ".." / ".."), str(env["outside"]), "discos/hd1"):
        response = client.get("/api/v1/storage/browse", params={"path": bad})
        assert response.status_code == 422, bad
        assert "path" in response.json()["fields"]
    assert client.get("/api/v1/storage/browse", params={"path": str(roots / "nada")}).status_code == 404


def test_symlink_to_outside_is_hidden_and_refused(env: dict[str, Any]) -> None:
    client, roots = env["client"], env["roots"]
    link = roots / "atalho"
    try:
        os.symlink(env["outside"], link, target_is_directory=True)
    except OSError:
        pytest.skip("este sistema não permite criar links simbólicos")
    names = [
        f["name"] for f in client.get("/api/v1/storage/browse", params={"path": str(roots)}).json()["folders"]
    ]
    assert "atalho" not in names
    response = client.post("/api/v1/storage/location/validate", json={"path": str(link)})
    assert response.status_code == 422


def test_location_endpoints_are_admin_only(env: dict[str, Any]) -> None:
    client = env["client"]
    client.post("/api/v1/auth/logout")
    client.post("/api/v1/auth/login", json={"username": "visitante", "password": PASSWORD})
    assert client.get("/api/v1/storage/browse").status_code == 403
    assert client.post("/api/v1/storage/location/validate", json={"path": str(env["b"])}).status_code == 403
    body = {"path": str(env["b"]), "mode": "fresh"}
    assert client.put("/api/v1/storage/location", json=body).status_code == 403
    assert client.post("/api/v1/storage/migration/abandon").status_code == 403


# ---------------------------------------------------------------- validação


def validate(client: TestClient, path: Path | str) -> dict[str, Any]:
    response = client.post("/api/v1/storage/location/validate", json={"path": str(path)})
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def test_validation_explains_each_problem(env: dict[str, Any]) -> None:
    client, roots, a, b = env["client"], env["roots"], env["a"], env["b"]
    assert "já é o local atual" in validate(client, a)["errors"][0]
    assert "dentro do outro" in validate(client, a / "sub")["errors"][0]
    assert "dentro do outro" in validate(client, roots / "hd1")["errors"][0]
    assert "%" in validate(client, roots / "hd2" / "100%")["errors"][0]
    (roots / "hd2" / "arquivo.txt").write_text("x", encoding="utf-8")
    assert "arquivo, não uma pasta" in validate(client, roots / "hd2" / "arquivo.txt")["errors"][0]

    fresh = validate(client, b)
    assert fresh["ok"] is True
    assert fresh["will_create"] is True
    assert fresh["current_path"] == str(a)
    assert fresh["same_volume"] is True
    assert fresh["migrate_problem"] == ""
    assert fresh["disk_free_bytes"] > 0
    foreign = validate(client, roots / "hd2")
    assert foreign["ok"] is True
    assert any("não são gravações do NVR" in w for w in foreign["warnings"])


def test_unwritable_folder_suggests_the_command(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    client, b = env["client"], env["b"]
    b.mkdir(parents=True)
    fs = env["container"].validate_storage_location._fs
    denied = FolderCheck(b, True, True, False, False, "Acesso negado", 0, False)
    monkeypatch.setattr(fs, "check", lambda path: denied)
    error = validate(client, b)["errors"][0]
    assert "Acesso negado" in error
    assert ("icacls" in error) if os.name == "nt" else ("chown" in error)
    response = client.put("/api/v1/storage/location", json={"path": str(b), "mode": "fresh"})
    assert response.status_code == 422
    assert "Acesso negado" in response.json()["fields"]["path"]


def test_migrate_refused_when_recordings_do_not_fit(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, container, cam, a, b = env["client"], env["container"], env["camera"], env["a"], env["b"]
    record(container, a, cam["id"], 3, size_mb=2)
    validator = container.validate_storage_location

    class TinyDisk:
        def usage(self, path: Path) -> DiskUsage:
            return DiskUsage(100 * GIB, 2 * GIB + 1024 * 1024)  # 1 MB além da reserva de 2 GB

    monkeypatch.setattr(validator, "_disk", TinyDisk())
    monkeypatch.setattr(validator._fs, "same_volume", lambda x, y: False)
    check = validate(client, b)
    assert check["ok"] is True
    assert "não cabem no novo disco" in check["migrate_problem"]
    response = client.put("/api/v1/storage/location", json={"path": str(b), "mode": "migrate"})
    assert response.status_code == 422
    assert "não cabem" in response.json()["fields"]["mode"]
    assert client.put("/api/v1/storage/location", json={"path": str(b), "mode": "fresh"}).status_code == 200


# ---------------------------------------------------------------- troca


def test_fresh_start_leaves_old_files_and_comes_back(env: dict[str, Any]) -> None:
    client, container, cam, a, b = env["client"], env["container"], env["camera"], env["a"], env["b"]
    old = record(container, a, cam["id"], 3)
    assert len(indexed(container, cam["id"])) == 3

    response = client.put("/api/v1/storage/location", json={"path": str(b), "mode": "fresh"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["location_path"] == str(b)
    assert body["migration"] is None
    assert body["unindexed_segments"] == 3
    assert b.is_dir()
    assert all(p.exists() for p in old)  # nada é apagado
    assert indexed(container, cam["id"]) == []
    assert container.record_dir.record_path().startswith(f"{b.as_posix()}/%path/")
    assert client.get("/api/v1/storage").json()["location_path"] == str(b)
    assert "storage.location_changed" in actions(container)

    back = validate(client, a)
    assert any("voltam para a linha do tempo" in w for w in back["warnings"])
    assert client.put("/api/v1/storage/location", json={"path": str(a), "mode": "fresh"}).status_code == 200
    asyncio.run(container.index_segments())
    assert len(indexed(container, cam["id"])) == 3


def test_migrate_moves_files_and_index_without_duplicates(env: dict[str, Any]) -> None:
    client, container, cam, a, b = env["client"], env["container"], env["camera"], env["a"], env["b"]
    record(container, a, cam["id"], 4)
    (a / "notas.txt").write_text("não é do NVR", encoding="utf-8")

    response = client.put("/api/v1/storage/location", json={"path": str(b), "mode": "migrate"})
    assert response.status_code == 200, response.text
    started = response.json()["migration"]
    assert started["state"] == "running"
    assert (started["total_files"], started["from_path"], started["to_path"]) == (4, str(a), str(b))

    done = wait_migration(client)
    assert done["state"] == "done", done
    assert (done["moved_files"], done["moved_bytes"]) == (4, started["total_bytes"])
    assert len(list((b / f"cam-{cam['id']}-main").glob("*.mp4"))) == 4
    assert not (a / f"cam-{cam['id']}-main").exists()  # pasta vazia removida
    assert (a / "notas.txt").exists()  # o que não é do NVR fica

    location_b = asyncio.run(
        container.segment_index.known(asyncio.run(container.ensure_storage_location()).id)
    )
    assert len(location_b) == 4
    asyncio.run(container.index_segments())  # a varredura do novo local não duplica
    rows = indexed(container, cam["id"])
    assert len(rows) == 4
    assert len({loc for loc, _ in rows}) == 1
    assert {"storage.location_changed", "storage.migration_done"} <= set(actions(container))


def test_migration_resumes_after_crash_and_waits_for_open_file(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, container, cam, a, b = env["client"], env["container"], env["camera"], env["a"], env["b"]
    paths = record(container, a, cam["id"], 3)
    runner = container.migration_runner
    monkeypatch.setattr(runner, "start", lambda: None)  # "queda" logo após a troca: nada migrou
    assert client.put("/api/v1/storage/location", json={"path": str(b), "mode": "migrate"}).status_code == 200
    # Cópia completa de uma tentativa anterior que caiu antes de apagar a origem.
    copied = b / paths[0].parent.name / paths[0].name
    copied.parent.mkdir(parents=True)
    copied.write_bytes(paths[0].read_bytes())
    os.utime(paths[2], None)  # o mais recente parece estar em gravação

    events: list[Event] = []
    monkeypatch.setattr(runner, "_events", type("Bus", (), {"publish": lambda self, e: events.append(e)})())

    async def resume() -> MigrationState:
        task = asyncio.create_task(runner.run())
        await asyncio.sleep(0.5)
        assert paths[2].exists()  # espera o arquivo "aberto"
        assert not paths[0].exists()
        assert not paths[1].exists()
        past = time.time() - 3600
        os.utime(paths[2], (past, past))  # "fechou"
        result = await asyncio.wait_for(task, 10)
        assert result is not None
        return result.state

    assert asyncio.run(resume()) is MigrationState.DONE
    assert not any(p.exists() for p in paths)
    assert len(list(copied.parent.glob("*.mp4"))) == 3
    assert len(indexed(container, cam["id"])) == 3
    final = MigrationOut.model_validate(events[-1].data)  # evento SSE no formato da API
    assert (events[-1].type, final.state, final.moved_files) == ("storage.migration", "done", 3)


def test_failed_migration_blocks_new_changes_until_resumed_or_abandoned(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, container, cam, a, b = env["client"], env["container"], env["camera"], env["a"], env["b"]
    record(container, a, cam["id"], 3)
    store = container.migration_runner._store  # type: ignore[attr-defined]
    real_move = store.move
    broken = {"on": True}

    def flaky_move(src: Path, dst: Path, rel: str) -> None:
        if broken["on"]:
            raise PermissionError(errno.EACCES, "Permissão negada")
        real_move(src, dst, rel)

    monkeypatch.setattr(store, "move", flaky_move)
    assert client.put("/api/v1/storage/location", json={"path": str(b), "mode": "migrate"}).status_code == 200
    failed = wait_migration(client)
    assert failed["state"] == "failed"
    assert "Permissão negada" in failed["error"]
    assert "interrompida" in validate(client, env["roots"] / "hd2" / "outra")["errors"][0]
    assert client.post("/api/v1/storage/migration/abandon").status_code == 200  # abandonar...
    assert indexed(container, cam["id"]) == []  # ...tira do índice o que ficou para trás
    assert len(list((a / f"cam-{cam['id']}-main").glob("*.mp4"))) == 3  # sem apagar nada
    assert client.post("/api/v1/storage/migration/resume").status_code == 409

    # Nova troca (de volta para A) com falha, agora retomada com sucesso.
    assert client.put("/api/v1/storage/location", json={"path": str(a), "mode": "fresh"}).status_code == 200
    asyncio.run(container.index_segments())
    assert client.put("/api/v1/storage/location", json={"path": str(b), "mode": "migrate"}).status_code == 200
    assert wait_migration(client)["state"] == "failed"
    broken["on"] = False
    resumed = client.post("/api/v1/storage/migration/resume")
    assert resumed.status_code == 200
    assert wait_migration(client)["state"] == "done"
    assert len(indexed(container, cam["id"])) == 3
    assert {"storage.migration_failed", "storage.migration_abandoned", "storage.migration_resumed"} <= set(
        actions(container)
    )


# ---------------------------------------------------------------- mover arquivos


def test_move_across_volumes_copies_before_deleting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src_root, dst_root = tmp_path / "a", tmp_path / "b"
    src = write(src_root / "cam-x-main", T0, 1)
    content = src.read_bytes()

    def cross_device(a: Any, b: Any) -> None:
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(fs_store.os, "rename", cross_device)
    rel = f"cam-x-main/{src.name}"
    FileSegmentStore().move(src_root, dst_root, rel)
    assert not src.exists()
    assert (dst_root / rel).read_bytes() == content
    assert list((dst_root / "cam-x-main").iterdir()) == [dst_root / rel]  # sem .part sobrando


def test_move_never_overwrites_a_different_file(tmp_path: Path) -> None:
    src = write(tmp_path / "a" / "cam-x-main", T0, 1)
    other = tmp_path / "b" / "cam-x-main" / src.name
    other.parent.mkdir(parents=True)
    other.write_bytes(b"outro conteudo")
    with pytest.raises(FileExistsError):
        FileSegmentStore().move(tmp_path / "a", tmp_path / "b", f"cam-x-main/{src.name}")
    assert src.exists()
    assert other.read_bytes() == b"outro conteudo"
    with pytest.raises(ValueError, match="inválido"):
        FileSegmentStore().move(tmp_path / "a", tmp_path / "b", f"../cam-x-main/{src.name}")
