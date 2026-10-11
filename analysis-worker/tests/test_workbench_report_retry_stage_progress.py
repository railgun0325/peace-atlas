"""Changed retry inputs/plans must not project old stage candidates as progress."""
import asyncio
import copy
import os

import httpx
import pytest

from app.workbench import harness_runtime, pipeline, report_analysis, report_generation
from app.workbench.job_retries import report_resume_checkpoint
from app.workbench.jobs import JobRunner
from app.workbench.report_inputs import capture_report_input_snapshot
from app.workbench.report_progress import report_note_progress
from app.workbench.store import Store


STAGES = {"harness_state", "synthesis_state", "report_generation", "progress"}


@pytest.fixture(autouse=True)
def isolated_model_boundary(monkeypatch):
    for key in list(os.environ):
        if key.startswith(("DEEPSEEK_", "WORKBENCH_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-only-not-a-real-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-flash")
    def forbidden(*args, **kwargs):
        raise AssertionError("Retry progress fixtures must not send HTTP requests")
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)


def request(mode="dsh"):
    return {"title": "保存入力の段階回復", "mode": "ai", "analysis_harness": mode,
            "start_date": "2026-10-01", "end_date": "2026-10-09",
            "include_news": True, "include_social": True, "read_original": False,
            "harness_max_model_calls": 64, "harness_max_tool_calls": 512,
            "time_budget_seconds": 3600}


def material(text="公開の資料本文。"):
    return {"type": "news", "title": "公開の資料", "content": text,
            "url": "https://fixture.example/report-stage", "source_name": "公開資料",
            "published_at": "2026-10-08", "metadata": {"content_kind": "body"}}


def note_progress(**changes):
    return {"stage": "evidence_notes", "level": 1, "planned_batch_count": 3,
            "current_batch_number": 2, "completed_unit_count": 200, "total_unit_count": 601,
            "model_request_count": 13, "completed_request_count": 11,
            "reused_reviewed_batch_count": 0, **changes}


def checkpoint(snapshot):
    sources, _ = report_analysis.source_samples(snapshot["items"])
    units, _, _ = report_analysis._reading_units(sources)
    review = {"citation_id": "S001", "quote": "公開の資料本文。",
              "reading": "公開資料の記述。", "limitations": "独立した検証ではない。"}
    return {"version": 3, "complete": True, "source_reviews": [review],
            "fragment_reviews": [{**review, "unit_id": "S001"}],
            "reviewed_fragment_fingerprints": {k: report_analysis._digest(v) for k, v in units.items()},
            "research_context_fingerprint": report_analysis._digest(report_analysis._reading_context(None)),
            "coverage_progress": {"input_source_count": 1, "reviewed_source_count": 1,
                                  "pending_source_count": 0},
            "harness_state": {"complete": True, "input_fingerprint": "old-harness",
                              "model_calls": 59, "tool_calls": 255},
            "synthesis_state": {"complete": True, "input_fingerprint": "old-synthesis"},
            "report_generation": {"phase": "review_pending", "complete": False,
                "input_fingerprint": "old-generation", "draft": "# Old fixture draft",
                "attempts": [{"source_review_input_synthesis": {"progress": note_progress()}}]},
            "progress": note_progress()}


def failed_parent(store, *, mode="dsh", old_input="valid"):
    store.import_items([material()])
    params = request(mode)
    snapshot = asyncio.run(pipeline.prepare_report_input(
        store.report_items(params["start_date"], params["end_date"]), params))
    frozen = capture_report_input_snapshot(snapshot, params)
    result = {"report_checkpoint": checkpoint(snapshot)}
    if old_input != "missing":
        result["report_input_snapshot"] = copy.deepcopy(frozen)
        if old_input == "corrupt":
            result["report_input_snapshot"]["snapshot"]["items"][0]["content"] = "tampered"
    job = store.create_job("report", params)
    store.update_job(job["id"], status="failed", error="fixture old stage failed", result=result)
    return store.get_job(job["id"])


def retry(store, parent, *, input_mode="original", **options):
    preview = store.report_retry_preview(parent["id"])
    selected = preview["original_input" if input_mode == "original" else "current_input"]
    return store.retry_job(parent["id"], report_options={
        "input_mode": input_mode, "input_sha256": selected["sha256"], **options})


def assert_readings_retained(child, parent):
    state = child["result"]["report_checkpoint"]
    original = parent["result"]["report_checkpoint"]
    for key in ("source_reviews", "fragment_reviews", "reviewed_fragment_fingerprints",
                "coverage_progress", "research_context_fingerprint"):
        assert state[key] == original[key]
    assert not STAGES.intersection(state)


