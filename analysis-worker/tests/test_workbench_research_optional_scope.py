"""Natural completion, visible finite scope, and mandatory estimate confirmation."""
import asyncio
import time

from fastapi.testclient import TestClient
import pytest

from app.workbench import research_engine as engine
from app.workbench import research_estimate
from app.workbench.jobs import JobRunner
from app.workbench.server import create_app
from app.workbench.social.common import account
from app.workbench.store import Store


def params(**changes):
    return {"source": "twitter", "seed_sources": ["twitter"], "mode": "account", "handle": "root",
            "keyword": "", "start_date": "2026-10-01", "end_date": "2026-10-10", "depth": 4,
            "max_accounts": 0, "max_records": 0, "max_edges": 0, "time_budget_seconds": 0,
            "max_scrolls": 10, "max_records_per_account": 2, "max_neighbors_per_account": 300,
            "expand_relations": ["following"], "analysis_mode": "rules", **changes}


class WideScope:
    closed = 0
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        type(self).closed += 1
    async def profile(self, platform, handle):
        return {**account(platform, handle), "profile": {"verified_in_dom": True, "bio": "公開プロフィール", "observed_at": "2026-10-10T00:00:00Z"}}
    async def posts(self, platform, handle, options, limit, accept):
        item = {"platform": platform, "type": "social", "account_key": f"twitter:{handle}", "title": handle,
                "content": f"{handle} 個別公開資料", "url": f"https://x.com/{handle}/status/{1 if handle == 'root' else int(handle.removeprefix('public')) + 2}",
                "published_at": "2026-10-05T00:00:00Z", "metadata": {"content_kind": "body"}}
        return {"items": [item], "accounts": [account(platform, handle)], "relations": [], "diagnostics": [{"ok": True}]}
    async def neighbors(self, platform, handle, direction, options, known_count):
        rows = [account(platform, f"public{i}") for i in range(250)] if handle == "root" else []
        return {"accounts": rows, "items": [], "relations": [{"source_key": "twitter:root", "target_key": row["account_key"],
                "relation": "follows", "evidence_url": "https://x.com/root/following", "observed_at": "2026-10-10T00:00:00Z"} for row in rows],
                "diagnostics": [{"ok": True}]}


def test_zero_total_stops_finish_finite_wide_scope_beyond_old_account_edge_limits(monkeypatch):
    monkeypatch.setattr(engine, "BrowserResearchAdapter", WideScope)
    result = asyncio.run(engine.run_research(params()))
    summary = result["summary"]
    assert summary["outcome"] == "completed" and summary["collection_complete"] is True
    assert (summary["account_count"], summary["record_count"], summary["edge_count"]) == (251, 251, 250)
    assert summary["max_accounts"] == summary["max_records"] == summary["max_edges"] == summary["time_budget_seconds"] == 0
    assert result["frontier"] == [] and result["deferred_frontier"] == []
    assert summary["limit_hits"] == [] and len(result["visited"]) == 251


def test_no_total_time_limit_remains_cancellable_with_last_branch_checkpoint(monkeypatch):
    checkpoints = []
    entered = asyncio.Event()
    class Hanging(WideScope):
        async def profile(self, platform, handle):
            entered.set()
            await asyncio.Event().wait()
    monkeypatch.setattr(engine, "BrowserResearchAdapter", Hanging)
    async def scenario():
        task = asyncio.create_task(engine.run_research(params(), checkpoint=checkpoints.append))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(scenario())
    assert checkpoints and checkpoints[-1]["frontier"][0]["account_key"] == "twitter:root"
    assert checkpoints[-1]["frontier"][0]["status"] == "processing"


def test_estimate_is_read_only_parameter_bound_expiring_and_required_for_start(tmp_path, monkeypatch):
    monkeypatch.delenv("WORKBENCH_SESSION_TOKEN", raising=False)
    store = Store(tmp_path / "confirmation.sqlite3")
    with TestClient(create_app(store, run_worker=False)) as client:
        values = params()
        missing = client.post("/api/v2/jobs", json={"kind": "research", "params": values})
        assert missing.status_code == 422 and store.list_jobs()["total"] == 0
        estimate = client.post("/api/v2/research/estimate", json=values)
        assert estimate.status_code == 200, estimate.text
        preview = estimate.json()
        assert 0 < preview["lower_seconds"] < preview["upper_seconds"]
        assert preview["confidence"] == "low" and preview["valid_for_seconds"] == 1800
        assert store.list_jobs()["total"] == 0
        changed = client.post("/api/v2/jobs", json={"kind": "research", "params": {**values, "depth": 5}, "preview_token": preview["preview_token"]})
        assert changed.status_code == 422 and store.list_jobs()["total"] == 0
        submitted = client.post("/api/v2/jobs", json={"kind": "research", "params": values, "preview_token": preview["preview_token"]})
        assert submitted.status_code == 202, submitted.text
        assert submitted.json()["params"]["time_budget_seconds"] == 0
        future = time.time() + 1801
        monkeypatch.setattr(research_estimate.time, "time", lambda: future)
        expired = client.post("/api/v2/jobs", json={"kind": "research", "params": values, "preview_token": preview["preview_token"]})
        assert expired.status_code == 422 and store.list_jobs()["total"] == 1


