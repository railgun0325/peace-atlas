"""Prepare a verified, inert SQLite replacement without touching the source.

This module deliberately imports no server, worker, browser, or model client.
Current complete workbench backups and the complete prior schema without the
deferred-frontier table are supported. A desktop caller must
still confirm the summary and retain its old database before installing this
candidate; preparation never changes the running application's database.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from datetime import date, datetime, timezone
from functools import partial
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from tempfile import TemporaryDirectory
import time
from uuid import uuid4

from .research_repository import BASES, NODE_STATUSES, RELATIONSHIPS, RUN_STATUSES
from .job_retries import RETRY_METADATA, retry_parent_links, retry_root
from .store import ACCOUNT_STATUSES, JOB_STATUSES, TOKYO, Store


class RestoreError(Exception):
    """A safe, user-facing preparation failure for JSON CLI responses."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.message, self.code = message, code


class _Deadline:
    def __init__(self, seconds: float):
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds):
            raise RestoreError("恢复预检时限无效。", "restore_invalid_timeout")
        self.end = time.monotonic() + seconds
        self.check()

    def expired(self) -> bool:
        return time.monotonic() >= self.end

    def check(self) -> None:
        if self.expired():
            raise RestoreError("恢复预检超时；源文件和当前资料库保持不变。", "restore_timeout")


class _Connection(sqlite3.Connection):
    def __init__(self, *args, deadline: _Deadline, **kwargs):
        self.deadline = deadline
        deadline.check()
        super().__init__(*args, **kwargs)
        self.row_factory = sqlite3.Row
        self.set_progress_handler(lambda: int(deadline.expired()), 1000)

    def execute(self, *args, **kwargs):
        self.deadline.check()
        result = super().execute(*args, **kwargs)
        self.deadline.check()
        return result


def _connect(path: Path, deadline: _Deadline, *, readonly: bool = False) -> _Connection:
    deadline.check()
    # immutable avoids SQLite creating/updating the source's SHM/WAL files.
    # It is safe here only because nonempty sidecars are rejected and the
    # complete source fingerprint is verified again after preparation.
    target = path.as_uri() + "?mode=ro&immutable=1" if readonly else str(path)
    connection = sqlite3.connect(target, uri=readonly, timeout=0.05,
                                 factory=partial(_Connection, deadline=deadline))
    try:
        connection.execute("PRAGMA busy_timeout = 50")
        connection.execute("PRAGMA trusted_schema = OFF")
        connection.execute("PRAGMA foreign_keys = ON")
        if readonly:
            connection.execute("PRAGMA query_only = ON")
        else:
            connection.execute("PRAGMA journal_mode = DELETE")
        return connection
    except BaseException:
        connection.close()
        raise


class _TemplateStore(Store):
    """Derive requirements from the real Store, within the same deadline."""

    def __init__(self, path: Path, deadline: _Deadline):
        self._restore_deadline = deadline
        super().__init__(path)

    def _connect(self):
        return _connect(Path(self.path), self._restore_deadline)


_TOKEN = re.compile(r"--[^\n]*|/\*.*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|"
                    r"`(?:``|[^`])*`|\[[^\]]*\]|[A-Za-z_][A-Za-z_0-9]*|\d+(?:\.\d+)?|\S", re.S)


def _tokens(sql: str | None) -> tuple[str, ...]:
    values = []
    for value in _TOKEN.findall(sql or ""):
        if value.startswith(("--", "/*")):
            continue
        if value.startswith("'"):
            values.append(value)
        elif value.startswith(('"', '`', '[')):
            values.append(value[1:-1].replace(value[0] * 2, value[0]).lower())
        else:
            values.append(value.lower())
    return tuple(values)


