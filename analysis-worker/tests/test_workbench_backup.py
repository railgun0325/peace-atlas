import asyncio
from contextlib import closing
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import pytest

from app.workbench.backup import BackupError, BackupService
from app.workbench.server import create_app
from app.workbench.store import Store


def material(index=1):
    return {"title": f"公開資料 {index}", "url": f"https://example.org/{index}",
            "content": f"憲法九条についての公開討論 {index}", "type": "news",
            "published_at": "2026-10-08T09:00:00+09:00"}


def test_online_snapshot_contains_committed_wal_and_report_and_restores(tmp_path):
    store = Store(tmp_path / "source.sqlite3")
    # Keep a reader open so committed WAL pages cannot be checkpointed away.
    keeper = store._connect()
    try:
        keeper.execute("BEGIN")
        keeper.execute("SELECT COUNT(*) FROM wb_items").fetchone()
        store.import_items([material()])
        store.save_draft("collection", {"schema": 1, "values": {"keyword": "平和"}})
        report = store.save_report({"title": "資料の記録", "start_date": "2026-10-08", "end_date": "2026-10-08",
                                   "mode": "evidence", "markdown": "# 記録\n出典 https://example.org/1", "snapshot": [material()], "source_count": 1})
        with TestClient(create_app(store, run_worker=False)) as client:
            response = client.get("/api/v2/backup")
            assert response.status_code == 200
            assert response.content.startswith(b"SQLite format 3\0")
            assert response.headers["cache-control"] == "no-store"
            assert 'filename="peace-atlas-backup-' in response.headers["content-disposition"]
        restored_path = tmp_path / "restored.sqlite3"
        restored_path.write_bytes(response.content)
        with closing(sqlite3.connect(restored_path)) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        restored = Store(restored_path)
        assert restored.list_items()["total"] == 1
        assert restored.get_report(report["id"])["snapshot"] == [material()]
        assert restored.get_draft("collection")["data"]["values"]["keyword"] == "平和"
        store.import_items([material(2)])
        assert restored.list_items()["total"] == 1
        assert store.list_items()["total"] == 2
    finally:
        keeper.close()