def test_worker_checkpoints_zero_limits_without_dropping_edges_or_export_counts(tmp_path, monkeypatch):
    class DenseScope(WideScope):
        async def neighbors(self, platform, handle, direction, options, known_count):
            candidates = [f"public{i}" for i in range(44)] if handle == "root" else [f"public{i}" for i in range(5) if f"public{i}" != handle]
            rows = [account(platform, value) for value in candidates]
            return {"accounts": rows, "items": [], "relations": [{"source_key": f"twitter:{handle}", "target_key": row["account_key"],
                    "relation": "follows", "evidence_url": f"https://x.com/{handle}/following", "observed_at": "2026-10-10T00:00:00Z"} for row in rows],
                    "diagnostics": [{"ok": True}]}
    monkeypatch.setattr(engine, "BrowserResearchAdapter", DenseScope)
    store = Store(tmp_path / "scope.sqlite3")
    job = store.create_job("research", params())
    result = asyncio.run(JobRunner(store)._research(job))
    assert result["_status"] == "succeeded", result.get("diagnostics")
    exported = store.export_research(job["id"])
    assert len(exported["nodes"]) == 45 and len(exported["items"]) == 45 and len(exported["edges"]) == 259
    assert exported["run"]["params"]["max_edges"] == 0


def test_report_zero_time_reaches_pipeline_as_none_and_preserves_finite_source_snapshot(tmp_path, monkeypatch):
    from app.workbench import pipeline
    from app.workbench.server import ReportParams, ReportRetryOptions
    assert ReportParams(time_budget_seconds=0, start_date="2026-10-01", end_date="2026-10-10").time_budget_seconds == 0
    assert ReportRetryOptions(time_budget_seconds=0, harness_max_model_calls=0, harness_max_tool_calls=0).time_budget_seconds == 0
    received = []
    async def build(material, parameters, **kwargs):
        received.append(kwargs.get("time_budget_seconds"))
        return {"id": "scope-report", "title": "范围分析", "mode": "ai", "markdown": "来源 [1]", "source_count": 1,
                "start_date": "2026-10-01", "end_date": "2026-10-10", "snapshot": {"sources": material}}
    monkeypatch.setattr(pipeline, "build_report", build)
    store = Store(tmp_path / "report.sqlite3")
    store.import_items([{"type": "news", "title": "公開資料", "url": "https://example.org/one", "content": "資料内容", "published_at": "2026-10-05T00:00:00Z"}])
    job = store.create_job("report", {"mode": "ai", "read_original": False, "time_budget_seconds": 0, "start_date": "2026-10-01", "end_date": "2026-10-10"})
    asyncio.run(JobRunner(store)._execute(job))
    assert received == [None]


def test_report_progress_moves_from_reading_to_synthesis_to_source_review(tmp_path, monkeypatch):
    from app.workbench import pipeline
    store = Store(tmp_path / "progress.sqlite3")
    store.import_items([{"type": "news", "title": "公開資料", "url": "https://example.org/progress", "content": "資料内容", "published_at": "2026-10-05T00:00:00Z"}])
    job = store.create_job("report", {"mode": "ai", "read_original": False, "time_budget_seconds": 0, "start_date": "2026-10-01", "end_date": "2026-10-10"})
    observed = []
    async def build(material, parameters, **kwargs):
        state = {"coverage_progress": {"reviewed_source_count": 601, "input_source_count": 601, "pending_source_count": 0}}
        await kwargs["checkpoint"](state)
        observed.append(store.get_job(job["id"])["progress"])
        state = {**state, "harness_state": {"model_calls": 24, "tool_calls": 64, "complete": True},
            "progress": {"stage": "evidence_notes", "level": 1, "planned_batch_count": 4, "current_batch_number": 3,
                         "completed_unit_count": 401, "total_unit_count": 601, "model_request_count": 6, "completed_request_count": 5}}
        await kwargs["checkpoint"](state)
        observed.append(store.get_job(job["id"])["progress"])
        assert "当前批次 3/4" in store.job_events(job["id"])[-1]["message"]
        state = {**state, "report_generation": {"phase": "review_pending", "draft_attempt_count": 1}}
        await kwargs["checkpoint"](state)
        observed.append(store.get_job(job["id"])["progress"])
        assert "核对来源与研究目标" in store.job_events(job["id"])[-1]["message"]
        return {"id": "progress-report", "title": "范围分析", "mode": "ai", "markdown": "来源 [1]", "source_count": 1,
                "start_date": "2026-10-01", "end_date": "2026-10-10", "snapshot": {"sources": material}}
    monkeypatch.setattr(pipeline, "build_report", build)
    asyncio.run(JobRunner(store)._execute(job))
    assert observed == [55, 70, 88]
    assert store.get_job(job["id"])["status"] == "succeeded"