def _checks(sql: str) -> tuple[tuple[str, ...], ...]:
    tokens, checks = _tokens(sql), []
    for index, token in enumerate(tokens):
        if token != "check" or tokens[index + 1:index + 2] != ("(",):
            continue
        depth, end = 1, index + 2
        while end < len(tokens) and depth:
            depth += (tokens[end] == "(") - (tokens[end] == ")")
            end += 1
        if depth:
            raise RestoreError("数据库约束无法识别。", "restore_schema")
        checks.append(tokens[index + 2:end - 1])
    return tuple(sorted(checks))


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _schema(connection: _Connection) -> dict:
    objects = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY name").fetchall()
    tables = {}
    for obj in objects:
        if obj["type"] in {"view", "trigger"} or "virtual" in _tokens(obj["sql"]):
            raise RestoreError("不接受含视图、触发器或虚拟表的恢复文件。", "restore_unsafe_schema")
        if obj["type"] not in {"table", "index"}:
            raise RestoreError("数据库含不支持的结构。", "restore_unsafe_schema")
        if obj["type"] == "table":
            tables[obj["name"]] = obj["sql"]
    flags = {row["name"]: (row["type"], row["wr"], row["strict"])
             for row in connection.execute("PRAGMA table_list") if row["schema"] == "main"}
    result = {}
    for table, sql in tables.items():
        # PRAGMAs cover columns, keys, indexes and FKs. Check expressions and
        # uncommon execution-changing clauses need separate examination.
        extra = tuple(token for token in _tokens(sql) if token in {
            "collate", "conflict", "deferrable", "initially", "autoincrement", "generated"})
        columns = {row["name"]: (" ".join(row["type"].upper().split()), row["notnull"],
                   _tokens(row["dflt_value"]), row["pk"], row["hidden"])
                   for row in connection.execute(f"PRAGMA table_xinfo({_quote(table)})")}
        if any(column[-1] for column in columns.values()):
            raise RestoreError("数据库含不支持的隐藏或生成字段。", "restore_unsafe_schema")
        indexes = Counter()
        for row in connection.execute(f"PRAGMA index_list({_quote(table)})"):
            indexed = tuple((column["name"], column["desc"], column["coll"])
                            for column in connection.execute(f"PRAGMA index_xinfo({_quote(row['name'])})")
                            if column["key"])
            if row["partial"] or any(column[0] is None for column in indexed):
                raise RestoreError("数据库含不支持的表达式或部分索引。", "restore_unsafe_schema")
            indexes[(row["unique"], indexed)] += 1
        grouped = defaultdict(list)
        for row in connection.execute(f"PRAGMA foreign_key_list({_quote(table)})"):
            grouped[row["id"]].append((row["seq"], row["table"], row["from"], row["to"],
                                      row["on_update"], row["on_delete"], row["match"]))
        foreign_keys = sorted(tuple(sorted(rows)) for rows in grouped.values())
        result[table] = (columns, indexes, foreign_keys, _checks(sql), flags.get(table), extra)
    return {"tables": result,
            "application_id": connection.execute("PRAGMA application_id").fetchone()[0],
            "user_version": connection.execute("PRAGMA user_version").fetchone()[0]}


def _validate_schema(connection: _Connection, expected: dict, *, allow_legacy_deferred=False) -> bool:
    actual = _schema(connection)
    names = set(actual["tables"])
    current_names = set(expected["tables"])
    optional_legacy = {'wb_research_deferred', 'wb_public_source_cache'}
    legacy_deferred = (allow_legacy_deferred and names < current_names
                       and current_names - names <= optional_legacy)
    if names != current_names and not legacy_deferred:
        raise RestoreError("恢复文件不是当前版本的完整工作台备份；表结构缺失或包含未知内容。", "restore_schema")
    for name, shape in actual["tables"].items():
        if shape != expected["tables"][name]:
            raise RestoreError(f"恢复文件的 {name} 结构不兼容；请使用当前版本的完整备份。", "restore_schema")
    if (actual["application_id"], actual["user_version"]) != (expected["application_id"], expected["user_version"]):
        raise RestoreError("恢复文件使用了不支持的数据库版本。", "restore_schema")
    return legacy_deferred


def _integrity(connection: _Connection) -> None:
    rows = connection.execute("PRAGMA integrity_check")
    first = rows.fetchone()
    if first is None or first[0] != "ok" or rows.fetchone() is not None:
        raise RestoreError("恢复文件的 SQLite 完整性检查失败。", "restore_integrity")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise RestoreError("恢复文件含损坏的数据关联。", "restore_foreign_keys")


