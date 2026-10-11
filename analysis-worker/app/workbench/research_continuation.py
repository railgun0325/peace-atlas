"""Explicit budget extensions of one saved, canonical research run.

Availability is derived from retained public work, not a partial status or a
historical rejection counter. Enqueue and parameter updates share the retry
root reservation transaction on both supported stores.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from .job_retries import RETRY_METADATA, RetryConflict
from .limits import RESEARCH_RECORDS_MAX, RESEARCH_TIME_MIN, RESEARCH_TIME_MAX, RESEARCH_ACCOUNTS_MAX, RESEARCH_EDGES_MAX

CONTINUATION_LIMITS = {"max_accounts": (0, RESEARCH_ACCOUNTS_MAX), "max_edges": (0, RESEARCH_EDGES_MAX),
                       "max_records": (0, RESEARCH_RECORDS_MAX),
                       "time_budget_seconds": (RESEARCH_TIME_MIN, RESEARCH_TIME_MAX)}
CAP_LIMITS = {"account_budget": "max_accounts", "edge_budget": "max_edges", "record_budget": "max_records"}


def validate_continuation_limits(values):
    if not isinstance(values, dict) or set(values) - CONTINUATION_LIMITS.keys():
        raise ValueError("继续研究只接受账号、关系、材料和本次时长四项预算。")
    for key, value in values.items():
        low, high = CONTINUATION_LIMITS[key]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"{key} 必须为 {low} 至 {high} 的整数。")
    return values


def _url(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme in {"https", "http"} and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def continuation_state(run):
    """Explain whether a retained collection branch can actually advance."""
    from .research_frontier import infer_missing_research_work
    run = run or {}
    params = run.get("params") or {}
    summary = infer_missing_research_work(run.get("summary") or {}, params)
    excluded = set(run.get("excluded_account_keys") or [])
    excluded.update(row.get("account_key") for row in run.get("accounts") or [] if row.get("status") == "excluded")
    admitted = {row.get("account_key") for row in run.get("accounts") or [] if row.get("account_key") not in excluded}
    saved_urls = {row.get("url") for row in run.get("items") or []}
    saved_observations = set()
    for row in run.get("relations", run.get("edges", [])) or []:
        for observed in [row, *(row.get("observations") or [])]:
            saved_observations.add((observed.get("source_key"), observed.get("target_key"),
                observed.get("relation", observed.get("relationship")), observed.get("evidence_url") or next(iter(observed.get("evidence_urls") or []), None), observed.get("observed_at")))
    deferred_accounts, deferred_items, deferred_relations = set(), set(), set()
    for entry in run.get("deferred_frontier") or []:
        key, acc = entry.get("account_key"), entry.get("account") or {}
        if key in excluded or acc.get("status") == "excluded":
            continue
        try:
            from .social.common import account
            if account(acc["platform"], acc["url"])["account_key"] != key:
                continue
        except (KeyError, ValueError, TypeError):
            continue
        paths = [path for path in entry.get("discovery_paths") or []
                 if path.get("parent_key") not in excluded and any(_url(url) for url in path.get("evidence_urls") or [])]
        if key not in admitted and paths:
            deferred_accounts.add(key)
        if key in admitted or paths:
            deferred_items.update(row["url"] for row in entry.get("items") or []
                                  if _url(row.get("url")) and row.get("url") not in saved_urls)
            deferred_relations.update((row.get("source_key"), row.get("target_key"), row.get("relation"), row.get("evidence_url"), row.get("observed_at"))
                for row in entry.get("relations") or [] if row.get("relation") in {"follows", "mentions"}
                and not {row.get("source_key"), row.get("target_key")} & excluded
                and (row.get("source_key"), row.get("target_key"), row.get("relation"), row.get("evidence_url"), row.get("observed_at")) not in saved_observations
                and (_url(row.get("evidence_url")) or any(_url(url) for url in row.get("evidence_urls") or [])))
    visited = set(run.get("visited") or [])
    nodes = {row.get("account_key"): row for row in run.get("nodes") or [] if row.get("account_key") in admitted}
    pending_work = set()
    for key, row in nodes.items():
        work = row.get("pending_work") or {}
        if key not in visited and (any(work.get(field) is True for field in ("profile", "posts", "mentions"))
                or any(direction in {"following", "followers"} for direction in work.get("neighbors") or [])):
            pending_work.add(key)
    frontier = {row.get("account_key") for row in run.get("frontier") or []
                if row.get("account_key") in admitted and row.get("account_key") not in visited
                and row.get("status") not in {"completed", "complete", "skipped", "visited"}}
    terminal_failure = run.get("status") in {"failed", "interrupted", "cancelled", "partial"}
    pending_queries = 0
    if (terminal_failure and summary.get("collection_complete") is not True
            or summary.get("seed_query_stats") or summary.get("pending_seed_queries")) and params.get("mode", "keyword") != "account":
        plan = run.get("query_plan") or {}
        try:
            from .research_engine import retained_seed_queries
            pending_queries = len(retained_seed_queries(params, plan, summary))
        except (KeyError, ValueError, TypeError):
            pending_queries = 0
    pending_roots = 0
    if terminal_failure and summary.get("collection_complete") is not True and params.get("mode") == "account":
        try:
            from .research_engine import normalize_research_seeds
            roots = normalize_research_seeds(params)
            from .social.common import account
            pending_roots = sum(account("instagram" if "instagram.com" in urlsplit(url).netloc else "twitter", url)["account_key"]
                                not in admitted | visited | excluded for url in roots)
        except (KeyError, ValueError, TypeError):
            pass
    result = {"continuation_available": False, "continuation_reason": "没有已保存的候选或未完成采集分支。",
              "continuation_deferred_accounts": len(deferred_accounts), "continuation_deferred_items": len(deferred_items),
              "continuation_deferred_relations": len(deferred_relations), "continuation_pending_accounts": len(pending_work | frontier),
              "continuation_pending_queries": pending_queries, "continuation_pending_roots": pending_roots}
    if run.get("status") in {"queued", "running"}:
        result["continuation_reason"] = "该研究已有待执行或运行中的任务，请等待完成或先取消。"
    elif deferred_accounts or deferred_items or deferred_relations or pending_work or frontier or pending_queries or pending_roots:
        result.update(continuation_available=True, continuation_reason="已保存可继续的公开候选、观察证据或未完成采集分支。")
    elif summary.get("frontier_recovery_required") or summary.get("collection_recovery_required") or summary.get("legacy_deferred_unknown"):
        result["continuation_reason"] = "旧任务没有保存被预算阻止的候选 URL 或待执行分支，不能凭次数恢复研究；请另建研究。"
    elif summary.get("analysis_pending_evidence", 0):
        result["continuation_reason"] = "采集没有待执行工作；待审 AI 证据请使用原任务的分析重试入口。"
    return result


class ResearchContinuation:
    def _continuation_snapshot(self, connection, run_id, row, *, context=None):
        from .research_frontier import project_deferred
        run = dict(row) if isinstance(row, dict) and isinstance(row.get("summary"), dict) else self._run_record(row)
        run["excluded_account_keys"] = self._excluded_account_keys(connection) if context is None else context["excluded_account_keys"]
        blocked = self._cleanup_source_keys(connection, run.get("metadata")) if context is None else (
            context["cleanup_source_keys"] | set((run.get("metadata") or {}).get("purged_source_keys") or []))
        run["_cleanup_source_keys"] = blocked
        run["deferred_frontier"] = project_deferred(self._read_deferred(connection, run_id, include_materials=False),
            blocked, run["excluded_account_keys"])
        run["nodes"] = [self._research_node(row) for row in self._execute(connection,
            "SELECT * FROM wb_research_nodes WHERE run_id=?", (run_id,)).fetchall()]
        run["accounts"] = [self._account(row) for row in self._execute(connection,
            "SELECT DISTINCT a.* FROM wb_accounts a JOIN wb_research_nodes n ON n.account_id=a.id WHERE n.run_id=?", (run_id,)).fetchall()]
        for acc in run["accounts"]:
            acc["account_key"] = self.social_account_key(acc["platform"], acc["handle"])
        run["items"] = [dict(row) for row in self._execute(connection,
            "SELECT i.id,i.url FROM wb_items i JOIN wb_research_items ri ON ri.item_id=i.id WHERE ri.run_id=? AND " + self.item_visibility("i"), (run_id,)).fetchall()]
        run["edges"] = [self._research_edge(row) for row in self._execute(connection,
            "SELECT * FROM wb_research_edges WHERE run_id=?", (run_id,)).fetchall()]
        self._project_research_cleanup(connection, run, blocked=blocked)
        return run

    def continue_research_job(self, run_id, limits):
        from .research_engine import ResearchParams
        from .research_repository import _public_json
        from .store import _now
        validate_continuation_limits(limits)
        with self._transaction(write=True) as connection:
            resolved = self._run_id(connection, run_id)
            if not resolved:
                raise LookupError("待继续的研究不存在")
            current = self._execute(connection, "SELECT current_job_id FROM wb_research_runs WHERE id=?", (resolved,)).fetchone()
            parents = self._lock_retry_roots(connection, [current["current_job_id"]])
            # Refresh the authoritative current pointer and limits after root
            # reservation; an older capped ancestor cannot replace this row.
            suffix = " FOR UPDATE" if self._postgres else ""
            row = self._execute(connection, "SELECT * FROM wb_research_runs WHERE id=?" + suffix, (resolved,)).fetchone()
            latest = self._job(self._execute(connection, "SELECT * FROM wb_jobs WHERE id=?", (row["current_job_id"],)).fetchone())
            root, branch = self._retry_branch(connection, latest["id"], parents=parents)
            if branch["status"] in {"queued", "running"} or latest["status"] in {"queued", "running"}:
                raise RetryConflict("该研究已有待执行或运行中的任务，请等待完成或先取消。")
            if latest["kind"] != "research":
                raise ValueError("保存的当前任务不属于研究。")
            run = self._continuation_snapshot(connection, resolved, row)
            run["status"] = latest["status"]
            availability = continuation_state(run)
            if not availability["continuation_available"]:
                raise ValueError(availability["continuation_reason"])
            baseline = ResearchParams.parse(run["params"])
            original = {key: baseline.public_limit(key) for key in CONTINUATION_LIMITS}
            chosen = {**original, **limits}
            if any(chosen[key] != 0 and (original[key] == 0 or chosen[key] < original[key]) for key in CONTINUATION_LIMITS):
                raise ValueError("继续研究的任何预算都不能低于最新已保存上限。")
            durable = {"max_accounts": self._execute(connection, "SELECT COUNT(*) AS total FROM wb_research_nodes WHERE run_id=?", (resolved,)).fetchone()["total"],
                       "max_edges": self._execute(connection, "SELECT COUNT(*) AS total FROM wb_research_edges WHERE run_id=?", (resolved,)).fetchone()["total"],
                       "max_records": self._execute(connection, "SELECT COUNT(*) AS total FROM wb_research_items WHERE run_id=?", (resolved,)).fetchone()["total"]}
            if any(chosen[key] != 0 and chosen[key] < total for key, total in durable.items()):
                raise ValueError("继续研究的预算不能低于已经保存的账号、关系或材料总数。")
            summary = run.get("summary") or {}
            reasons = set(summary.get("limit_hits") or [])
            reasons.add(summary.get("collection_stop_reason") or summary.get("stop_reason"))
            for reason, key in CAP_LIMITS.items():
                recorded_limit = summary.get(key, original[key])
                if isinstance(recorded_limit, bool) or not isinstance(recorded_limit, int):
                    recorded_limit = original[key]
                if reason in reasons and chosen[key] != 0 and chosen[key] <= recorded_limit:
                    raise ValueError(f"本次研究达到 {reason}，继续对应工作必须提高 {key}。")
            saved_params = {key: value for key, value in run["params"].items() if key not in {RETRY_METADATA, "resume_run_id", "_analysis_only", "_excluded_account_keys"}}
            # Freeze per-account scope even if its original value was a parsed
            # default depending on the old global record/account budgets.
            saved_params.setdefault("max_records_per_account", baseline.max_records_per_account)
            saved_params.setdefault("max_neighbors_per_account", baseline.max_neighbors_per_account)
            saved_params.update(chosen)
            ResearchParams.parse(saved_params)
            encoded = _public_json(saved_params)
            child_params = {**saved_params, "resume_run_id": resolved,
                            RETRY_METADATA: {"parent_job_id": latest["id"], "root_job_id": root}}
            job = self.create_job("research", child_params, _connection=connection)
            stamp = _now()
            self._execute(connection, "UPDATE wb_research_runs SET params=?,revision=revision+1,updated_at=? WHERE id=?", (encoded, stamp, resolved))
            self._attach_research_job(connection, resolved, job["id"], stamp)
            self._event(connection, job["id"], f"由任务 {latest['id']} 重试创建；原记录保留。")
        return job
