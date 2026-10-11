"""Durable research checkpoints and evidence graphs shared by SQLite and PG.

Discovery paths describe how research found an account. They do not certify
affiliation. Human account decisions and claim reviews are never overwritten
by a later automatic checkpoint.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from uuid import uuid4
from .research_frontier import ResearchFrontier, DEFERRED_TABLE_SQL, deferred_summary, project_deferred, infer_missing_research_work
from .research_continuation import ResearchContinuation, continuation_state


BASES = {"observed", "reported", "inferred"}
NODE_STATUSES = {"pending", "visited", "skipped", "blocked", "failed", "discovered", "queued", "running", "processing", "complete", "completed"}
RELATIONSHIPS = {"follows", "mentions", "reposts", "replies", "quotes", "shared_url", "co_event",
                 "co_occurrence", "affiliation", "coordination", "profile_link", "discovered_via",
                 "shared_link", "shared_event", "declared_affiliation"}
RUN_STATUSES = {"queued", "running", "succeeded", "partial", "failed", "cancelled", "interrupted"}
SECRET_KEYS = {"password", "passwd", "secret", "api_key", "apikey", "token", "access_token", "auth_token",
               "refresh_token", "authorization", "cookies", "cookie", "sessionid", "csrf_token", "bridge_token", "ct0"}


class DraftConflict(ValueError):
    """The caller edited a stale draft; the saved newer revision is retained."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _public_json(value, *, max_bytes=262144):
    def check(item, depth=0):
        if depth > 20:
            raise ValueError("保存内容嵌套过深")
        if isinstance(item, Mapping):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError("保存内容的字段名必须为字符串")
                normalized = key.lower().replace("-", "_")
                if normalized in SECRET_KEYS or normalized.endswith(("_password", "_secret", "_token", "_api_key")):
                    raise ValueError("研究记录和草稿不能保存账号密码、会话或密钥")
                check(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                check(child, depth + 1)
    check(value)
    encoded = _json(value)
    if len(encoded.encode("utf-8")) > max_bytes:
        raise ValueError("保存内容超过大小限制")
    return encoded


def _array(value, name, *, limit=2000):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"{name} 必须为最多 {limit} 项的数组")
    return value


def _urls(value, *, limit=100000):
    from .store import _url
    return list(dict.fromkeys(_url(entry, "evidence_url") for entry in _array([] if value is None else value, "evidence_urls", limit=limit) if entry))


def _key(value):
    if not isinstance(value, str) or ":" not in value:
        raise ValueError("研究节点需要规范 account_key")
    from .store import Store
    platform, handle = value.split(":", 1)
    return Store.social_account_key(platform, handle)


def _digest(*values):
    return hashlib.sha256(_json(values).encode("utf-8")).hexdigest()


def source_url_key(value):
    """Bind publisher URLs and public-post aliases to one source identity."""
    from .store import _social_url, _url
    try:
        return (_social_url(_url(value)) or "").rstrip("/")
    except (ValueError, TypeError):
        return ""


def _object(row, fields):
    if row is None:
        return None
    value = dict(row)
    for field in fields:
        if field in value:
            value[field] = json.loads(value[field])
    return value


def _merge_capture(previous, current):
    """Keep previous stage findings when a later checkpoint enriches a subset."""
    if isinstance(previous, dict) and isinstance(current, dict):
        return {**previous, **{key: _merge_capture(previous.get(key), value) for key, value in current.items()}}
    if isinstance(previous, list) and isinstance(current, list):
        merged, known = [], set()
        for item in [*previous, *current]:
            marker = _json(item)
            if marker not in known:
                merged.append(item)
                known.add(marker)
        return merged
    return previous if current in (None, "") and previous is not None else current