_JSON_FIELDS = {
    "wb_accounts": {"tags": list, "metadata": dict},
    "wb_items": {"topics": list, "entities": list, "locations": list, "metadata": dict},
    "wb_item_revisions": {"prior_capture": dict},
    "wb_jobs": {"params": dict, "result": (dict, type(None))},
    # Older reports use a list; current reports use an object with items.
    "wb_reports": {"snapshot": (dict, list)},
    "wb_research_runs": {"params": dict, "summary": dict, "frontier": list, "visited": list,
                         "query_plan": dict, "diagnostics": list, "analysis": dict, "metadata": dict},
    "wb_research_nodes": {"record": dict},
    "wb_research_edges": {"record": dict},
    "wb_research_observations": {"record": dict},
    "wb_research_claims": {"record": dict},
    "wb_research_claim_observations": {"record": dict},
    "wb_research_deferred": {"record": (dict, str)},
    "wb_drafts": {"data": dict},
    "wb_public_source_cache": {"page": dict},
}


def _array_contents(value, entry_type, label) -> None:
    if not isinstance(value, list) or any(not isinstance(entry, entry_type) for entry in value):
        raise RestoreError(f"恢复文件的 {label} 数组内容无效。", "restore_data")


def _optional_member(value, member, shape, label) -> None:
    if member in value and not isinstance(value[member], shape):
        raise RestoreError(f"恢复文件的 {label}.{member} 数据类型无效。", "restore_data")


def _timestamp(value, label, *, optional=False) -> None:
    if optional and value in (None, ""):
        return
    try:
        # Match the overview's parser, including its timezone conversion.
        # Historical ISO dates and naive SQLite timestamps remain accepted;
        # requiring canonical microseconds or a Z suffix would reject them.
        datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TOKYO)
    except (ValueError, TypeError, AttributeError, OverflowError, OSError) as exc:
        raise RestoreError(f"恢复文件的 {label} 日期或时间无效。", "restore_data") from exc


def _json_contents(table, field, value) -> None:
    label = f"{table}.{field}"
    if table == "wb_items" and field == "metadata":
        for member in ("atlas_cleanup", "atlas_activity_reviews"):
            _optional_member(value, member, dict, label)
        cleanup = value.get("atlas_cleanup", {})
        if cleanup and cleanup.get("status") not in {"active", "trashed"}:
            raise RestoreError("恢复文件的资料清理状态无效。", "restore_data")
        # The material list falls back to these legacy text captures when the
        # canonical body is empty, then slices the selected text for preview.
        for member in ("clean_text", "content_text"):
            _optional_member(value, member, (str, type(None)), label)
    if field in {"tags", "topics", "entities", "visited"}:
        _array_contents(value, str, label)
    if field == "locations" or (table == "wb_research_runs" and field in {"frontier", "diagnostics"}):
        _array_contents(value, dict, label)
    if table == "wb_research_runs" and field in {"analysis", "metadata"} and "decisions" in value:
        _array_contents(value["decisions"], dict, f"{label}.decisions")
    if table == "wb_research_runs" and field == "metadata":
        # Research readers consume these as source-key sets. Reject malformed
        # deletion state before installation; missing/null remains legacy-empty.
        _optional_member(value, "purged_source_keys", (list, type(None)), label)
        if value.get("purged_source_keys") is not None:
            _array_contents(value["purged_source_keys"], str, f"{label}.purged_source_keys")
    if table.startswith("wb_research_") and field == "record" and isinstance(value, dict):
        if "evidence_urls" in value:
            _array_contents(value["evidence_urls"], str, f"{label}.evidence_urls")
        for member in ("observed_at", "first_observed_at", "last_observed_at"):
            if member in value:
                _timestamp(value[member], f"{label}.{member}", optional=True)
        if table == "wb_research_deferred" and "published_at" in value:
            _timestamp(value["published_at"], f"{label}.published_at", optional=True)
        if table == "wb_research_nodes":
            _optional_member(value, "account_capture", dict, label)
            capture = value.get("account_capture", {})
            _optional_member(capture, "metadata", dict, f"{label}.account_capture")
    if table == "wb_jobs" and field == "result" and value is not None:
        # Batch history and retry controls consume these persisted members.
        # Null summaries/counts and integer strings appeared in older outputs.
        _optional_member(value, "summary", (dict, type(None)), label)
        if isinstance(value.get('summary'), dict):
            _optional_member(value['summary'], 'stop_reason', (str, type(None)), f'{label}.summary')
        if "diagnostics" in value:
            _array_contents(value["diagnostics"], dict, f"{label}.diagnostics")
        for member in ("inserted", "duplicates"):
            if member not in value or value[member] in (None, ""):
                continue
            try:
                int(value[member])
            except (ValueError, TypeError, OverflowError) as exc:
                raise RestoreError(f"恢复文件的 {label}.{member} 数值无效。", "restore_data") from exc
    if table == "wb_reports" and field == "snapshot":
        sources = value if isinstance(value, list) else next(
            (value[key] for key in ("sources", "items", "source_list") if key in value), [])
        _array_contents(sources, dict, label)


