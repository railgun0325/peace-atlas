"""Report recovery uses exact saved inputs, never a silently refreshed context."""
import asyncio
import copy

import pytest
from fastapi.testclient import TestClient

from app.workbench import pipeline
from app.workbench.jobs import JobRunner
from app.workbench.server import create_app
from app.workbench.store import Store


def material(text="公開の資料本文。"):
    return {"type": "news", "title": "公開の資料", "content": text,
            "url": "https://fixture.example/article", "source_name": "公開資料",
            "published_at": "2026-10-08", "metadata": {"content_kind": "body"}}


def params():
    return {"title": "保存入力の報告", "mode": "ai", "analysis_harness": "off",
            "start_date": "2026-10-01", "end_date": "2026-10-09",
            "include_news": True, "include_social": True, "read_original": False}


def frozen(store, monkeypatch, checkpoint=None):
    store.import_items([material()])
    job = store.create_job("report", params())
    claimed = store.claim_job()
    async def stop_before_paid(snapshot, title, **options):
        saved = store.get_job(job["id"])["result"]
        assert saved["report_input_snapshot"]["snapshot"]["items"][0]["citation_id"] == "S001"
        if checkpoint is not None:
            store.update_job(job["id"], result={**saved, "report_checkpoint": copy.deepcopy(checkpoint)})
        raise ValueError("synthetic paid-stage boundary")
    monkeypatch.setattr(pipeline, "_ai_report", stop_before_paid)
    with pytest.raises(ValueError, match="synthetic paid-stage boundary"):
        asyncio.run(JobRunner(store)._perform(claimed, asyncio.Event()))
    store.update_job(job["id"], status="failed", error="synthetic paid-stage boundary")
    return store.get_job(job["id"])


def test_legacy_original_retry_without_full_input_refuses_before_child(tmp_path):
    store = Store(tmp_path / "legacy.sqlite3")
    job = store.create_job("report", params())
    store.update_job(job["id"], status="failed", result={"report_checkpoint": {
        "version": 3, "complete": True, "source_reviews": [],
        "harness_state": {"complete": True, "model_calls": 59, "tool_calls": 255}}})
    with pytest.raises(ValueError, match="完整输入快照"):
        store.retry_job(job["id"])
    assert store.list_jobs()["total"] == 1


def test_complete_input_is_durable_before_paid_stage(tmp_path, monkeypatch):
    store = Store(tmp_path / "capture.sqlite3")
    job = frozen(store, monkeypatch)
    from app.workbench.report_inputs import validate_report_input_snapshot
    saved = validate_report_input_snapshot(job["result"]["report_input_snapshot"], job["params"])
    assert saved["snapshot"]["scope"]["start_date"] == "2026-10-01"
    assert saved["snapshot"]["items"][0]["content"] == "公開の資料本文。"


def test_original_retry_reads_saved_input_even_when_current_source_changes(tmp_path, monkeypatch):
    store = Store(tmp_path / "original.sqlite3")
    original = frozen(store, monkeypatch)
    store.import_items([material("変更された新しい本文。")])
    child = store.retry_job(original["id"], report_options={
        "input_mode": "original", "input_sha256": original["result"]["report_input_snapshot"]["sha256"]})
    seen = []
    async def report(snapshot, title, **options):
        seen.append(copy.deepcopy(snapshot))
        return "# 報告\n\n保存された公開資料。[S001]"
    monkeypatch.setattr(pipeline, "_ai_report", report)
    def forbidden(*args, **kwargs):
        raise AssertionError("original retry must not read current source repository")
    monkeypatch.setattr(store, "report_items", forbidden)
    result = asyncio.run(JobRunner(store)._perform(store.claim_job(), asyncio.Event()))
    assert seen[0]["items"][0]["content"] == "公開の資料本文。"
    assert result["report_input_snapshot"]["sha256"] == original["result"]["report_input_snapshot"]["sha256"]
    assert child["params"]["_report_input_mode"] == "original"
    assert store.get_job(original["id"]) == original


