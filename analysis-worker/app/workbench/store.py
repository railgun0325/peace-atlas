"""Durable workbench repository, isolated from all legacy tables and files.

Connections are deliberately short lived so request handlers and queue workers
can share a Store safely. SQLite writers use BEGIN IMMEDIATE; PostgreSQL queue
claims use row locks. No dotenv files are read and no external calls are made.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager, nullcontext
from datetime import date, datetime, timedelta, timezone
import json
import hashlib
import math
import os
import re
from pathlib import Path
import sqlite3
from typing import Any, Iterator, Mapping
from urllib.parse import urlsplit
from uuid import uuid4

from ..dedupe import content_fingerprint, normalize_url
from .research_repository import ResearchRepository, _public_json
from .research_batches import ResearchBatches
from .job_retries import JobRetries
from .registry_graph import RegistryRepository
from .activity_events import ActivityRepository
from .cleanup import CleanupRepository


UTC = timezone.utc
TOKYO = timezone(timedelta(hours=9))
TERMINAL = frozenset({"succeeded", "partial", "failed", "cancelled", "interrupted"})
JOB_STATUSES = TERMINAL | {"queued", "running"}
ACCOUNT_STATUSES = {"candidate", "verified", "excluded"}
_ITEM_JOIN = """SELECT i.*, a.status AS linked_account_status,
    a.evidence_url AS linked_evidence_url, a.url AS linked_account_url FROM wb_items i
    LEFT JOIN wb_accounts a ON a.id = i.account_id"""
_QUEUE_ENQUEUE_LOCK = 0x5045414345515545


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _iso_date(value: Any, field: str) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError(f"{field} 必须为 YYYY-MM-DD")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field} 必须为有效日期") from exc


def _published(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time(), TOKYO)
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("published_at 必须为 ISO 日期或时间") from exc
    else:
        raise ValueError("published_at 必须为 ISO 日期或时间")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TOKYO)
    return parsed.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _date_bounds(start: Any, end: Any) -> tuple[str | None, str | None]:
    start, end = _iso_date(start, "start_date"), _iso_date(end, "end_date")
    if start and end and start > end:
        raise ValueError("start_date 不得晚于 end_date")
    lower = _published(start) if start else None
    upper = _published(date.fromisoformat(end) + timedelta(days=1)) if end else None
    return lower, upper


def _url(value: Any, field: str = "url") -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须为 HTTP(S) URL")
    value = value.strip()
    parsed = urlsplit(value)
    if (parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or any(ch.isspace() for ch in value)):
        raise ValueError(f"{field} 必须为有效 HTTP(S) URL")
    return normalize_url(value)


def _strings(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field} 必须为字符串数组")
    return list(dict.fromkeys(item.strip() for item in value if item.strip()))


def _social_url(value: str | None) -> str | None:
    if not value:
        return value
    parsed = urlsplit(value)
    if parsed.hostname in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
        match = re.fullmatch(r"/(?:[A-Za-z0-9_]+|i/web)/status/(\d+)(?:/(?:photo|video)/\d+)?/?", parsed.path)
        if match:
            return f"https://x.com/i/status/{match[1]}"
    if parsed.hostname in {"instagram.com", "www.instagram.com"}:
        match = re.fullmatch(r"/(?:[A-Za-z0-9_.]+/)?(?:p|reel)/([A-Za-z0-9_-]+)/?", parsed.path)
        if match:
            return f"https://www.instagram.com/p/{match[1]}"
    return value


def _pagination(page: int, page_size: int) -> tuple[int, int]:
    if isinstance(page, bool) or isinstance(page_size, bool):
        raise ValueError("分页参数必须为整数")
    if not isinstance(page, int) or not isinstance(page_size, int):
        raise ValueError("分页参数必须为整数")
    if page < 1 or not 1 <= page_size <= 200:
        raise ValueError("page 必须大于零，page_size 必须在 1 至 200 之间")
    return page, page_size


def _like(value: str) -> str:
    return "%" + value.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"


class Store(ResearchRepository, ResearchBatches, JobRetries, RegistryRepository, ActivityRepository, CleanupRepository):
    def __init__(self, path: str | Path | None = None, database_url: str | None = None):
        self.database_url = database_url or (os.environ.get("WORKBENCH_DATABASE_URL") if path is None else None)
        self._postgres = bool(self.database_url)
        self._keeper: sqlite3.Connection | None = None
        self._memory_uri = False
        if self._postgres:
            if not self.database_url.startswith(("postgres://", "postgresql://")):
                raise ValueError("WORKBENCH_DATABASE_URL 仅支持 PostgreSQL")
            self.path = None
        else:
            selected = str(path if path is not None else os.environ.get(
                "WORKBENCH_DB_PATH", str(Path(__file__).resolve().parents[2] / "data" / "workbench.sqlite3")))
            if selected == ":memory:":
                self.path = f"file:workbench-{uuid4()}?mode=memory&cache=shared"
                self._memory_uri = True
                self._keeper = self._connect()
            else:
                self.path = str(Path(selected).expanduser().resolve())
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def close(self) -> None:
        if self._keeper is not None:
            self._keeper.close()
            self._keeper = None

    def _connect(self):
        if self._postgres:
            import psycopg
            from psycopg.rows import dict_row
            return psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=10)
        connection = sqlite3.connect(self.path, timeout=15, uri=self._memory_uri)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 15000")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def _transaction(self, write: bool = False) -> Iterator[Any]:
        connection = self._connect()
        try:
            if not self._postgres:
                connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _execute(self, connection, sql: str, params: tuple | list = ()):
        if self._postgres:
            sql = sql.replace("?", "%s")
        return connection.execute(sql, params)

    def _initialize(self) -> None:
        self._migrate_job_kinds()
        statements = [
            """CREATE TABLE IF NOT EXISTS wb_accounts (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, handle TEXT NOT NULL,
                platform TEXT NOT NULL, url TEXT NOT NULL, organization TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('candidate','verified','excluded')),
                evidence_url TEXT NOT NULL, tags TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS wb_items (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT UNIQUE,
                content TEXT NOT NULL, content_hash TEXT NOT NULL UNIQUE,
                source_name TEXT NOT NULL, type TEXT NOT NULL CHECK(type IN ('news','social')),
                published_at TEXT, publication_date_unknown INTEGER NOT NULL,
                topics TEXT NOT NULL, entities TEXT NOT NULL, locations TEXT NOT NULL,
                account_id TEXT, metadata TEXT NOT NULL, created_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS wb_jobs (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('collect','report','login','research')),
                status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','partial','failed','cancelled','interrupted')),
                params TEXT NOT NULL, result TEXT, error TEXT, progress INTEGER NOT NULL CHECK(progress BETWEEN 0 AND 100),
                created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
                cancel_requested INTEGER NOT NULL DEFAULT 0)""",
            """CREATE TABLE IF NOT EXISTS wb_job_events (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES wb_jobs(id) ON DELETE CASCADE,
                message TEXT NOT NULL, level TEXT NOT NULL, created_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS wb_reports (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, start_date TEXT NOT NULL,
                end_date TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('evidence','ai')),
                markdown TEXT NOT NULL, snapshot TEXT NOT NULL,
                source_count INTEGER NOT NULL CHECK(source_count >= 0), job_id TEXT,
                created_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS wb_account_keys (
                account_key TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES wb_accounts(id))""",
            """CREATE TABLE IF NOT EXISTS wb_item_revisions (
                id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES wb_items(id),
                prior_capture TEXT NOT NULL, revised_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS wb_relations (
                id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES wb_accounts(id),
                target_id TEXT NOT NULL REFERENCES wb_accounts(id), relation TEXT NOT NULL,
                evidence_url TEXT NOT NULL, observed_at TEXT, last_observed_at TEXT, created_at TEXT NOT NULL,
                UNIQUE(source_id,target_id,relation))""",
            "CREATE INDEX IF NOT EXISTS wb_relations_target ON wb_relations(target_id)",
            "CREATE INDEX IF NOT EXISTS wb_items_published ON wb_items(published_at, created_at)",
            "CREATE INDEX IF NOT EXISTS wb_items_kind ON wb_items(type, published_at)",
            "CREATE INDEX IF NOT EXISTS wb_items_account ON wb_items(account_id)",
            "CREATE INDEX IF NOT EXISTS wb_jobs_queue ON wb_jobs(status, created_at, id)",
            "CREATE INDEX IF NOT EXISTS wb_events_job ON wb_job_events(job_id, created_at, id)",
            "CREATE INDEX IF NOT EXISTS wb_reports_created ON wb_reports(created_at, id)",
        ]
        with self._transaction(write=True) as connection:
            for statement in statements:
                self._execute(connection, statement)
            if self._postgres:
                self._execute(connection, "ALTER TABLE wb_accounts ADD COLUMN IF NOT EXISTS metadata TEXT NOT NULL DEFAULT '{}'")
            elif not any(row["name"] == "metadata" for row in connection.execute("PRAGMA table_info(wb_accounts)")):
                connection.execute("ALTER TABLE wb_accounts ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")
            if self._postgres:
                self._execute(connection, "ALTER TABLE wb_relations ADD COLUMN IF NOT EXISTS last_observed_at TEXT")
            elif not any(row["name"] == "last_observed_at" for row in connection.execute("PRAGMA table_info(wb_relations)")):
                connection.execute("ALTER TABLE wb_relations ADD COLUMN last_observed_at TEXT")
            self._execute(connection, "UPDATE wb_relations SET last_observed_at = observed_at WHERE last_observed_at IS NULL")
            for row in self._execute(connection, "SELECT id,platform,handle FROM wb_accounts WHERE platform IN ('twitter','instagram') ORDER BY created_at,id").fetchall():
                try:
                    key = self.social_account_key(row["platform"], row["handle"])
                except ValueError:
                    continue
                self._execute(connection, "INSERT INTO wb_account_keys (account_key,account_id) VALUES (?,?) ON CONFLICT DO NOTHING", (key, row["id"]))
        self._initialize_research()
        self._initialize_batches()

    def _migrate_job_kinds(self) -> None:
        """Extend kinds without replacing historical jobs, children or indexes."""
        if self._postgres:
            with self._transaction(write=True) as connection:
                existing = self._execute(connection, "SELECT to_regclass('wb_jobs') AS name").fetchone()
                if existing["name"]:
                    self._execute(connection, "ALTER TABLE wb_jobs DROP CONSTRAINT IF EXISTS wb_jobs_kind_check")
                    self._execute(connection, "ALTER TABLE wb_jobs ADD CONSTRAINT wb_jobs_kind_check CHECK(kind IN ('collect','report','login','research'))")
            return
        connection = self._connect()
        try:
            row = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='wb_jobs'").fetchone()
            if row is None or all(f"'{kind}'" in row["sql"] for kind in ("collect", "report", "login", "research")):
                return
            # SQLite requires the pragma outside the transaction. The replacement
            # is atomic and the unchanged wb_jobs name keeps child references valid.
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("BEGIN IMMEDIATE")
            objects = connection.execute("SELECT sql FROM sqlite_master WHERE tbl_name='wb_jobs' AND type IN ('index','trigger') AND sql IS NOT NULL").fetchall()
            schema = row["sql"].replace("wb_jobs", "wb_jobs_upgrade", 1)
            schema, replacements = re.subn(r"CHECK\s*\(\s*kind\s+IN\s*\([^)]*\)\s*\)",
                "CHECK(kind IN ('collect','report','login','research'))", schema, count=1, flags=re.I)
            if replacements != 1:
                raise ValueError("任务类型约束无法安全升级，已保留原有数据。")
            connection.execute(schema)
            connection.execute("INSERT INTO wb_jobs_upgrade SELECT * FROM wb_jobs")
            connection.execute("DROP TABLE wb_jobs")
            connection.execute("ALTER TABLE wb_jobs_upgrade RENAME TO wb_jobs")
            for obj in objects:
                connection.execute(obj["sql"])
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("任务库升级发现关联异常，已保留原有数据。")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _item(row) -> dict | None:
        if row is None:
            return None
        result = dict(row)
        metadata = json.loads(result.pop("metadata"))
        # Stored metadata cannot replace canonical repository fields.
        result = {**metadata, **result, "metadata": metadata}
        for field in ("topics", "entities", "locations"):
            result[field] = json.loads(result[field])
        result["publication_date_unknown"] = bool(result["publication_date_unknown"])
        result["url"] = result["url"] or ""
        linked_status = result.pop("linked_account_status", None)
        linked_evidence = result.pop("linked_evidence_url", None)
        linked_url = result.pop("linked_account_url", None)
        if linked_status is not None:
            result["account_status"] = linked_status
            result["evidence_url"] = linked_evidence or ""
            result["account_url"] = linked_url or ""
        elif result["type"] == "social":
            result["account_status"] = "candidate"
        return result

    @staticmethod
    def _account(row) -> dict | None:
        if row is None:
            return None
        result = dict(row)
        result["tags"] = json.loads(result["tags"])
        result["metadata"] = json.loads(result.get("metadata") or "{}")
        return result

    @staticmethod
    def _job(row) -> dict | None:
        if row is None:
            return None
        result = dict(row)
        result["params"] = json.loads(result["params"])
        result["result"] = json.loads(result["result"]) if result["result"] is not None else None
        result["cancel_requested"] = bool(result["cancel_requested"])
        return result

    @staticmethod
    def _report(row) -> dict | None:
        if row is None:
            return None
        result = dict(row)
        if "snapshot" in result:
            result["snapshot"] = json.loads(result["snapshot"])
        return result

    def import_items(self, items: list[dict], *, _connection=None) -> dict:
        if not isinstance(items, list):
            raise ValueError("items 必须为数组")
        result = {"inserted": 0, "updated": 0, "duplicates": 0, "rejected": 0, "issues": [], "ids": [], "resolved_ids": []}
        known = {"id", "title", "url", "content", "content_hash", "source_name", "type",
                 "published_at", "publication_date_unknown", "topics", "entities", "locations",
                 "account_id", "created_at", "metadata"}
        with (nullcontext(_connection) if _connection is not None else self._transaction(write=True)) as connection:
            for index, data in enumerate(items):
                try:
                    if not isinstance(data, Mapping):
                        raise ValueError("条目必须为对象")
                    title, content = data.get("title", ""), data.get("content", "")
                    if not isinstance(title, str) or not isinstance(content, str):
                        raise ValueError("title 和 content 必须为字符串")
                    title, content = title.strip(), content.strip()
                    if not title and not content:
                        raise ValueError("title 和 content 不能同时为空")
                    item_type = data.get("type", "news")
                    if item_type not in {"news", "social"}:
                        raise ValueError("type 必须为 news 或 social")
                    source = data.get("source_name", "")
                    if not isinstance(source, str):
                        raise ValueError("source_name 必须为字符串")
                    account_id = data.get("account_id") or None
                    if account_id is not None and not isinstance(account_id, str):
                        raise ValueError("account_id 必须为字符串")
                    locations = data.get("locations") or []
                    if not isinstance(locations, list) or any(not isinstance(item, dict) for item in locations):
                        raise ValueError("locations 必须为对象数组")
                    supplied_metadata = data.get("metadata") or {}
                    if not isinstance(supplied_metadata, Mapping):
                        raise ValueError("metadata 必须为对象")
                    metadata = {**supplied_metadata, **{key: value for key, value in data.items() if key not in known}}
                    # These values come from the account table, never evidence payloads.
                    metadata.pop("linked_account_status", None)
                    metadata.pop("linked_evidence_url", None)
                    # Activity review is a local decision, never importer trust.
                    metadata.pop("atlas_activity_reviews", None)
                    metadata.pop("atlas_cleanup", None)
                    _public_json(metadata)
                    if metadata.get("account_status", "candidate") not in ACCOUNT_STATUSES:
                        raise ValueError("account_status 无效")
                    published = _published(data.get("published_at"))
                    item_id = str(uuid4())
                    canonical_url = _url(data.get("url")) or None
                    if item_type == "social":
                        metadata.setdefault("original_url", canonical_url)
                        canonical_url = _social_url(canonical_url)
                    fingerprint = (hashlib.sha256(("social:" + canonical_url).encode()).hexdigest()
                                   if item_type == "social" and canonical_url else content_fingerprint(title, content))
                    if item_type == "news":
                        # A publisher URL owns its identity. Matching text at a
                        # different publisher must never discard that source.
                        metadata["content_group_hash"] = content_fingerprint(title, content)
                        if canonical_url:
                            fingerprint = hashlib.sha256(("news:" + canonical_url).encode()).hexdigest()
                        metadata["capture_version"] = 1
                    values = (item_id, title, canonical_url, content,
                              fingerprint, source.strip(), item_type, published,
                              int(published is None), _json(_strings(data.get("topics"), "topics")),
                              _json(_strings(data.get("entities"), "entities")), _json(locations),
                              account_id, _json(metadata), _now())
                except (ValueError, TypeError, OverflowError) as exc:
                    result["rejected"] += 1
                    result["issues"].append({"index": index, "message": str(exc)})
                    continue
                cursor = self._execute(connection, """INSERT INTO wb_items
                    (id,title,url,content,content_hash,source_name,type,published_at,
                     publication_date_unknown,topics,entities,locations,account_id,metadata,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""", values)
                if cursor.rowcount:
                    result["inserted"] += 1
                    result["ids"].append(item_id)
                    result["resolved_ids"].append(item_id)
                else:
                    result["duplicates"] += 1
                    # Resolve only records which passed validation. A duplicate
                    # can match an existing URL or the same content at another
                    # URL; research checkpoints need its real retained identity.
                    # Prefer the URL if two independent unique keys collide.
                    resolved = self._execute(connection, """SELECT id FROM wb_items
                        WHERE url = ? OR content_hash = ?
                        ORDER BY CASE WHEN url = ? THEN 0 ELSE 1 END, id LIMIT 1""",
                        (canonical_url, fingerprint, canonical_url)).fetchone()
                    if resolved is None:
                        raise RuntimeError("Deduplicated material has no retained record")
                    result["resolved_ids"].append(resolved["id"])
                    if item_type == "social" and canonical_url:
                        previous = self._execute(connection, "SELECT * FROM wb_items WHERE url = ? AND type = 'social'", (canonical_url,)).fetchone()
                        if previous and self._complete_social_capture(connection, previous, values, metadata):
                            result["updated"] += 1
                    elif item_type == "news" and canonical_url:
                        previous = self._execute(connection, "SELECT * FROM wb_items WHERE url = ? AND type = 'news'", (canonical_url,)).fetchone()
                        if previous and self._complete_news_capture(connection, previous, values, metadata):
                            result["updated"] += 1
        return result

    def import_captured_news(self, before: dict, captured: dict) -> dict | None:
        """Commit a slow public-page capture only against its unchanged source."""
        with self._transaction(write=True) as connection:
            suffix = " FOR UPDATE" if self._postgres else ""
            row = self._execute(connection, "SELECT * FROM wb_items WHERE id=? AND type='news'" + suffix, (before["id"],)).fetchone()
            if not row:
                return None
            current = dict(row)
            metadata = json.loads(current["metadata"])
            if (metadata.get("atlas_cleanup", {}).get("status") == "trashed"
                    or any(current.get(key) != before.get(key) for key in ("title", "content", "published_at", "source_name"))):
                return None
            supplied = {**captured, "url": current["url"]}
            return self.import_items([supplied], _connection=connection)

    def _complete_news_capture(self, connection, previous, values, metadata) -> bool:
        """Keep corrections at the same source without rewriting old captures."""
        old = dict(previous)
        prior_metadata = json.loads(old["metadata"])
        published = values[7] or old["published_at"]
        if (prior_metadata.get("content_kind") == "public_article_body"
                and metadata.get("content_kind") in {"rss_summary", "headline_only"}
                and values[1] == old["title"] and published == old["published_at"]):
            # A later aggregator summary cannot erase the captured publisher
            # body. A changed headline/date still creates a reviewable revision.
            return False
        changed = (values[1], values[3], values[5], published) != (
            old["title"], old["content"], old["source_name"], old["published_at"])
        # New provenance can fill an old import's missing fields. A later
        # observation time alone does not invent another content version.
        added = {key: value for key, value in metadata.items()
                 if key not in prior_metadata and key not in {"capture_version", "content_group_hash"}}
        if not changed and not added:
            return False
        stamp = _now()
        revision_id = str(uuid4())
        self._execute(connection, "INSERT INTO wb_item_revisions (id,item_id,prior_capture,revised_at) VALUES (?,?,?,?)",
                      (revision_id, old["id"], _json(old), stamp))
        self._freeze_research_source(connection, old["id"], revision_id)
        version = self._execute(connection, "SELECT COUNT(*) AS total FROM wb_item_revisions WHERE item_id=?", (old["id"],)).fetchone()["total"] + 1
        merged = {**prior_metadata, **(metadata if changed else added),
                  "content_group_hash": content_fingerprint(values[1], values[3]),
                  "capture_version": version, "capture_recorded_at": stamp}
        self._execute(connection, """UPDATE wb_items SET title=?,content=?,content_hash=?,source_name=?,published_at=?,
            publication_date_unknown=?,topics=?,entities=?,locations=?,account_id=?,metadata=? WHERE id=?""",
            (values[1], values[3], values[4], values[5], published, int(published is None),
             values[9], values[10], values[11], old["account_id"] or values[12], _json(merged), old["id"]))
        return True

    def _complete_social_capture(self, connection, previous, values, metadata) -> bool:
        """Fill proven gaps on recapture, retaining previous evidence for audit."""
        old = dict(previous)
        prior_metadata = json.loads(old["metadata"])
        new_kind = metadata.get("content_kind", "body")
        old_kind = prior_metadata.get("content_kind", "body")
        improve_original = (metadata.get("text_representation") == "original_after_platform_toggle"
                            and prior_metadata.get("text_representation") != "original_after_platform_toggle"
                            and bool(values[3]) and values[3] != old["content"])
        improve_body = bool(values[3]) and new_kind == "body" and (not old["content"] or old_kind in {"legacy_unverified", "image_alt"} or improve_original)
        improve_date = old["published_at"] is None and values[7] is not None
        improve_author = not old["account_id"] and bool(values[12])
        # Early legacy imports retained SQLite UTC timestamps without a suffix.
        # Correct only that explicitly identified source with the same instant.
        improve_capture_time = False
        improve_legacy_metrics = False
        if prior_metadata.get("import_source") == metadata.get("import_source") == "legacy_collectors":
            before, after = prior_metadata.get("collected_at"), metadata.get("collected_at")
            try:
                parsed = datetime.fromisoformat(before)
                improve_capture_time = parsed.tzinfo is None and _published(after) == _published(parsed.replace(tzinfo=UTC))
            except (ValueError, TypeError):
                pass
            improve_legacy_metrics = "legacy_engagement" in metadata and "legacy_engagement" not in prior_metadata
        if not (improve_body or improve_date or improve_author or improve_capture_time or improve_legacy_metrics):
            return False
        revision_id = str(uuid4())
        self._execute(connection, "INSERT INTO wb_item_revisions (id,item_id,prior_capture,revised_at) VALUES (?,?,?,?)",
                      (revision_id, old["id"], _json(old), _now()))
        self._freeze_research_source(connection, old["id"], revision_id)
        merged = {**prior_metadata, **metadata, "completed_at": _now()}
        if not improve_body:
            merged["content_kind"] = old_kind
        merged["date_scope"] = "known" if (old["published_at"] or values[7]) else "unknown"
        self._execute(connection, """UPDATE wb_items SET title=?,content=?,source_name=?,published_at=?,
            publication_date_unknown=?,topics=?,entities=?,locations=?,account_id=?,metadata=? WHERE id=?""",
            (values[1] if improve_body else old["title"], values[3] if improve_body else old["content"],
             values[5] if improve_author or improve_body else old["source_name"], old["published_at"] or values[7],
             int(not (old["published_at"] or values[7])), values[9] if improve_body else old["topics"],
             values[10] if improve_body else old["entities"], values[11] if improve_body else old["locations"],
             old["account_id"] or values[12], _json(merged), old["id"]))
        return True

    def list_items(self, query: str = "", type: str = "", topic: str = "", source: str = "",
                   start_date: str = "", end_date: str = "", page: int = 1, page_size: int = 25, trash: bool = False) -> dict:
        page, page_size = _pagination(page, page_size)
        lower, upper = _date_bounds(start_date, end_date)
        clauses, params = [self.item_visibility("i", trash)], []
        if type:
            if type not in {"news", "social"}:
                raise ValueError("type 必须为 news 或 social")
            clauses.append("i.type = ?")
            params.append(type)
        if query:
            clauses.append("(LOWER(i.title) LIKE LOWER(?) ESCAPE '!' OR LOWER(i.content) LIKE LOWER(?) ESCAPE '!' OR LOWER(i.source_name) LIKE LOWER(?) ESCAPE '!')")
            params.extend([_like(query)] * 3)
        if topic:
            clauses.append("i.topics LIKE ? ESCAPE '!'")
            params.append(_like(_json(topic)))
        if source:
            clauses.append("i.source_name = ?")
            params.append(source)
        if lower:
            clauses.append("i.published_at >= ?")
            params.append(lower)
        if upper:
            clauses.append("i.published_at < ?")
            params.append(upper)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._transaction() as connection:
            total = self._execute(connection, "SELECT COUNT(*) AS total FROM wb_items i" + where, params).fetchone()["total"]
            rows = self._execute(connection, _ITEM_JOIN + where +
                " ORDER BY CASE WHEN i.published_at IS NULL THEN 1 ELSE 0 END, i.published_at DESC, i.created_at DESC, i.id DESC LIMIT ? OFFSET ?",
                params + [page_size, (page - 1) * page_size]).fetchall()
        return {"items": [self._item(row) for row in rows], "total": total, "page": page, "page_size": page_size}

    def get_item(self, id: str) -> dict | None:
        with self._transaction() as connection:
            return self._item(self._execute(connection, _ITEM_JOIN + " WHERE i.id = ?", (id,)).fetchone())

    def item_revisions(self, id: str, page: int = 1, page_size: int = 25) -> dict:
        page, page_size = _pagination(page, page_size)
        with self._transaction() as connection:
            if self._execute(connection, "SELECT id FROM wb_items WHERE id=?", (id,)).fetchone() is None:
                raise LookupError("资料不存在")
            total = self._execute(connection, "SELECT COUNT(*) AS total FROM wb_item_revisions WHERE item_id=?", (id,)).fetchone()["total"]
            rows = self._execute(connection, "SELECT id,prior_capture,revised_at FROM wb_item_revisions WHERE item_id=? ORDER BY revised_at DESC,id DESC LIMIT ? OFFSET ?",
                                 (id, page_size, (page - 1) * page_size)).fetchall()
            records = [{"id": row["id"], "revised_at": row["revised_at"],
                        "capture": self._item(json.loads(row["prior_capture"]))} for row in rows]
        return {"items": records, "total": total, "page": page, "page_size": page_size}

    def report_items(self, start_date: str, end_date: str, include_news: bool = True,
                     include_social: bool = True, limit: int = 5000) -> list[dict]:
        """Freeze report selection in one query; concurrent imports cannot shift pages."""
        lower, upper = _date_bounds(start_date, end_date)
        kinds = [kind for kind, include in (("news", include_news), ("social", include_social)) if include]
        if not kinds:
            return []
        placeholders = ",".join("?" for _ in kinds)
        with self._transaction() as connection:
            rows = self._execute(connection, _ITEM_JOIN +
                f" WHERE {self.item_visibility('i')} AND i.type IN ({placeholders}) AND i.published_at >= ? AND i.published_at < ?"
                " ORDER BY i.published_at DESC, i.id LIMIT ?", [*kinds, lower, upper, limit + 1]).fetchall()
        if len(rows) > limit:
            raise ValueError(f"本次范围超过 {limit} 条，请缩小日期范围后再生成报告。")
        return [self._item(row) for row in rows]

    def list_accounts(self) -> list[dict]:
        with self._transaction() as connection:
            return [self._account(row) for row in self._execute(connection,
                "SELECT * FROM wb_accounts ORDER BY updated_at DESC, id DESC").fetchall()]

    def get_account(self, id: str) -> dict | None:
        with self._transaction() as connection:
            return self._account(self._execute(connection, "SELECT * FROM wb_accounts WHERE id = ?", (id,)).fetchone())

    @staticmethod
    def social_account_key(platform: str, handle: str) -> str:
        if not isinstance(platform, str) or not isinstance(handle, str):
            raise ValueError("社媒账号需要有效平台和用户名。")
        platform = str(platform).strip().lower()
        handle = str(handle).strip().lstrip("@").lower()
        if platform not in {"twitter", "instagram"} or not re.fullmatch(r"[a-z0-9_.]{1,50}", handle):
            raise ValueError("社媒账号需要有效平台和用户名。")
        return f"{platform}:{handle}"

    def import_social(self, items: list[dict], accounts: list[dict], relations: list[dict], *, _connection=None) -> dict:
        """Commit discovery, evidence and associations together; never grant trust."""
        counts = {"accounts_inserted": 0, "accounts_existing": 0, "accounts_updated": 0, "relations_inserted": 0,
                  "relations_existing": 0, "relations_updated": 0, "associations_rejected": 0}
        issues, account_ids = [], {}
        with (nullcontext(_connection) if _connection is not None else self._transaction(write=True)) as connection:
            for index, account in enumerate(accounts):
                try:
                    key = self.social_account_key(account.get("platform"), account.get("handle"))
                    platform, handle = key.split(":", 1)
                    url = _url(account.get("url") or f"https://{'x.com' if platform == 'twitter' else 'www.instagram.com'}/{handle}")
                    tags = _strings(account.get("tags"), "tags")
                except (ValueError, TypeError, AttributeError) as exc:
                    counts["associations_rejected"] += 1
                    issues.append({"section": "accounts", "index": index, "message": str(exc)})
                    continue
                profile = account.get("profile") or {}
                public_profile = None
                if isinstance(profile, dict) and profile.get("verified_in_dom") is True:
                    from .social.common import normalize_handle
                    from .pipeline import _url as profile_url
                    try:
                        if normalize_handle(platform, profile.get("url")) == handle:
                            public_profile = {"url": profile_url(profile["url"])}
                            for field, limit in (("bio", 5000), ("observed_at", 80)):
                                if isinstance(profile.get(field), str):
                                    public_profile[field] = profile[field][:limit]
                            for field in ("following_count", "followers_count"):
                                count = profile.get(field)
                                if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                                    public_profile[field] = count
                            # DOM discovery supplies candidates, never registry trust.
                            links = profile.get("links")
                            if isinstance(links, list):
                                public_profile["links"] = []
                                for link in links[:10]:
                                    raw = link.get("url") if isinstance(link, dict) else link
                                    try:
                                        if isinstance(raw, str) and raw:
                                            saved_link = {"url": profile_url(raw)}
                                            # Visible labels are source text, never trust or an expanded URL.
                                            if isinstance(link, dict) and isinstance(link.get("label"), str):
                                                saved_link["label"] = link["label"][:500]
                                            public_profile["links"].append(saved_link)
                                    except ValueError:
                                        continue
                            for field in ("website", "external_url"):
                                if isinstance(profile.get(field), str):
                                    try:
                                        public_profile[field] = profile_url(profile[field])
                                    except ValueError:
                                        continue
                            public_profile["display_name"] = str(account.get("name") or handle)[:160]
                            if isinstance(public_profile.get("bio"), str):
                                public_profile["bio"] = public_profile["bio"][:5000]
                    except ValueError:
                        pass
                existing = self._execute(connection, "SELECT account_id AS id FROM wb_account_keys WHERE account_key = ?", (key,)).fetchone()
                if existing is None:
                    existing = self._execute(connection, "SELECT id FROM wb_accounts WHERE platform = ? AND LOWER(LTRIM(handle, '@')) = ? ORDER BY created_at, id LIMIT 1", (platform, handle)).fetchone()
                if existing:
                    account_id = existing["id"]
                    counts["accounts_existing"] += 1
                    if public_profile:
                        suffix = " FOR UPDATE" if self._postgres else ""
                        previous = self._account(self._execute(connection, "SELECT * FROM wb_accounts WHERE id=?" + suffix, (account_id,)).fetchone())
                        metadata = dict(previous.get("metadata") or {})
                        metadata["last_public_profile"] = public_profile
                        manual_fields = metadata.get("registry_manual_fields", [])
                        display_name = previous["name"]
                        placeholder = display_name.strip().lstrip("@").lower() == handle
                        auto_name = metadata.get("registry_origin") == "social_discovery"
                        if "name" not in manual_fields and (placeholder or auto_name) and public_profile["display_name"].strip().lstrip("@").lower() != handle:
                            display_name = public_profile["display_name"]
                        if display_name != previous["name"] or metadata != previous.get("metadata"):
                            self._execute(connection, "UPDATE wb_accounts SET name=?,metadata=?,updated_at=? WHERE id=?",
                                (display_name, _json(metadata), _now(), account_id))
                            counts["accounts_updated"] += 1
                else:
                    account_id, stamp = str(uuid4()), _now()
                    from .account_identity import RESERVED_METADATA
                    metadata = {key: value for key, value in (account.get("metadata") or {}).items()
                                if key not in RESERVED_METADATA}
                    metadata.update(registry_origin="social_discovery", registry_manual_fields=[])
                    if public_profile:
                        metadata["last_public_profile"] = public_profile
                    self._execute(connection, """INSERT INTO wb_accounts
                        (id,name,handle,platform,url,organization,status,evidence_url,tags,metadata,created_at,updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (account_id, str(account.get("name") or handle)[:160],
                        handle, platform, url, str(account.get("organization") or "")[:200], "candidate", "", _json(tags),
                        _json(metadata), stamp, stamp))
                    counts["accounts_inserted"] += 1
                self._execute(connection, "INSERT INTO wb_account_keys (account_key,account_id) VALUES (?,?) ON CONFLICT DO NOTHING", (key, account_id))
                account_ids[key] = account_id

            def resolve(key):
                if not key:
                    return None
                if key in account_ids:
                    return account_ids[key]
                existing = self._execute(connection, "SELECT account_id FROM wb_account_keys WHERE account_key = ?", (key,)).fetchone()
                return existing["account_id"] if existing else None

            prepared = []
            for item in items:
                item = dict(item)
                key = item.pop("account_key", None)
                item["account_id"] = resolve(key)
                item["type"] = "social"
                item["account_status"] = "candidate"
                item["evidence_url"] = ""
                prepared.append(item)
            result = self.import_items(prepared, _connection=connection)
            for index, relation in enumerate(relations):
                try:
                    source, target = resolve(relation.get("source_key")), resolve(relation.get("target_key"))
                    if not source or not target or source == target:
                        raise ValueError("关系需要两个不同且已保存的账号。")
                    if relation.get("relation", "follows") != "follows":
                        raise ValueError("不支持的关系类型。")
                    evidence = _url(relation.get("evidence_url"))
                    observed = _published(relation.get("observed_at"))
                except (ValueError, TypeError, AttributeError) as exc:
                    counts["associations_rejected"] += 1
                    issues.append({"section": "relations", "index": index, "message": str(exc)})
                    continue
                cursor = self._execute(connection, """INSERT INTO wb_relations
                    (id,source_id,target_id,relation,evidence_url,observed_at,last_observed_at,created_at)
                    VALUES (?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""", (str(uuid4()), source, target, "follows", evidence, observed, observed, _now()))
                counts["relations_inserted" if cursor.rowcount else "relations_existing"] += 1
                if not cursor.rowcount:
                    previous = self._execute(connection, "SELECT * FROM wb_relations WHERE source_id = ? AND target_id = ? AND relation = 'follows'", (source, target)).fetchone()
                    last_seen = previous["last_observed_at"] or previous["observed_at"]
                    newer = observed and (not last_seen or observed > last_seen)
                    if (evidence and not previous["evidence_url"]) or newer:
                        self._execute(connection, "UPDATE wb_relations SET evidence_url=?,observed_at=?,last_observed_at=? WHERE id=?",
                            (previous["evidence_url"] or evidence, previous["observed_at"] or observed,
                             observed if newer else last_seen, previous["id"]))
                        counts["relations_updated"] += 1
            result["issues"].extend(issues)
            from .account_verification import schedule_account_checks
            schedule_account_checks(self, account_ids.values(), _connection=connection)
        return {**result, **counts, "account_ids": account_ids}

    def account_relations(self, id: str, page: int = 1, page_size: int = 1000) -> dict:
        if (isinstance(page, bool) or not isinstance(page, int) or page < 1
                or isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 1000):
            raise ValueError("关系页码须为正整数，每页需在 1 至 1000 条之间。")
        with self._transaction() as connection:
            if self._execute(connection, "SELECT id FROM wb_accounts WHERE id = ?", (id,)).fetchone() is None:
                raise LookupError("账号不存在")
            rows = self._execute(connection, """SELECT r.*, a.name AS source_name, b.name AS target_name,
                a.handle AS source_handle, b.handle AS target_handle,
                a.platform AS source_platform, b.platform AS target_platform
                FROM wb_relations r JOIN wb_accounts a ON a.id = r.source_id
                JOIN wb_accounts b ON b.id = r.target_id WHERE r.source_id = ? OR r.target_id = ?
                ORDER BY r.observed_at DESC, r.id LIMIT ? OFFSET ?""", (id, id, page_size, (page - 1) * page_size)).fetchall()
            total = self._execute(connection, "SELECT COUNT(*) AS total FROM wb_relations WHERE source_id = ? OR target_id = ?", (id, id)).fetchone()["total"]
        return {"items": [dict(row) for row in rows], "total": total, "page": page, "page_size": page_size,
                "limit": page_size, "truncated": total > len(rows)}

    def save_account(self, data: dict, id: str | None = None) -> dict:
        if not isinstance(data, Mapping):
            raise ValueError("账号必须为对象")
        fields = ("name", "handle", "platform", "url", "organization", "status", "evidence_url", "tags")
        with self._transaction(write=True) as connection:
            if id:
                suffix = " FOR UPDATE" if self._postgres else ""
                previous = self._account(self._execute(connection, "SELECT * FROM wb_accounts WHERE id = ?" + suffix, (id,)).fetchone())
                if previous is None:
                    raise LookupError("账号不存在")
            else:
                previous = {"name": "", "handle": "", "platform": "", "url": "", "organization": "",
                            "status": "candidate", "evidence_url": "", "tags": [], "created_at": _now()}
            account = {**previous, **{field: data[field] for field in fields if field in data}}
            for field in fields[:-1]:
                if not isinstance(account[field], str):
                    raise ValueError(f"{field} 必须为字符串")
                account[field] = account[field].strip()
            if not account["name"]:
                raise ValueError("账号名称不能为空")
            if account["status"] not in ACCOUNT_STATUSES:
                raise ValueError("账号状态无效")
            account["url"] = _url(account["url"])
            account["evidence_url"] = _url(account["evidence_url"], "evidence_url")
            identity_changed = id and (account["platform"].lower(), account["handle"].lstrip("@").lower()) != (
                previous["platform"].lower(), previous["handle"].lstrip("@").lower())
            if identity_changed:
                linked = self._execute(connection, "SELECT id FROM wb_items WHERE account_id = ? LIMIT 1", (id,)).fetchone()
                related = self._execute(connection, "SELECT id FROM wb_relations WHERE source_id = ? OR target_id = ? LIMIT 1", (id, id)).fetchone()
                researched = self._execute(connection, "SELECT id FROM wb_research_nodes WHERE account_id=? LIMIT 1", (id,)).fetchone()
                if linked or related or researched:
                    raise ValueError("该账号已关联原始资料或关系，不能改为另一个平台或用户名；请新建账号以保留原有证据归属。")
            if id and (identity_changed or account["url"] != previous["url"]):
                account["status"] = "candidate"
                account["evidence_url"] = ""
            if account["status"] == "verified" and not account["evidence_url"]:
                raise ValueError("核验账号必须提供 evidence_url")
            account["tags"] = _strings(account["tags"], "tags")
            account["id"] = id or str(uuid4())
            account["updated_at"] = _now()
            metadata = dict(previous.get("metadata") or {})
            manual_fields = set(metadata.get("registry_manual_fields") or [])
            manual_fields.update(field for field in ("name", "organization", "tags") if field in data)
            metadata["registry_manual_fields"] = sorted(manual_fields)
            account["metadata"] = metadata
            key = None
            if account["platform"] in {"twitter", "instagram"} and account["handle"]:
                key = self.social_account_key(account["platform"], account["handle"])
                duplicate = self._execute(connection, "SELECT account_id FROM wb_account_keys WHERE account_key = ?", (key,)).fetchone()
                if duplicate and duplicate["account_id"] != account["id"]:
                    raise ValueError("这个平台的同名账号已存在，请编辑已有账号。")
                duplicate_record = self._execute(connection, "SELECT id FROM wb_accounts WHERE platform = ? AND LOWER(LTRIM(handle, '@')) = ? AND id != ? LIMIT 1",
                    (account["platform"], account["handle"].lstrip("@").lower(), account["id"])).fetchone()
                if duplicate_record:
                    raise ValueError("这个平台的同名账号已存在，请编辑已有账号。")
            values = [account[field] if field != "tags" else _json(account[field]) for field in fields]
            if id:
                self._execute(connection, "UPDATE wb_accounts SET " + ",".join(f"{field} = ?" for field in fields)
                    + ",updated_at = ? WHERE id = ?", values + [account["updated_at"], id])
            else:
                self._execute(connection, "INSERT INTO wb_accounts (" + ",".join(fields)
                    + ",updated_at,id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    values + [account["updated_at"], account["id"], account["created_at"]])
            self._execute(connection, "DELETE FROM wb_account_keys WHERE account_id = ?", (account["id"],))
            self._execute(connection, "UPDATE wb_accounts SET metadata=? WHERE id=?", (_json(metadata), account["id"]))
            if key:
                self._execute(connection, "INSERT INTO wb_account_keys (account_key,account_id) VALUES (?,?)", (key, account["id"]))
            from .account_verification import schedule_account_checks
            schedule_account_checks(self, [account["id"]], _connection=connection)
            return self._account(self._execute(connection, "SELECT * FROM wb_accounts WHERE id=?", (account["id"],)).fetchone())

    def create_job(self, kind: str, params: dict, *, _connection=None) -> dict:
        if kind not in {"collect", "report", "login", "research"}:
            raise ValueError("任务类型必须为 collect、report、login 或 research")
        if not isinstance(params, dict):
            raise ValueError("任务参数必须为对象")
        job = {"id": str(uuid4()), "kind": kind, "status": "queued", "params": json.loads(_json(params)),
               "result": None, "error": None, "progress": 0, "created_at": None,
               "started_at": None, "finished_at": None, "cancel_requested": False}
        with (nullcontext(_connection) if _connection is not None else self._transaction(write=True)) as connection:
            # Assign queue order while holding the writer transaction. Windows
            # can return the same clock tick for independent requests; a UUID
            # tiebreaker would silently reverse their committed enqueue order.
            if self._postgres:
                self._execute(connection, "SELECT pg_advisory_xact_lock(?)", (_QUEUE_ENQUEUE_LOCK,))
            canonical = "____-__-__T__:__:__.______Z"
            previous = self._execute(connection, "SELECT created_at FROM wb_jobs WHERE created_at LIKE ? ORDER BY created_at DESC, id DESC LIMIT 1", (canonical,)).fetchone()
            # Valid older backups can omit fractions or timezone suffixes.
            # Compare their instants without rewriting their saved records.
            legacy = self._execute(connection, "SELECT created_at FROM wb_jobs WHERE created_at NOT LIKE ?", (canonical,)).fetchall()
            latest = None
            for row in ([previous] if previous else []) + list(legacy):
                prior = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
                if prior.tzinfo is None:
                    prior = prior.replace(tzinfo=UTC)
                latest = max(latest, prior) if latest is not None else prior
            created = datetime.fromisoformat(_now().replace("Z", "+00:00"))
            if latest is not None and created <= latest:
                created = latest + timedelta(microseconds=1)
            job["created_at"] = created.isoformat(timespec="microseconds").replace("+00:00", "Z")
            self._execute(connection, """INSERT INTO wb_jobs
                (id,kind,status,params,result,error,progress,created_at,started_at,finished_at,cancel_requested)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""", [*map(job.get, ("id", "kind", "status")), _json(params),
                    None, None, 0, job["created_at"], None, None, 0])
        return job

    def get_job(self, id: str) -> dict | None:
        with self._transaction() as connection:
            return self._job(self._execute(connection, "SELECT * FROM wb_jobs WHERE id = ?", (id,)).fetchone())

    def list_jobs(self, page: int = 1, page_size: int = 25) -> dict:
        return self._list_records("wb_jobs", page, page_size, self._job)

    def _list_records(self, table: str, page: int, page_size: int, decoder) -> dict:
        page, page_size = _pagination(page, page_size)
        if table not in {"wb_jobs", "wb_reports"}:
            raise ValueError("Unknown repository table")
        # Report bodies/snapshots stay on the detail endpoint, avoiding a large list payload.
        columns = "*" if table == "wb_jobs" else "id,title,start_date,end_date,mode,source_count,job_id,created_at"
        with self._transaction() as connection:
            total = self._execute(connection, f"SELECT COUNT(*) AS total FROM {table}").fetchone()["total"]
            rows = self._execute(connection, f"SELECT {columns} FROM {table} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                                 (page_size, (page - 1) * page_size)).fetchall()
        return {"items": [decoder(row) for row in rows], "total": total, "page": page, "page_size": page_size}

    def update_job(self, id: str, **fields) -> dict | None:
        allowed = {"status", "result", "error", "progress", "started_at", "finished_at", "cancel_requested"}
        if set(fields) - allowed:
            raise ValueError("不支持的任务更新字段")
        with self._transaction(write=True) as connection:
            suffix = " FOR UPDATE" if self._postgres else ""
            job = self._job(self._execute(connection, "SELECT * FROM wb_jobs WHERE id = ?" + suffix, (id,)).fetchone())
            if job is None:
                return None
            target = fields.get("status", job["status"])
            if target not in JOB_STATUSES:
                raise ValueError("任务状态无效")
            if job["status"] in TERMINAL:
                if target != job["status"] or fields:
                    raise ValueError("终态任务不可修改；重试须创建新任务")
                return job
            if job["status"] == "running" and target == "queued":
                raise ValueError("运行中的任务不能重新入队")
            if "progress" in fields and (isinstance(fields["progress"], bool) or not isinstance(fields["progress"], int) or not 0 <= fields["progress"] <= 100):
                raise ValueError("progress 必须为 0 至 100 的整数")
            if "error" in fields and fields["error"] is not None and not isinstance(fields["error"], str):
                raise ValueError("error 必须为字符串")
            if "cancel_requested" in fields and not isinstance(fields["cancel_requested"], bool):
                raise ValueError("cancel_requested 必须为布尔值")
            if target == "running" and job["started_at"] is None:
                fields.setdefault("started_at", _now())
            if target in TERMINAL:
                fields.setdefault("finished_at", _now())
            if target == "succeeded":
                fields.setdefault("progress", 100)
            if fields:
                values = [_json(value) if key == "result" and value is not None else
                          int(value) if key == "cancel_requested" else value for key, value in fields.items()]
                self._execute(connection, "UPDATE wb_jobs SET " + ",".join(f"{key} = ?" for key in fields)
                              + " WHERE id = ?", values + [id])
            return self._job(self._execute(connection, "SELECT * FROM wb_jobs WHERE id = ?", (id,)).fetchone())

    def _event(self, connection, id: str, message: str, level: str = "info") -> dict:
        event = {"id": str(uuid4()), "job_id": id, "message": message, "level": level, "created_at": _now()}
        self._execute(connection, "INSERT INTO wb_job_events (id,job_id,message,level,created_at) VALUES (?,?,?,?,?)",
                      list(event.values()))
        return event

    def add_job_event(self, id: str, message: str, level: str = "info") -> dict:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("任务事件内容不能为空")
        if level not in {"info", "warning", "error", "success", "debug"}:
            raise ValueError("任务事件级别无效")
        with self._transaction(write=True) as connection:
            if self._execute(connection, "SELECT id FROM wb_jobs WHERE id = ?", (id,)).fetchone() is None:
                raise LookupError("任务不存在")
            return self._event(connection, id, message, level)

    def job_events(self, id: str) -> list[dict]:
        with self._transaction() as connection:
            return [dict(row) for row in self._execute(connection,
                "SELECT * FROM wb_job_events WHERE job_id = ? ORDER BY created_at, id", (id,)).fetchall()]

    def recover_jobs(self) -> int:
        with self._transaction(write=True) as connection:
            suffix = " FOR UPDATE" if self._postgres else ""
            rows = self._execute(connection, "SELECT id FROM wb_jobs WHERE status = 'running'" + suffix).fetchall()
            for row in rows:
                self._execute(connection, "UPDATE wb_jobs SET status = 'interrupted', finished_at = ?, error = ? WHERE id = ?",
                    (_now(), "执行进程已中断。请重试以创建新的任务。", row["id"]))
                self._execute(connection, "UPDATE wb_research_runs SET status='interrupted',updated_at=? WHERE current_job_id=? AND status IN ('queued','running')", (_now(), row["id"]))
                self._event(connection, row["id"], "启动恢复：先前运行中的任务已标记为中断。", "warning")
            return len(rows)

    def claim_job(self, *, kind: str | None = None, exclude_kind: str | None = None, verification: bool | None = None) -> dict | None:
        """Atomically claim the oldest matching job; no filters retains legacy FIFO."""
        for value in (kind, exclude_kind):
            if value is not None and (not isinstance(value, str) or value not in {"collect", "report", "login", "research"}):
                raise ValueError("任务类型筛选无效")
        if kind is not None and exclude_kind is not None:
            raise ValueError("任务类型筛选不能同时包含和排除")
        if verification is not None and not isinstance(verification, bool):
            raise ValueError("核验任务筛选无效")
        filters, values = "", []
        if kind is not None:
            filters, values = " AND kind = ?", [kind]
        elif exclude_kind is not None:
            filters, values = " AND kind <> ?", [exclude_kind]
        if verification is not None:
            # Both engines compare JSON boolean true, never truthy strings.
            special = "(kind='collect' AND " + ("params::jsonb->'_account_verification' = 'true'::jsonb" if self._postgres
                       else "json_type(params, '$._account_verification') = 'true'") + ")"
            filters += " AND " + (special if verification else "NOT COALESCE(" + special + ", FALSE)")
        with self._transaction(write=True) as connection:
            suffix = " FOR UPDATE SKIP LOCKED" if self._postgres else ""
            ordering = "created_at, id" if self._postgres else "julianday(created_at), rowid"
            row = self._execute(connection, "SELECT * FROM wb_jobs WHERE status = 'queued' AND cancel_requested = 0"
                                + filters + " ORDER BY " + ordering + " LIMIT 1" + suffix, values).fetchone()
            if row is None:
                return None
            self._execute(connection, "UPDATE wb_jobs SET status = 'running', started_at = ? WHERE id = ? AND status = 'queued'",
                          (_now(), row["id"]))
            return self._job(self._execute(connection, "SELECT * FROM wb_jobs WHERE id = ?", (row["id"],)).fetchone())

    def request_cancel(self, id: str) -> dict | None:
        with self._transaction(write=True) as connection:
            suffix = " FOR UPDATE" if self._postgres else ""
            row = self._execute(connection, "SELECT * FROM wb_jobs WHERE id = ?" + suffix, (id,)).fetchone()
            if row is None:
                return None
            if row["status"] == "queued":
                self._execute(connection, "UPDATE wb_jobs SET status = 'cancelled', cancel_requested = 1, finished_at = ? WHERE id = ?", (_now(), id))
                self._execute(connection, "UPDATE wb_research_runs SET status='cancelled',updated_at=? WHERE current_job_id=?", (_now(), id))
                self._event(connection, id, "任务在开始执行前已取消。", "info")
            elif row["status"] == "running" and not row["cancel_requested"]:
                self._execute(connection, "UPDATE wb_jobs SET cancel_requested = 1 WHERE id = ?", (id,))
                self._event(connection, id, "已请求取消；正在等待执行器安全停止。", "info")
            return self._job(self._execute(connection, "SELECT * FROM wb_jobs WHERE id = ?", (id,)).fetchone())

    def save_report(self, data: dict) -> dict:
        if not isinstance(data, Mapping):
            raise ValueError("报告必须为对象")
        if not isinstance(data.get("title"), str) or not data["title"].strip():
            raise ValueError("报告标题不能为空")
        if not isinstance(data.get("markdown"), str) or not data["markdown"].strip():
            raise ValueError("报告正文不能为空")
        mode = data.get("mode", "evidence")
        if mode not in {"evidence", "ai"}:
            raise ValueError("报告模式必须为 evidence 或 ai")
        start, end = _iso_date(data.get("start_date"), "start_date"), _iso_date(data.get("end_date"), "end_date")
        _date_bounds(start, end)
        count = data.get("source_count", 0)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("source_count 必须为非负整数")
        snapshot = json.loads(_json(data.get("snapshot", [])))
        report = {"id": str(uuid4()), "title": data["title"].strip(), "start_date": start,
                  "end_date": end, "mode": mode, "markdown": data["markdown"], "snapshot": snapshot,
                  "source_count": count, "job_id": data.get("job_id"), "created_at": _now()}
        with self._transaction(write=True) as connection:
            self._execute(connection, """INSERT INTO wb_reports
                (id,title,start_date,end_date,mode,markdown,snapshot,source_count,job_id,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", [_json(value) if key == "snapshot" else value for key, value in report.items()])
        return report

    def list_reports(self, page: int = 1, page_size: int = 25) -> dict:
        return self._list_records("wb_reports", page, page_size, self._report)

    def get_report(self, id: str) -> dict | None:
        with self._transaction() as connection:
            return self._report(self._execute(connection, "SELECT * FROM wb_reports WHERE id = ?", (id,)).fetchone())

    def overview(self) -> dict:
        with self._transaction() as connection:
            types = {row["type"]: row["count"] for row in self._execute(connection,
                "SELECT type,COUNT(*) AS count FROM wb_items WHERE " + self.item_visibility() + " GROUP BY type").fetchall()}
            stats = {"items": sum(types.values()), "news": types.get("news", 0), "social": types.get("social", 0),
                     "reports": self._execute(connection, "SELECT COUNT(*) AS count FROM wb_reports").fetchone()["count"],
                     "active_jobs": self._execute(connection, "SELECT COUNT(*) AS count FROM wb_jobs WHERE status IN ('queued','running')").fetchone()["count"]}
            sources = [dict(row) for row in self._execute(connection,
                "SELECT source_name AS name,COUNT(*) AS count FROM wb_items WHERE " + self.item_visibility() + " GROUP BY source_name ORDER BY count DESC, name").fetchall()]
            evidence = self._execute(connection, "SELECT id,topics,locations,published_at FROM wb_items WHERE " + self.item_visibility()).fetchall()
            recent_items = [self._item(row) for row in self._execute(connection, _ITEM_JOIN + " WHERE " + self.item_visibility("i") + " ORDER BY i.created_at DESC, i.id DESC LIMIT 6").fetchall()]
            recent_jobs = [self._job(row) for row in self._execute(connection, "SELECT * FROM wb_jobs ORDER BY created_at DESC,id DESC LIMIT 6").fetchall()]
            recent_reports = [dict(row) for row in self._execute(connection,
                "SELECT id,title,start_date,end_date,mode,source_count,job_id,created_at FROM wb_reports ORDER BY created_at DESC,id DESC LIMIT 6").fetchall()]
        topics, timeline = Counter(), Counter()
        locations = {}
        for row in evidence:
            topics.update(set(json.loads(row["topics"])))
            if row["published_at"]:
                local_date = datetime.fromisoformat(row["published_at"].replace("Z", "+00:00")).astimezone(TOKYO).date().isoformat()
                timeline[local_date] += 1
            seen = set()
            for location in json.loads(row["locations"]):
                name = location.get("name") or location.get("canonical_name")
                lat, lon = location.get("lat", location.get("latitude")), location.get("lon", location.get("longitude"))
                if not isinstance(name, str) or not name.strip() or isinstance(lat, bool) or isinstance(lon, bool):
                    continue
                # The v2 overview counts text mentions; it does not need or
                # invent map coordinates. Explicit invalid coordinates remain
                # rejected for older imported records that supplied them.
                if lat is not None or lon is not None:
                    try:
                        lat, lon = float(lat), float(lon)
                    except (TypeError, ValueError):
                        continue
                    if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
                        continue
                key = name.strip()
                if key in seen:
                    continue
                seen.add(key)
                entry = locations.setdefault(key, {"name": name.strip(), "count": 0, "item_ids": []})
                entry["count"] += 1
                entry["item_ids"].append(row["id"])
        return {"stats": stats, "topics": [{"name": name, "count": count} for name, count in sorted(topics.items(), key=lambda pair: (-pair[1], pair[0]))],
                "timeline": [{"date": day, "count": count} for day, count in sorted(timeline.items())],
                "sources": sources, "locations": sorted(locations.values(), key=lambda item: (-item["count"], item["name"])),
                "recent_items": recent_items, "recent_jobs": recent_jobs, "recent_reports": recent_reports}