def _invalid_constant(value):
    raise ValueError("nonfinite JSON number")


def _finite_float(value):
    number = float(value)
    return number if math.isfinite(number) else _invalid_constant(value)


def _validate_retry_lineage(connection: _Connection, deadline: _Deadline) -> None:
    jobs = {}
    metadata = {}
    for row in connection.execute('SELECT id,kind,params FROM wb_jobs'):
        deadline.check()
        record = dict(row)
        record['params'] = json.loads(record['params'])
        jobs[record['id']] = record
        if RETRY_METADATA not in record['params']:
            continue
        value = record['params'][RETRY_METADATA]
        if (not isinstance(value, dict) or set(value) != {'parent_job_id', 'root_job_id'}
                or any(not isinstance(identity, str) or not identity for identity in value.values())):
            raise RestoreError('恢复文件的重试任务关联数据无效。', 'restore_data')
        metadata[record['id']] = value
    parents = retry_parent_links(jobs.values(),
        (dict(row) for row in connection.execute('SELECT job_id,retry_of_job_id FROM wb_job_batch_jobs')),
        (dict(row) for row in connection.execute('SELECT job_id,message FROM wb_job_events WHERE message LIKE ?',
                                                ('由任务 % 重试创建；原记录保留。',))),
        (dict(row) for row in connection.execute('SELECT job_id,run_id,created_at FROM wb_research_jobs')),
        check=deadline.check)
    for child, parent in parents.items():
        deadline.check()
        if child not in jobs or parent not in jobs or jobs[child]['kind'] != jobs[parent]['kind']:
            raise RestoreError('恢复文件的重试任务原记录不存在或类型不一致。', 'restore_data')
    # Shared interpretation keeps restored single-task and series retries on
    # the same durable branch, including old event/alias-only backups.
    for identity in parents:
        deadline.check()
        try:
            root = retry_root(identity, parents, check=deadline.check)
        except ValueError as exc:
            raise RestoreError('恢复文件的重试任务关联存在循环。', 'restore_data') from exc
        if identity in metadata and metadata[identity]['root_job_id'] != root:
            raise RestoreError('恢复文件的重试任务根记录不一致。', 'restore_data')


def _validate_deferred_data(connection: _Connection, deadline: _Deadline) -> None:
    """Reject component corruption before a restored run reaches the worker."""
    from .research_frontier import validate_deferred_entries

    components = {"header", "items", "relations", "discovery_paths", "evidence_urls"}
    arrays = components - {"header"}
    captures = defaultdict(dict)
    for row in connection.execute("SELECT run_id,account_key,component,ordinal,record FROM wb_research_deferred ORDER BY run_id,account_key,component,ordinal"):
        deadline.check()
        component, ordinal = row["component"], row["ordinal"]
        if component not in components or type(ordinal) is not int or ordinal < 0:
            raise RestoreError("恢复文件的待准入候选组件或顺序无效。", "restore_data")
        value = json.loads(row["record"])
        shape = str if component == "evidence_urls" else dict
        if not isinstance(value, shape):
            raise RestoreError("恢复文件的待准入候选组件内容无效。", "restore_data")
        capture = captures[(row["run_id"], row["account_key"])]
        capture.setdefault(component, []).append((ordinal, value))

    entries_by_run = defaultdict(list)
    for (run_id, key), capture in captures.items():
        deadline.check()
        headers = capture.get("header", [])
        if len(headers) != 1:
            raise RestoreError("恢复文件的待准入候选缺少唯一标识记录。", "restore_data")
        position, header = headers[0]
        if header.get("account_key") != key or any(field in header for field in arrays):
            raise RestoreError("恢复文件的待准入候选标识或组件绑定不一致。", "restore_data")
        entry = dict(header)
        for component in arrays:
            records = capture.get(component, [])
            if [ordinal for ordinal, _ in records] != list(range(len(records))):
                raise RestoreError("恢复文件的待准入候选组件顺序不连续。", "restore_data")
            entry[component] = [value for _, value in records]
        entries_by_run[run_id].append((position, entry))

    for run_id, records in entries_by_run.items():
        deadline.check()
        records.sort(key=lambda row: row[0])
        if [position for position, _ in records] != list(range(len(records))):
            raise RestoreError("恢复文件的待准入候选顺序不连续。", "restore_data")
        try:
            # An admitted pending body may outlive a discarded discovery path.
            # Trust only real run nodes, never the cache's admission_state.
            admitted_keys = {row["account_key"] for row in connection.execute(
                "SELECT account_key FROM wb_research_nodes WHERE run_id=?", (run_id,))}
            validate_deferred_entries([entry for _, entry in records], admitted_keys=admitted_keys)
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            raise RestoreError("恢复文件的待准入候选公开来源或工作记录无效。", "restore_data") from exc
        deadline.check()