def test_latest_changed_child_does_not_publish_old_draft_before_first_callback(tmp_path):
    store = Store(tmp_path / "changed.sqlite3")
    parent = failed_parent(store)
    store.import_items([material("変更された新しい公開本文。")])
    child = retry(store, parent, input_mode="latest")
    assert_readings_retained(child, parent)
    assert report_note_progress(child["result"]["report_checkpoint"]) is None
    assert store.get_job(parent["id"]) == parent


@pytest.mark.parametrize("old_input", ["missing", "corrupt"])
def test_latest_unverifiable_original_cannot_publish_completed_stage_candidates(tmp_path, old_input):
    store = Store(tmp_path / "unverifiable.sqlite3")
    parent = failed_parent(store, old_input=old_input)
    child = retry(store, parent, input_mode="latest")
    assert_readings_retained(child, parent)
    assert store.get_job(parent["id"]) == parent


@pytest.mark.parametrize("input_mode", ["original", "latest"])
@pytest.mark.parametrize("before,after", [("off", "dsh"), ("dsh", "off")])
def test_same_input_analysis_plan_switch_clears_only_later_candidates(tmp_path, input_mode, before, after):
    store = Store(tmp_path / "plan.sqlite3")
    parent = failed_parent(store, mode=before)
    child = retry(store, parent, input_mode=input_mode, analysis_harness=after)
    assert child["result"]["report_input_snapshot"]["input_fingerprint"] == parent["result"]["report_input_snapshot"]["input_fingerprint"]
    assert_readings_retained(child, parent)
    assert store.get_job(parent["id"]) == parent


@pytest.mark.parametrize("input_mode", ["original", "latest"])
@pytest.mark.parametrize("key,value", [("harness_max_model_calls", 128),
    ("harness_max_tool_calls", 1024), ("time_budget_seconds", 7200)])
def test_same_input_plan_only_relaxed_limits_keep_completed_candidates(tmp_path, input_mode, key, value):
    store = Store(tmp_path / "relaxed.sqlite3")
    parent = failed_parent(store)
    child = retry(store, parent, input_mode=input_mode, **{key: value})
    assert child["result"]["report_checkpoint"] == parent["result"]["report_checkpoint"]
    resolved = report_resume_checkpoint(child["params"], parent, store.get_job,
        frozen_input=child["result"]["report_input_snapshot"])
    assert resolved == parent["result"]["report_checkpoint"]
    assert store.get_job(parent["id"]) == parent


def test_cancelled_changed_latest_then_original_does_not_restore_old_ancestor_stages(tmp_path):
    store = Store(tmp_path / "cancelled-chain.sqlite3")
    root = failed_parent(store)
    store.import_items([material("変更された新しい公開本文。")])
    changed = retry(store, root, input_mode="latest")
    store.request_cancel(changed["id"])
    parent = store.get_job(changed["id"])
    child = retry(store, parent)
    resolved = report_resume_checkpoint(child["params"], parent, store.get_job,
        frozen_input=child["result"]["report_input_snapshot"])
    assert not STAGES.intersection(resolved)
    assert_readings_retained(child, root)
    assert store.get_job(root["id"]) == root and store.get_job(parent["id"]) == parent


def test_same_input_plan_recovers_missing_stages_from_actual_store_ancestor(tmp_path):
    store = Store(tmp_path / "same-chain.sqlite3")
    root = failed_parent(store)
    parent = retry(store, root)
    result = copy.deepcopy(parent["result"])
    for key in STAGES:
        result["report_checkpoint"].pop(key, None)
    store.update_job(parent["id"], status="failed", result=result, error="fixture absent legacy stages")
    parent = store.get_job(parent["id"])
    child = retry(store, parent)
    resolved = report_resume_checkpoint(child["params"], parent, store.get_job,
        frozen_input=child["result"]["report_input_snapshot"])
    for key in STAGES - {"progress"}:
        assert resolved[key] == root["result"]["report_checkpoint"][key]
    assert store.get_job(root["id"]) == root and store.get_job(parent["id"]) == parent


