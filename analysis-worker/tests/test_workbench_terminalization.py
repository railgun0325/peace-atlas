"""A saved result retains its lane until terminalization becomes durable."""
import asyncio

import pytest

from app.workbench import pipeline
from app.workbench.jobs import JobRunner
from app.workbench.store import Store


def test_dependency_cancellation_is_failed_and_does_not_stop_next_queued_job(tmp_path, monkeypatch):
    store = Store(tmp_path / "dependency-cancel.sqlite3")
    first = store.create_job("collect", {})
    second = store.create_job("collect", {})
    runner = JobRunner(store)
    async def perform(job, committing):
        if job["id"] == first["id"]:
            raise asyncio.CancelledError("dependency self-cancelled")
        committing.set()
        return {"inserted": 1, "diagnostics": []}
    monkeypatch.setattr(runner, "_perform", perform)
    async def exercise():
        await runner.start()
        try:
            await until(lambda: store.get_job(second["id"])["status"] == "succeeded")
            assert not runner.task.done()
        finally:
            await runner.stop()
    asyncio.run(exercise())
    assert store.get_job(first["id"])["status"] == "failed"
    assert "应用仍在运行" in store.get_job(first["id"])["error"]
    assert store.get_job(second["id"])["result"]["inserted"] == 1


async def until(predicate, timeout=4):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("shutdown", [False, True])
def test_real_database_terminal_fault_blocks_next_work_and_preserves_report_on_exit(tmp_path, monkeypatch, shutdown):
    store = Store(tmp_path / "finalization.sqlite3")
    params = {"start_date": "2026-10-08", "end_date": "2026-10-08", "mode": "evidence"}
    first = store.create_job("report", {**params, "title": "First"})
    second = store.create_job("report", {**params, "title": "Second"})
    with store._transaction(write=True) as connection:
        connection.execute("""CREATE TRIGGER terminal_fault BEFORE UPDATE OF status ON wb_jobs
            WHEN NEW.id='%s' AND NEW.status IN ('succeeded','failed','interrupted','cancelled','partial')
            BEGIN SELECT RAISE(ABORT, 'temporary terminal storage fault'); END""" % first["id"])
    calls = []
    async def report(material, request):
        calls.append(request["title"])
        return {"title": request["title"], "markdown": "# Saved report\nSource snapshot.",
            "start_date": request["start_date"], "end_date": request["end_date"],
            "mode": "evidence", "source_count": 0, "snapshot": []}
    monkeypatch.setattr(pipeline, "build_report", report)
    runner = JobRunner(store)
    async def scenario():
        await runner.start()
        stopping = None
        try:
            await until(lambda: store.list_reports()["total"] == 1)
            await asyncio.sleep(0.15)
            assert calls == ["First"]
            assert store.get_job(second["id"])["status"] == "queued"
            if shutdown:
                stopping = asyncio.create_task(runner.stop())
                await asyncio.sleep(0.15)
                assert not stopping.done()
            with store._transaction(write=True) as connection:
                connection.execute("DROP TRIGGER terminal_fault")
            if stopping:
                await asyncio.wait_for(stopping, 3)
                assert store.get_job(second["id"])["status"] == "queued"
            await until(lambda: store.get_job(first["id"])["status"] == "succeeded")
            saved = store.get_job(first["id"])
            assert store.get_report(saved["result"]["report_id"])["job_id"] == first["id"]
            reopened = Store(store.path)
            assert reopened.get_job(first["id"])["result"] == saved["result"]
            if stopping:
                assert reopened.recover_jobs() == 0  # No other live worker owns this database.
                await runner.start()
            await until(lambda: store.get_job(second["id"])["status"] == "succeeded")
            assert reopened.recover_jobs() == 0
            assert calls == ["First", "Second"]
            assert store.list_reports()["total"] == 2
            assert store.overview()["stats"]["active_jobs"] == 0
        finally:
            with store._transaction(write=True) as connection:
                connection.execute("DROP TRIGGER IF EXISTS terminal_fault")
            await runner.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize("after_commit", [False, True])
def test_short_terminal_write_failure_preserves_result_without_repeating_work(tmp_path, monkeypatch, after_commit):
    store = Store(tmp_path / "short-fault.sqlite3")
    original = store.create_job("collect", {})
    claimed = store.claim_job()
    runner = JobRunner(store)
    update, calls, writes = store.update_job, [], []
    async def perform(job, committing):
        calls.append(job["id"])
        committing.set()
        return {"inserted": 3, "duplicates": 1, "diagnostics": []}
    def transient(job_id, **fields):
        writes.append(fields)
        if len(writes) == 1:
            if after_commit:
                update(job_id, **fields)
            raise OSError("short terminal write fault")
        return update(job_id, **fields)
    monkeypatch.setattr(runner, "_perform", perform)
    monkeypatch.setattr(store, "update_job", transient)
    asyncio.run(runner._execute(claimed))
    saved = store.get_job(original["id"])
    assert saved["status"] == "succeeded"
    assert saved["result"] == {"inserted": 3, "duplicates": 1, "diagnostics": []}
    assert calls == [original["id"]] and len(writes) == 2
    assert store.overview()["stats"]["active_jobs"] == 0
