"""Real-store retry branches shared by job details, series and old backups."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json

import pytest
from fastapi.testclient import TestClient

from app.workbench.job_retries import RetryConflict, retry_parent_links, retry_root
from app.workbench.server import create_app
from app.workbench.store import Store


def task():
    return {"kind": "collect", "params": {"source": "google_news", "keyword": "平和",
        "start_date": "2026-10-01", "end_date": "2026-10-08", "max_records": 10}}


def failed_series(store):
    batch = store.create_job_batch("Saved original", [task()], 10)
    original = batch["jobs"][0]
    store.update_job(original["id"], status="failed")
    return batch, original


def test_mixed_http_retry_race_creates_one_durable_branch(tmp_path):
    store = Store(tmp_path / "mixed-race.sqlite3")
    batch, original = failed_series(store)
    with TestClient(create_app(store, run_worker=False)) as client:
        def retry(index):
            path = f"jobs/{original['id']}" if index % 2 else f"job-batches/{batch['id']}"
            return client.post(f"/api/v2/{path}/retry")
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(retry, range(8)))
        assert sum(result.status_code == 202 for result in results) == 1
        assert all(result.status_code in {202, 409} for result in results)
        jobs = store.list_jobs()["items"]
        assert len(jobs) == 2
        child = next(job for job in jobs if job["id"] != original["id"])
        assert child["params"]["_workbench_retry"] == {
            "parent_job_id": original["id"], "root_job_id": original["id"]}
        assert store.get_job(original["id"])["params"] == original["params"]
        assert store.get_job_batch(batch["id"])["retryable_count"] == 0
    reopened = Store(store.path)
    with pytest.raises(RetryConflict):
        reopened.retry_job(original["id"])
    assert reopened.list_jobs()["total"] == 2


def test_old_ancestor_cannot_repeat_queued_or_succeeded_grandchild(tmp_path):
    store = Store(tmp_path / "grandchild.sqlite3")
    batch, original = failed_series(store)
    child_batch = store.retry_job_batch(batch["id"])
    child = child_batch["jobs"][0]
    store.update_job(child["id"], status="failed")
    grandchild = store.retry_job(child["id"])
    assert grandchild["params"]["_workbench_retry"] == {
        "parent_job_id": child["id"], "root_job_id": original["id"]}
    assert store.get_job_batch(batch["id"])["retryable_count"] == 0
    for retry in (lambda: store.retry_job(original["id"]), lambda: store.retry_job_batch(batch["id"]),
                  lambda: store.retry_job_batch(child_batch["id"])):
        with pytest.raises(RetryConflict):
            retry()
    store.update_job(grandchild["id"], status="succeeded")
    for retry in (lambda: store.retry_job(original["id"]), lambda: store.retry_job(child["id"]),
                  lambda: store.retry_job_batch(batch["id"])):
        with pytest.raises(ValueError):
            retry()
    assert store.list_jobs()["total"] == 3


def test_explicit_retry_after_latest_failure_uses_latest_parent_and_keeps_root(tmp_path):
    store = Store(tmp_path / "latest-failed.sqlite3")
    batch, original = failed_series(store)
    child = store.retry_job(original["id"])
    store.update_job(child["id"], status="failed")
    retried = store.retry_job_batch(batch["id"])
    grandchild = retried["jobs"][0]
    assert grandchild["retry_of_job_id"] == child["id"]
    assert grandchild["params"]["_workbench_retry"] == {
        "parent_job_id": child["id"], "root_job_id": original["id"]}
    store.update_job(grandchild["id"], status="interrupted")
    next_job = store.retry_job(original["id"])
    assert next_job["params"]["_workbench_retry"] == {
        "parent_job_id": grandchild["id"], "root_job_id": original["id"]}


def test_legacy_batch_grandchild_and_individual_event_survive_reopen(tmp_path):
    store = Store(tmp_path / "legacy.sqlite3")
    batch, original = failed_series(store)
    child_batch = store.retry_job_batch(batch["id"])
    child = child_batch["jobs"][0]
    # Simulate an already delivered pre-lineage backup without changing schema.
    with store._transaction(write=True) as connection:
        connection.execute("UPDATE wb_jobs SET params=? WHERE id=?", (json.dumps(original["params"]), child["id"]))
    store.update_job(child["id"], status="failed")
    legacy_grandchild = store.create_job("collect", original["params"])
    store.add_job_event(legacy_grandchild["id"], f"由任务 {child['id']} 重试创建；原记录保留。")
    reopened = Store(store.path)
    assert reopened.get_job_batch(batch["id"])["retryable_count"] == 0
    with pytest.raises(RetryConflict):
        reopened.retry_job_batch(batch["id"])
    reopened.update_job(legacy_grandchild["id"], status="failed")
    newest = reopened.retry_job(original["id"])
    assert newest["params"]["_workbench_retry"] == {
        "parent_job_id": legacy_grandchild["id"], "root_job_id": original["id"]}


def test_resume_queue_state_and_legacy_alias_preserve_evidence(tmp_path):
    store = Store(tmp_path / "research.sqlite3")
    params = {"keyword": "平和", "start_date": "2026-10-01", "end_date": "2026-10-08"}
    original = store.create_job("research", params)
    store.create_research_run(original["id"], params)
    frontier = [{"account_key": "twitter:waiting", "depth": 1}]
    store.checkpoint_research(original["id"], status="cancelled", phase="cancelled",
        frontier=frontier, visited=["twitter:saved"], summary={"record_count": 0})
    store.update_job(original["id"], status="cancelled")
    continuation = store.resume_research_job(original["id"], params)
    run = store.get_research_run(original["id"])
    assert run["job_id"] == continuation["id"]
    assert run["status"] == run["phase"] == store.research_graph(original["id"])["status"] == "queued"
    assert run["frontier"] == frontier and run["visited"] == ["twitter:saved"]
    assert continuation["params"]["_workbench_retry"]["root_job_id"] == original["id"]
    store.request_cancel(continuation["id"])
    with store._transaction(write=True) as connection:
        connection.execute("UPDATE wb_jobs SET params=? WHERE id=?", (json.dumps(params), continuation["id"]))
    latest = store.retry_job(original["id"])
    assert latest["params"]["_workbench_retry"] == {
        "parent_job_id": continuation["id"], "root_job_id": original["id"]}


def test_same_clock_alias_order_cannot_infer_a_cycle():
    jobs = [{"id": "root", "kind": "research", "params": {}},
            {"id": "z-legacy", "kind": "research", "params": {}},
            {"id": "a-new", "kind": "research", "params": {"_workbench_retry": {
                "parent_job_id": "z-legacy", "root_job_id": "root"}}}]
    aliases = [{"job_id": row["id"], "run_id": "root", "created_at": "same-tick"} for row in jobs]
    parents = retry_parent_links(jobs, research_jobs=aliases)
    assert parents == {"a-new": "z-legacy", "z-legacy": "root"}
    assert retry_root("a-new", parents) == "root"
    with pytest.raises(ValueError, match="循环"):
        retry_root("a", {"a": "b", "b": "a"})


def test_postgres_root_locks_have_stable_order_and_refresh_after_lock(tmp_path, monkeypatch):
    store = Store(tmp_path / "pg-contract.sqlite3")
    calls, reads = [], []
    class Cursor:
        def fetchone(self):
            return {"id": "root"}
    class Connection:
        def execute(self, sql, values):
            calls.append((sql, values))
            return Cursor()
    @contextmanager
    def transaction(*, write=False):
        assert write
        yield Connection()
    def index(connection):
        reads.append(len(calls))
        return {"a-child": "a-root", "z-child": "z-root"}
    monkeypatch.setattr(store, "_postgres", True)
    monkeypatch.setattr(store, "_transaction", transaction)
    monkeypatch.setattr(store, "_retry_index", index)
    with store._transaction(write=True) as connection:
        store._lock_retry_roots(connection, ["z-child", "a-child"])
    assert reads == [0, 2]
    assert [values for _, values in calls] == [("a-root",), ("z-root",)]
    assert all(sql.endswith("FOR UPDATE") and "%s" in sql for sql, _ in calls)


def test_hard_collection_cap_with_pending_analysis_can_retry_only_analysis(tmp_path):
    store = Store(tmp_path / "pending-analysis.sqlite3")
    try:
        parameters = {"keyword": "反戦", "start_date": "2026-10-01", "end_date": "2026-10-08", "analysis_mode": "ai"}
        original = store.create_job("research", parameters)
        store.create_research_run(original["id"], parameters)
        summary = {"outcome": "partial", "stop_reason": "record_budget", "collection_complete": False,
                   "collection_outcome": "partial", "collection_stop_reason": "record_budget", "analysis_status": "partial",
                   "analysis_pending_evidence": 80, "phase": "analysis", "record_count": 1}
        store.checkpoint_research(original["id"], summary=summary, phase="finished", status="partial")
        store.update_job(original["id"], status="partial", result={"summary": summary})
        child = store.retry_job(original["id"])
        assert child["params"]["_analysis_only"] is True
        assert child["params"]["resume_run_id"] == original["id"]
        with pytest.raises(RetryConflict):
            store.retry_job(original["id"])
        store.update_job(child["id"], status="succeeded")
        with pytest.raises(ValueError):
            store.retry_job(original["id"])
    finally:
        store.close()


def test_cancelled_analysis_retry_uses_run_summary_when_job_result_is_missing(tmp_path):
    store = Store(tmp_path / "cancelled-analysis.sqlite3")
    try:
        parameters = {"keyword": "反戦", "start_date": "2026-10-01", "end_date": "2026-10-08", "analysis_mode": "ai"}
        original = store.create_job("research", parameters)
        store.create_research_run(original["id"], parameters)
        store.checkpoint_research(original["id"], status="cancelled", phase="cancelled", summary={
            "collection_complete": False, "collection_outcome": "time_budget", "collection_stop_reason": "time_budget",
            "analysis_status": "partial", "analysis_pending_evidence": 1, "phase": "analysis", "record_count": 1})
        store.update_job(original["id"], status="cancelled")
        child = store.retry_job(original["id"])
        assert child["params"]["_analysis_only"] is True
        assert child["params"]["resume_run_id"] == original["id"]
    finally:
        store.close()


def test_hard_collection_cap_without_pending_analysis_stays_nonretryable(tmp_path):
    store = Store(tmp_path / "finished-cap.sqlite3")
    try:
        parameters = {"keyword": "反戦", "start_date": "2026-10-01", "end_date": "2026-10-08", "analysis_mode": "ai"}
        original = store.create_job("research", parameters)
        store.create_research_run(original["id"], parameters)
        summary = {"outcome": "partial", "stop_reason": "record_budget", "collection_complete": False,
                   "collection_outcome": "partial", "analysis_status": "completed", "analysis_pending_evidence": 0}
        store.checkpoint_research(original["id"], summary=summary, phase="finished", status="partial")
        store.update_job(original["id"], status="partial", result={"summary": summary})
        with pytest.raises(ValueError, match="预算"):
            store.retry_job(original["id"])
    finally:
        store.close()


def test_task_series_allows_pending_analysis_after_collection_cap_and_guards_active_child(tmp_path):
    store = Store(tmp_path / "batch-analysis.sqlite3")
    try:
        parameters = {"keyword": "反戦", "start_date": "2026-10-01", "end_date": "2026-10-08", "analysis_mode": "ai", "max_records": 10}
        batch = store.create_job_batch("AI continuation", [{"kind": "research", "params": parameters}], 10)
        original = batch["jobs"][0]
        store.create_research_run(original["id"], parameters)
        summary = {"outcome": "partial", "stop_reason": "record_budget", "collection_complete": False,
                   "collection_outcome": "partial", "collection_stop_reason": "record_budget", "analysis_status": "partial",
                   "analysis_pending_evidence": 80, "phase": "analysis", "record_count": 1}
        store.checkpoint_research(original["id"], summary=summary, phase="finished", status="partial")
        store.update_job(original["id"], status="partial", result={"summary": summary})
        assert store.get_job_batch(batch["id"])["retryable_count"] == 1
        child_batch = store.retry_job_batch(batch["id"])
        child = child_batch["jobs"][0]
        assert child["params"]["_analysis_only"] is True
        assert store.get_job_batch(batch["id"])["retryable_count"] == 0
        with pytest.raises((RetryConflict, ValueError)):
            store.retry_job_batch(batch["id"])
        store.update_job(child["id"], status="succeeded")
        with pytest.raises(ValueError):
            store.retry_job(original["id"])
        assert store.list_jobs()["total"] == 2
    finally:
        store.close()