@pytest.mark.parametrize("new_draft_saved", [False, True])
def test_real_runner_callbacks_show_fresh_stages_and_preserve_failure_checkpoint(tmp_path, monkeypatch, new_draft_saved):
    store = Store(tmp_path / "runner.sqlite3")
    parent = failed_parent(store)
    store.import_items([material("変更された新しい公開本文。")])
    child = retry(store, parent, input_mode="latest")
    records = []
    original_update = store.update_job
    def record_update(identity, **fields):
        if "report_checkpoint" in (fields.get("result") or {}) and "progress" in fields:
            records.append(copy.deepcopy(fields))
        return original_update(identity, **fields)
    monkeypatch.setattr(store, "update_job", record_update)
    reading = copy.deepcopy(parent["result"]["report_checkpoint"])
    for key in STAGES:
        reading.pop(key, None)
    async def read(*args, previous_review, checkpoint, **options):
        assert not STAGES.intersection(previous_review)
        await checkpoint(copy.deepcopy(reading))
        return copy.deepcopy(reading)
    async def harness(*args, previous_state, checkpoint, **options):
        assert previous_state is None
        state = {"complete": True, "input_fingerprint": "new-harness", "runtime_version": "fixture",
                 "model_calls": 3, "tool_calls": 7, "findings": {"limitations": ["fixture"]}}
        await checkpoint(state)
        return state
    async def generation(*args, previous_state, previous_synthesis, synthesis_checkpoint, checkpoint, **options):
        assert previous_state is previous_synthesis is None
        await synthesis_checkpoint({"complete": False, "input_fingerprint": "new-synthesis",
            "progress": note_progress(current_batch_number=1, completed_unit_count=0,
                total_unit_count=1, model_request_count=4, completed_request_count=3)})
        if new_draft_saved:
            await checkpoint({"complete": False, "phase": "review_pending", "draft": "# New fixture draft",
                              "input_fingerprint": "new-generation", "attempts": []})
        raise RuntimeError("fixture compression or source-review failure")
    monkeypatch.setattr(report_analysis, "read_sources", read)
    monkeypatch.setattr(harness_runtime, "run_evidence_harness", harness)
    monkeypatch.setattr(report_generation, "generate_checked_report", generation)
    asyncio.run(JobRunner(store)._execute(store.claim_job()))
    saved = store.get_job(child["id"])
    assert saved["status"] == "failed"
    assert [row["progress"] for row in records[:3]] == [55, 55, 55]
    current = records[2]["result"]["report_checkpoint"]
    notes = report_note_progress(current)
    assert notes["scope"] == "synthesis" and notes["progress"]["current_batch_number"] == 1
    assert notes["progress"]["completed_unit_count"] == 0
    messages = [row["message"] for row in store.job_events(child["id"])]
    assert not any("200/601" in message or "11/13" in message for message in messages)
    assert sum("报告草稿已保存" in message for message in messages) == int(new_draft_saved)
    if new_draft_saved:
        assert records[3]["progress"] == 88
        assert saved["result"]["report_checkpoint"]["report_generation"]["draft"] == "# New fixture draft"
    else:
        assert "report_generation" not in saved["result"]["report_checkpoint"]
    assert saved["result"]["report_checkpoint"]["harness_state"]["input_fingerprint"] == "new-harness"
    assert store.get_job(parent["id"]) == parent


def test_real_runner_cancel_after_new_harness_preserves_only_current_evidence(tmp_path, monkeypatch):
    store = Store(tmp_path / "runner-cancel.sqlite3")
    parent = failed_parent(store, mode="off")
    child = retry(store, parent, analysis_harness="dsh")
    reading = copy.deepcopy(parent["result"]["report_checkpoint"])
    for key in STAGES:
        reading.pop(key, None)
    async def read(*args, previous_review, checkpoint, **options):
        assert not STAGES.intersection(previous_review)
        await checkpoint(copy.deepcopy(reading))
        return copy.deepcopy(reading)
    async def harness(*args, previous_state, checkpoint, **options):
        assert previous_state is None
        store.request_cancel(child["id"])
        await checkpoint({"complete": True, "input_fingerprint": "new-harness", "model_calls": 3,
                          "tool_calls": 7, "runtime_version": "fixture", "findings": {}})
        raise AssertionError("Cancellation must stop after the persisted harness")
    async def forbidden_generation(*args, **options):
        raise AssertionError("Cancelled report must not enter generation")
    monkeypatch.setattr(report_analysis, "read_sources", read)
    monkeypatch.setattr(harness_runtime, "run_evidence_harness", harness)
    monkeypatch.setattr(report_generation, "generate_checked_report", forbidden_generation)
    asyncio.run(JobRunner(store)._execute(store.claim_job()))
    saved = store.get_job(child["id"])
    assert saved["status"] == "cancelled"
    state = saved["result"]["report_checkpoint"]
    assert state["harness_state"]["input_fingerprint"] == "new-harness"
    assert not {"synthesis_state", "report_generation", "progress"}.intersection(state)
    assert state["source_reviews"] == reading["source_reviews"]
    assert not any("报告草稿已保存" in row["message"] for row in store.job_events(child["id"]))
    assert store.get_job(parent["id"]) == parent
