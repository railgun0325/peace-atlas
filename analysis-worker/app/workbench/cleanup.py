"""Previewed material cleanup with a durable recycle bin and explicit purge."""
from __future__ import annotations

import hashlib
import json


class CleanupRepository:
    @staticmethod
    def _analysis_source_keys(analysis):
        """Read supported provenance fields, never URLs from free-form text."""
        from .research_repository import source_url_key
        keys = set()

        def source(url):
            if isinstance(url, str):
                keys.add(source_url_key(url))

        def support(row):
            if not isinstance(row, dict):
                return
            urls = row.get("evidence_urls")
            if isinstance(urls, list):
                for url in urls:
                    source(url)
            for field in ("evidence_url", "review_evidence_url", "source_url"):
                source(row.get(field))
            for variant in row.get("identity_variants") or []:
                support(variant)
            for observation in row.get("observations") or []:
                if isinstance(observation, dict):
                    source(observation.get("evidence_url"))

        for evidence in analysis.get("evidence") or []:
            if isinstance(evidence, dict):
                source(evidence.get("url"))
        for field in ("findings", "affiliations"):
            for row in analysis.get(field) or []:
                support(row)
        return keys - {""}

    def _remember_purged_sources(self, connection, rows):
        """Keep source tombstones in existing run metadata, not source bodies."""
        from .research_repository import _public_json, source_url_key
        keys = {source_url_key(row["url"]) for row in rows} - {""}
        item_ids = [row["id"] for row in rows]

        def pending_removed_sources(analysis):
            pending = (analysis.get("ai") or {}).get("pending_review")
            removed = set()
            if isinstance(pending, dict):
                expected, sources = pending.get("evidence_hashes") or {}, pending.get("evidence_source_keys") or {}
                if isinstance(sources, dict):
                    removed.update(identity for identity, key in sources.items() if isinstance(key, str) and key in keys)
                # Older pending batches may have only the catalogue mapping.
                # Bind those URLs to this pending batch's evidence IDs.
                if isinstance(expected, dict):
                    removed.update(evidence["id"] for evidence in analysis.get("evidence") or []
                        if isinstance(evidence, dict) and isinstance(evidence.get("id"), str)
                        and evidence["id"] in expected and source_url_key(evidence.get("url")) in keys)
            return pending, removed

        run_ids = {row["run_id"] for row in self._execute(connection, "SELECT DISTINCT run_id FROM wb_research_items WHERE item_id IN (" + ",".join("?" for _ in item_ids) + ")", item_ids).fetchall()}
        # An edge can cite an ordinary library source captured in another run.
        # Preserve its observations, but bind removal to every dependent run.
        for table in ("wb_research_edges", "wb_research_claims"):
            for row in self._execute(connection, "SELECT run_id,record FROM " + table).fetchall():
                record = json.loads(row["record"])
                urls = [*(record.get("evidence_urls") or []), record.get("review_evidence_url", "")]
                if any(source_url_key(url) in keys for url in urls):
                    run_ids.add(row["run_id"])
        # Pending observations can reference library material before any
        # admitted node or research-item association exists.
        for row in self._execute(connection, "SELECT run_id,component,record FROM wb_research_deferred").fetchall():
            record = json.loads(row["record"])
            urls = [record] if row["component"] == "evidence_urls" else [
                *(record.get("evidence_urls") or []), record.get("evidence_url", ""),
                record.get("url", "") if row["component"] == "items" else ""]
            if any(source_url_key(url) in keys for url in urls):
                run_ids.add(row["run_id"])
        # Analysis can cite ordinary library sources without any research-item,
        # edge, claim or deferred association. Include completed catalogues and
        # source-bound findings as well as profile-only pending source-key maps.
        pending_filter = "jsonb_typeof((analysis::jsonb)->'ai'->'pending_review')='object'" if self._postgres else "json_type(analysis,'$.ai.pending_review')='object'"
        catalogue_filters = [f"jsonb_typeof((analysis::jsonb)->'{field}')='array'" if self._postgres else
                             f"json_type(analysis,'$.{field}')='array'" for field in ("evidence", "findings", "affiliations")]
        for row in self._execute(connection, "SELECT id,analysis FROM wb_research_runs WHERE " + " OR ".join([pending_filter, *catalogue_filters])).fetchall():
            analysis = json.loads(row["analysis"])
            if self._analysis_source_keys(analysis) & keys or pending_removed_sources(analysis)[1]:
                run_ids.add(row["id"])
        for run_id in run_ids:
            suffix = " FOR UPDATE" if self._postgres else ""
            row = self._execute(connection, "SELECT metadata,analysis FROM wb_research_runs WHERE id=?" + suffix, (run_id,)).fetchone()
            metadata = json.loads(row["metadata"])
            metadata["purged_source_keys"] = sorted(set(metadata.get("purged_source_keys") or []) | keys)
            analysis = json.loads(row["analysis"])
            pending, pending_removed = pending_removed_sources(analysis)
            if pending_removed:
                analysis["ai"] = {**analysis.get("ai", {}), "pending_review": None,
                    "pending_review_invalidation": {"reason": "source_purged", "removed_evidence_count": len(pending_removed),
                        "batch_id_sha256": hashlib.sha256(str(pending.get("batch_id", "")).encode("utf-8")).hexdigest()}}
            if pending_removed:
                self._execute(connection, "UPDATE wb_research_runs SET metadata=?,analysis=?,revision=revision+1 WHERE id=?",
                    (_public_json(metadata, max_bytes=2097152), _public_json(analysis, max_bytes=2097152), run_id))
            else:
                self._execute(connection, "UPDATE wb_research_runs SET metadata=?,revision=revision+1 WHERE id=?",
                              (_public_json(metadata, max_bytes=2097152), run_id))
            from .research_frontier import project_deferred
            from .store import _now
            self._checkpoint_deferred(connection, run_id,
                project_deferred(self._read_deferred(connection, run_id), set(metadata["purged_source_keys"])), _now())

    def item_visibility(self, alias="", trash=False):
        column = (alias + "." if alias else "") + "metadata"
        field = f"({column}::jsonb)->'atlas_cleanup'->>'status'" if self._postgres else f"json_extract({column}, '$.atlas_cleanup.status')"
        return f"COALESCE({field}, '') {'=' if trash else '!='} 'trashed'"

    def _cleanup_rows(self, connection, ids, action):
        if action not in {"trash", "restore", "purge"} or not isinstance(ids, list) or not 1 <= len(ids) <= 200 or any(not isinstance(i, str) for i in ids):
            raise ValueError("清理动作需选择 1 至 200 条资料。")
        ids = sorted(set(ids))
        suffix = " FOR UPDATE" if self._postgres else ""
        rows = self._execute(connection, "SELECT * FROM wb_items WHERE id IN (" + ",".join("?" for _ in ids) + ") ORDER BY id" + suffix, ids).fetchall()
        if len(rows) != len(ids):
            raise LookupError("部分资料已不存在，请重新预览。")
        for row in rows:
            flag = json.loads(row["metadata"]).get("atlas_cleanup", {})
            trashed = isinstance(flag, dict) and flag.get("status") == "trashed"
            if action in {"restore", "purge"} and not trashed:
                raise ValueError("恢复与彻底删除只能用于回收站资料。")
            if action == "trash" and trashed:
                raise ValueError("部分资料已经在回收站，请重新选择。")
        return rows

    @staticmethod
    def _cleanup_digest(rows, action):
        body = json.dumps([action, [dict(row) for row in rows]], ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def cleanup_preview(self, ids, action):
        with self._transaction() as connection:
            rows = self._cleanup_rows(connection, ids, action)
            return {"action": action, "count": len(rows), "preview_digest": self._cleanup_digest(rows, action),
                    "items": [{key: row[key] for key in ("id", "title", "url", "type", "source_name")} for row in rows],
                    "effect": "移入回收站后不参与资料检索、新报告、活动地图和原帖提及统计，可恢复。彻底删除只删除所选源记录及其版本与研究关联；已生成报告中的独立快照保留。"}

    def cleanup_apply(self, ids, action, preview_digest, reason="", confirm_purge=False):
        from .store import _now
        from .research_repository import _public_json
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise ValueError("请记录清理或恢复原因，最多 500 字。")
        if action == "purge" and confirm_purge is not True:
            raise ValueError("彻底删除需要明确确认不可恢复。")
        with self._transaction(write=True) as connection:
            rows = self._cleanup_rows(connection, ids, action)
            if preview_digest != self._cleanup_digest(rows, action):
                raise ValueError("资料在预览后发生变化，请重新核对清理范围。")
            active = self._execute(connection, "SELECT kind,params FROM wb_jobs WHERE status IN ('queued','running') AND kind IN ('collect','research')").fetchall()
            if any(row["kind"] != "collect" or json.loads(row["params"]).get("_account_verification") is not True for row in active):
                raise ValueError("采集或研究正在执行，请等待完成或先停止任务后再清理，避免同时重建资料。")
            if action == "purge":
                self._remember_purged_sources(connection, rows)
            if action in {"trash", "purge"}:
                self.clear_public_source_cache([row['url'] for row in rows], _connection=connection)
            for row in rows:
                if action == "purge":
                    self._execute(connection, "DELETE FROM wb_research_items WHERE item_id=?", (row["id"],))
                    self._execute(connection, "DELETE FROM wb_item_revisions WHERE item_id=?", (row["id"],))
                    self._execute(connection, "DELETE FROM wb_items WHERE id=?", (row["id"],))
                else:
                    metadata = json.loads(row["metadata"])
                    old = metadata.get("atlas_cleanup", {})
                    history = old.get("history", []) if isinstance(old, dict) else []
                    if not isinstance(history, list):
                        history = []
                    decision = {"status": "trashed" if action == "trash" else "active", "reason": reason.strip(), "at": _now()}
                    metadata["atlas_cleanup"] = {**decision, "history": [*history[-19:], decision]}
                    self._execute(connection, "UPDATE wb_items SET metadata=? WHERE id=?", (_public_json(metadata), row["id"]))
        return {"action": action, "count": len(rows)}
