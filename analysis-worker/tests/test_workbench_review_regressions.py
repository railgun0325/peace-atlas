"""Independent release-review regressions; only temporary stores and fake work."""
import asyncio
from pathlib import Path
import time

import pytest

from app.entities import load_entity_config, match_entities
from app.workbench.jobs import JobRunner
from app.workbench.store import Store


def test_citizens_and_referendum_do_not_identify_democratic_party_for_people():
    configuration = load_entity_config(Path(__file__).resolve().parents[1] / "config" / "japan_peace_entities.yml")
    article_title = "「9条守る」の中身はバラバラ...「解釈改憲」に異議 市民の熟議で2つの改憲案、模擬国民投票目指す"
    matches = match_entities(article_title + " 主権者である国民の判断を問う。", configuration)
    assert "国民民主党" not in {row["normalized_name"] for row in matches}
    for explicit_name in ("国民民主党", "Democratic Party for the People", "DPP"):
        matches = match_entities(explicit_name + " が声明を公表。", configuration)
        assert "国民民主党" in {row["normalized_name"] for row in matches}


def test_worker_monitor_failure_stops_inflight_work(tmp_path, monkeypatch):
    """A monitor read error must not leave an unowned paid request running."""
    store = Store(tmp_path / "monitor-failure.sqlite3")
    store.create_job("collect", {})
    job = store.claim_job()
    runner = JobRunner(store)

    async def scenario():
        aborted = asyncio.Event()
        tasks = []

        async def inflight(job, committing):
            tasks.append(asyncio.current_task())
            try:
                await asyncio.Event().wait()
            finally:
                aborted.set()

        def failed_monitor(job_id):
            raise OSError("injected transient status read failure")

        monkeypatch.setattr(runner, "_perform", inflight)
        monkeypatch.setattr(store, "get_job", failed_monitor)
        try:
            await runner._execute(job)
            stopped_before_return = aborted.is_set()
        finally:
            # Cleanup fixture work even when testing the unfixed implementation.
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        assert stopped_before_return, "JobRunner returned after failure with live unowned pipeline work"

    asyncio.run(scenario())


def test_committed_result_wins_when_monitor_read_fails_at_completion(tmp_path, monkeypatch):
    store = Store(tmp_path / "commit-race.sqlite3")
    store.create_job("report", {})
    job = store.claim_job()
    runner = JobRunner(store)
    original_get = store.get_job

    async def scenario():
        release = asyncio.Event()
        loop = asyncio.get_running_loop()

        async def committing_result(job, committing):
            committing.set()
            await release.wait()
            return {"report_id": "persisted-report", "source_count": 1}

        def monitor_failure(job_id):
            # Complete the commit while its concurrent status read is failing.
            loop.call_soon_threadsafe(release.set)
            time.sleep(0.03)
            raise OSError("injected read error at completed commit")

        monkeypatch.setattr(runner, "_perform", committing_result)
        monkeypatch.setattr(store, "get_job", monitor_failure)
        await runner._execute(job)

    asyncio.run(scenario())
    saved = original_get(job["id"])
    assert saved["status"] == "succeeded"
    assert saved["result"]["report_id"] == "persisted-report"
    assert store.overview()["stats"]["active_jobs"] == 0


def test_failure_to_append_final_log_cannot_overwrite_success(tmp_path, monkeypatch):
    store = Store(tmp_path / "final-event.sqlite3")
    store.create_job("collect", {})
    job = store.claim_job()
    runner = JobRunner(store)
    original_event = store.add_job_event

    async def completed(job, committing):
        committing.set()
        return {"inserted": 1}

    def event_failure(jid, message, level="info"):
        if message.startswith("任务完成"):
            raise OSError("injected terminal log write failure")
        return original_event(jid, message, level)

    monkeypatch.setattr(runner, "_perform", completed)
    monkeypatch.setattr(store, "add_job_event", event_failure)
    asyncio.run(runner._execute(job))
    assert store.get_job(job["id"])["status"] == "succeeded"
    assert store.get_job(job["id"])["result"] == {"inserted": 1}
    assert store.overview()["stats"]["active_jobs"] == 0


def test_shutdown_commit_failure_records_terminal_state_before_exit(tmp_path, monkeypatch):
    store = Store(tmp_path / "shutdown-commit.sqlite3")
    store.create_job("report", {})
    job = store.claim_job()
    runner = JobRunner(store)

    async def scenario():
        commit_started = asyncio.Event()
        release = asyncio.Event()

        async def failed_commit(job, committing):
            committing.set()
            commit_started.set()
            await release.wait()
            raise OSError("injected commit failure while exiting")

        monkeypatch.setattr(runner, "_perform", failed_commit)
        executing = asyncio.create_task(runner._execute(job))
        await commit_started.wait()
        executing.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await executing

    asyncio.run(scenario())
    failed = store.get_job(job["id"])
    assert failed["status"] == "failed" and failed["finished_at"]
    assert failed["result"] is None
    assert store.overview()["stats"]["active_jobs"] == 0