def test_corrupt_original_input_cannot_enqueue_or_be_silently_refreshed(tmp_path, monkeypatch):
    store = Store(tmp_path / "corrupt.sqlite3")
    store.import_items([material()])
    snapshot = asyncio.run(pipeline.prepare_report_input(store.report_items("2026-10-01", "2026-10-09"), params()))
    from app.workbench.report_inputs import capture_report_input_snapshot
    saved = capture_report_input_snapshot(snapshot, params())
    saved["snapshot"]["items"][0]["content"] = "tampered"
    job = store.create_job("report", params())
    store.update_job(job["id"], status="failed", result={"report_input_snapshot": saved})
    with pytest.raises(ValueError, match="快照"):
        store.retry_job(job["id"])
    assert store.list_jobs()["total"] == 1


def test_latest_is_explicit_and_keeps_strict_complete_context_fingerprint(tmp_path, monkeypatch):
    store = Store(tmp_path / "latest.sqlite3")
    original = frozen(store, monkeypatch)
    before = original["result"]["report_input_snapshot"]
    store.import_items([material("変更された新しい本文。")])
    preview = store.report_retry_preview(original["id"])
    assert preview["original_input"]["available"] is True
    assert preview["changes"]["source_count"] == 1
    assert preview["current_input"]["input_fingerprint"] != before["input_fingerprint"]
    child = store.retry_job(original["id"], report_options={"input_mode": "latest",
        "input_sha256": preview["current_input"]["sha256"]})
    assert child["params"]["_report_input_mode"] == "latest"
    store.import_items([material("排隊後の変更は固定された入力に入らない。")])
    seen = []
    async def report(snapshot, title, **options):
        seen.append(copy.deepcopy(snapshot))
        return "# 報告\n\n現在保存された公開資料。[S001]"
    monkeypatch.setattr(pipeline, "_ai_report", report)
    result = asyncio.run(JobRunner(store)._perform(store.claim_job(), asyncio.Event()))
    assert seen[0]["items"][0]["content"] == "変更された新しい本文。"
    assert result["report_input_snapshot"]["input_fingerprint"] != before["input_fingerprint"]
    assert store.get_job(original["id"]) == original


def test_preview_and_original_missing_input_api_are_read_only(tmp_path, monkeypatch):
    store = Store(tmp_path / "preview.sqlite3")
    store.import_items([material()])
    job = store.create_job("report", params())
    store.update_job(job["id"], status="failed", result={"report_checkpoint": {"version": 3}})
    async def paid(*args, **kwargs):
        raise AssertionError("preview must not call a model")
    monkeypatch.setattr(pipeline, "_ai_report", paid)
    with TestClient(create_app(store, run_worker=False)) as client:
        response = client.get(f"/api/v2/jobs/{job['id']}/retry-preview")
        assert response.status_code == 200, response.text
        preview = response.json()
        assert preview["original_input"]["available"] is False
        assert preview["cost_level"] == "new_analysis_with_matching_readings_only"
        rejected = client.post(f"/api/v2/jobs/{job['id']}/retry")
        assert rejected.status_code == 422, rejected.text
        assert store.list_jobs()["total"] == 1


def test_latest_changed_after_preview_rejects_without_child(tmp_path, monkeypatch):
    store = Store(tmp_path / "stale-latest.sqlite3")
    original = frozen(store, monkeypatch)
    preview = store.report_retry_preview(original["id"])
    store.import_items([material("プレビュー後に変わった本文。")])
    with pytest.raises(ValueError, match="重新预览"):
        store.retry_job(original["id"], report_options={"input_mode": "latest",
            "input_sha256": preview["current_input"]["sha256"]})
    assert store.list_jobs()["total"] == 1


def test_original_wrong_or_missing_sha_rejects_without_child(tmp_path, monkeypatch):
    store = Store(tmp_path / "stale-original.sqlite3")
    original = frozen(store, monkeypatch)
    for options in ({}, {"input_mode": "original", "input_sha256": "0" * 64}):
        with pytest.raises(ValueError, match="重新预览"):
            store.retry_job(original["id"], report_options=options)
    assert store.list_jobs()["total"] == 1


