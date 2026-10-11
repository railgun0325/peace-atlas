"""Task series creation and retry are atomic and preserve successful evidence."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from app.workbench.store import Store


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "batches.sqlite3")
    yield value
    value.close()


def task(kind="collect", keyword="平和", target=100):
    return {"kind": kind, "params": {"keyword": keyword, "source": "google_news", "max_records": target,
                                       "start_date": "2026-10-01", "end_date": "2026-10-06"}}


def test_create_all_tasks_in_order_above_one_hundred_and_restart(store):
    batch = store.create_job_batch("深入检索任务串", [task(keyword=str(index)) for index in range(5)], 500)
    assert len(batch["jobs"]) == 5 and batch["task_count"] == 5
    assert batch["total_records"] == batch["planned_records"] == 500
    assert batch["status"] == "queued" and batch["progress"] == 0
    assert [job["params"]["keyword"] for job in batch["jobs"]] == [str(number) for number in range(5)]
    assert store.list_jobs()["total"] == 5
    reopened = Store(store.path)
    assert reopened.get_job_batch(batch["id"])["jobs"] == batch["jobs"]
    reopened.close()


def test_batch_create_rolls_back_all_jobs_on_sql_failure(store):
    with sqlite3.connect(store.path) as con:
        con.execute("CREATE TRIGGER fail_batch BEFORE INSERT ON wb_jobs WHEN NEW.params LIKE '%break-here%' BEGIN SELECT RAISE(ABORT, 'disk failed'); END")
    with pytest.raises(sqlite3.IntegrityError, match="disk failed"):
        store.create_job_batch("Atomic", [task(), task(keyword="break-here"), task()], 300)
    assert store.list_jobs()["total"] == 0 and store.list_job_batches()["total"] == 0


@pytest.mark.parametrize("title,tasks,target", [
    ("", [task()], 100), ("bad", [], 100), ("bad", [task()] * 201, 20100),
    ("bad", [task()], 1000001), ("bad", [task()], True),
    ("bad", [task(kind="report")], 100), ("bad", [task(target=101)], 100),
    ("bad", [task(kind="research", target=1000001)], 10000),
    ("bad", [{"kind": "research", "params": {"password": "never", "max_records": 200}}], 200),
])
def test_batch_input_rejected_before_any_write(store, title, tasks, target):
    with pytest.raises(ValueError):
        store.create_job_batch(title, tasks, target)
    assert store.list_jobs()["total"] == 0 and store.list_job_batches()["total"] == 0


def test_failed_batch_does_not_report_artificial_one_hundred_percent(store):
    batch = store.create_job_batch("Mixed", [task(), task(), task()], 300)
    first, second, third = batch["jobs"]
    store.update_job(first["id"], status="succeeded", result={"inserted": 5})
    store.update_job(second["id"], status="failed", progress=20, result={"inserted": 2})
    store.update_job(third["id"], status="cancelled", progress=0)
    final = store.get_job_batch(batch["id"])
    assert final["status"] == "partial" and final["progress"] == 40
    assert final["completed_count"] == 3 and final["succeeded_count"] == 1
    assert final["records_saved"] == 7 and final["target_reached"] is False
    assert final["counts"] == {"succeeded": 1, "failed": 1, "cancelled": 1}


def test_cancel_stops_only_queued_and_running_and_preserves_saved_evidence(store):
    batch = store.create_job_batch("Cancel", [task(), task(), task()], 300)
    first, second, third = batch["jobs"]
    store.update_job(first["id"], status="succeeded", result={"inserted": 3})
    store.update_job(second["id"], status="running", progress=30)
    cancelled = store.cancel_job_batch(batch["id"])
    assert cancelled["cancel_requested_count"] == 2
    assert store.get_job(first["id"])["status"] == "succeeded"
    assert store.get_job(second["id"])["cancel_requested"] is True
    assert store.get_job(third["id"])["status"] == "cancelled"
    assert store.claim_job() is None
    assert len(store.job_events(second["id"])) == len(store.job_events(third["id"])) == 1
    again = store.cancel_job_batch(batch["id"])
    assert again["cancel_requested_count"] == 0 and again["records_saved"] == 3


def test_retry_only_unfinished_tasks_and_preserves_original_batch(store):
    batch = store.create_job_batch("Retry", [task(keyword=str(number)) for number in range(6)], 600)
    statuses = ["succeeded", "partial", "failed", "cancelled", "interrupted", "running"]
    for job, status in zip(batch["jobs"], statuses):
        store.update_job(job["id"], status=status, progress=10)
    retried = store.retry_job_batch(batch["id"])
    assert [job["params"]["keyword"] for job in retried["jobs"]] == ["1", "2", "3", "4"]
    assert retried["parent_batch_id"] == batch["id"] and retried["total_records"] == 400
    assert len(store.get_job_batch(batch["id"])["jobs"]) == 6
    with pytest.raises(ValueError, match="重试"):
        store.retry_job_batch(batch["id"])
    for job in retried["jobs"]:
        store.update_job(job["id"], status="succeeded")
    with pytest.raises(ValueError, match="重试"):
        store.retry_job_batch(batch["id"])


def test_research_retry_resumes_same_run_without_copying_existing_material(store):
    batch = store.create_job_batch("Research Retry", [task(kind="research", target=200)], 200)
    original = batch["jobs"][0]
    store.create_research_run(original["id"], original["params"])
    store.checkpoint_research(original["id"], nodes=[{"account_key": "twitter:peace", "depth": 0, "status": "completed"}],
        visited=["twitter:peace"], frontier=[], status="partial")
    store.update_job(original["id"], status="partial", result={"run_id": original["id"], "inserted": 1})
    retried = store.retry_job_batch(batch["id"])
    child = retried["jobs"][0]
    assert child["params"]["resume_run_id"] == original["id"]
    run = store.get_research_run(child["id"])
    assert run["id"] == original["id"] and run["job_id"] == child["id"]
    assert len(run["nodes"]) == 1 and run["visited"] == ["twitter:peace"]
    assert store.list_research_runs()["total"] == 1


@pytest.mark.parametrize("stop_reason", ["record_budget", "account_budget", "edge_budget"])
def test_hard_budget_partial_is_not_counted_or_retried(store, stop_reason):
    batch = store.create_job_batch("Budget reached", [task(kind="research", target=200)], 200)
    job = batch["jobs"][0]
    store.create_research_run(job["id"], job["params"])
    store.checkpoint_research(job["id"], status="partial", summary={"stop_reason": stop_reason})
    store.update_job(job["id"], status="partial", result={"run_id": job["id"], "summary": {"stop_reason": stop_reason}})
    final = store.get_job_batch(batch["id"])
    assert final["status"] == "partial" and final["retryable_count"] == 0
    with pytest.raises(ValueError, match="重试"):
        store.retry_job_batch(batch["id"])
    assert store.list_jobs()["total"] == 1 and store.list_job_batches()["total"] == 1
    assert store.get_research_run(job["id"])["job_id"] == job["id"]


def test_batch_retry_keeps_time_budget_and_legacy_partial_but_skips_hard_caps(store):
    reasons = ["record_budget", "account_budget", "edge_budget", "time_budget", None]
    batch = store.create_job_batch("Mixed budgets", [task(kind="research", keyword=str(index), target=200)
                                                    for index in range(len(reasons))], 1000)
    for job, reason in zip(batch["jobs"], reasons):
        result = {"summary": {"stop_reason": reason}} if reason else None
        store.update_job(job["id"], status="partial", result=result)
    assert store.get_job_batch(batch["id"])["retryable_count"] == 2
    retried = store.retry_job_batch(batch["id"])
    assert [job["params"]["keyword"] for job in retried["jobs"]] == ["3", "4"]
    assert retried["total_records"] == 400
    assert store.get_job_batch(batch["id"])["status"] == "partial"


@pytest.mark.parametrize("child_status,summary", [("succeeded", {}), ("partial", {"stop_reason": "record_budget"})])
def test_old_cancelled_parent_does_not_repeat_success_or_capped_child(store, child_status, summary):
    batch = store.create_job_batch("Cancelled parent", [task(kind="research", target=200)], 200)
    parent = batch["jobs"][0]
    store.create_research_run(parent["id"], parent["params"])
    store.cancel_job_batch(batch["id"])
    assert store.get_job_batch(batch["id"])["retryable_count"] == 1
    child_batch = store.retry_job_batch(batch["id"])
    child = child_batch["jobs"][0]
    assert store.get_job_batch(batch["id"])["retryable_count"] == 0
    store.checkpoint_research(parent["id"], status=child_status, summary=summary)
    store.update_job(child["id"], status=child_status, result={"run_id": parent["id"], "summary": summary})
    assert store.get_job_batch(batch["id"])["retryable_count"] == 0
    assert store.get_job_batch(child_batch["id"])["retryable_count"] == 0
    with pytest.raises(ValueError, match="重试"):
        store.retry_job_batch(batch["id"])
    assert store.list_jobs()["total"] == 2 and store.list_job_batches()["total"] == 2
    assert store.get_research_run(parent["id"])["job_id"] == child["id"]


def test_concurrent_retry_creates_one_series(store):
    batch = store.create_job_batch("Concurrent Retry", [task()], 100)
    store.update_job(batch["jobs"][0]["id"], status="failed")
    def retry(_):
        try:
            return store.retry_job_batch(batch["id"])
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(retry, range(8)))
    assert len([result for result in results if result]) == 1
    assert store.list_job_batches()["total"] == 2
    assert store.list_jobs()["total"] == 2


def test_batch_listing_paginates_every_series_without_queue_limit(store):
    for number in range(55):
        store.create_job_batch(f"Series {number}", [task()], 100)
    listing = store.list_job_batches(page=2, page_size=50)
    assert listing["total"] == 55 and len(listing["items"]) == 5
    assert store.list_jobs()["total"] == 55


def test_missing_series_actions_are_explicit(store):
    assert store.get_job_batch("missing") is None
    for action in (store.cancel_job_batch, store.retry_job_batch):
        with pytest.raises(LookupError):
            action("missing")


def test_invalid_resume_batch_creation_rolls_back_every_job(store):
    with pytest.raises(ValueError, match="不存在"):
        store.create_job_batch("Invalid resume", [task(), {"kind": "research", "params": {"resume_run_id": "missing", "max_records": 200}}], 300)
    assert store.list_jobs()["total"] == 0 and store.list_job_batches()["total"] == 0


def test_same_clock_tick_keeps_planned_task_order_when_queue_claims(store, monkeypatch):
    from app.workbench import store as store_module
    monkeypatch.setattr(store_module, "_now", lambda: "2026-10-06T00:00:00.000000Z")
    batch = store.create_job_batch("Stable FIFO", [task(keyword=str(index)) for index in range(10)], 1000)
    expected = [job["id"] for job in batch["jobs"]]
    actual = [store.claim_job()["id"] for _ in range(10)]
    assert actual == expected