def _validate_data(connection: _Connection, deadline: _Deadline) -> None:
    # SQLite's TEXT PRIMARY KEY still admits NULL on ordinary rowid tables,
    # and affinity permits text in INTEGER fields. Store's readers rely on
    # actual text/integer values even when SQLite considers them intact.
    tables = connection.execute("SELECT name FROM sqlite_schema WHERE type='table'").fetchall()
    table_names = {row["name"] for row in tables}
    for table_row in tables:
        table = table_row["name"]
        for column in connection.execute(f"PRAGMA table_info({_quote(table)})"):
            name = _quote(column["name"])
            storage = {"TEXT": "text", "INTEGER": "integer"}.get(column["type"].upper())
            if storage is None:
                continue
            required = bool(column["notnull"] or column["pk"])
            condition = f"typeof({name}) NOT IN ('{storage}'" + ("" if required else ",'null'") + ")"
            if column["pk"] and storage == "text":
                condition += f" OR length({name}) = 0"
            if connection.execute(f"SELECT 1 FROM {_quote(table)} WHERE {condition} LIMIT 1").fetchone():
                raise RestoreError(f"恢复文件的 {table}.{column['name']} 字段值无效。", "restore_data")
            if column["name"].endswith("_at"):
                for row in connection.execute(f"SELECT {name} FROM {_quote(table)}"):
                    deadline.check()
                    _timestamp(row[0], f"{table}.{column['name']}", optional=not required)
    for table, fields in _JSON_FIELDS.items():
        if table in {"wb_research_deferred", "wb_public_source_cache"} and table not in table_names:
            # Schema validation permits exactly the complete prior version;
            # the source stays read-only and this one table is added in stage.
            continue
        columns = ",".join(_quote(field) for field in fields)
        for row in connection.execute(f"SELECT {columns} FROM {_quote(table)}"):
            deadline.check()
            for field, shape in fields.items():
                raw = row[field]
                try:
                    value = None if table == "wb_jobs" and field == "result" and raw is None else json.loads(
                        raw, parse_constant=_invalid_constant, parse_float=_finite_float)
                except (ValueError, TypeError, RecursionError) as exc:
                    raise RestoreError(f"恢复文件的 {table}.{field} 含损坏的 JSON。", "restore_data") from exc
                if not isinstance(value, shape):
                    raise RestoreError(f"恢复文件的 {table}.{field} 数据类型无效。", "restore_data")
                _json_contents(table, field, value)
            deadline.check()
    for row in connection.execute("SELECT start_date,end_date FROM wb_reports"):
        deadline.check()
        try:
            start, end = (date.fromisoformat(value).isoformat() if value else "" for value in row)
            if any(value and len(value) != 10 for value in row) or (start and end and start > end):
                raise ValueError("invalid report period")
        except (ValueError, TypeError) as exc:
            raise RestoreError("恢复文件的报告日期范围无效。", "restore_data") from exc
    enums = {
        ("wb_accounts", "status"): ACCOUNT_STATUSES,
        ("wb_items", "type"): {"news", "social"},
        ("wb_jobs", "kind"): {"collect", "report", "login", "research"},
        ("wb_jobs", "status"): JOB_STATUSES,
        ("wb_reports", "mode"): {"evidence", "ai"},
        ("wb_research_runs", "status"): RUN_STATUSES,
        ("wb_research_nodes", "status"): NODE_STATUSES,
        ("wb_research_edges", "relationship"): RELATIONSHIPS,
        ("wb_research_edges", "basis"): BASES,
        ("wb_research_claims", "status"): {"candidate", "confirmed", "rejected"},
        ("wb_research_claim_reviews", "status"): {"candidate", "confirmed", "rejected"},
        ("wb_research_claims", "basis"): BASES,
    }
    for (table, field), allowed in enums.items():
        if any(row[0] not in allowed for row in connection.execute(
                f"SELECT DISTINCT {_quote(field)} FROM {_quote(table)}")):
            raise RestoreError(f"恢复文件的 {table}.{field} 状态无效。", "restore_data")
    numeric = {
        "wb_jobs": {"progress": (0, 100), "cancel_requested": (0, 1)},
        "wb_items": {"publication_date_unknown": (0, 1)},
        "wb_reports": {"source_count": (0, None)},
        "wb_research_runs": {"revision": (1, None)},
        "wb_research_nodes": {"depth": (0, 20)},
        "wb_drafts": {"version": (1, 100), "revision": (1, None)},
        "wb_draft_revisions": {"revision": (1, None)},
        "wb_job_batches": {"total_records": (1, None)},
        "wb_job_batch_jobs": {"position": (0, None), "target_records": (1, None)},
    }
    for table, fields in numeric.items():
        for field, (lower, upper) in fields.items():
            condition = f"typeof({_quote(field)}) != 'integer' OR {_quote(field)} < ?"
            params = [lower]
            if upper is not None:
                condition += f" OR {_quote(field)} > ?"
                params.append(upper)
            if connection.execute(f"SELECT 1 FROM {_quote(table)} WHERE {condition} LIMIT 1", params).fetchone():
                raise RestoreError(f"恢复文件的 {table}.{field} 数值无效。", "restore_data")
    _validate_retry_lineage(connection, deadline)
    if "wb_research_deferred" in table_names:
        _validate_deferred_data(connection, deadline)