def test_context_change_invalidates_complete_stages_but_preserves_exact_reading(tmp_path, monkeypatch):
    from app.workbench import report_inputs
    from app.workbench.report_analysis import _digest, _reading_context, _reading_units, source_samples
    store = Store(tmp_path / "context-only.sqlite3")
    store.import_items([material()])
    request = params()
    context = {"research_goal": "公開の資料を比較する", "accounts": [{"account_key": "twitter:fixture",
        "metadata": {"account_identity": {"type": "unknown", "reviewed_at": "2026-10-08"}}}],
        "relationship_claims": [{"id": "claim-fixture", "status": "candidate",
            "identity_qualifiers": ["地域", "刊行物"]}]}
    snapshot = asyncio.run(pipeline.prepare_report_input(store.report_items("2026-10-01", "2026-10-09"), request, research_context=context))
    original = report_inputs.capture_report_input_snapshot(snapshot, request)
    sources, _ = source_samples(snapshot["items"])
    units, _, _ = _reading_units(sources)
    checkpoint = {"version": 3, "research_context_fingerprint": _digest(_reading_context(context)),
        "reviewed_fragment_fingerprints": {k: _digest(v) for k, v in units.items()},
        "fragment_reviews": [{"unit_id": "S001", "quote": "公開の資料本文。", "reading": "公開資料の記述。",
            "limitations": "独立した検証ではない。", "citation_id": "S001"}],
        "harness_state": {"complete": True}, "synthesis_state": {"complete": True}, "report_generation": {"draft": "saved"}}
    job = store.create_job("report", request)
    store.update_job(job["id"], status="failed", result={"report_input_snapshot": original, "report_checkpoint": checkpoint})
    latest_snapshot = copy.deepcopy(snapshot)
    latest_snapshot["research_context"]["accounts"][0]["metadata"]["account_identity"]["reviewed_at"] = "2026-10-11"
    latest_snapshot["research_context"]["relationship_claims"][0]["status"] = "confirmed"
    current = report_inputs.capture_report_input_snapshot(latest_snapshot, request)
    monkeypatch.setattr(report_inputs, "current_report_input", lambda *args: copy.deepcopy(current))
    preview = store.report_retry_preview(job["id"])
    assert preview["changes"]["source_count"] == 0
    assert preview["changes"]["account_counts"] == {"added": 0, "removed": 0, "changed": 1}
    assert preview["changes"]["claim_counts"] == {"added": 0, "removed": 0, "changed": 1}
    assert preview["changes"]["context_changed"] is True
    assert preview["reading_reuse"]["matched"] == preview["reading_reuse"]["total"] == 1
    assert preview["reading_reuse"]["pending"] == 0
    assert all(preview["stage_reuse"]["latest"][key] == "invalidated" for key in ("harness", "draft", "compression"))
    # Removing a qualifier is substantive input change, even if review status
    # and raw source text are unchanged. It must never be excluded from hashes.
    qualifier_removed = copy.deepcopy(latest_snapshot)
    qualifier_removed["research_context"]["relationship_claims"][0]["identity_qualifiers"].pop()
    assert report_inputs.capture_report_input_snapshot(qualifier_removed, request)["input_fingerprint"] != current["input_fingerprint"]


def test_child_cancelled_before_first_callback_retains_all_checkpoint_candidates(tmp_path, monkeypatch):
    store = Store(tmp_path / "early-cancel.sqlite3")
    checkpoint = {"version": 3, "source_reviews": [{"citation_id": "S001"}],
        "harness_state": {"complete": True}, "report_generation": {"draft": "saved"}}
    original = frozen(store, monkeypatch, checkpoint=checkpoint)
    child = store.retry_job(original["id"], report_options={"input_mode": "original",
        "input_sha256": original["result"]["report_input_snapshot"]["sha256"]})
    assert child["result"]["report_checkpoint"] == checkpoint
    store.request_cancel(child["id"])
    grandchild = store.retry_job(child["id"], report_options={"input_mode": "original",
        "input_sha256": child["result"]["report_input_snapshot"]["sha256"]})
    assert grandchild["result"]["report_checkpoint"] == checkpoint
    assert grandchild["result"]["report_input_snapshot"]["sha256"] == original["result"]["report_input_snapshot"]["sha256"]


