"""Consistent online SQLite backups; credentials and browser profiles stay outside."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Lock
import time

from fastapi.responses import FileResponse


class BackupError(Exception):
    def __init__(self, message: str, code: str, status: int = 503):
        super().__init__(message)
        self.code, self.status = code, status


class BackupResponse(FileResponse):
    """Clean private scratch files even when sending fails or the client leaves."""
    def __init__(self, directory: TemporaryDirectory, filename: str):
        self.directory = directory
        super().__init__(Path(directory.name) / "workbench.sqlite3", filename=filename,
                         media_type="application/vnd.sqlite3", headers={"Cache-Control": "no-store"})

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.directory.cleanup()


class BackupService:
    def __init__(self, timeout_seconds: float = 60):
        self._lock = Lock()
        self.timeout_seconds = timeout_seconds

    def response(self, store) -> BackupResponse:
        if store._postgres:
            raise BackupError("当前使用 PostgreSQL，请使用数据库的备份工具。", "backup_unavailable", 409)
        if not self._lock.acquire(blocking=False):
            raise BackupError("另一份备份正在生成，请稍后重试。", "backup_busy", 409)
        directory = None
        try:
            directory = TemporaryDirectory(prefix="peace-atlas-backup-")
            target = Path(directory.name) / "workbench.sqlite3"
            deadline = time.monotonic() + self.timeout_seconds

            def progress(status, remaining, total):
                if time.monotonic() > deadline:
                    raise BackupError("备份生成超时，请稍后重试；原始资料保持不变。", "backup_timeout")

            # Copy through SQLite's online backup API, including committed WAL
            # pages, instead of copying a potentially stale main database file.
            with closing(store._connect()) as source, closing(sqlite3.connect(target)) as destination:
                source.backup(destination, pages=256, progress=progress, sleep=0.05)
                destination.execute("PRAGMA journal_mode = DELETE")
                if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise BackupError("备份完整性检查失败，未生成下载文件。", "backup_integrity")
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            return BackupResponse(directory, f"peace-atlas-backup-{stamp}.sqlite3")
        except BaseException:
            if directory is not None:
                directory.cleanup()
            raise
        finally:
            self._lock.release()