def _sidecars(source: Path) -> None:
    for suffix in ("-wal", "-journal"):
        neighbor = Path(str(source) + suffix)
        if neighbor.exists() and neighbor.stat().st_size:
            raise RestoreError("请选择通过“下载完整备份”生成的独立文件；此数据库仍有运行日志。", "restore_source_active")


def _fingerprint(path: Path, deadline: _Deadline) -> tuple:
    deadline.check()
    before, digest = path.stat(), hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            deadline.check()
            digest.update(chunk)
    after = path.stat()
    fields = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    if fields(before) != fields(after):
        raise RestoreError("恢复源文件在预检时发生变化，请重新选择完整备份。", "restore_source_changed")
    deadline.check()
    return (*fields(after), digest.hexdigest())


def _interrupt_jobs(connection: _Connection, deadline: _Deadline) -> int:
    stamp = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    message = "备份恢复：此前等待或运行中的任务已中断；请显式重试以创建新任务。"
    connection.execute("BEGIN IMMEDIATE")
    try:
        interrupted = 0
        rows = connection.execute("SELECT id FROM wb_jobs WHERE status IN ('queued','running')").fetchall()
        connection.execute("""UPDATE wb_research_runs SET status='interrupted',updated_at=?
            WHERE status IN ('queued','running') OR current_job_id IN
            (SELECT id FROM wb_jobs WHERE status IN ('queued','running'))""", (stamp,))
        for row in rows:
            deadline.check()
            connection.execute("""UPDATE wb_jobs SET status='interrupted',finished_at=?,
                error=?,cancel_requested=1 WHERE id=?""", (stamp, message, row["id"]))
            connection.execute("INSERT INTO wb_job_events(id,job_id,message,level,created_at) VALUES (?,?,?,'warning',?)",
                               (str(uuid4()), row["id"], message, stamp))
            interrupted += 1
        deadline.check()
        connection.commit()
        return interrupted
    except BaseException:
        connection.rollback()
        raise