def test_latest_nonresearch_read_original_is_not_silently_disabled(tmp_path):
    from app.workbench.report_inputs import capture_report_input_snapshot
    store = Store(tmp_path / "latest-original-reader.sqlite3")
    store.import_items([material()])
    request = {**params(), "read_original": True}
    snapshot = asyncio.run(pipeline.prepare_report_input(store.report_items("2026-10-01", "2026-10-09"), request))
    job = store.create_job("report", request)
    store.update_job(job["id"], status="failed", result={
        "report_input_snapshot": capture_report_input_snapshot(snapshot, request)})
    preview = store.report_retry_preview(job["id"])
    assert preview["original_input"]["available"] is True
    assert preview["current_input"]["available"] is False
    assert "读取原文" in preview["current_input"]["reason"]
    assert preview["may_read_public_network"] is False
    with pytest.raises(ValueError, match="读取原文"):
        store.retry_job(job["id"], report_options={"input_mode": "latest", "input_sha256": "0" * 64})
    assert store.list_jobs()["total"] == 1


def test_status_polling_projects_only_input_receipt_without_mutating_frozen_sources(tmp_path, monkeypatch):
    store = Store(tmp_path / "polling.sqlite3")
    original = frozen(store, monkeypatch)
    saved = copy.deepcopy(original['result']['report_input_snapshot'])
    with TestClient(create_app(store, run_worker=False)) as client:
        detail = client.get(f"/api/v2/jobs/{original['id']}").json()
        listed = client.get('/api/v2/jobs').json()['items'][0]
        preview = client.get(f"/api/v2/jobs/{original['id']}/retry-preview").json()
        assert preview['original_input']['available'] is True
        child = client.post(f"/api/v2/jobs/{original['id']}/retry", json={
            'input_mode': 'original', 'input_sha256': preview['original_input']['sha256']})
        assert child.status_code == 202, child.text
        cancelled = client.post(f"/api/v2/jobs/{child.json()['id']}/cancel").json()
        for public in (detail, listed, child.json(), cancelled):
            receipt = public['result']['report_input_snapshot']
            assert receipt['summary_only'] is True and receipt['source_count'] == 1
            assert receipt['sha256'] == saved['sha256']
            assert 'snapshot' not in receipt and 'params_scope' not in receipt
    assert store.get_job(original['id'])['result']['report_input_snapshot'] == saved
    assert store.get_job(child.json()['id'])['result']['report_input_snapshot'] == saved


def test_corrupt_child_copy_cannot_be_hidden_by_valid_parent_reference(tmp_path, monkeypatch):
    from app.workbench.report_inputs import resolve_report_input
    store = Store(tmp_path / 'child-integrity.sqlite3')
    original = frozen(store, monkeypatch)
    child = store.retry_job(original['id'], report_options={'input_mode': 'original',
        'input_sha256': original['result']['report_input_snapshot']['sha256']})
    changed = copy.deepcopy(child['result'])
    changed['report_input_snapshot']['snapshot']['items'][0]['content'] = 'tampered child input'
    store.update_job(child['id'], result=changed)
    with pytest.raises(ValueError, match='快照'):
        resolve_report_input(store, store.get_job(child['id']))


def test_failed_input_save_prevents_any_paid_stage(tmp_path, monkeypatch):
    store = Store(tmp_path / 'save-before-paid.sqlite3')
    store.import_items([material()])
    store.create_job('report', params())
    original_update = store.update_job
    calls = []
    def update(identity, **fields):
        if 'report_input_snapshot' in (fields.get('result') or {}):
            raise OSError('synthetic persistence failure')
        return original_update(identity, **fields)
    async def paid(*args, **options):
        calls.append('paid')
        raise AssertionError('must not enter paid stage')
    monkeypatch.setattr(store, 'update_job', update)
    monkeypatch.setattr(pipeline, '_ai_report', paid)
    with pytest.raises(OSError, match='synthetic persistence failure'):
        asyncio.run(JobRunner(store)._perform(store.claim_job(), asyncio.Event()))
    assert calls == []