def test_backup_uses_store_not_environment_and_requires_desktop_session(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_DATABASE_URL", "postgresql://unused.example/database")
    monkeypatch.setenv("WORKBENCH_SESSION_TOKEN", "backup-test-token")
    with TestClient(create_app(Store(tmp_path / "actual.sqlite3"), run_worker=False)) as client:
        assert client.get("/api/v2/backup").status_code == 401
        headers = {"x-workbench-token": "backup-test-token"}
        capabilities = client.get("/api/v2/capabilities", headers=headers).json()
        assert capabilities["backup"]["available"] is True
        assert capabilities["storage"]["engine"] == "sqlite"
        assert client.get("/api/v2/health", headers=headers).json()["storage"] == "sqlite"
        assert client.get("/api/v2/backup", headers=headers).status_code == 200


def test_snapshot_does_not_read_neighboring_credentials_or_profiles(tmp_path):
    store = Store(tmp_path / "workbench.sqlite3")
    (tmp_path / "connection.secure").write_bytes(b"secret-api-key-marker")
    (tmp_path / "social").mkdir()
    (tmp_path / "social" / "state.json").write_text("secret-cookie-marker")
    with TestClient(create_app(store, run_worker=False)) as client:
        response = client.get("/api/v2/backup")
        assert response.status_code == 200
        assert b"secret-api-key-marker" not in response.content
        assert b"secret-cookie-marker" not in response.content


def test_backup_excludes_uncommitted_transaction_and_cleans_on_disconnect(tmp_path):
    store = Store(tmp_path / "active.sqlite3")
    store.import_items([material()])
    with closing(store._connect()) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE wb_items SET title = 'uncommitted'")
        response = BackupService().response(store)
        backup_path = Path(response.path)
        with closing(sqlite3.connect(backup_path)) as copy:
            assert copy.execute("SELECT title FROM wb_items").fetchone()[0] == "公開資料 1"
        async def fail_send(message):
            raise ConnectionError("client disconnected")
        async def receive():
            return {"type": "http.disconnect"}
        with pytest.raises(ConnectionError):
            asyncio.run(response({"type": "http", "method": "GET", "headers": []}, receive, fail_send))
        assert not backup_path.parent.exists()
        writer.rollback()


def test_backup_serialization_timeout_cleanup_and_postgres_error(tmp_path, monkeypatch):
    store = Store(tmp_path / "data.sqlite3")
    service = BackupService()
    service._lock.acquire()
    try:
        with pytest.raises(BackupError, match="另一份备份"):
            service.response(store)
    finally:
        service._lock.release()
    monkeypatch.setattr(store, "_postgres", True)
    with pytest.raises(BackupError) as error:
        service.response(store)
    assert error.value.code == "backup_unavailable"
    monkeypatch.setattr(store, "_postgres", False)
    service.timeout_seconds = -1
    with pytest.raises(BackupError) as error:
        service.response(store)
    assert error.value.code == "backup_timeout"
    service.timeout_seconds = 60
    response = service.response(store)
    response.directory.cleanup()


def test_memory_database_backup(tmp_path):
    store = Store(":memory:")
    try:
        store.import_items([material()])
        response = BackupService().response(store)
        try:
            with closing(sqlite3.connect(response.path)) as connection:
                assert connection.execute("SELECT COUNT(*) FROM wb_items").fetchone()[0] == 1
        finally:
            response.directory.cleanup()
    finally:
        store.close()


def test_overlapping_downloads_keep_independent_snapshots_and_cleanup(tmp_path):
    store = Store(tmp_path / "concurrent.sqlite3")
    service = BackupService()
    store.import_items([material(1)])
    first = service.response(store)
    first_path = Path(first.path)
    store.import_items([material(2)])
    second = service.response(store)
    second_path = Path(second.path)
    assert first_path.parent != second_path.parent
    downloaded = [bytearray(), bytearray()]

    async def scenario():
        first_started, release_first = asyncio.Event(), asyncio.Event()

        async def receive():
            return {"type": "http.disconnect"}

        async def send_first(message):
            if message["type"] == "http.response.start":
                first_started.set()
                await release_first.wait()
            downloaded[0].extend(message.get("body", b""))

        async def send_second(message):
            downloaded[1].extend(message.get("body", b""))

        scope = {"type": "http", "method": "GET", "headers": []}
        pending = asyncio.create_task(first(scope, receive, send_first))
        try:
            await asyncio.wait_for(first_started.wait(), 5)
            await second(scope, receive, send_second)
            assert not second_path.parent.exists()
            assert first_path.exists()
        finally:
            release_first.set()
            await pending

    try:
        asyncio.run(scenario())
        assert not first_path.parent.exists() and not second_path.parent.exists()
        for index, expected_count in enumerate((1, 2)):
            restored_path = tmp_path / f"copy-{index}.sqlite3"
            restored_path.write_bytes(downloaded[index])
            with closing(sqlite3.connect(restored_path)) as connection:
                assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert connection.execute("SELECT COUNT(*) FROM wb_items").fetchone()[0] == expected_count
    finally:
        first.directory.cleanup()
        second.directory.cleanup()


def test_cancelled_backup_download_cleans_scratch_and_allows_next_backup(tmp_path):
    store = Store(tmp_path / "cancelled.sqlite3")
    service = BackupService()
    response = service.response(store)
    path = Path(response.path)

    async def scenario():
        started = asyncio.Event()

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            started.set()
            await asyncio.Event().wait()

        pending = asyncio.create_task(response({"type": "http", "method": "GET", "headers": []}, receive, send))
        try:
            await asyncio.wait_for(started.wait(), 5)
        finally:
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending

    asyncio.run(scenario())
    assert not path.parent.exists()
    following = service.response(store)
    following.directory.cleanup()
