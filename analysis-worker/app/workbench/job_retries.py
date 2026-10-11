"""Durable retry ancestry shared by individual jobs and task series.

New retries keep their parent and root in the existing JSON params column.
Older backups retain batch links, research aliases and the original retry event;
these are read without modifying any historical parameters or source evidence.
"""
from __future__ import annotations

from collections import defaultdict
import copy
import json
import re

from .research_batches import HARD_RESEARCH_BUDGETS, is_retryable_job

RETRY_METADATA = "_workbench_retry"
LEGACY_RETRY_EVENT = re.compile(r"^由任务 ([A-Za-z0-9_-]{1,64}) 重试创建；原记录保留。$")


class RetryConflict(ValueError):
    """A continuation already owns this retry branch."""


def report_resume_checkpoint(params, parent, get_job, *, frozen_input=None):
    """Recover absent legacy stage records only along the same report branch.

    Preserve the immediate parent's reading/draft progress and never replace a
    present stage record. Each owning stage still checks its input fingerprint,
    sources and quota before reuse. Historical jobs are read, never rewritten.
    """
    if not isinstance(parent, dict) or parent.get("kind") != "report":
        return None
    result = parent.get("result") or {}
    state = result.get("report_checkpoint") if isinstance(result, dict) else None
    if not isinstance(state, dict):
        return state
    state = copy.deepcopy(state)
    from .report_inputs import report_checkpoint_for_input
    def selected(candidate):
        return report_checkpoint_for_input(candidate, params, parent, frozen_input)
    metadata = params.get(RETRY_METADATA) or {}
    if not isinstance(metadata, dict):
        return selected(state)
    root_id = metadata.get("root_job_id")
    if metadata.get("parent_job_id") != parent.get("id") or not isinstance(root_id, str):
        return selected(state)
    operational = {RETRY_METADATA, "_report_input_ref", "_report_input_mode",
                   "time_budget_seconds", "harness_max_model_calls", "harness_max_tool_calls"}
    def scope(value):
        return json.dumps({key: value[key] for key in value if key not in operational},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    expected_scope = scope(params)
    current, seen = parent, set()
    fields = ("harness_state", "synthesis_state", "report_generation")
    for _ in range(64):
        identity = current.get("id")
        current_params = current.get("params")
        if (not isinstance(identity, str) or identity in seen or current.get("kind") != "report"
                or not isinstance(current_params, dict) or scope(current_params) != expected_scope):
            break
        seen.add(identity)
        saved_result = current.get("result") or {}
        saved = saved_result.get("report_checkpoint") if isinstance(saved_result, dict) else None
        if isinstance(saved, dict):
            saved = report_checkpoint_for_input(saved, params, current, frozen_input)
            for field in fields:
                if field not in state and isinstance(saved.get(field), dict):
                    state[field] = copy.deepcopy(saved[field])
        if all(field in state for field in fields) or identity == root_id:
            break
        link = current_params.get(RETRY_METADATA) or {}
        if not isinstance(link, dict) or link.get("root_job_id") != root_id:
            break
        next_id = link.get("parent_job_id")
        if not isinstance(next_id, str) or next_id in seen:
            break
        current = get_job(next_id)
        if not isinstance(current, dict):
            break
    return selected(state)


def retry_parent_links(jobs, batch_links=(), events=(), research_jobs=(), *, check=None):
    """Resolve saved ancestry; also usable by read-only backup validation."""
    check = check or (lambda: None)
    records = {}
    for row in jobs:
        check()
        records[row["id"]] = row
    parents = {}
    for row in records.values():
        check()
        params = row["params"]
        params = json.loads(params) if isinstance(params, str) else params
        metadata = params.get(RETRY_METADATA)
        if isinstance(metadata, dict) and isinstance(metadata.get("parent_job_id"), str):
            parents[row["id"]] = metadata["parent_job_id"]
    for row in batch_links:
        check()
        if row.get("retry_of_job_id"):
            parents.setdefault(row["job_id"], row["retry_of_job_id"])
    for row in events:
        check()
        match = LEGACY_RETRY_EVENT.fullmatch(row["message"])
        parent = records.get(match[1]) if match else None
        child = records.get(row["job_id"])
        if parent and child and parent["kind"] == child["kind"]:
            parents.setdefault(child["id"], parent["id"])
    previous, first = {}, {}
    for row in sorted(research_jobs, key=lambda row: (row["run_id"], row["job_id"] != row["run_id"], row["created_at"], row["job_id"])):
        check()
        prior = previous.get(row["run_id"])
        first.setdefault(row["run_id"], row["job_id"])
        if prior and row["job_id"] not in parents:
            # Equal historical clock ticks can order UUIDs before their real
            # predecessor. Never infer a parent that is an explicit descendant.
            candidate, seen = prior, set()
            while candidate in parents and candidate not in seen and candidate != row["job_id"]:
                check()
                seen.add(candidate)
                candidate = parents[candidate]
            if candidate == row["job_id"]:
                prior = first[row["run_id"]]
            if prior != row["job_id"]:
                parents[row["job_id"]] = prior
        previous[row["run_id"]] = row["job_id"]
    return parents


def retry_root(job_id, parents, *, check=None):
    check = check or (lambda: None)
    seen = set()
    while job_id in parents:
        check()
        if job_id in seen:
            raise ValueError("重试任务关联存在循环，请检查恢复文件。")
        seen.add(job_id)
        job_id = parents[job_id]
    return job_id


class JobRetries:
    def _analysis_retry_snapshot(self, connection, job):
        if job.get("kind") != "research" or job.get("status") not in {"partial", "failed", "cancelled", "interrupted"}:
            return None
        run_id = self._run_id(connection, job["id"])
        if not run_id:
            return None
        row = self._execute(connection, "SELECT summary,phase,analysis FROM wb_research_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            return None
        # The durable run is newer than job.result after a cancellation during
        # analysis, and must be consulted before a hard collection-cap refusal.
        summary = json.loads(row["summary"])
        analysis = json.loads(row["analysis"])
        ai = analysis.get("ai") or {}
        pending = summary.get("analysis_status") in {"failed", "partial", "pending", "running"}
        if summary.get("analysis_status") != "completed":
            pending = pending or (ai.get("requested") is True and ai.get("complete") is False)
        from .research_engine import collection_snapshot_is_terminal
        if pending and collection_snapshot_is_terminal(summary, row["phase"]):
            return {"run_id": run_id, "summary": summary}
        return None

    def _job_retryable(self, connection, job):
        return is_retryable_job(job) or self._analysis_retry_snapshot(connection, job) is not None

    @staticmethod
    def _retry_refusal(job):
        summary = (job.get("result") or {}).get("summary") or {}
        if job["kind"] == "research" and job["status"] == "partial" and summary.get("stop_reason") in HARD_RESEARCH_BUDGETS:
            return "本次研究已达到材料、账号或关系预算。相同参数不能扩大覆盖，请规划后续任务串或新建研究。"
        return "只能重试失败、取消、中断或部分完成的任务；已成功的后续任务不会重复执行。"

    def _retry_index(self, connection):
        jobs = [dict(row) for row in self._execute(connection, "SELECT id,kind,params FROM wb_jobs").fetchall()]
        batches = [dict(row) for row in self._execute(connection,
            "SELECT job_id,retry_of_job_id FROM wb_job_batch_jobs WHERE retry_of_job_id IS NOT NULL").fetchall()]
        events = [dict(row) for row in self._execute(connection,
            "SELECT job_id,message FROM wb_job_events WHERE message LIKE ?", ("由任务 % 重试创建；原记录保留。",)).fetchall()]
        aliases = [dict(row) for row in self._execute(connection,
            "SELECT job_id,run_id,created_at FROM wb_research_jobs").fetchall()]
        return retry_parent_links(jobs, batches, events, aliases)

    @staticmethod
    def _retry_root(job_id, parents):
        return retry_root(job_id, parents)

    def _lock_retry_roots(self, connection, job_ids):
        parents = self._retry_index(connection)
        roots = sorted({self._retry_root(job_id, parents) for job_id in job_ids})
        for root in roots:
            suffix = " FOR UPDATE" if self._postgres else ""
            if self._execute(connection, "SELECT id FROM wb_jobs WHERE id=?" + suffix, (root,)).fetchone() is None:
                raise LookupError("重试任务的原记录不存在")
        # On PG a competing writer may have added a retry while we waited for
        # the root lock. Refresh ancestry only after all roots are reserved.
        return self._retry_index(connection)

    def _superseded_research_caps(self, connection, job_id, jobs, parents):
        """Ignore only older caps superseded by a durable budget extension."""
        budgets = {"account_budget": "max_accounts", "edge_budget": "max_edges", "record_budget": "max_records"}
        capped = [job for job in jobs if job["kind"] == "research" and job["status"] == "partial"
                  and ((job.get("result") or {}).get("summary") or {}).get("stop_reason") in budgets]
        run_id = self._run_id(connection, job_id) if capped else None
        if not run_id:
            return set()
        run = self._execute(connection, "SELECT current_job_id,params FROM wb_research_runs WHERE id=?", (run_id,)).fetchone()
        current = next((job for job in jobs if job["id"] == run["current_job_id"]), None) if run else None
        if not current or current["kind"] != "research":
            return set()
        from .research_engine import PipelineError, ResearchParams
        try:
            chosen = vars(ResearchParams.parse(current["params"]))
            if chosen != vars(ResearchParams.parse(json.loads(run["params"]))):
                return set()
        except (PipelineError, ValueError, TypeError, KeyError):
            return set()
        ancestors, identity = set(), current["id"]
        while identity in parents:
            identity = parents[identity]
            ancestors.add(identity)
        limits = {*budgets.values(), "time_budget_seconds"}
        superseded = set()
        for job in capped:
            if job["id"] not in ancestors or self._run_id(connection, job["id"]) != run_id:
                continue
            try:
                original = vars(ResearchParams.parse(job["params"]))
            except (PipelineError, ValueError, TypeError, KeyError):
                continue
            summary = (job.get("result") or {}).get("summary") or {}
            cap = budgets[summary["stop_reason"]]
            recorded = summary.get(cap, original[cap])
            if (not isinstance(recorded, bool) and isinstance(recorded, int)
                    and all(chosen[key] == value for key, value in original.items() if key not in limits)
                    and all(chosen[key] >= original[key] for key in limits)
                    and chosen[cap] > max(original[cap], recorded)):
                superseded.add(job["id"])
        return superseded

    def _retry_branch(self, connection, job_id, *, parents=None):
        parents = self._retry_index(connection) if parents is None else parents
        root = self._retry_root(job_id, parents)
        children = defaultdict(list)
        for child, parent in parents.items():
            children[parent].append(child)
        pending, identities = [root], set()
        while pending:
            identity = pending.pop()
            if identity in identities:
                raise ValueError("重试任务关联存在循环，请检查恢复文件。")
            identities.add(identity)
            pending.extend(children[identity])
        jobs = []
        # Bound parameter counts independently of the amount of saved history.
        identities = sorted(identities)
        for offset in range(0, len(identities), 200):
            selected = identities[offset:offset + 200]
            rows = self._execute(connection, "SELECT * FROM wb_jobs WHERE id IN (" + ",".join("?" for _ in selected) + ")", selected).fetchall()
            jobs.extend(self._job(row) for row in rows)
        if not jobs or not any(job["id"] == job_id for job in jobs):
            raise LookupError("任务不存在")
        # Pre-existing duplicate branches must not allow a newer failed sibling
        # to repeat work still active, completed, or already at its hard cap.
        active = [job for job in jobs if job["status"] in {"queued", "running"}]
        superseded = self._superseded_research_caps(connection, job_id, jobs, parents)
        blocked = [job for job in jobs if job["status"] not in {"queued", "running"}
                   and job["id"] not in superseded and not self._job_retryable(connection, job)]
        def ordering(job):
            generation, identity = 0, job["id"]
            while identity in parents:
                generation += 1
                identity = parents[identity]
            return job["created_at"], generation, job["id"]
        latest = max(active or blocked or jobs, key=ordering)
        return root, latest

    def _retry_params(self, connection, latest, root):
        params = {**latest["params"], RETRY_METADATA: {"parent_job_id": latest["id"], "root_job_id": root}}
        if latest["kind"] == "research":
            run_id = self._run_id(connection, latest["id"])
            if run_id:
                params["resume_run_id"] = run_id
                if self._analysis_retry_snapshot(connection, latest) is not None:
                    params["_analysis_only"] = True
        return params

    def report_retry_preview(self, job_id):
        from .report_inputs import retry_preview
        with self._transaction() as connection:
            requested = self._job(self._execute(connection, "SELECT * FROM wb_jobs WHERE id=?", (job_id,)).fetchone())
            if requested is None:
                raise LookupError("任务不存在")
            _, latest = self._retry_branch(connection, job_id)
        return retry_preview(self, requested, latest)

    def retry_job(self, job_id: str, *, report_options: dict | None = None, report_validator=None) -> dict:
        """Reserve one branch and create only its next explicit failed retry."""
        from .store import _now
        if report_options is not None and not isinstance(report_options, dict):
            raise ValueError("报告重试运行参数必须是对象。")
        with self._transaction(write=True) as connection:
            requested = self._job(self._execute(connection, "SELECT * FROM wb_jobs WHERE id=?", (job_id,)).fetchone())
            if requested is None:
                raise LookupError("任务不存在")
            if not self._job_retryable(connection, requested):
                raise ValueError(self._retry_refusal(requested))
            parents = self._lock_retry_roots(connection, [job_id])
            root, latest = self._retry_branch(connection, job_id, parents=parents)
            if latest["status"] in {"queued", "running"}:
                raise RetryConflict("此任务已有排队或运行中的重试，请查看现有任务。")
            if not self._job_retryable(connection, latest):
                raise ValueError(self._retry_refusal(latest))
            params = self._retry_params(connection, latest, root)
            selected_input = None
            if report_options:
                if latest["kind"] != "report" or not isinstance(report_options, dict):
                    raise ValueError("只有报告重试可以调整分析运行参数。")
                limits = {"harness_max_model_calls": (0, 4096), "harness_max_tool_calls": (0, 65536), "time_budget_seconds": (0, 2592000)}
                if set(report_options) - {"analysis_harness", "input_mode", "input_sha256", *limits}:
                    raise ValueError("报告重试只能调整调查方式和运行上限，不能更改来源范围。")
                for key, value in report_options.items():
                    if key == "input_mode":
                        if not isinstance(value, str) or value not in {"original", "latest"}:
                            raise ValueError("报告重试输入方式只能为 original 或 latest。")
                    elif key == "input_sha256":
                        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                            raise ValueError("报告重试须提交免费预览的输入 SHA-256。")
                    elif key == "analysis_harness":
                        if not isinstance(value, str) or value not in {"off", "dsh"} or (value == "dsh" and params.get("mode") != "ai"):
                            raise ValueError("DSH 工具分析仅适用于 AI 报告模式。")
                    elif type(value) is not int or not limits[key][0] <= value <= limits[key][1] or key.startswith("harness_") and value == 1 or key == "time_budget_seconds" and 0 < value < 60:
                        raise ValueError("报告重试运行上限格式无效。")
                params.update({key: value for key, value in report_options.items() if key not in {"input_mode", "input_sha256"}})
            if latest["kind"] == "report":
                from .report_inputs import current_report_input, saved_input, validate_report_input_snapshot
                mode = (report_options or {}).get("input_mode", "original")
                if mode == "original":
                    selected_input = validate_report_input_snapshot(saved_input(latest), params)
                    expected = selected_input["sha256"]
                else:
                    selected_input = current_report_input(self, params)
                    expected = selected_input["input_fingerprint"]
                if (report_options or {}).get("input_sha256") != expected:
                    raise RetryConflict("报告输入与免费预览不一致或未提供预览 SHA-256；请重新预览后重试。")
                params.pop("_report_input_ref", None)
                params["_report_input_mode"] = mode
                if mode == "original":
                    params["_report_input_ref"] = {"job_id": latest["id"], "sha256": selected_input["sha256"]}
            if latest["kind"] == "report" and report_validator is not None:
                report_validator(copy.deepcopy(params))
            job = self.create_job(latest["kind"], params, _connection=connection)
            if selected_input is not None:
                # Bind the checked input in the same transaction as the child.
                # A later source/review update cannot change queued latest work.
                job["result"] = {"report_input_snapshot": selected_input}
                previous_checkpoint = (latest.get("result") or {}).get("report_checkpoint")
                if isinstance(previous_checkpoint, dict):
                    # Preserve it even if this child is cancelled before its
                    # first reading callback. Every stage still checks its own
                    # exact input; copying a candidate is not cache acceptance.
                    from .report_inputs import report_checkpoint_for_input
                    job["result"]["report_checkpoint"] = report_checkpoint_for_input(
                        copy.deepcopy(previous_checkpoint), params, latest, selected_input)
                from .store import _json
                self._execute(connection, "UPDATE wb_jobs SET result=? WHERE id=?", (_json(job["result"]), job["id"]))
            if latest["kind"] == "research" and params.get("resume_run_id"):
                self._attach_research_job(connection, params["resume_run_id"], job["id"], _now())
            self._event(connection, job["id"], f"由任务 {latest['id']} 重试创建；原记录保留。")
        return job
