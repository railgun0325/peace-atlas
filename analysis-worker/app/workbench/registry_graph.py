"""An evidence graph across saved runs and source records; no inferred edges."""
from __future__ import annotations

import json
from .relationship_analysis import mention_handles
from .research_repository import source_url_key


class RegistryRepository:
    def apply_account_checks(self, checks):
        from .store import _json, _now
        from .research_repository import _public_json
        from .account_verification import input_fingerprint, source_candidates, registered_sites
        applied = []
        with self._transaction(write=True) as connection:
            sites = registered_sites(self, connection)
            for check in checks:
                suffix = " FOR UPDATE" if self._postgres else ""
                account = self._account(self._execute(connection, "SELECT * FROM wb_accounts WHERE id=?" + suffix, (check["account_id"],)).fetchone())
                if not account:
                    continue
                key = f"{account['platform']}:{account['handle'].lstrip('@').lower()}"
                changed = (key, account["url"], account["status"], account["evidence_url"]) != (check["account_key"], check["account_url"], check["previous_status"], check["previous_evidence_url"])
                if check.get("input_sha256"):
                    current = source_candidates(account, check.get("requested_source_url", ""), sites)
                    changed = changed or input_fingerprint(account, current) != check["input_sha256"]
                if changed:
                    applied.append({**check, "verified": False, "outcome": "changed_during_check", "message": "检查期间账号被编辑，未覆盖更新后的人工决定。"})
                    continue
                if account["status"] != "candidate":
                    applied.append(check)
                    continue
                metadata = dict(account.get("metadata") or {})
                history = metadata.get("public_verification_history", [])
                history = history if isinstance(history, list) else []
                metadata["public_verification_history"] = [*history[-7:], check]
                metadata["public_verification"] = check
                schedule = metadata.get("verification_schedule")
                if isinstance(schedule, dict) and schedule.get("input_sha256") == check.get("input_sha256"):
                    metadata["verification_schedule"] = {**schedule, "state": "checked", "checked_at": check["checked_at"],
                                                         "outcome": check["outcome"]}
                identity = metadata.get("account_identity") or {}
                kind = check.get("registered_account_type", "unknown")
                if check["verified"] and kind in {"organization", "individual"} and not (isinstance(identity, dict) and identity.get("manual_locked") is True):
                    metadata["account_identity"] = {"version": 1, "type": kind, "basis": "registered_source_backlink",
                        "confidence_scope": "已登记可信来源的类型与准确主页回链；不等于组织隶属、观点可信度或活动立场。",
                        "evidence_urls": [check["evidence_url"]], "manual_locked": False,
                        "reviewed_at": check["checked_at"], "note": ""}
                self._execute(connection, "UPDATE wb_accounts SET status=?, evidence_url=?, metadata=?, updated_at=? WHERE id=?",
                              ("verified" if check["verified"] else "candidate", check.get("evidence_url", "") if check["verified"] else account["evidence_url"], _public_json(metadata), _now(), account["id"]))
                applied.append(check)
        return {"checked": len(applied), "verified": sum(row["verified"] for row in applied),
                "pending": sum(not row["verified"] and row["outcome"] != "retained" for row in applied),
                "diagnostics": [{"source": row["account_key"], "ok": row["outcome"] not in {"source_unavailable", "source_redirected", "identity_mismatch"}, **row} for row in applied]}

    def registry_graph(self):
        from .account_identity import describe_identity
        from .account_relevance import describe_relevance
        from .graph_insights import graph_insights
        accounts = self.list_accounts()
        nodes = {f"{a['platform']}:{a['handle'].lstrip('@').lower()}":
                 {**a, **describe_identity(a), **describe_relevance(a), "account_key": f"{a['platform']}:{a['handle'].lstrip('@').lower()}", "account_id": a["id"], "account_status": a["status"], "depth": 0,
                  "evidence_urls": [a["evidence_url"]] if a["evidence_url"] else []} for a in accounts if a["platform"] in {"twitter", "instagram"} and a["handle"]}
        ids = {a["account_id"]: key for key, a in nodes.items()}
        edges = {}
        scan_sources = {}

        def scan(name, total, rows, limit):
            scan_sources[name] = {"total_rows": total, "scanned_rows": len(rows),
                                  "omitted_rows": max(0, total - len(rows)), "limit": limit}

        def add(source, target, relation, urls, basis="observed", **extra):
            urls = list(dict.fromkeys(url for url in urls if isinstance(url, str) and url.startswith("https://")))
            if source == target or source not in nodes or target not in nodes or not urls or basis not in {"observed", "reported"}:
                return
            key = (source, target, relation, basis)
            if key in edges:
                edges[key]["evidence_urls"] = list(dict.fromkeys([*edges[key]["evidence_urls"], *urls]))[:30]
            else:
                edges[key] = {"source_key": source, "target_key": target, "relationship": relation, "basis": basis, "evidence_urls": urls, **extra}

        with self._transaction() as connection:
            removed_sources = self._cleanup_source_keys(connection)
            relation_total = self._execute(connection, "SELECT COUNT(*) AS total FROM wb_relations").fetchone()["total"]
            rows = self._execute(connection, "SELECT * FROM wb_relations ORDER BY last_observed_at DESC LIMIT 5000").fetchall()
            scan("account_relations", relation_total, rows, 5000)
            for row in rows:
                if source_url_key(row["evidence_url"]) in removed_sources:
                    continue
                add(ids.get(row["source_id"]), ids.get(row["target_id"]), row["relation"], [row["evidence_url"]], observed_at=row["last_observed_at"])
            research_total = self._execute(connection, "SELECT COUNT(*) AS total FROM wb_research_edges").fetchone()["total"]
            rows = self._execute(connection, "SELECT * FROM wb_research_edges ORDER BY updated_at DESC LIMIT 5000").fetchall()
            scan("research_relations", research_total, rows, 5000)
            for row in rows:
                record = json.loads(row["record"])
                record = self._live_research_evidence(record, removed_sources)
                if record is None:
                    continue
                add(row["source_key"], row["target_key"], row["relationship"], record.get("evidence_urls", []), row["basis"], run_id=row["run_id"])
            # A literal @mention in a saved author's own post is observable.
            # Co-occurrence in articles or AI affiliation guesses is not an edge.
            post_scope = self.item_visibility() + " AND type='social' AND account_id IS NOT NULL"
            post_total = self._execute(connection, "SELECT COUNT(*) AS total FROM wb_items WHERE " + post_scope).fetchone()["total"]
            rows = self._execute(connection, "SELECT account_id,content,url,published_at FROM wb_items WHERE " + post_scope + " ORDER BY created_at DESC LIMIT 10000").fetchall()
            scan("social_posts", post_total, rows, 10000)
            for row in rows:
                source = ids.get(row["account_id"])
                if not source:
                    continue
                platform = nodes[source]["platform"]
                for handle in mention_handles(platform, row["content"]):
                    target = platform + ":" + handle.lower()
                    if target in nodes:
                        add(source, target, "mentions", [row["url"]], observed_at=row["published_at"], evidence_quote="@" + handle)
            failed = self._execute(connection, "SELECT id,status,kind,params,result FROM wb_jobs WHERE status IN ('failed','partial','interrupted') AND kind IN ('research','collect') ORDER BY created_at DESC LIMIT 8").fetchall()
        issues = []
        for row in failed:
            params, result = json.loads(row["params"]), json.loads(row["result"] or "{}")
            if params.get("_account_verification"):
                continue
            issues.append({"job_id": row["id"], "status": row["status"], "source": params.get("source"),
                           "outcomes": list(dict.fromkeys(d.get("outcome", "") for d in result.get("diagnostics", []) if not d.get("ok")))})
        values = list(nodes.values())
        visible = values[:300]
        visible_keys = {row["account_key"] for row in visible}
        links = [row for row in edges.values() if row["source_key"] in visible_keys and row["target_key"] in visible_keys][:1000]
        complete = not any(source["omitted_rows"] for source in scan_sources.values())
        return {"nodes": visible, "edges": links, "total_nodes": len(values), "total_edges": len(edges), "diagnostics": issues,
                "insights": graph_insights(visible, links),
                "total_edges_exact": complete,
                "coverage_metadata": {"complete": complete, "edge_count_lower_bound": len(edges), "sources": scan_sources},
                "coverage": "来自保存的公开关注、研究关系与原帖提及；未取得的关系不会补线。图形布局不表示亲疏、组织隶属或政治立场。", "limited": not complete or len(values) > 300 or len(edges) > 1000}
