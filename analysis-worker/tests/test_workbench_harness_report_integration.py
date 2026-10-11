"""Report/API integration using temp SQLite, synthetic HTTP and host stdio.

This is a host protocol fixture, not real-provider or DSH SDK acceptance.
"""
import asyncio
import copy
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.workbench import harness_runtime, pipeline
from app.workbench.server import create_app
from app.workbench.store import Store
from test_workbench_harness_runtime import (
    QUOTE, REASONING, SYNTHETIC_KEY, fake_sidecar, final_findings, response,
)


@pytest.fixture(autouse=True)
def synthetic_model_settings(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", SYNTHETIC_KEY)
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://host-route.example/v1")
    monkeypatch.setenv("DEEPSEEK_MODEL", "fixture-model")
    monkeypatch.delenv("DEEPSEEK_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.delenv("DEEPSEEK_MAX_OUTPUT_TOKENS", raising=False)


def source(number):
    text = ("第一条公开来源：" if number == 1 else "第二条公开来源：") + QUOTE
    return {"type": "social", "source_type": "twitter", "account_key": "twitter:alpha",
        "title": text, "content": text, "source_name": "alpha",
        "url": f"https://x.com/alpha/status/{number}", "published_at": "2026-10-08T00:00:00Z",
        "metadata": {"content_kind": "body", "collected_at": "2026-10-09T00:00:00Z", "collection_method": "visible_dom"}}


def create_run(store):
    params = {"research_goal": "比较公开计划与实际举行的证据", "source": "twitter",
        "start_date": "2026-10-01", "end_date": "2026-10-09", "max_accounts": 30}
    job = store.create_job("research", params)
    run = store.create_research_run(job["id"], params)
    store.checkpoint_research(run["id"], nodes=[{"account_key": "twitter:alpha"}],
        items=[source(1), source(2)], status="succeeded", phase="finished",
        summary={"stop_reason": "account_budget", "visited_count": 1},
        analysis={"ai": {"complete": True}, "affiliations": []})
    store.update_job(job["id"], status="succeeded")
    return run


def report_params(run, harness="dsh"):
    return {"research_run_id": run["id"], "start_date": "2026-10-01", "end_date": "2026-10-09",
        "mode": "ai", "read_original": False, "analysis_harness": harness,
        "harness_max_model_calls": 6, "harness_max_tool_calls": 12}


def terminal(client, identity):
    for _ in range(500):
        job = client.get("/api/v2/jobs/" + identity).json()
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.01)
    raise AssertionError("Host fixture report job did not reach a terminal state")


def synthetic_transport(monkeypatch, *, failed_drafts=0):
    native = httpx.AsyncClient
    calls, remaining_failures = [], [failed_drafts]
    def handler(request):
        assert str(request.url) == "https://host-route.example/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer " + SYNTHETIC_KEY
        payload = json.loads(request.content)
        if payload.get("tools"):
            calls.append(("harness", copy.deepcopy(payload), None))
            return httpx.Response(200, json=response(reasoning_content=REASONING))
        data = json.loads(payload["messages"][1]["content"])
        if "candidate_report" in data:
            kind, content = "review", json.dumps({"accepted": True, "issues": []})
        elif payload.get("response_format"):
            kind = "reading"
            content = json.dumps({"source_reviews": [{"citation_id": row["citation_id"],
                "quote": row["content_fragments"][0]["text"][:40],
                "reading": "保存来源仅陈述计划，不能当作已经举行。", "limitations": "尚无实际执行的独立资料。"}
                for row in data["sources"]]}, ensure_ascii=False)
        else:
            kind = "draft"
            if remaining_failures[0]:
                remaining_failures[0] -= 1
                calls.append((kind, copy.deepcopy(payload), copy.deepcopy(data)))
                return httpx.Response(503, text="synthetic report draft failure")
            content = "# 公开计划与执行证据\n\n来源仅称会议仍属计划，尚未举行；是否实际执行仍不足以判断。[S001][S002]"
        calls.append((kind, copy.deepcopy(payload), copy.deepcopy(data)))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": content, "reasoning_content": REASONING}}]})
    monkeypatch.setattr(pipeline.httpx, "AsyncClient", lambda **kwargs:
        native(transport=httpx.MockTransport(handler), **kwargs))
    return calls


def schema_snapshot(store):
    with store._transaction() as connection:
        return [(row["name"], row["sql"]) for row in store._execute(connection,
            "SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name").fetchall()]


def create_report(client, run, harness="dsh"):
    created = client.post("/api/v2/jobs", json={"kind": "report", "params": report_params(run, harness)})
    assert created.status_code == 202, created.text
    return terminal(client, created.json()["id"])


def test_api_complete_reading_harness_checked_generation_and_snapshot_coexist_without_ddl(monkeypatch, tmp_path):
    _, processes = fake_sidecar(monkeypatch, tmp_path, "model")
    calls = synthetic_transport(monkeypatch)
    store = Store(tmp_path / "host-integration.sqlite3")
    run = create_run(store)
    before_schema = schema_snapshot(store)
    with TestClient(create_app(store)) as client:
        job = create_report(client, run)
        assert job["status"] == "succeeded", job
        saved = client.get("/api/v2/reports/" + job["result"]["report_id"]).json()
    snapshot = saved["snapshot"]
    assert saved["mode"] == "ai" and saved["source_count"] == 2
    assert snapshot["analysis_harness"] == "dsh" and snapshot["harness_state"]["complete"] is True
    assert snapshot["harness_state"]["model_calls"] == 1 and snapshot["harness_state"]["tool_calls"] == 2
    assert snapshot["analysis_review"]["reading_state"]["complete"] is True
    assert {row["citation_id"] for row in snapshot["analysis_review"]["source_reviews"]} == {"S001", "S002"}
    assert all(row["complete_text_sent"] for row in snapshot["analysis_review"]["coverage"])
    assert snapshot["report_generation"]["complete"] is True and snapshot["report_quality_review"]["complete"] is True
    assert [kind for kind, _, _ in calls] == ["reading", "harness", "draft", "review"]
    draft = next(data for kind, _, data in calls if kind == "draft")
    assert draft["harness_research"]["findings"] == final_findings()
    assert draft["harness_research"]["independent_fact_verification"] is False
    # Success replaces the job's progress with a small report receipt. The
    # report snapshot remains the durable complete reading/generation record.
    assert store.get_job(job["id"])["result"]["report_id"] == saved["id"]
    checkpoint = snapshot["analysis_review"]["reading_state"]
    assert checkpoint["complete"] is True and checkpoint["harness_state"]["complete"] is True
    assert checkpoint["report_generation"]["complete"] is True
    assert REASONING not in json.dumps(saved, ensure_ascii=False)
    assert SYNTHETIC_KEY not in json.dumps(saved, ensure_ascii=False)
    assert all(account["status"] == "candidate" for account in store.get_research_run(run["id"])["accounts"])
    assert schema_snapshot(store) == before_schema
    assert all("harness" not in sql.lower() for _, sql in before_schema)
    assert all(process.returncode is not None for process in processes)


def test_api_failed_draft_retry_reuses_reading_and_completed_harness_without_extra_model_call(monkeypatch, tmp_path):
    _, processes = fake_sidecar(monkeypatch, tmp_path, "model")
    calls = synthetic_transport(monkeypatch, failed_drafts=1)
    store = Store(tmp_path / "retry-harness.sqlite3")
    run = create_run(store)
    with TestClient(create_app(store)) as client:
        failed = create_report(client, run)
        assert failed["status"] == "failed", failed
        previous = failed["result"]["report_checkpoint"]
        assert previous["complete"] is True and previous["harness_state"]["complete"] is True
        assert not previous["report_generation"]["complete"]
        assert store.list_reports()["total"] == 0
        retried = client.post(f"/api/v2/jobs/{failed['id']}/retry")
        assert retried.status_code == 202, retried.text
        completed = terminal(client, retried.json()["id"])
        assert completed["status"] == "succeeded", completed
        saved = store.get_report(completed["result"]["report_id"])
    assert len(processes) == 1
    assert [kind for kind, _, _ in calls].count("reading") == 1
    assert [kind for kind, _, _ in calls].count("harness") == 1
    assert saved["snapshot"]["harness_state"]["reused_completed_checkpoint"] is True
    assert saved["snapshot"]["analysis_review"]["reading_state"]["resumed_source_count"] == 2
    assert store.get_job(failed["id"])["status"] == "failed"


def test_api_retry_research_revision_change_invalidates_harness_and_reuses_unchanged_source_reading(monkeypatch, tmp_path):
    _, processes = fake_sidecar(monkeypatch, tmp_path, "model")
    calls = synthetic_transport(monkeypatch, failed_drafts=1)
    store = Store(tmp_path / "changed-bindings.sqlite3")
    run = create_run(store)
    with TestClient(create_app(store)) as client:
        failed = create_report(client, run)
        assert failed["status"] == "failed", failed
        old_harness = failed["result"]["report_checkpoint"]["harness_state"]
        store.checkpoint_research(run["id"], status="succeeded", phase="finished",
            summary={"stop_reason": "manual_stop", "visited_count": 1})
        retried = client.post(f"/api/v2/jobs/{failed['id']}/retry")
        assert retried.status_code == 202, retried.text
        completed = terminal(client, retried.json()["id"])
        assert completed["status"] == "succeeded", completed
        saved = store.get_report(completed["result"]["report_id"])
    assert len(processes) == 2
    # A summary/revision change does not change source text, reading goal or
    # dates. Existing exact source reading can be reused; the full-context
    # Harness decision cache must still be invalidated.
    assert [kind for kind, _, _ in calls].count("reading") == 1
    assert [kind for kind, _, _ in calls].count("harness") == 2
    assert saved["snapshot"]["harness_state"]["input_fingerprint"] != old_harness["input_fingerprint"]
    assert "reused_completed_checkpoint" not in saved["snapshot"]["harness_state"]
    assert saved["snapshot"]["analysis_review"]["reading_state"]["resumed_source_count"] == 2


def test_pipeline_changed_research_goal_rereads_sources_and_invalidates_harness(monkeypatch, tmp_path):
    from app.workbench.research_reports import prepare_research_report
    _, processes = fake_sidecar(monkeypatch, tmp_path, "model")
    calls = synthetic_transport(monkeypatch, failed_drafts=1)
    store = Store(tmp_path / "changed-goal.sqlite3")
    run = create_run(store)
    with TestClient(create_app(store)) as client:
        failed = create_report(client, run)
        assert failed["status"] == "failed", failed
    previous = failed["result"]["report_checkpoint"]
    items, context = prepare_research_report(store.export_research(run["id"]))
    context["research_goal"] = "比较公开计划是否实际执行及撤回条件"
    checkpoints = []
    report = asyncio.run(pipeline.build_report(items, report_params(run), research_context=context,
        previous_review=previous, checkpoint=lambda state: checkpoints.append(state), time_budget_seconds=5))
    assert [kind for kind, _, _ in calls].count("reading") == 2
    assert [kind for kind, _, _ in calls].count("harness") == 2
    assert len(processes) == 2
    reading = report["snapshot"]["analysis_review"]["reading_state"]
    assert reading["resumed_source_count"] == 0 and reading["complete"] is True
    assert reading["research_context"]["research_goal"] == context["research_goal"]
    assert report["snapshot"]["harness_state"]["input_fingerprint"] != previous["harness_state"]["input_fingerprint"]


def test_unknown_harness_citation_does_not_save_report_and_retry_keeps_source_reading(monkeypatch, tmp_path):
    directory, _ = fake_sidecar(monkeypatch, tmp_path, "model")
    script_path = directory / "sidecar.mjs"
    original_script = script_path.read_text(encoding="utf-8")
    forged = final_findings()
    forged["findings"][0]["citation_ids"] = ["S099"]
    script_path.write_text(original_script.replace(json.dumps(final_findings(), ensure_ascii=False),
        json.dumps(forged, ensure_ascii=False)), encoding="utf-8")
    calls = synthetic_transport(monkeypatch)
    store = Store(tmp_path / "unknown-harness-citation.sqlite3")
    run = create_run(store)
    with TestClient(create_app(store)) as client:
        failed = create_report(client, run)
        assert failed["status"] == "failed", failed
        checkpoint = failed["result"]["report_checkpoint"]
        assert checkpoint["complete"] is True and checkpoint["harness_state"]["complete"] is False
        assert store.list_reports()["total"] == 0
        assert [kind for kind, _, _ in calls] == ["reading", "harness"]
        script_path.write_text(original_script, encoding="utf-8")
        retried = client.post(f"/api/v2/jobs/{failed['id']}/retry")
        assert retried.status_code == 202, retried.text
        completed = terminal(client, retried.json()["id"])
        assert completed["status"] == "succeeded", completed
    assert [kind for kind, _, _ in calls].count("reading") == 1
    assert [kind for kind, _, _ in calls].count("harness") == 2
    assert store.list_reports()["total"] == 1


def test_standard_off_report_does_not_invoke_harness_or_add_harness_snapshot(monkeypatch, tmp_path):
    calls = synthetic_transport(monkeypatch)
    async def forbidden(*args, **kwargs):
        raise AssertionError("Standard report invoked Harness")
    monkeypatch.setattr(harness_runtime, "run_evidence_harness", forbidden)
    store = Store(tmp_path / "standard-off.sqlite3")
    run = create_run(store)
    with TestClient(create_app(store)) as client:
        completed = create_report(client, run, "off")
        assert completed["status"] == "succeeded", completed
        saved = store.get_report(completed["result"]["report_id"])
    assert [kind for kind, _, _ in calls] == ["reading", "draft", "review"]
    assert "harness_state" not in saved["snapshot"] and "analysis_harness" not in saved["snapshot"]


def test_api_unavailable_runtime_and_evidence_dsh_are_rejected_before_enqueue(monkeypatch, tmp_path):
    monkeypatch.setattr(harness_runtime, "capability", lambda: {"id": "dsh", "available": False})
    store = Store(tmp_path / "unavailable-runtime.sqlite3")
    run = create_run(store)
    initial = store.list_jobs()["total"]
    with TestClient(create_app(store, run_worker=False)) as client:
        unavailable = client.post("/api/v2/jobs", json={"kind": "report", "params": report_params(run)})
        assert unavailable.status_code == 422 and "运行时" in unavailable.text
        evidence = client.post("/api/v2/jobs", json={"kind": "report", "params": {**report_params(run), "mode": "evidence"}})
        assert evidence.status_code == 422
        unknown = client.post("/api/v2/jobs", json={"kind": "report", "params": {**report_params(run), "analysis_harness": "arbitrary"}})
        assert unknown.status_code == 422
    assert store.list_jobs()["total"] == initial and store.list_reports()["total"] == 0


def ordinary_params():
    return {"title": "公开计划证据", "mode": "ai", "analysis_harness": "off",
        "start_date": "2026-10-01", "end_date": "2026-10-09", "read_original": False,
        "include_news": True, "include_social": False}


def create_failed_report(store, *, mode="ai"):
    job = store.create_job("report", {**ordinary_params(), "mode": mode})
    store.update_job(job["id"], status="failed", error="synthetic previous report failure")
    return store.get_job(job["id"])


def ready_runtime(monkeypatch):
    monkeypatch.setattr(harness_runtime, "capability", lambda: {
        "id": "dsh", "available": True, "version": "0.2.0-rc.2", "detail": "host fixture"})


def test_standard_failed_report_can_enable_dsh_on_retry_and_reuse_two_readings_preserving_scope_and_lineage(monkeypatch, tmp_path):
    directory, processes = fake_sidecar(monkeypatch, tmp_path, "model")
    # Ordinary news reports have no research graph; keep the fixture's final
    # suggestions within that empty account scope instead of inventing nodes.
    script_path = directory / "sidecar.mjs"
    final = final_findings()
    final["focus_accounts"] = []
    final["next_steps"] = []
    script_path.write_text(script_path.read_text(encoding="utf-8").replace(
        json.dumps(final_findings(), ensure_ascii=False), json.dumps(final, ensure_ascii=False)), encoding="utf-8")
    calls = synthetic_transport(monkeypatch, failed_drafts=1)
    store = Store(tmp_path / "standard-to-dsh-retry.sqlite3")
    records = []
    for number in (1, 2):
        row = source(number)
        row.update(type="news", source_type="rss", url=f"https://public.example/source-{number}")
        row.pop("account_key")
        records.append(row)
    assert store.import_items(records)["inserted"] == 2
    with TestClient(create_app(store)) as client:
        created = client.post("/api/v2/jobs", json={"kind": "report", "params": ordinary_params()})
        assert created.status_code == 202, created.text
        failed = terminal(client, created.json()["id"])
        assert failed["status"] == "failed", failed
        assert failed["result"]["report_checkpoint"]["complete"] is True
        assert len(failed["result"]["report_checkpoint"]["source_reviews"]) == 2
        assert len(processes) == 0
        original_params = copy.deepcopy(failed["params"])
        overrides = {"analysis_harness": "dsh", "harness_max_model_calls": 6,
            "harness_max_tool_calls": 12, "time_budget_seconds": 1800}
        retried = client.post(f"/api/v2/jobs/{failed['id']}/retry", json=overrides)
        assert retried.status_code == 202, retried.text
        completed = terminal(client, retried.json()["id"])
        assert completed["status"] == "succeeded", completed
        saved = store.get_report(completed["result"]["report_id"])
    lineage = completed["params"]["_workbench_retry"]
    assert lineage == {"parent_job_id": failed["id"], "root_job_id": failed["id"]}
    for field, value in original_params.items():
        if field not in overrides:
            assert completed["params"][field] == value
    assert all(completed["params"][field] == value for field, value in overrides.items())
    assert store.get_job(failed["id"])["params"] == original_params
    assert store.get_job(failed["id"])["status"] == "failed"
    assert len(processes) == 1 and all(process.returncode is not None for process in processes)
    assert [kind for kind, _, _ in calls] == ["reading", "draft", "harness", "draft", "review"]
    assert saved["snapshot"]["analysis_review"]["reading_state"]["resumed_source_count"] == 2
    assert saved["snapshot"]["harness_state"]["complete"] is True
    assert saved["source_count"] == 2 and saved["mode"] == "ai"


def test_retry_api_rejects_source_scope_goal_mode_and_unknown_changes_without_new_jobs(monkeypatch, tmp_path):
    ready_runtime(monkeypatch)
    store = Store(tmp_path / "retry-forbidden-scope.sqlite3")
    failed = create_failed_report(store)
    initial = store.list_jobs()["total"]
    forbidden = [{"start_date": "2026-01-01"}, {"end_date": "2026-12-31"},
        {"research_goal": "changed goal"}, {"research_run_id": "different-run"},
        {"mode": "evidence"}, {"include_news": False}, {"read_original": True}, {"unknown": "extra"}]
    with TestClient(create_app(store, run_worker=False)) as client:
        for change in forbidden:
            rejected = client.post(f"/api/v2/jobs/{failed['id']}/retry", json={"analysis_harness": "dsh", **change})
            assert rejected.status_code == 422, rejected.text
            assert store.list_jobs()["total"] == initial
    assert store.get_job(failed["id"])["params"] == failed["params"]


def test_retry_store_rejects_unapproved_scope_fields_even_without_api_validation(tmp_path):
    store = Store(tmp_path / "retry-store-forbidden-scope.sqlite3")
    failed = create_failed_report(store)
    for field in ("start_date", "end_date", "research_goal", "research_run_id", "mode", "include_news", "unknown"):
        with pytest.raises(ValueError):
            store.retry_job(failed["id"], report_options={field: "unapproved"})
        assert store.list_jobs()["total"] == 1


def test_non_report_retry_rejects_report_runtime_and_budget_overrides(monkeypatch, tmp_path):
    ready_runtime(monkeypatch)
    store = Store(tmp_path / "retry-non-report.sqlite3")
    failed = store.create_job("research", {"source": "twitter", "research_goal": "saved research"})
    store.update_job(failed["id"], status="failed", error="synthetic research failure")
    with TestClient(create_app(store, run_worker=False)) as client:
        for overrides in ({"analysis_harness": "dsh"}, {"harness_max_model_calls": 6}, {"time_budget_seconds": 1800}):
            rejected = client.post(f"/api/v2/jobs/{failed['id']}/retry", json=overrides)
            assert rejected.status_code == 422, rejected.text
            assert store.list_jobs()["total"] == 1


def test_retry_api_rejects_bounds_booleans_and_implicit_numeric_coercion(monkeypatch, tmp_path):
    ready_runtime(monkeypatch)
    store = Store(tmp_path / "retry-invalid-budgets.sqlite3")
    failed = create_failed_report(store)
    invalid = [{"harness_max_model_calls": value} for value in (1, 4097, True, False, "6", 6.0)]
    invalid += [{"harness_max_tool_calls": value} for value in (1, 65537, True, "12", 12.0)]
    invalid += [{"time_budget_seconds": value} for value in (59, 2592001, True, "1800", 1800.0)]
    with TestClient(create_app(store, run_worker=False)) as client:
        for overrides in invalid:
            rejected = client.post(f"/api/v2/jobs/{failed['id']}/retry", json=overrides)
            assert rejected.status_code == 422, (overrides, rejected.text)
            assert store.list_jobs()["total"] == 1


def test_retry_store_rejects_non_object_options_and_boolean_budget_values(tmp_path):
    store = Store(tmp_path / "retry-store-invalid-types.sqlite3")
    failed = create_failed_report(store)
    invalid = [False, 0, [], "", "not options", {"harness_max_model_calls": True},
        {"harness_max_tool_calls": False}, {"time_budget_seconds": True}, {"analysis_harness": []}]
    for overrides in invalid:
        with pytest.raises(ValueError): store.retry_job(failed["id"], report_options=overrides)
        assert store.list_jobs()["total"] == 1


def test_retry_same_branch_queued_child_conflicts_without_second_child(monkeypatch, tmp_path):
    ready_runtime(monkeypatch)
    store = Store(tmp_path / "retry-queued-conflict.sqlite3")
    failed = create_failed_report(store)
    with TestClient(create_app(store, run_worker=False)) as client:
        first = client.post(f"/api/v2/jobs/{failed['id']}/retry", json={"analysis_harness": "dsh"})
        assert first.status_code == 202, first.text
        assert first.json()["status"] == "queued"
        second = client.post(f"/api/v2/jobs/{failed['id']}/retry", json={"analysis_harness": "off"})
        assert second.status_code == 409, second.text
        legacy = client.post(f"/api/v2/jobs/{failed['id']}/retry")
        assert legacy.status_code == 409
    assert store.list_jobs()["total"] == 2
    assert store.get_job(first.json()["id"])["params"]["analysis_harness"] == "dsh"
    assert store.get_job(first.json()["id"])["params"]["_workbench_retry"]["root_job_id"] == failed["id"]


def test_retry_dsh_unavailable_runtime_or_missing_key_rejects_without_new_jobs(monkeypatch, tmp_path):
    store = Store(tmp_path / "retry-not-ready.sqlite3")
    failed = create_failed_report(store)
    with TestClient(create_app(store, run_worker=False)) as client:
        with monkeypatch.context() as guard:
            guard.setattr(harness_runtime, "capability", lambda: {"id": "dsh", "available": False})
            unavailable = client.post(f"/api/v2/jobs/{failed['id']}/retry", json={"analysis_harness": "dsh"})
            assert unavailable.status_code == 422, unavailable.text
        with monkeypatch.context() as guard:
            guard.setattr(harness_runtime, "capability", lambda: {"id": "dsh", "available": True})
            guard.delenv("DEEPSEEK_API_KEY", raising=False)
            missing_key = client.post(f"/api/v2/jobs/{failed['id']}/retry", json={"analysis_harness": "dsh"})
            assert missing_key.status_code == 422, missing_key.text
    assert store.list_jobs()["total"] == 1


def test_legacy_no_body_retry_preserves_existing_standard_params_and_lineage(monkeypatch, tmp_path):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(harness_runtime, "capability", lambda: {"id": "dsh", "available": False})
    store = Store(tmp_path / "legacy-no-body-retry.sqlite3")
    failed = create_failed_report(store)
    with TestClient(create_app(store, run_worker=False)) as client:
        retried = client.post(f"/api/v2/jobs/{failed['id']}/retry")
        assert retried.status_code == 202, retried.text
    params = retried.json()["params"]
    assert {key: value for key, value in params.items() if key != "_workbench_retry"} == failed["params"]
    assert params["_workbench_retry"] == {"parent_job_id": failed["id"], "root_job_id": failed["id"]}
    assert store.list_jobs()["total"] == 2


def test_retry_cannot_enable_dsh_for_evidence_mode_even_when_provider_ready(monkeypatch, tmp_path):
    ready_runtime(monkeypatch)
    store = Store(tmp_path / "retry-evidence-mode.sqlite3")
    failed = create_failed_report(store, mode="evidence")
    with TestClient(create_app(store, run_worker=False)) as client:
        rejected = client.post(f"/api/v2/jobs/{failed['id']}/retry", json={"analysis_harness": "dsh"})
        assert rejected.status_code == 422, rejected.text
    assert store.list_jobs()["total"] == 1 and store.get_job(failed["id"])["params"]["mode"] == "evidence"


def test_existing_dsh_retry_checks_effective_runtime_for_no_body_and_budget_only(monkeypatch, tmp_path):
    store = Store(tmp_path / "retry-inherited-dsh-readiness.sqlite3")
    failed = store.create_job("report", {**ordinary_params(), "analysis_harness": "dsh"})
    store.update_job(failed["id"], status="failed", error="synthetic previous DSH failure")
    with TestClient(create_app(store, run_worker=False)) as client:
        for availability in ("runtime_missing", "key_missing"):
            with monkeypatch.context() as guard:
                guard.setattr(harness_runtime, "capability", lambda: {
                    "id": "dsh", "available": availability != "runtime_missing"})
                if availability == "key_missing":
                    guard.delenv("DEEPSEEK_API_KEY", raising=False)
                for options in (None, {"harness_max_model_calls": 6}):
                    url = f"/api/v2/jobs/{failed['id']}/retry"
                    rejected = client.post(url) if options is None else client.post(url, json=options)
                    assert rejected.status_code == 422, (availability, options, rejected.text)
                    assert store.list_jobs()["total"] == 1
        # Retrying an older off ancestor selects the latest failed dsh child.
        # Readiness must follow that effective branch, not the clicked job.
        ready_runtime(monkeypatch)
        ancestor = create_failed_report(store)
        child = store.retry_job(ancestor["id"], report_options={"analysis_harness": "dsh"})
        store.update_job(child["id"], status="failed", error="synthetic child DSH failure")
        assert store.get_job(ancestor["id"])["params"]["analysis_harness"] == "off"
        assert store.list_jobs()["total"] == 3
        for availability in ("runtime_missing", "key_missing"):
            with monkeypatch.context() as guard:
                guard.setattr(harness_runtime, "capability", lambda: {
                    "id": "dsh", "available": availability != "runtime_missing"})
                if availability == "key_missing":
                    guard.delenv("DEEPSEEK_API_KEY", raising=False)
                for options in (None, {"time_budget_seconds": 240}):
                    url = f"/api/v2/jobs/{ancestor['id']}/retry"
                    rejected = client.post(url) if options is None else client.post(url, json=options)
                    assert rejected.status_code == 422, (availability, options, rejected.text)
                    assert store.list_jobs()["total"] == 3