class ResearchRepository(ResearchFrontier, ResearchContinuation):
    def _freeze_research_source(self, connection, item_id, revision_id):
        """Existing runs keep their first referenced capture on later updates.

        The material revision is already being saved by the normal importer;
        the run stores only its pointer, without duplicating bodies or tables.
        Older runs gain this boundary before their next source update.
        """
        suffix = " FOR UPDATE OF r" if self._postgres else ""
        rows = self._execute(connection, "SELECT r.id,r.metadata FROM wb_research_runs r JOIN wb_research_items ri ON ri.run_id=r.id WHERE ri.item_id=? ORDER BY r.id" + suffix, (item_id,)).fetchall()
        for row in rows:
            metadata = json.loads(row["metadata"])
            versions = metadata.setdefault("research_source_revisions", {})
            if item_id not in versions:
                versions[item_id] = revision_id
                metadata["research_source_snapshot_version"] = 1
                self._execute(connection, "UPDATE wb_research_runs SET metadata=?,revision=revision+1 WHERE id=?",
                              (_public_json(metadata, max_bytes=2097152), row["id"]))

    def _research_source_items(self, connection, rows, metadata):
        versions = metadata.get("research_source_revisions") or {}
        wanted = {row["id"]: versions[row["id"]] for row in rows if row["id"] in versions}
        captures = {}
        identities = list(set(wanted.values()))
        for offset in range(0, len(identities), 200):
            part = identities[offset:offset + 200]
            for revision in self._execute(connection, "SELECT id,item_id,prior_capture FROM wb_item_revisions WHERE id IN (" + ",".join("?" for _ in part) + ")", part).fetchall():
                captures[revision["id"]] = (revision["item_id"], json.loads(revision["prior_capture"]))
        blocked = set(metadata.get("purged_source_keys") or [])
        items = []
        for row in rows:
            if source_url_key(row["url"]) in blocked:
                continue
            captured = dict(row)
            if row["id"] in wanted:
                previous = captures.get(wanted[row["id"]])
                if previous is None or previous[0] != row["id"]:
                    raise ValueError("研究来源版本缺失或不匹配，未用当前正文替换历史研究证据。")
                # Identity/account decisions remain live; source text and its
                # provenance come from the exact pre-update capture.
                captured = {**captured, **previous[1]}
                prior_metadata = json.loads(captured["metadata"])
                live_metadata = json.loads(row["metadata"])
                if "atlas_cleanup" in live_metadata:
                    prior_metadata["atlas_cleanup"] = live_metadata["atlas_cleanup"]
                else:
                    prior_metadata.pop("atlas_cleanup", None)
                captured["metadata"] = _json(prior_metadata)
            items.append(self._item(captured))
        return items

    def _cleanup_source_keys(self, connection, metadata=None):
        # The persisted public metadata has one representation on both stores;
        # parse the narrowed cleanup rows without backend-specific JSON casts.
        # This also preserves the exact status check when a restored item's
        # history contains an older "trashed" decision.
        rows = self._execute(connection, "SELECT url,metadata FROM wb_items WHERE metadata LIKE ?", ('%"atlas_cleanup"%',)).fetchall()
        keys = {source_url_key(row["url"]) for row in rows
                if (json.loads(row["metadata"]).get("atlas_cleanup") or {}).get("status") == "trashed"}
        if metadata is None:
            records = self._execute(connection, "SELECT metadata FROM wb_research_runs WHERE metadata LIKE ?", ('%"purged_source_keys"%',)).fetchall()
            for row in records:
                keys.update(json.loads(row["metadata"]).get("purged_source_keys") or [])
        else:
            keys.update(metadata.get("purged_source_keys") or [])
        return keys - {""}

    @staticmethod
    def _live_research_evidence(row, blocked, *, source_keys=None):
        """Project live support without erasing stored observations/reviews."""
        urls = list(row.get("evidence_urls") or [])
        if row.get("evidence_url"):
            urls.append(row["evidence_url"])
        if row.get("review_evidence_url"):
            urls.append(row["review_evidence_url"])
        if row.get("source_url"):
            urls.append(row["source_url"])
        source_keys = {} if source_keys is None else source_keys
        def key(url):
            if not isinstance(url, str):
                return source_url_key(url)
            if url not in source_keys:
                source_keys[url] = source_url_key(url)
            return source_keys[url]
        retained = list(dict.fromkeys(url for url in urls if not blocked or key(url) not in blocked))
        if urls and not retained:
            return None
        current = {**row, "evidence_urls": retained}
        for field in ("evidence_url", "source_url"):
            if blocked and row.get(field) and key(row[field]) in blocked:
                current.pop(field, None)
        if isinstance(row.get("identity_variants"), list):
            variants = [variant for raw in row["identity_variants"]
                        if (variant := ResearchRepository._live_research_evidence(raw, blocked, source_keys=source_keys)) is not None]
            if len(variants) != len(row["identity_variants"]):
                current["identity_variants"] = variants
                current["cleanup_excluded_identity_variants"] = len(row["identity_variants"]) - len(variants)
        if isinstance(row.get("observations"), list):
            current["observations"] = [observation for observation in row["observations"]
                if not blocked or key(observation.get("evidence_url")) not in blocked]
        if blocked and row.get("evidence_url") and key(row["evidence_url"]) in blocked:
            from .social.common import timestamp
            observed = [entry for entry in current.get("observations", [])
                        if entry.get("evidence_url") in retained and timestamp(entry.get("observed_at"))]
            if observed:
                observed.sort(key=lambda entry: timestamp(entry["observed_at"]))
                current.update(evidence_url=observed[-1]["evidence_url"], observed_at=observed[-1]["observed_at"],
                               first_observed_at=observed[0]["observed_at"], last_observed_at=observed[-1]["observed_at"])
            else:
                current.update(observed_at=None, first_observed_at=None, last_observed_at=None)
        return current

    def _project_research_cleanup(self, connection, run, *, blocked=None):
        if blocked is None:
            blocked = self._cleanup_source_keys(connection, run.get("metadata") or {})
        if not blocked:
            return run
        for node in run.get("nodes") or []:
            if "discovery_paths" in node:
                paths = []
                for path in node["discovery_paths"]:
                    urls = [url for url in path.get("evidence_urls", []) if source_url_key(url) not in blocked]
                    if urls:
                        paths.append({**path, "evidence_urls": urls})
                node["discovery_paths"] = paths
            node["evidence_urls"] = [url for url in node.get("evidence_urls", []) if source_url_key(url) not in blocked]
        for field in ("edges", "claims"):
            run[field] = [retained for row in run.get(field) or [] if (retained := self._live_research_evidence(row, blocked)) is not None]
        analysis = run.get("analysis") or {}
        evidence = analysis.get("evidence") or []
        removed = {row["id"] for row in evidence if source_url_key(row.get("url")) in blocked}
        projected = {**analysis}
        pending = (analysis.get("ai") or {}).get("pending_review")
        pending_removed = set()
        if isinstance(pending, dict):
            expected = pending.get("evidence_hashes") or {}
            sources = pending.get("evidence_source_keys") or {}
            if isinstance(expected, dict):
                pending_removed.update(set(expected) & removed)
            if isinstance(sources, dict):
                pending_removed.update(identity for identity, key in sources.items() if isinstance(key, str) and key in blocked)
            if pending_removed:
                projected["ai"] = {**analysis.get("ai", {}), "pending_review": None,
                    "pending_review_invalidation": {"reason": "source_cleanup", "removed_evidence_count": len(pending_removed),
                        "batch_id_sha256": hashlib.sha256(str(pending.get("batch_id", "")).encode("utf-8")).hexdigest()}}
        changed = bool(removed or pending_removed)
        legacy_unattributed_count = 0
        for field in ("findings", "affiliations"):
            retained = []
            for row in analysis.get(field) or []:
                if isinstance(row, str):
                    # Older checkpoints legitimately stored text without a
                    # source binding. Keep that history in place; never invent
                    # support or turn it into a current relationship claim.
                    retained.append(row)
                    legacy_unattributed_count += 1
                    continue
                current = self._live_research_evidence(row, blocked)
                ids = [identity for identity in row.get("evidence_ids") or [] if identity not in removed]
                if current is not None and (not row.get("evidence_ids") or ids):
                    retained.append({**current, "evidence_ids": ids})
            if field in analysis:
                projected[field] = retained
                changed = changed or retained != analysis[field]
        if changed or legacy_unattributed_count:
            analysis = {**projected, "evidence": [row for row in evidence if row["id"] not in removed]}
            ai = dict(analysis.get("ai") or {})
            if ai:
                available = len(analysis["evidence"])
                batches = [{**batch, "evidence_hashes": {key: value for key, value in (batch.get("evidence_hashes") or {}).items() if key not in removed}}
                           for batch in ai.get("review_batches") or []]
                batches = [batch for batch in batches if batch["evidence_hashes"]]
                reviewed = len({identity for batch in batches for identity in batch["evidence_hashes"]}) if ai.get("review_batches") else min(available, ai.get("input_evidence_count", 0))
                ai.update(available_evidence_count=available, input_evidence_count=reviewed, pending_evidence_count=max(0, available - reviewed),
                          excluded_cleanup_evidence_count=len(removed), source_scope="current_retained_sources")
                if "review_batches" in ai:
                    ai.update(review_batches=batches, completed_batches=len(batches))
                if "rejected_candidates" in ai:
                    ai["rejected_candidates"] = [row for row in ai["rejected_candidates"] if not set(row.get("evidence_ids") or []) & removed]
                analysis["ai"] = ai
            analysis["cleanup_projection"] = {"excluded_evidence_count": len(removed), "scope": "current_retained_sources"}
            if legacy_unattributed_count:
                analysis["cleanup_projection"].update(legacy_unattributed_count=legacy_unattributed_count,
                    legacy_text_scope="historical_without_source_support",
                    message="保留旧版未结构化分析文字；来源未能绑定，不计作当前可引用支持。")
            run["analysis"] = analysis
            run["summary"] = {**run.get("summary", {}), "analysis_available_evidence": ai.get("available_evidence_count", len(analysis["evidence"])),
                              "analysis_reviewed_evidence": ai.get("input_evidence_count", 0), "analysis_pending_evidence": ai.get("pending_evidence_count", 0),
                              "excluded_cleanup_evidence_count": len(removed)}
        return run

    def _initialize_research(self):
        statements = [
            """CREATE TABLE IF NOT EXISTS wb_research_runs (
                id TEXT PRIMARY KEY REFERENCES wb_jobs(id), current_job_id TEXT NOT NULL REFERENCES wb_jobs(id),
                params TEXT NOT NULL, phase TEXT NOT NULL, status TEXT NOT NULL,
                summary TEXT NOT NULL, frontier TEXT NOT NULL, visited TEXT NOT NULL,
                query_plan TEXT NOT NULL, diagnostics TEXT NOT NULL, analysis TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}',
                revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS wb_research_jobs (
                job_id TEXT PRIMARY KEY REFERENCES wb_jobs(id), run_id TEXT NOT NULL REFERENCES wb_research_runs(id),
                created_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS wb_research_nodes (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES wb_research_runs(id),
                account_key TEXT NOT NULL, account_id TEXT NOT NULL REFERENCES wb_accounts(id),
                depth INTEGER NOT NULL, parent_key TEXT, status TEXT NOT NULL, record TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(run_id,account_key))""",
            """CREATE TABLE IF NOT EXISTS wb_research_items (
                run_id TEXT NOT NULL REFERENCES wb_research_runs(id), item_id TEXT NOT NULL REFERENCES wb_items(id),
                created_at TEXT NOT NULL, PRIMARY KEY(run_id,item_id))""",
            """CREATE TABLE IF NOT EXISTS wb_research_edges (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES wb_research_runs(id),
                source_key TEXT NOT NULL, target_key TEXT NOT NULL, relationship TEXT NOT NULL,
                basis TEXT NOT NULL, record TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(run_id,source_key,target_key,relationship,basis))""",
            """CREATE TABLE IF NOT EXISTS wb_research_observations (
                id TEXT PRIMARY KEY, edge_id TEXT NOT NULL REFERENCES wb_research_edges(id),
                record TEXT NOT NULL, created_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS wb_research_claims (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES wb_research_runs(id),
                subject_key TEXT NOT NULL, relationship TEXT NOT NULL, object_label TEXT NOT NULL,
                object_key TEXT, basis TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('candidate','confirmed','rejected')),
                record TEXT NOT NULL, review_note TEXT NOT NULL DEFAULT '', review_evidence_url TEXT NOT NULL DEFAULT '',
                reviewed_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS wb_research_claim_reviews (
                id TEXT PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES wb_research_claims(id),
                status TEXT NOT NULL, note TEXT NOT NULL, evidence_url TEXT NOT NULL, created_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS wb_research_claim_observations (
                id TEXT PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES wb_research_claims(id),
                record TEXT NOT NULL, created_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS wb_drafts (
                key TEXT PRIMARY KEY, version INTEGER NOT NULL, revision INTEGER NOT NULL,
                data TEXT NOT NULL, updated_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS wb_draft_revisions (
                key TEXT PRIMARY KEY, revision INTEGER NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS wb_research_nodes_run ON wb_research_nodes(run_id,depth,status)",
            "CREATE INDEX IF NOT EXISTS wb_research_edges_run ON wb_research_edges(run_id,relationship)",
            "CREATE INDEX IF NOT EXISTS wb_research_claims_run ON wb_research_claims(run_id,status)",
            "CREATE INDEX IF NOT EXISTS wb_research_runs_updated ON wb_research_runs(updated_at,id)",
            DEFERRED_TABLE_SQL,
        ]
        with self._transaction(write=True) as connection:
            for statement in statements:
                self._execute(connection, statement)
            if self._postgres:
                self._execute(connection, "ALTER TABLE wb_research_runs ADD COLUMN IF NOT EXISTS metadata TEXT NOT NULL DEFAULT '{}'")
            elif not any(row["name"] == "metadata" for row in connection.execute("PRAGMA table_info(wb_research_runs)")):
                connection.execute("ALTER TABLE wb_research_runs ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")
            self._execute(connection, """INSERT INTO wb_draft_revisions (key,revision)
                SELECT key,revision FROM wb_drafts WHERE 1=1 ON CONFLICT DO NOTHING""")

    def create_research_run(self, job_id: str, params: dict, run_id: str | None = None) -> dict:
        from .store import _now
        if not isinstance(params, dict):
            raise ValueError("研究参数必须为对象")
        encoded, stamp = _public_json(params), _now()
        with self._transaction(write=True) as connection:
            job = self._execute(connection, "SELECT kind FROM wb_jobs WHERE id=?", (job_id,)).fetchone()
            if job is None or job["kind"] != "research":
                raise ValueError("研究记录需要已保存的 research 任务")
            chosen = run_id or job_id
            existing = self._execute(connection, "SELECT id FROM wb_research_runs WHERE id=?", (chosen,)).fetchone()
            if existing is None:
                if run_id and run_id != job_id:
                    raise LookupError("待继续的研究不存在")
                self._execute(connection, """INSERT INTO wb_research_runs
                    (id,current_job_id,params,phase,status,summary,frontier,visited,query_plan,diagnostics,analysis,revision,created_at,updated_at)
                    VALUES (?,?,?,'queued','queued','{}','[]','[]','{}','[]','{}',1,?,?)""", (chosen, job_id, encoded, stamp, stamp))
            self._attach_research_job(connection, chosen, job_id, stamp)
        return self.get_research_run(chosen)

    def _attach_research_job(self, connection, run_id, job_id, stamp):
        suffix = " FOR UPDATE" if self._postgres else ""
        run = self._execute(connection, "SELECT current_job_id FROM wb_research_runs WHERE id=?" + suffix, (run_id,)).fetchone()
        if run and run["current_job_id"] != job_id:
            current = self._execute(connection, "SELECT status FROM wb_jobs WHERE id=?", (run["current_job_id"],)).fetchone()
            if current and current["status"] in {"queued", "running"}:
                raise ValueError("该研究已有待执行或运行中的任务，请等待完成或先取消")
        linked = self._execute(connection, "SELECT run_id FROM wb_research_jobs WHERE job_id=?", (job_id,)).fetchone()
        if linked and linked["run_id"] != run_id:
            raise ValueError("任务已属于另一个研究")
        self._execute(connection, "INSERT INTO wb_research_jobs (job_id,run_id,created_at) VALUES (?,?,?) ON CONFLICT DO NOTHING", (job_id, run_id, stamp))
        job = self._execute(connection, "SELECT status FROM wb_jobs WHERE id=?", (job_id,)).fetchone()
        if job and job["status"] in {"queued", "running"}:
            self._execute(connection, """UPDATE wb_research_runs SET current_job_id=?,updated_at=?,status=?,
                phase=CASE WHEN ?='queued' THEN 'queued' ELSE phase END WHERE id=?""",
                (job_id, stamp, job["status"], job["status"], run_id))
        else:
            self._execute(connection, "UPDATE wb_research_runs SET current_job_id=?,updated_at=? WHERE id=?", (job_id, stamp, run_id))

    def attach_research_job(self, run_id: str, job_id: str) -> dict:
        from .store import _now
        with self._transaction(write=True) as connection:
            if self._execute(connection, "SELECT id FROM wb_research_runs WHERE id=?", (run_id,)).fetchone() is None:
                raise LookupError("研究不存在")
            job = self._execute(connection, "SELECT kind FROM wb_jobs WHERE id=?", (job_id,)).fetchone()
            if job is None or job["kind"] != "research":
                raise ValueError("研究记录需要已保存的 research 任务")
            self._attach_research_job(connection, run_id, job_id, _now())
        return self.get_research_run(run_id)

    def resume_research_job(self, run_id: str, params: dict) -> dict:
        """Reserve the frontier and enqueue one continuation in the same commit."""
        from .store import _now
        if not isinstance(params, dict):
            raise ValueError("研究参数必须为对象")
        _public_json(params)
        with self._transaction(write=True) as connection:
            resolved = self._run_id(connection, run_id)
            if not resolved:
                raise LookupError("待继续的研究不存在")
            current = self._execute(connection, "SELECT current_job_id FROM wb_research_runs WHERE id=?", (resolved,)).fetchone()
            parents = self._lock_retry_roots(connection, [current["current_job_id"]])
            root, latest = self._retry_branch(connection, current["current_job_id"], parents=parents)
            from .job_retries import RETRY_METADATA, RetryConflict
            from .research_batches import is_retryable_job
            if latest["status"] in {"queued", "running"}:
                raise RetryConflict("该研究已有待执行或运行中的任务，请等待完成或先取消")
            if not is_retryable_job(latest):
                raise ValueError("该研究的后续任务已成功或达到硬预算，不会重复执行。")
            saved_params = {**params, "resume_run_id": resolved,
                            RETRY_METADATA: {"parent_job_id": latest["id"], "root_job_id": root}}
            job = self.create_job("research", saved_params, _connection=connection)
            self._attach_research_job(connection, resolved, job["id"], _now())
        return job

    def _run_id(self, connection, value):
        row = self._execute(connection, "SELECT id FROM wb_research_runs WHERE id=?", (value,)).fetchone()
        if row:
            return row["id"]
        row = self._execute(connection, "SELECT run_id FROM wb_research_jobs WHERE job_id=?", (value,)).fetchone()
        return row["run_id"] if row else None

    def resolve_research_id(self, value):
        """Resolve current and legacy job links without decoding cached bodies."""
        with self._transaction() as connection:
            resolved = self._run_id(connection, value)
            if resolved:
                return resolved
            row = self._execute(connection, "SELECT params,result FROM wb_jobs WHERE id=?", (value,)).fetchone()
            if row:
                params, result = json.loads(row["params"]), json.loads(row["result"] or "{}")
                legacy = result.get("run_id") or params.get("resume_run_id")
                if legacy:
                    return self._run_id(connection, legacy)
        return None

    def _research_node(self, row):
        value = _object(row, ("record",))
        record = value.pop("record")
        value = {**record, **value}
        if "account_name" in value:
            value["name"] = value.pop("account_name")
            value["handle"] = value.pop("account_handle")
            value["platform"] = value.pop("account_platform")
            value["url"] = value.pop("account_url")
            value["account_status"] = value.pop("account_status")
            value["organization"] = value.pop("account_organization")
        if "account_metadata" in value:
            from .account_identity import describe_identity
            from .account_relevance import describe_relevance
            try:
                account_metadata = json.loads(value.pop("account_metadata") or "{}")
            except (TypeError, ValueError):
                account_metadata = {}
            identity_account = {"id": value.get("account_id"),
                "platform": value.get("platform"), "handle": value.get("handle"),
                "url": value.get("url"), "status": value.get("account_status"),
                "evidence_url": value.pop("account_evidence_url", None),
                "metadata": account_metadata}
            value.update(describe_identity(identity_account))
            value['metadata'] = account_metadata if isinstance(account_metadata, dict) else {}
            value.update(describe_relevance(value))
        return value

    def _research_edge(self, row):
        value = _object(row, ("record",))
        record = value.pop("record")
        return {**record, **value}

    def _research_claim(self, row):
        value = _object(row, ("record",))
        record = value.pop("record")
        return {**record, **value, 'review_outcome': 'inconclusive' if value['status'] == 'candidate' and value.get('reviewed_at') else value['status']}

    def _run_record(self, row):
        value = _object(row, ("params", "summary", "frontier", "visited", "query_plan", "diagnostics", "analysis", "metadata"))
        if value:
            value["run_id"] = value["id"]
            value["job_id"] = value["current_job_id"]
            value["summary"] = infer_missing_research_work(value["summary"], value["params"])
        return value

    def get_research_run(self, id: str) -> dict | None:
        from .store import _ITEM_JOIN
        with self._transaction() as connection:
            if self._postgres:
                # PostgreSQL's default READ COMMITTED gives each SELECT a new
                # snapshot. Freeze the run and its evidence before resolving
                # aliases so a concurrent checkpoint cannot mix revisions.
                self._execute(connection, "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            run_id = self._run_id(connection, id)
            if not run_id:
                return None
            run = self._run_record(self._execute(connection, "SELECT * FROM wb_research_runs WHERE id=?", (run_id,)).fetchone())
            run["excluded_account_keys"] = self._excluded_account_keys(connection)
            blocked = self._cleanup_source_keys(connection, run["metadata"])
            run["deferred_frontier"] = project_deferred(self._read_deferred(connection, run_id),
                blocked, run["excluded_account_keys"])
            if run["deferred_frontier"] or "deferred_count" in run["summary"]:
                run["summary"].update(deferred_summary(run["deferred_frontier"]))
            run["job_ids"] = [row["job_id"] for row in self._execute(connection, "SELECT job_id FROM wb_research_jobs WHERE run_id=? ORDER BY created_at,job_id", (run_id,)).fetchall()]
            rows = self._execute(connection, """SELECT n.*,a.name AS account_name,a.handle AS account_handle,
                a.platform AS account_platform,a.url AS account_url,a.status AS account_status,a.organization AS account_organization,
                a.evidence_url AS account_evidence_url,a.metadata AS account_metadata
                FROM wb_research_nodes n JOIN wb_accounts a ON a.id=n.account_id WHERE n.run_id=? ORDER BY n.depth,n.created_at,n.id""", (run_id,)).fetchall()
            run["nodes"] = [self._research_node(row) for row in rows]
            run["accounts"] = [self._account(row) for row in self._execute(connection, "SELECT DISTINCT a.* FROM wb_accounts a JOIN wb_research_nodes n ON n.account_id=a.id WHERE n.run_id=? ORDER BY a.id", (run_id,)).fetchall()]
            captures = {node["account_key"]: node.get("account_capture", {}) for node in run["nodes"]}
            for account in run["accounts"]:
                account["account_key"] = self.social_account_key(account["platform"], account["handle"])
                capture = captures.get(account["account_key"], {})
                if capture.get("profile"):
                    account["profile"] = capture["profile"]
                account["metadata"] = {**capture.get("metadata", {}), **account.get("metadata", {})}
            source_rows = self._execute(connection, _ITEM_JOIN + " JOIN wb_research_items ri ON ri.item_id=i.id WHERE ri.run_id=? AND " + self.item_visibility("i") + " ORDER BY i.created_at,i.id", (run_id,)).fetchall()
            run["items"] = self._research_source_items(connection, source_rows, run["metadata"])
            account_keys = {account["id"]: account["account_key"] for account in run["accounts"]}
            for item in run["items"]:
                if item.get("account_id") in account_keys:
                    item["account_key"] = account_keys[item["account_id"]]
            run["edges"] = [self._research_edge(row) for row in self._execute(connection, "SELECT * FROM wb_research_edges WHERE run_id=? ORDER BY created_at,id", (run_id,)).fetchall()]
            run["relations"] = [{**edge, "relation": edge["relationship"], "evidence_url": edge.get("evidence_url") or next(iter(edge.get("evidence_urls") or []), "")}
                                for edge in run["edges"] if edge["relationship"] in {"follows", "mentions"} and edge["basis"] == "observed"]
            run["claims"] = [claim for row in self._execute(connection, "SELECT * FROM wb_research_claims WHERE run_id=? ORDER BY created_at,id", (run_id,)).fetchall()
                             if self._current_research_claim(claim := self._research_claim(row))]
            self._project_research_cleanup(connection, run, blocked=blocked)
            run["relations"] = [{**edge, "relation": edge["relationship"], "evidence_url": edge.get("evidence_url") or next(iter(edge.get("evidence_urls") or []), "")}
                                for edge in run["edges"] if edge["relationship"] in {"follows", "mentions"} and edge["basis"] == "observed"]
            run["decisions"] = run["analysis"].get("decisions", [])
            state = continuation_state(run)
            run.update(state)
            if run["summary"]:
                run["summary"].update(state)
        return run

    def list_research_runs(self, page: int = 1, page_size: int = 25) -> dict:
        from .store import _pagination
        page, page_size = _pagination(page, page_size)
        with self._transaction() as connection:
            if self._postgres:
                self._execute(connection, "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            total = self._execute(connection, "SELECT COUNT(*) AS total FROM wb_research_runs").fetchone()["total"]
            rows = self._execute(connection, "SELECT r.*,j.status AS job_status FROM wb_research_runs r JOIN wb_jobs j ON j.id=r.current_job_id ORDER BY r.updated_at DESC,r.id DESC LIMIT ? OFFSET ?", (page_size, (page - 1) * page_size)).fetchall()
            values = [self._run_record(row) for row in rows]
            context = {"excluded_account_keys": self._excluded_account_keys(connection),
                       "cleanup_source_keys": self._cleanup_source_keys(connection, {})} if rows else None
            for value, row in zip(values, rows):
                snapshot = self._continuation_snapshot(connection, value["id"], row, context=context)
                snapshot["status"] = value["job_status"]
                state = continuation_state(snapshot)
                value.update(state)
                value["summary"].update(state)
                if snapshot["deferred_frontier"] or "deferred_count" in value["summary"]:
                    value["summary"].update(deferred_summary(snapshot["deferred_frontier"]))
        for value in values:
            for large in ("frontier", "visited", "diagnostics", "analysis"):
                value.pop(large, None)
        return {"items": values, "total": total, "page": page, "page_size": page_size}

    def export_research(self, run_id: str) -> dict:
        """Export every saved record from one read transaction, without UI caps."""
        captured = self.get_research_run(run_id)
        if captured is None:
            raise LookupError("研究不存在")
        collections = {key: captured[key] for key in ("nodes", "edges", "claims", "items", "accounts")}
        run = {key: value for key, value in captured.items() if key not in collections}
        keys = {node["account_key"] for node in collections["nodes"]}
        for node in collections["nodes"]:
            node["parent_in_view"] = not node.get("parent_key") or node["parent_key"] in keys
        return {"run_id": run["id"], "run": run, **collections,
                "total_nodes": len(collections["nodes"]), "total_edges": len(collections["edges"]),
                "total_items": len(collections["items"]), "total_claims": len(collections["claims"]),
                "items_truncated": False, "claims_truncated": False, "truncated": False,
                "limit": None, "phase": run["phase"], "status": run["status"], "summary": run["summary"],
                "filters": {"query": "", "platform": "", "max_depth": None, "relationship": "", "status": ""},
                "coverage": "包含本次研究全部已保存的公开证据；不代表完整关注网络或已确认组织隶属。"}

    def checkpoint_research(self, run_id: str, *, items=None, accounts=None, nodes=None, edges=None, claims=None,
                            summary=None, phase: str = "", status=None, frontier=None, visited=None,
                            query_plan=None, diagnostics=None, analysis=None, metadata=None, deferred_frontier=None) -> dict:
        from .store import _now
        batches = {key: _array([] if value is None else value, key) for key, value in
                   (("items", items), ("accounts", accounts), ("nodes", nodes), ("edges", edges), ("claims", claims))}
        if status is not None and status not in RUN_STATUSES:
            raise ValueError("研究状态无效")
        if not isinstance(phase, str) or len(phase) > 100:
            raise ValueError("研究阶段无效")
        for key, value in (("summary", summary), ("query_plan", query_plan), ("analysis", analysis), ("metadata", metadata)):
            if value is not None and not isinstance(value, dict):
                raise ValueError(f"{key} 必须为对象")
        for key, value in (("frontier", frontier), ("visited", visited), ("diagnostics", diagnostics)):
            if value is not None:
                _array(value, key, limit=10000)
        stamp = _now()
        with self._transaction(write=True) as connection:
            resolved = self._run_id(connection, run_id)
            if not resolved:
                raise LookupError("研究不存在")
            run_id = resolved
            stored_metadata = json.loads(self._execute(connection, "SELECT metadata FROM wb_research_runs WHERE id=?", (run_id,)).fetchone()["metadata"])
            purged = set(stored_metadata.get("purged_source_keys") or [])
            if deferred_frontier is not None:
                from .research_frontier import validate_deferred_entries
                admitted = {row["account_key"] for row in self._execute(connection,
                    "SELECT account_key FROM wb_research_nodes WHERE run_id=?", (run_id,)).fetchall()}
                admitted.update(_key(row.get("account_key")) for row in batches["nodes"])
                validate_deferred_entries(deferred_frontier, admitted_keys=admitted)
                deferred_frontier = project_deferred(deferred_frontier, self._cleanup_source_keys(connection, stored_metadata),
                                                    self._excluded_account_keys(connection))
                summary = {**summary, **deferred_summary(deferred_frontier)} if summary is not None else None
            if purged:
                # Old checkpoint/retry payloads cannot recreate a user's
                # purged material in this run or the ordinary library.
                batches["items"] = [entry for entry in batches["items"] if source_url_key(entry.get("url")) not in purged]
            # Derive ordinary candidate accounts for newly discovered identities;
            # import_social retains every existing human status and organization.
            account_batch = list(batches["accounts"])
            known_keys = set()
            for account in account_batch:
                known_keys.add(self.social_account_key(account.get("platform"), account.get("handle")))
            captured_keys = set(known_keys)
            for node in batches["nodes"]:
                key = _key(node.get("account_key"))
                if key not in known_keys:
                    platform, handle = key.split(":", 1)
                    account_batch.append({"platform": platform, "handle": handle, "name": node.get("name") or handle,
                                          "url": node.get("url") or node.get("profile_url") or "", "metadata": node.get("metadata") or {}})
                    known_keys.add(key)
            # Automated discovery may not assign an organization or trust label.
            account_batch = [{**entry, "organization": "", "status": "candidate"} for entry in account_batch]
            social = [entry for entry in batches["items"] if entry.get("type", "social") == "social"]
            news = [entry for entry in batches["items"] if entry.get("type") == "news"]
            imported = self.import_social(social, account_batch, [], _connection=connection)
            news_result = self.import_items(news, _connection=connection)
            for name in ("inserted", "updated", "duplicates", "rejected"):
                imported[name] += news_result[name]
            imported["ids"].extend(news_result["ids"])
            imported["resolved_ids"].extend(news_result["resolved_ids"])
            imported["issues"].extend(news_result["issues"])
            linked_items = set(imported["resolved_ids"])
            for item_id in linked_items:
                self._execute(connection, "INSERT INTO wb_research_items (run_id,item_id,created_at) VALUES (?,?,?) ON CONFLICT DO NOTHING", (run_id, item_id, stamp))
            record_count = self._execute(connection, "SELECT COUNT(*) AS total FROM wb_research_items WHERE run_id=?", (run_id,)).fetchone()["total"]
            if summary is not None and "record_count" in summary:
                # A source branch can be committed in several slices. Its
                # in-memory total must never get ahead of durable evidence.
                summary = {**summary, "record_count": record_count}
            captures = {self.social_account_key(entry.get("platform"), entry.get("handle")): entry for entry in account_batch
                        if self.social_account_key(entry.get("platform"), entry.get("handle")) in captured_keys}
            node_count = self._checkpoint_nodes(connection, run_id, batches["nodes"], stamp, captures)
            edge_count = self._checkpoint_edges(connection, run_id, batches["edges"], stamp)
            claim_count = self._checkpoint_claims(connection, run_id, batches["claims"], stamp)
            if deferred_frontier is not None:
                # New admitted nodes must actually exist before a cached
                # admission label can be persisted. All writes share rollback.
                self._checkpoint_deferred(connection, run_id, deferred_frontier, stamp)
            prior = self._execute(connection, "SELECT analysis,diagnostics,metadata FROM wb_research_runs WHERE id=?", (run_id,)).fetchone()
            if analysis is not None:
                source_bound_review = (analysis.get("ai") or {}).get("review_version") == 2
                # A source-bound review is a complete current snapshot.
                # Merging its lists resurrects invalidated sources/batches.
                if not source_bound_review:
                    analysis = _merge_capture(json.loads(prior["analysis"]), analysis)
                if purged:
                    # A cancelled/error producer can still hold an older paid
                    # response. Apply durable tombstones after every merge so
                    # it cannot persist that response again. Recoverable trash
                    # decisions must not erase the response needed by restore.
                    analysis = self._project_research_cleanup(connection, {"analysis": analysis}, blocked=purged)["analysis"]
                if source_bound_review:
                    # Preserve superseded claims/observations as history while
                    # marking only the retained source-bound candidates current.
                    self._reconcile_analysis_claims(connection, run_id, analysis, stamp)
            if metadata is not None:
                metadata = _merge_capture(json.loads(prior["metadata"]), metadata)
            if diagnostics is not None:
                diagnostics = _merge_capture(json.loads(prior["diagnostics"]), diagnostics)
            updates = {key: _public_json(value, max_bytes=2097152) for key, value in
                       (("summary", summary), ("frontier", frontier), ("visited", visited), ("query_plan", query_plan),
                        ("diagnostics", diagnostics), ("analysis", analysis), ("metadata", metadata)) if value is not None}
            if phase:
                updates["phase"] = phase
            if status is not None:
                updates["status"] = status
            updates["updated_at"] = stamp
            self._execute(connection, "UPDATE wb_research_runs SET " + ",".join(f"{key}=?" for key in updates) + ",revision=revision+1 WHERE id=?", [*updates.values(), run_id])
        return {**imported, "run_id": run_id, "nodes_saved": node_count, "edges_saved": edge_count,
                "claims_saved": claim_count, "items_linked": len(linked_items), "record_count": record_count}

    def _checkpoint_nodes(self, connection, run_id, nodes, stamp, captures):
        for node in nodes:
            key = _key(node.get("account_key"))
            depth = node.get("depth", 0)
            if isinstance(depth, bool) or not isinstance(depth, int) or not 0 <= depth <= 20:
                raise ValueError("研究节点深度必须在 0 至 20 之间")
            parent = _key(node["parent_key"]) if node.get("parent_key") else None
            if parent == key:
                raise ValueError("研究节点不能以自己作为发现来源")
            status = node.get("status", "pending")
            if status not in NODE_STATUSES:
                raise ValueError("研究节点状态无效")
            account = self._execute(connection, "SELECT account_id FROM wb_account_keys WHERE account_key=?", (key,)).fetchone()
            if account is None:
                raise ValueError("研究节点账号尚未保存")
            record = dict(node)
            score = record.setdefault("score", 0)
            if isinstance(score, bool) or not isinstance(score, (float, int)) or not 0 <= score <= 100:
                raise ValueError("研究节点优先级必须在 0 至 100 之间")
            if key in captures:
                record["account_capture"] = captures[key]
            record["evidence_urls"] = _urls(node.get("evidence_urls"))
            encoded = _public_json(record, max_bytes=2097152)
            previous = self._execute(connection, "SELECT * FROM wb_research_nodes WHERE run_id=? AND account_key=?", (run_id, key)).fetchone()
            if previous:
                prior = json.loads(previous["record"])
                record["account_capture"] = _merge_capture(prior.get("account_capture", {}), record.get("account_capture", {}))
                if previous["depth"] <= depth:
                    depth, parent = previous["depth"], previous["parent_key"]
                    record["path"] = prior.get("path", record.get("path", []))
                    record["discovery_reason"] = prior.get("discovery_reason", record.get("discovery_reason", ""))
                record["evidence_urls"] = list(dict.fromkeys([*prior.get("evidence_urls", []), *record["evidence_urls"]]))
                record["discovery_paths"] = _merge_capture(prior.get("discovery_paths", []), record.get("discovery_paths", []))
                pending = record.get("pending_work") or {}
                if previous["status"] in {"visited", "complete", "completed"} and status in {"pending", "queued", "discovered"} and not any(pending.values()):
                    status = previous["status"]
                self._execute(connection, "UPDATE wb_research_nodes SET depth=?,parent_key=?,status=?,record=?,updated_at=? WHERE id=?",
                              (depth, parent, status, _public_json(record, max_bytes=2097152), stamp, previous["id"]))
            else:
                self._execute(connection, "INSERT INTO wb_research_nodes (id,run_id,account_key,account_id,depth,parent_key,status,record,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                              (_digest(run_id, key), run_id, key, account["account_id"], depth, parent, status, encoded, stamp, stamp))
        return len(nodes)

    def _checkpoint_edges(self, connection, run_id, edges, stamp):
        from .store import _published
        for edge in edges:
            source, target = _key(edge.get("source_key")), _key(edge.get("target_key"))
            relationship = edge.get("relationship") or edge.get("relation")
            basis = edge.get("basis", "observed")
            if source == target or relationship not in RELATIONSHIPS or basis not in BASES:
                raise ValueError("研究关系的端点、类型或证据层级无效")
            for key in (source, target):
                if self._execute(connection, "SELECT id FROM wb_research_nodes WHERE run_id=? AND account_key=?", (run_id, key)).fetchone() is None:
                    raise ValueError("研究关系端点必须属于本次研究节点")
            evidence = _urls(edge.get("evidence_urls") or ([edge["evidence_url"]] if edge.get("evidence_url") else []))
            if not evidence:
                raise ValueError("研究关系必须保留公开证据链接")
            observed = _published(edge.get("observed_at"))
            record = {**edge, "relationship": relationship, "basis": basis, "evidence_urls": evidence, "observed_at": observed}
            encoded = _public_json(record)
            edge_id = _digest(run_id, source, target, relationship, basis)
            previous = self._execute(connection, "SELECT record FROM wb_research_edges WHERE id=?", (edge_id,)).fetchone()
            if previous:
                prior = json.loads(previous["record"])
                if "evidence_id" not in record and prior.get("evidence_id"):
                    record["evidence_id"] = prior["evidence_id"]
                record["evidence_urls"] = list(dict.fromkeys([*prior.get("evidence_urls", []), *evidence]))
                # Analysis summaries cite saved observations without another
                # platform visit. They must not erase a known observation time.
                if observed is None:
                    record["observed_at"] = prior.get("observed_at") or prior.get("first_observed_at")
                record["first_observed_at"] = prior.get("first_observed_at") or prior.get("observed_at") or observed
                self._execute(connection, "UPDATE wb_research_edges SET record=?,updated_at=? WHERE id=?", (_public_json(record), stamp, edge_id))
            else:
                self._execute(connection, "INSERT INTO wb_research_edges (id,run_id,source_key,target_key,relationship,basis,record,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                              (edge_id, run_id, source, target, relationship, basis, encoded, stamp, stamp))
            self._execute(connection, "INSERT INTO wb_research_observations (id,edge_id,record,created_at) VALUES (?,?,?,?) ON CONFLICT DO NOTHING", (_digest(edge_id, encoded), edge_id, encoded, stamp))
            if relationship == "follows" and basis == "observed":
                self.import_social([], [], [{"source_key": source, "target_key": target, "relation": "follows",
                                            "evidence_url": evidence[0], "observed_at": observed}], _connection=connection)
        return len(edges)

    @staticmethod
    def _current_research_claim(claim):
        return bool(claim.get('reviewed_at')) or claim.get("status") != "candidate" or claim.get("analysis_current") is not False

    def _reconcile_analysis_claims(self, connection, run_id, analysis, stamp):
        active = {(row.get("account_key"), str(row.get("organization") or "").strip(),
                   "reported" if row.get("status") == "self_declared" else "inferred")
                  for row in analysis.get("affiliations") or []}
        rows = self._execute(connection, "SELECT * FROM wb_research_claims WHERE run_id=? AND status='candidate'", (run_id,)).fetchall()
        for row in rows:
            record = json.loads(row["record"])
            if row['reviewed_at'] or row["relationship"] != "affiliation" or record.get("method") not in {"configured_ai", "explicit_profile_statement"}:
                continue
            current = (row["subject_key"], row["object_label"], row["basis"]) in active
            if record.get("analysis_current", True) != current:
                record["analysis_current"] = current
                record["analysis_snapshot_updated_at"] = stamp
                self._execute(connection, "UPDATE wb_research_claims SET record=?,updated_at=? WHERE id=?", (_public_json(record), stamp, row["id"]))

    def _checkpoint_claims(self, connection, run_id, claims, stamp):
        for claim in claims:
            subject = _key(claim.get("subject_key") or claim.get("account_key"))
            relationship = claim.get("relationship", "affiliation")
            target = _key(claim["object_key"]) if claim.get("object_key") else None
            label = claim.get("object_label") or claim.get("organization") or claim.get("target_label") or target or ""
            basis = claim.get("basis", "inferred")
            if relationship not in RELATIONSHIPS | {"association"} or basis not in BASES or not isinstance(label, str) or not label.strip() or len(label) > 500:
                raise ValueError("关联主张的类型、对象或证据层级无效")
            if self._execute(connection, "SELECT id FROM wb_research_nodes WHERE run_id=? AND account_key=?", (run_id, subject)).fetchone() is None:
                raise ValueError("关联主张的账号必须属于本次研究")
            # Consolidated identities can retain the union from many reviewed
            # batches. Keep its complete URL provenance within the full-source
            # ceiling; the record's JSON budget still fails explicitly.
            evidence = _urls(claim.get("evidence_urls"), limit=10240)
            claim_id = _digest(run_id, subject, relationship, target, label.strip(), basis)
            record = {**claim, "evidence_urls": evidence, "status": "candidate", "analysis_current": True}
            captured = _public_json(record)
            suffix = " FOR UPDATE" if self._postgres else ""
            previous = self._execute(connection, "SELECT record,status,reviewed_at FROM wb_research_claims WHERE id=?" + suffix, (claim_id,)).fetchone()
            if previous:
                prior = json.loads(previous["record"])
                if previous["status"] in {"confirmed", "rejected"} or previous['reviewed_at']:
                    # The review applies to this precise recorded content.
                    # Retain new automatic captures separately for auditing.
                    record = dict(prior)
                record["evidence_urls"] = list(dict.fromkeys([*prior.get("evidence_urls", []), *evidence]))
                self._execute(connection, "UPDATE wb_research_claims SET record=?,updated_at=? WHERE id=?", (_public_json(record), stamp, claim_id))
            else:
                self._execute(connection, """INSERT INTO wb_research_claims
                    (id,run_id,subject_key,relationship,object_label,object_key,basis,status,record,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,'candidate',?,?,?)""", (claim_id, run_id, subject, relationship, label.strip(), target, basis, _public_json(record), stamp, stamp))
            self._execute(connection, "INSERT INTO wb_research_claim_observations (id,claim_id,record,created_at) VALUES (?,?,?,?) ON CONFLICT DO NOTHING", (_digest(claim_id, captured), claim_id, captured, stamp))
        return len(claims)

    def review_research_claim(self, run_id: str, claim_id: str, status: str, note: str = "", evidence_url: str = "", *, _connection=None) -> dict:
        from .store import _now, _url
        from contextlib import nullcontext
        if status not in {"confirmed", "rejected", "inconclusive"}:
            raise ValueError("复核状态必须为 confirmed、rejected 或 inconclusive")
        if not isinstance(note, str) or len(note) > 4000:
            raise ValueError("人工复核备注无效")
        evidence_url, stamp = _url(evidence_url, "evidence_url"), _now()
        if status == "confirmed" and not evidence_url:
            raise ValueError("确认关联主张必须提供公开 evidence_url")
        with (nullcontext(_connection) if _connection is not None else self._transaction(write=True)) as connection:
            resolved = self._run_id(connection, run_id)
            row = self._execute(connection, "SELECT * FROM wb_research_claims WHERE run_id=? AND id=?", (resolved, claim_id)).fetchone()
            if row is None:
                raise LookupError("关联主张不存在")
            self._execute(connection, "UPDATE wb_research_claims SET status=?,review_note=?,review_evidence_url=?,reviewed_at=?,updated_at=? WHERE id=?", ('candidate' if status == 'inconclusive' else status, note.strip(), evidence_url, stamp, stamp, claim_id))
            self._execute(connection, "INSERT INTO wb_research_claim_reviews (id,claim_id,status,note,evidence_url,created_at) VALUES (?,?,?,?,?,?)", (str(uuid4()), claim_id, status, note.strip(), evidence_url, stamp))
            saved = self._research_claim(self._execute(connection, "SELECT * FROM wb_research_claims WHERE id=?", (claim_id,)).fetchone())
        return saved

    def research_graph(self, run_id: str, query: str = "", platform: str = "", max_depth=None,
                       relationship: str = "", status: str = "", limit: int = 300) -> dict:
        from .store import _like, _ITEM_JOIN
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("图谱 limit 必须在 1 至 1000 之间")
        if max_depth is not None and (isinstance(max_depth, bool) or not isinstance(max_depth, int) or not 0 <= max_depth <= 20):
            raise ValueError("图谱深度必须在 0 至 20 之间")
        if platform and platform not in {"twitter", "instagram"}:
            raise ValueError("图谱平台无效")
        if relationship and relationship not in RELATIONSHIPS:
            raise ValueError("图谱关系类型无效")
        if status and status not in NODE_STATUSES | {"candidate", "verified", "excluded"}:
            raise ValueError("图谱节点状态无效")
        if not isinstance(query, str) or len(query) > 300:
            raise ValueError("图谱检索词无效")
        with self._transaction() as connection:
            if self._postgres:
                self._execute(connection, "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            resolved = self._run_id(connection, run_id)
            if not resolved:
                raise LookupError("研究不存在")
            clauses, params = ["n.run_id=?"], [resolved]
            if query:
                clauses.append("(LOWER(a.name) LIKE LOWER(?) ESCAPE '!' OR LOWER(a.handle) LIKE LOWER(?) ESCAPE '!')")
                params.extend([_like(query)] * 2)
            if platform:
                clauses.append("a.platform=?")
                params.append(platform)
            if max_depth is not None:
                clauses.append("n.depth<=?")
                params.append(max_depth)
            if status:
                clauses.append("a.status=?" if status in {"candidate", "verified", "excluded"} else "n.status=?")
                params.append(status)
            if relationship:
                clauses.append("EXISTS (SELECT 1 FROM wb_research_edges re WHERE re.run_id=n.run_id AND re.relationship=? AND (re.source_key=n.account_key OR re.target_key=n.account_key))")
                params.append(relationship)
            base = " FROM wb_research_nodes n JOIN wb_accounts a ON a.id=n.account_id WHERE " + " AND ".join(clauses)
            total = self._execute(connection, "SELECT COUNT(*) AS total" + base, params).fetchone()["total"]
            rows = self._execute(connection, "SELECT n.*,a.name AS account_name,a.handle AS account_handle,a.platform AS account_platform,a.url AS account_url,a.status AS account_status,a.organization AS account_organization,a.evidence_url AS account_evidence_url,a.metadata AS account_metadata" + base + " ORDER BY n.depth,n.created_at,n.id LIMIT ?", [*params, limit]).fetchall()
            nodes = [self._research_node(row) for row in rows]
            keys = [node["account_key"] for node in nodes]
            edges, claims, edge_total, claim_total = [], [], 0, 0
            if keys:
                slots = ",".join("?" for _ in keys)
                where = f"run_id=? AND source_key IN ({slots}) AND target_key IN ({slots})"
                values = [resolved, *keys, *keys]
                if relationship:
                    where += " AND relationship=?"
                    values.append(relationship)
                edge_total = self._execute(connection, "SELECT COUNT(*) AS total FROM wb_research_edges WHERE " + where, values).fetchone()["total"]
                edges = [self._research_edge(row) for row in self._execute(connection, "SELECT * FROM wb_research_edges WHERE " + where + " ORDER BY created_at,id", values).fetchall()]
                current_claims = [claim for row in self._execute(connection, f"SELECT * FROM wb_research_claims WHERE run_id=? AND subject_key IN ({slots}) ORDER BY created_at,id", [resolved, *keys]).fetchall()
                                  if self._current_research_claim(claim := self._research_claim(row))]
                claim_total, claims = len(current_claims), current_claims[:limit * 2]
            selected = set(keys)
            for node in nodes:
                node["parent_in_view"] = not node.get("parent_key") or node["parent_key"] in selected
            run = self._run_record(self._execute(connection, "SELECT * FROM wb_research_runs WHERE id=?", (resolved,)).fetchone())
            snapshot = self._continuation_snapshot(connection, resolved, run)
            state = continuation_state(snapshot)
            run.update(state)
            if run["summary"]:
                run["summary"].update(state)
            waiting = snapshot["deferred_frontier"]
            if waiting or "deferred_count" in run["summary"]:
                run["summary"].update(deferred_summary(waiting))
            accounts = [self._account(row) for row in self._execute(connection, "SELECT DISTINCT a.* FROM wb_accounts a JOIN wb_research_nodes n ON n.account_id=a.id WHERE n.run_id=? ORDER BY a.id LIMIT ?", (resolved, limit)).fetchall()]
            source_rows = self._execute(connection, _ITEM_JOIN + " JOIN wb_research_items ri ON ri.item_id=i.id WHERE ri.run_id=? AND " + self.item_visibility("i") + " ORDER BY i.created_at,i.id LIMIT ?", (resolved, limit * 4)).fetchall()
            items = self._research_source_items(connection, source_rows, run["metadata"])
            total_items = self._execute(connection, "SELECT COUNT(*) AS total FROM wb_research_items ri JOIN wb_items i ON i.id=ri.item_id WHERE ri.run_id=? AND " + self.item_visibility("i"), (resolved,)).fetchone()["total"]
            current = self._project_research_cleanup(connection, {**run, "nodes": nodes, "edges": edges, "claims": current_claims if keys else []},
                                                     blocked=snapshot["_cleanup_source_keys"])
            run = {key: value for key, value in current.items() if key not in {"nodes", "edges", "claims"}}
            edges, current_claims = current["edges"], current["claims"]
            edge_total, claim_total = len(edges), len(current_claims)
            edges = edges[:limit * 4]
            claims = current_claims[:limit * 2]
        from .graph_insights import graph_insights, explicit_root_keys
        from .account_relevance import describe_relevance
        roots = explicit_root_keys(run.get('params') or {})
        for node in nodes:
            node.update(describe_relevance(node, node['account_key'] in roots))
        return {"run_id": resolved, "run": run, "nodes": nodes, "edges": edges, "claims": claims, "items": items, "accounts": accounts, "total_nodes": total,
                "insights": graph_insights(nodes, edges, explicit_roots=roots),
                "total_edges": edge_total, "total_items": total_items, "items_truncated": total_items > len(items),
                "total_claims": claim_total, "claims_truncated": claim_total > len(claims),
                "truncated": total > len(nodes) or edge_total > len(edges) or total_items > len(items) or claim_total > len(claims),
                "limit": limit, "phase": run["phase"], "status": run["status"], "summary": run["summary"],
                "filters": {"query": query, "platform": platform, "max_depth": max_depth, "relationship": relationship, "status": status},
                "coverage": "仅展示本次实际发现并保存的公开证据，不代表完整关注网络或已确认组织隶属。"}

    def get_draft(self, key: str = "collection") -> dict | None:
        if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", key):
            raise ValueError("草稿名称无效")
        with self._transaction() as connection:
            return _object(self._execute(connection, "SELECT * FROM wb_drafts WHERE key=?", (key,)).fetchone(), ("data",))

    def save_draft(self, key: str, data: dict, expected_revision: int | None = None, version: int = 1) -> dict:
        from .store import _now
        if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", key) or not isinstance(data, dict):
            raise ValueError("草稿名称或内容无效")
        if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= 100:
            raise ValueError("草稿版本无效")
        if expected_revision is not None and (isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0):
            raise ValueError("草稿 revision 无效")
        encoded = _public_json(data)
        with self._transaction(write=True) as connection:
            # Lock the singleton on PG; ON CONFLICT protects a concurrent first save.
            suffix = " FOR UPDATE" if self._postgres else ""
            previous = self._execute(connection, "SELECT * FROM wb_drafts WHERE key=?" + suffix, (key,)).fetchone()
            revision = previous["revision"] if previous else 0
            if expected_revision is not None and expected_revision != revision:
                raise DraftConflict("草稿已由另一个窗口更新，已保留最新保存内容")
            counter = revision
            if previous is None:
                history = self._execute(connection, "SELECT revision FROM wb_draft_revisions WHERE key=?" + suffix, (key,)).fetchone()
                counter = history["revision"] if history else 0
            next_revision = counter + 1
            if previous:
                self._execute(connection, "UPDATE wb_drafts SET version=?,revision=?,data=?,updated_at=? WHERE key=?", (version, next_revision, encoded, _now(), key))
            else:
                cursor = self._execute(connection, "INSERT INTO wb_drafts (key,version,revision,data,updated_at) VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING", (key, version, next_revision, encoded, _now()))
                if not cursor.rowcount:
                    raise DraftConflict("草稿已由另一个窗口更新，已保留最新保存内容")
            self._execute(connection, """INSERT INTO wb_draft_revisions (key,revision) VALUES (?,?)
                ON CONFLICT(key) DO UPDATE SET revision=EXCLUDED.revision""", (key, next_revision))
            saved = _object(self._execute(connection, "SELECT * FROM wb_drafts WHERE key=?", (key,)).fetchone(), ("data",))
        return saved

    def clear_draft(self, key: str = "collection", expected_revision: int | None = None) -> bool:
        self.get_draft(key)  # Validate the public key before opening a write transaction.
        if expected_revision is not None and (isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0):
            raise ValueError("草稿 revision 无效")
        with self._transaction(write=True) as connection:
            suffix = " FOR UPDATE" if self._postgres else ""
            row = self._execute(connection, "SELECT revision FROM wb_drafts WHERE key=?" + suffix, (key,)).fetchone()
            if expected_revision is not None and expected_revision != (row["revision"] if row else 0):
                raise DraftConflict("草稿已由另一个窗口更新，不能清除新内容")
            return bool(self._execute(connection, "DELETE FROM wb_drafts WHERE key=?", (key,)).rowcount)