def prepare_restore(source: Path, output_dir: Path, *, timeout_seconds: float = 60) -> dict:
    """Validate a backup and prepare ``workbench.sqlite3`` plus ``summary.json``.

    ``output_dir`` must be new or empty. All failures remove only this call's
    staged files. The source is read-only, never migrated, checkpointed, or
    written. Only the complete immediately prior schema without the deferred
    table is migrated, in the copied stage; other missing/unknown schemas are
    rejected rather than guessed.
    """
    deadline = _Deadline(timeout_seconds)
    source, output_dir = Path(source).expanduser().resolve(), Path(output_dir).expanduser().absolute()
    created_directory = False
    owned = []
    try:
        if not source.is_file():
            raise RestoreError("恢复源文件不存在或不是普通文件。", "restore_source")
        if output_dir.is_symlink() or (output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir()))):
            raise RestoreError("恢复暂存目录必须是新的空目录。", "restore_output")
        if source.parent == output_dir.resolve():
            raise RestoreError("恢复暂存目录不得包含源文件。", "restore_output")
        _sidecars(source)
        with source.open("rb") as stream:
            if stream.read(16) != b"SQLite format 3\0":
                raise RestoreError("所选文件不是 SQLite 完整备份。", "restore_not_sqlite")
        original = _fingerprint(source, deadline)
        if not output_dir.exists():
            output_dir.mkdir(parents=True, exist_ok=False)
            created_directory = True
        target, summary_path = output_dir / "workbench.sqlite3", output_dir / "summary.json"
        with TemporaryDirectory(prefix=".restore-template-", dir=output_dir) as scratch:
            template_path = Path(scratch) / "template.sqlite3"
            template = _TemplateStore(template_path, deadline)
            template.close()
            with closing(_connect(template_path, deadline, readonly=True)) as connection:
                expected = _schema(connection)
        with closing(_connect(source, deadline, readonly=True)) as input_connection:
            legacy_deferred = _validate_schema(input_connection, expected, allow_legacy_deferred=True)
            _integrity(input_connection)
            _validate_data(input_connection, deadline)
            # Exclusive file creation keeps accidental replacement impossible.
            with target.open("xb"):
                owned.append(target)
            with closing(_connect(target, deadline)) as candidate:
                input_connection.backup(candidate, pages=128,
                    progress=lambda *_: deadline.check(), sleep=0.01)
                candidate.execute("PRAGMA journal_mode = DELETE")
                if legacy_deferred:
                    from .research_frontier import DEFERRED_TABLE_SQL
                    candidate.execute(DEFERRED_TABLE_SQL)
                    from .public_source_cache import CACHE_TABLE_SQL, CACHE_INDEX_SQL
                    candidate.execute(CACHE_TABLE_SQL)
                    candidate.execute(CACHE_INDEX_SQL)
                    candidate.commit()
                _validate_schema(candidate, expected)
                interrupted = _interrupt_jobs(candidate, deadline)
                _integrity(candidate)
                _validate_data(candidate, deadline)
                counts = {name: candidate.execute(f"SELECT COUNT(*) FROM {_quote(table)}").fetchone()[0]
                          for name, table in {"items": "wb_items", "accounts": "wb_accounts", "reports": "wb_reports",
                              "research_runs": "wb_research_runs", "jobs": "wb_jobs", "drafts": "wb_drafts"}.items()}
        _sidecars(source)
        if _fingerprint(source, deadline) != original:
            raise RestoreError("恢复源文件在预检时发生变化，请重新选择完整备份。", "restore_source_changed")
        if any(Path(str(target) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
            raise RestoreError("恢复候选库未形成独立文件。", "restore_integrity")
        fingerprint = _fingerprint(target, deadline)
        summary = {"size_bytes": target.stat().st_size, "sha256": fingerprint[-1], "counts": counts,
                   "interrupted_jobs_count": interrupted, "backup_file_name": source.name}
        with summary_path.open("x", encoding="utf-8") as stream:
            owned.append(summary_path)
            json.dump(summary, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        deadline.check()
        return summary
    except BaseException as exc:
        # Never glob or recursively delete a caller-owned directory. Sidecars
        # can only belong to our exclusively-created candidate file.
        for path in reversed(owned):
            for owned_path in (path, *(Path(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal"))):
                owned_path.unlink(missing_ok=True)
        if created_directory:
            try:
                output_dir.rmdir()
            except OSError:
                pass
        if isinstance(exc, RestoreError):
            raise
        if isinstance(exc, sqlite3.DatabaseError):
            deadline.check()
            raise RestoreError("恢复文件无法通过 SQLite 校验。", "restore_integrity") from exc
        if isinstance(exc, OSError):
            raise RestoreError("恢复预检无法读写文件；请检查文件和空暂存目录。", "restore_io") from exc
        raise
