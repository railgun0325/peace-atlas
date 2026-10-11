"""Offline contract probes for paid-response repair; never live model claims."""
import asyncio
import copy
import hashlib
import json
import sqlite3

import pytest

from app.workbench import relationship_analysis as relationships
from app.workbench.pipeline import PipelineError
from app.workbench.research_engine import compact_analysis_catalog
from app.workbench.store import Store
from app.workbench.research_repository import source_url_key


def account(handle="alpha"):
    return {"account_key": "twitter:" + handle, "name": handle, "status": "candidate", "profile": {
        "verified_in_dom": True, "url": f"https://x.com/{handle}/", "bio": "所属：公开团体A。",
        "observed_at": "2026-10-09T00:00:00Z"}}


def posts(count):
    return [{"account_key": "twitter:alpha", "url": f"https://x.com/alpha/status/{index + 1}",
             "content": f"公开团体A 的公开材料 {index + 1}", "published_at": "2026-10-08T00:00:00Z",
             "metadata": {"collected_at": "2026-10-09T00:00:00Z"}} for index in range(count)]


def test_legacy_accepted_observation_survives_a_preexisting_global_duplicate(tmp_path, monkeypatch):
    """One global duplicate must not discard a whole paid source-bound batch."""
    accounts = [account()]
    items = posts(100)
    for item in items:
        item["metadata"]["collected_at"] = "2030-10-09T00:00:00Z"
        item["evidence_id"] = relationships.stable_id("post", item["url"])
    selected = min(items, key=lambda item: item["evidence_id"])
    selected["content"] += " @beta"
    old_global = copy.deepcopy(selected)
    old_global.pop("evidence_id")
    old_global["metadata"]["collected_at"] = "2026-10-08T00:00:00Z"
    attempts = []

    async def first_model(system, data, **options):
        attempts.append(copy.deepcopy(data))
        if len(attempts) == 2:
            raise PipelineError("可控旧包未完成批次，无原始响应。")
        return {"affiliation_candidates": []}

    monkeypatch.setattr(relationships, "configured_ai_json", first_model)
    previous = run(relationships.analyze_relationships_with_ai(accounts, items, [], use_ai=True))
    assert previous["ai"]["input_evidence_count"] == 80
    accepted = previous["ai"]["review_batches"][0]["evidence_hashes"]
    assert selected["evidence_id"] in accepted
    # Match the actual old package checkpoint: accepted hashes, no failed raw.
    for field in ("pending_review", "review_work_version", "review_attempts"):
        previous["ai"].pop(field, None)
    store = Store(tmp_path / "legacy-duplicate.sqlite3")
    try:
        assert store.import_social([old_global], [{"platform": "twitter", "handle": "alpha"}], [])["inserted"] == 1
        job = store.create_job("research", {})
        research = store.create_research_run(job["id"], {})
        store.checkpoint_research(research["id"], items=items,
            accounts=[{"platform": "twitter", "handle": "alpha", "profile": accounts[0]["profile"]}],
            nodes=[{"account_key": "twitter:alpha", "status": "visited"}], analysis=compact_analysis_catalog(previous))
        saved = store.get_research_run(research["id"])
    finally:
        store.close()
    duplicate = next(row for row in saved["items"] if row["url"].endswith("/" + selected["url"].rsplit("/", 1)[1]))
    assert not duplicate.get("evidence_id")
    assert duplicate["metadata"]["collected_at"] == old_global["metadata"]["collected_at"]
    immutable = copy.deepcopy(saved)
    captures, requests = [], []

    async def cancel_before_paid_request(system, data, **options):
        requests.append(copy.deepcopy(data))
        raise asyncio.CancelledError()

    async def checkpoint(value):
        captures.append(copy.deepcopy(value))

    monkeypatch.setattr(relationships, "configured_ai_json", cancel_before_paid_request)
    with pytest.raises(asyncio.CancelledError):
        run(relationships.analyze_relationships_with_ai(saved["accounts"], saved["items"], [], use_ai=True,
            previous_analysis=saved["analysis"], checkpoint=checkpoint))
    ai = captures[-1]["ai"]
    assert ai["resumed_batches"] == 1 and ai["input_evidence_count"] == 80
    assert ai["pending_evidence_count"] == 21 and not ai["complete"]
    assert len(requests) == 1
    assert not {row["id"] for row in requests[0]["evidence"] if row["type"] != "profile"} & accepted.keys()
    restored = next(row for row in captures[-1]["evidence"] if row["id"] == selected["evidence_id"])
    assert relationships._review_hash(restored) == accepted[selected["evidence_id"]]
    assert restored["observed_at"] == "2030-10-09T00:00:00Z"
    reconciliation = ai["source_observation_reconciliation"]
    assert reconciliation["restored_count"] == 1 and reconciliation["new_platform_observations"] == 0
    assert reconciliation["observations"][0]["accepted_source_sha256"] == accepted[selected["evidence_id"]]
    mention = next(row for row in captures[-1]["findings"] if row["relation"] == "mentions")
    assert mention["evidence_ids"] == [selected["evidence_id"]]
    assert mention["evidence_urls"] == [selected["url"]]
    assert saved == immutable


@pytest.mark.parametrize("change", [
    "body_after_excerpt", "author", "published_at", "context", "no_archive", "collected_newer",
    "collected_equal", "created_newer", "created_equal", "created_missing", "original_url_changed",
    "canonical_post_changed", "accepted_hash_changed", "archived_observed_changed", "missing_full_hash",
    "missing_full_length", "duplicate_archived_subject", "duplicate_archived_identity", "duplicate_current_source",
    "current_identity_conflict", "purged_source_absent", "malformed_archived_identity",
])
def test_saved_observation_reconciliation_requires_every_original_source_proof(monkeypatch, change):
    accounts, original = [account()], posts(1)
    original[0]["content"] = "公开团体A " + "完整原文" * 700
    original[0]["metadata"]["collected_at"] = "2030-10-09T00:00:00Z"

    async def model(system, data, **options):
        return {"affiliation_candidates": []}

    monkeypatch.setattr(relationships, "configured_ai_json", model)
    previous = run(relationships.analyze_relationships_with_ai(accounts, original, [], use_ai=True))
    context = previous["ai"]["context_fingerprint"]
    archived = next(row for row in previous["evidence"] if row["type"] == "post")
    old_id = archived["id"]
    current = copy.deepcopy(original)
    current[0].update(url="https://x.com/i/status/1", created_at="2026-10-08T00:00:01Z")
    current[0]["metadata"].update(original_url=archived["url"], collected_at="2026-10-08T00:00:00Z")
    intact, audit = relationships._reconcile_saved_post_observations(current, compact_analysis_catalog(previous), context)
    assert audit["restored_count"] == 1 and intact[0]["metadata"]["collected_at"] == archived["observed_at"]
    assert relationships._review_hash(relationships.evidence_catalog([], intact, [])[0]) == previous["ai"]["review_batches"][0]["evidence_hashes"][old_id]
    previous = compact_analysis_catalog(previous)
    archived = next(row for row in previous["evidence"] if row["type"] == "post")
    if change == "body_after_excerpt":
        body = current[0]["content"]
        current[0]["content"] = body[:2000] + "改" + body[2001:]
    elif change == "author":
        current[0]["account_key"] = "twitter:beta"
    elif change == "published_at":
        current[0]["published_at"] = "2026-10-07T00:00:00Z"
    elif change == "context":
        context = "different_model_or_scope_context"
    elif change == "no_archive":
        previous["evidence"] = []
    elif change.startswith("collected_"):
        current[0]["metadata"]["collected_at"] = "2030-10-10T00:00:00Z" if change.endswith("newer") else archived["observed_at"]
    elif change.startswith("created_"):
        if change.endswith("missing"):
            current[0].pop("created_at")
        else:
            current[0]["created_at"] = "2030-10-10T00:00:00Z" if change.endswith("newer") else archived["observed_at"]
    elif change == "original_url_changed":
        current[0]["metadata"]["original_url"] = archived["url"] + "/"
    elif change == "canonical_post_changed":
        current[0]["url"] = "https://x.com/i/status/2"
    elif change == "accepted_hash_changed":
        previous["ai"]["review_batches"][0]["evidence_hashes"][old_id] = "f" * 64
    elif change == "archived_observed_changed":
        archived["observed_at"] = "2030-10-11T00:00:00Z"
    elif change == "missing_full_hash":
        archived.pop("content_sha256")
    elif change == "missing_full_length":
        archived.pop("source_char_count")
    elif change == "duplicate_archived_subject":
        second = {**archived, "id": "another-old-source"}
        previous["evidence"].append(second)
        previous["ai"]["review_batches"][0]["evidence_hashes"][second["id"]] = relationships._review_hash(second)
    elif change == "duplicate_archived_identity":
        previous["evidence"].append(copy.deepcopy(archived))
    elif change == "duplicate_current_source":
        current.append(copy.deepcopy(current[0]))
    elif change == "current_identity_conflict":
        current.append({**copy.deepcopy(current[0]), "evidence_id": old_id, "url": "https://x.com/i/status/2"})
    elif change == "purged_source_absent":
        current = []
        previous["evidence"] = [row for row in previous["evidence"] if row["type"] != "post"]
    elif change == "malformed_archived_identity":
        archived["id"] = [old_id]
    before_items, before_previous = copy.deepcopy(current), copy.deepcopy(previous)
    unchanged, rejected = relationships._reconcile_saved_post_observations(current, previous, context)
    assert rejected["restored_count"] == 0 and unchanged == current
    assert current == before_items and previous == before_previous


def test_saved_observation_cannot_recreate_a_trashed_or_purged_body(tmp_path, monkeypatch):
    accounts, original = [account()], posts(1)
    original[0]["metadata"]["collected_at"] = "2030-10-09T00:00:00Z"

    async def model(system, data, **options):
        return {"affiliation_candidates": []}

    monkeypatch.setattr(relationships, "configured_ai_json", model)
    previous = run(relationships.analyze_relationships_with_ai(accounts, original, [], use_ai=True))
    older = copy.deepcopy(original)
    older[0]["metadata"]["collected_at"] = "2026-10-08T00:00:00Z"
    store = Store(tmp_path / "source-observation-cleanup.sqlite3")
    try:
        item_id = store.import_social(older, [{"platform": "twitter", "handle": "alpha"}], [])["resolved_ids"][0]
        job = store.create_job("research", {})
        research = store.create_research_run(job["id"], {})
        store.checkpoint_research(research["id"], items=original,
            accounts=[{"platform": "twitter", "handle": "alpha", "profile": accounts[0]["profile"]}],
            nodes=[{"account_key": "twitter:alpha", "status": "visited"}], analysis=compact_analysis_catalog(previous))
        store.update_job(job["id"], status="partial")

        def current_restoration_count():
            saved = store.get_research_run(research["id"])
            restored, audit = relationships._reconcile_saved_post_observations(saved["items"], saved["analysis"], previous["ai"]["context_fingerprint"])
            assert len(restored) == len(saved["items"])
            return audit["restored_count"]

        def clean(action):
            preview = store.cleanup_preview([item_id], action)
            store.cleanup_apply([item_id], action, preview["preview_digest"], "Source reconciliation cleanup regression", confirm_purge=action == "purge")

        assert current_restoration_count() == 1
        clean("trash")
        assert current_restoration_count() == 0
        clean("restore")
        assert current_restoration_count() == 1
        clean("trash")
        clean("purge")
        assert current_restoration_count() == 0
        assert store.get_research_run(research["id"])["items"] == []
        assert source_url_key(original[0]["url"]) in store.get_research_run(research["id"])["metadata"]["purged_source_keys"]
    finally:
        store.close()


def run(coroutine):
    return asyncio.run(coroutine)


def good_candidate(data):
    source = next(row for row in data["evidence"] if row["type"] == "profile" and row["account_key"] == "twitter:alpha")
    return {"account_key": "twitter:alpha", "organization": "公开团体A", "claim": "公开自述仍待独立核对。", "evidence_ids": [source["id"]]}


def test_malformed_paid_json_is_checkpointed_and_repaired_against_original_mapping(monkeypatch):
    calls, checkpoints = [], []
    malformed = {"affiliation_candidates": [], "summary": "不能忽略多余字段。"}

    async def model(system, data, **options):
        calls.append(copy.deepcopy(data))
        if len(calls) == 1:
            return copy.deepcopy(malformed)
        assert data["previous_response"] == malformed
        assert any(issue["code"] == "top_fields" for issue in data["validation_issues"])
        assert data["accounts"] == calls[0]["accounts"] and data["evidence"] == calls[0]["evidence"]
        assert "不得" in system
        return {"affiliation_candidates": []}

    async def checkpoint(result):
        checkpoints.append(copy.deepcopy(result))

    monkeypatch.setattr(relationships, "configured_ai_json", model)
    result = run(relationships.analyze_relationships_with_ai([account()], posts(1), [], use_ai=True, checkpoint=checkpoint))
    assert len(calls) == 2 and result["ai"]["complete"]
    pending = next(row["ai"]["pending_review"] for row in checkpoints if row["ai"].get("pending_review", {}).get("raw_response") == malformed)
    assert pending["raw_response_sha256"] == hashlib.sha256(json.dumps(malformed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert not pending["raw_response_omitted"] and pending["evidence_hashes"]
    assert result["ai"]["pending_review"] is None
    assert result["ai"]["schema_repair_calls"] == 1


def test_unknown_reference_is_repaired_without_silently_dropping_the_candidate(monkeypatch):
    calls = []

    async def model(system, data, **options):
        calls.append(copy.deepcopy(data))
        row = good_candidate(data)
        if len(calls) == 1:
            row["evidence_ids"] = ["invented-evidence-id"]
        else:
            assert data["previous_response"]["affiliation_candidates"][0]["evidence_ids"] == ["invented-evidence-id"]
            assert any(issue["code"] == "evidence_reference" for issue in data["validation_issues"])
        return {"affiliation_candidates": [row]}

    monkeypatch.setattr(relationships, "configured_ai_json", model)
    original = [account()]
    result = run(relationships.analyze_relationships_with_ai(original, posts(1), [], use_ai=True))
    candidate = next(row for row in result["affiliations"] if row["method"] == "configured_ai")
    assert len(calls) == 2 and result["ai"]["complete"]
    assert candidate["status"] == "candidate" and candidate["requires_review"]
    assert candidate["evidence_ids"] != ["invented-evidence-id"] and original[0]["status"] == "candidate"


def test_cancelled_repair_restores_raw_response_and_preserves_the_accepted_batch(tmp_path, monkeypatch):
    calls, checkpoints = [], []
    malformed = {"affiliation_candidates": [{"wrong": "paid invalid row"}]}

    async def model(system, data, **options):
        calls.append(copy.deepcopy(data))
        if len(calls) == 1:
            return {"affiliation_candidates": []}
        if len(calls) == 2:
            return copy.deepcopy(malformed)
        raise asyncio.CancelledError()

    async def checkpoint(result):
        checkpoints.append(copy.deepcopy(result))

    monkeypatch.setattr(relationships, "configured_ai_json", model)
    with pytest.raises(asyncio.CancelledError):
        run(relationships.analyze_relationships_with_ai([account()], posts(100), [], use_ai=True, checkpoint=checkpoint))
    saved = checkpoints[-1]
    assert saved["ai"]["completed_batches"] == 1 and saved["ai"]["input_evidence_count"] == 80
    assert saved["ai"]["pending_review"]["raw_response"] == malformed
    store = Store(tmp_path / "saved-repair.sqlite3")
    try:
        job = store.create_job("research", {})
        research = store.create_research_run(job["id"], {})
        store.checkpoint_research(research["id"], analysis=compact_analysis_catalog(saved))
        restored = store.get_research_run(research["id"])["analysis"]
    finally:
        store.close()
    retried = []

    async def recovered_model(system, data, **options):
        retried.append(copy.deepcopy(data))
        assert data["previous_response"] == malformed
        assert {row["id"] for row in data["evidence"]} == {row["id"] for row in calls[1]["evidence"]}
        return {"affiliation_candidates": []}

    monkeypatch.setattr(relationships, "configured_ai_json", recovered_model)
    result = run(relationships.analyze_relationships_with_ai([account()], posts(100), [], use_ai=True, previous_analysis=restored))
    assert result["ai"]["resumed_batches"] == 1 and len(retried) == 1
    assert result["ai"]["complete"] and result["ai"]["pending_evidence_count"] == 0


def test_two_failed_repairs_leave_durable_explicit_pending_work(monkeypatch):
    calls = []

    async def model(system, data, **options):
        calls.append(data)
        return {"affiliation_candidates": [], "extra": True}

    monkeypatch.setattr(relationships, "configured_ai_json", model)
    result = run(relationships.analyze_relationships_with_ai([account()], posts(1), [], use_ai=True))
    assert len(calls) == 3 and not result["ai"]["complete"]
    assert result["ai"]["input_evidence_count"] == 0
    assert result["ai"]["pending_review"]["repair_attempts"] == 2
    assert result["ai"]["pending_review"]["status"] == "repair_exhausted"
    assert result["ai"]["pending_review"]["validation_issues"][0]["code"] == "top_fields"


def test_oversized_invalid_raw_is_hashed_and_explicitly_not_recoverable(monkeypatch):
    calls = []
    malformed = {"affiliation_candidates": [], "extra": "未采用的公开模型输出" * 40000}

    async def model(system, data, **options):
        calls.append(data)
        return malformed

    monkeypatch.setattr(relationships, "configured_ai_json", model)
    result = run(relationships.analyze_relationships_with_ai([account()], posts(1), [], use_ai=True))
    pending = result["ai"]["pending_review"]
    assert len(calls) == 1 and not result["ai"]["complete"]
    assert pending["raw_response_omitted"] is True and "raw_response" not in pending
    assert pending["raw_response_bytes"] > pending["raw_response_byte_limit"]
    assert pending["raw_response_sha256"] and pending["status"] == "raw_response_omitted"
    assert len(json.dumps(compact_analysis_catalog(result), ensure_ascii=False).encode()) < 1500000


def test_changed_pending_source_invalidates_the_old_raw_before_a_fresh_request(monkeypatch):
    calls, checkpoints = [], []

    async def model(system, data, **options):
        calls.append(data)
        if len(calls) == 1:
            return {"affiliation_candidates": [], "extra": True}
        raise asyncio.CancelledError()

    async def checkpoint(result):
        checkpoints.append(copy.deepcopy(result))

    monkeypatch.setattr(relationships, "configured_ai_json", model)
    original = posts(1)
    with pytest.raises(asyncio.CancelledError):
        run(relationships.analyze_relationships_with_ai([account()], original, [], use_ai=True, checkpoint=checkpoint))
    changed = copy.deepcopy(original)
    changed[0]["content"] += " 来源更正"
    new_calls = []

    async def updated_model(system, data, **options):
        new_calls.append(data)
        assert "previous_response" not in data and "validation_issues" not in data
        assert any("来源更正" in row["excerpt"] for row in data["evidence"])
        return {"affiliation_candidates": []}

    monkeypatch.setattr(relationships, "configured_ai_json", updated_model)
    result = run(relationships.analyze_relationships_with_ai([account()], changed, [], use_ai=True, previous_analysis=checkpoints[-1]))
    assert len(new_calls) == 1 and result["ai"]["complete"]
    assert result["ai"]["pending_review_invalidation"]["reason"] == "evidence_changed"


def test_provider_length_retries_with_larger_actual_capacity_not_a_schema_repair(monkeypatch):
    calls = []
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-flash")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    monkeypatch.delenv("DEEPSEEK_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.delenv("DEEPSEEK_MAX_OUTPUT_TOKENS", raising=False)

    async def model(system, data, **options):
        calls.append(copy.deepcopy(options))
        if len(calls) == 1:
            error = PipelineError("AI 输出未完整结束；未采用截断内容。")
            error.ai_diagnostic = {"finish_reason": "length", "requested_max_tokens": options["max_tokens"], "completion_tokens": 32768}
            raise error
        assert "previous_response" not in data
        return {"affiliation_candidates": []}

    monkeypatch.setattr(relationships, "configured_ai_json", model)
    result = run(relationships.analyze_relationships_with_ai([account()], posts(1), [], use_ai=True, time_budget_seconds=1800))
    assert [row["max_tokens"] for row in calls] == [32768, 65536]
    assert result["ai"]["complete"] and result["ai"]["schema_repair_calls"] == 0
    assert result["ai"]["review_attempts"][0]["provider_diagnostic"]["finish_reason"] == "length"


def test_http_failure_is_not_automatically_repaired_or_marked_reviewed(monkeypatch):
    calls = []

    async def model(system, data, **options):
        calls.append(data)
        raise PipelineError("现有 DeepSeek 连接返回 HTTP 503；本次未使用 AI 结果。")

    monkeypatch.setattr(relationships, "configured_ai_json", model)
    result = run(relationships.analyze_relationships_with_ai([account()], posts(1), [], use_ai=True))
    assert len(calls) == 1 and not result["ai"]["complete"] and result["ai"]["input_evidence_count"] == 0
    assert result["ai"]["schema_repair_calls"] == 0
    assert result["ai"]["pending_review"]["status"] == "request_failed"
    assert result["ai"]["review_attempts"][0]["error_category"] == "provider_request"


def pending_run(store, *, omit_catalogue=False):
    job = store.create_job("research", {})
    research = store.create_research_run(job["id"], {})
    material = {**posts(1)[0], "type": "social", "title": "公开来源"}
    store.checkpoint_research(research["id"], accounts=[{"platform": "twitter", "handle": "alpha", "name": "alpha",
        "url": "https://x.com/alpha/", "status": "candidate"}], nodes=[{"account_key": "twitter:alpha"}], items=[material])
    material = store.get_research_run(research["id"])["items"][0]
    catalogue = relationships.evidence_catalog([account()], [material], [])
    expected = {row["id"]: relationships._review_hash(row) for row in catalogue}
    malformed = {"affiliation_candidates": [], "extra": "deleted_source_fragment_for_repair"}
    pending = {"batch_id": relationships._review_hash(expected), "context_fingerprint": "0" * 64,
        "evidence_hashes": expected, "evidence_source_keys": {row["id"]: source_url_key(row["url"]) for row in catalogue},
        "repair_attempts": 0, "requested_max_tokens": 32768, "status": "validation_failed",
        "validation_issues": [{"code": "top_fields"}], **relationships._bounded_response(malformed)}
    analysis = {"evidence": [] if omit_catalogue else catalogue, "findings": [], "affiliations": [],
        "ai": {"requested": True, "complete": False, "review_work_version": 1, "pending_review": pending,
            "review_batches": [], "input_evidence_count": 0, "available_evidence_count": len(catalogue), "pending_evidence_count": len(catalogue)}}
    store.checkpoint_research(research["id"], analysis=analysis, phase="finished", status="partial")
    store.update_job(job["id"], status="partial")
    return research["id"], material, analysis


def clean(store, item_id, action):
    preview = store.cleanup_preview([item_id], action)
    return store.cleanup_apply([item_id], action, preview["preview_digest"], "资料清理边界测试", confirm_purge=action == "purge")


@pytest.mark.parametrize("omit_catalogue", [False, True])
def test_related_trash_hides_raw_restore_recovers_it_and_purge_permanently_invalidates(tmp_path, omit_catalogue):
    store = Store(tmp_path / "pending-cleanup.sqlite3")
    try:
        run_id, material, analysis = pending_run(store, omit_catalogue=omit_catalogue)
        report = store.save_report({"title": "独立报告快照", "mode": "evidence", "markdown": "独立已保存快照", "snapshot": {"items": [material]}})
        clean(store, material["id"], "trash")
        current = store.get_research_run(run_id)["analysis"]
        assert current["ai"]["pending_review"] is None
        assert "deleted_source_fragment_for_repair" not in json.dumps(current)
        assert current["ai"]["pending_review_invalidation"]["reason"] == "source_cleanup"
        # Trash is a reversible projection; the durable paid draft remains so
        # restoring the source restores the same draft rather than buying it.
        with sqlite3.connect(store.path) as db:
            raw = json.loads(db.execute("SELECT analysis FROM wb_research_runs WHERE id=?", (run_id,)).fetchone()[0])
            assert raw["ai"]["pending_review"] == analysis["ai"]["pending_review"]
        clean(store, material["id"], "restore")
        assert store.get_research_run(run_id)["analysis"]["ai"]["pending_review"] == analysis["ai"]["pending_review"]
        clean(store, material["id"], "trash")
        clean(store, material["id"], "purge")
        with sqlite3.connect(store.path) as db:
            raw = json.loads(db.execute("SELECT analysis FROM wb_research_runs WHERE id=?", (run_id,)).fetchone()[0])
            assert raw["ai"]["pending_review"] is None
            assert "deleted_source_fragment_for_repair" not in json.dumps(raw)
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
            backup = sqlite3.connect(tmp_path / "purged.sqlite3")
            db.backup(backup)
            backup.close()
        reopened = Store(tmp_path / "purged.sqlite3")
        try:
            assert reopened.get_research_run(run_id)["analysis"]["ai"]["pending_review"] is None
        finally:
            reopened.close()
        assert store.get_report(report["id"])["snapshot"] == report["snapshot"]
    finally:
        store.close()


@pytest.mark.parametrize("omit_catalogue", [False, True])
def test_unrelated_trash_and_purge_leave_paid_pending_response_unchanged(tmp_path, omit_catalogue):
    store = Store(tmp_path / "unrelated-cleanup.sqlite3")
    try:
        run_id, material, analysis = pending_run(store, omit_catalogue=omit_catalogue)
        unrelated = store.import_items([{**material, "url": "https://x.com/alpha/status/999", "content": "另一份无关正文"}])["resolved_ids"][0]
        for action in ("trash", "purge"):
            clean(store, unrelated, action)
            assert store.get_research_run(run_id)["analysis"]["ai"]["pending_review"] == analysis["ai"]["pending_review"]
        with sqlite3.connect(store.path) as db:
            raw = json.loads(db.execute("SELECT analysis FROM wb_research_runs WHERE id=?", (run_id,)).fetchone()[0])
            assert raw["ai"]["pending_review"] == analysis["ai"]["pending_review"]
    finally:
        store.close()


@pytest.mark.parametrize("code,defect", [
    ("top_type", "top_type"), ("candidate_list_type", "candidate_list_type"),
    ("candidate_list_length", "candidate_list_length"), ("row_type", "row_type"), ("row_fields", "row_fields"),
    ("account_reference", "account_reference"), ("evidence_ids_type", "evidence_ids_type"),
    ("evidence_ids_length", "evidence_ids_length"), ("self_evidence", "self_evidence"),
    ("organization_value", "organization_value"), ("claim_value", "claim_value"),
])
def test_specific_strict_validation_diagnostic_guides_same_batch_repair(monkeypatch, code, defect):
    calls = []

    async def model(system, data, **options):
        calls.append(copy.deepcopy(data))
        row = good_candidate(data)
        if len(calls) > 1:
            assert any(issue["code"] == code for issue in data["validation_issues"])
            return {"affiliation_candidates": [row]}
        if defect == "top_type":
            return []
        if defect == "candidate_list_type":
            return {"affiliation_candidates": {}}
        if defect == "candidate_list_length":
            return {"affiliation_candidates": [row] * 16}
        if defect == "row_type":
            return {"affiliation_candidates": [None]}
        if defect == "row_fields":
            row["status"] = "verified"
        elif defect == "account_reference":
            row["account_key"] = "twitter:unknown"
        elif defect == "evidence_ids_type":
            row["evidence_ids"] = row["evidence_ids"][0]
        elif defect == "evidence_ids_length":
            row["evidence_ids"] = []
        elif defect == "self_evidence":
            row["evidence_ids"] = [next(source["id"] for source in data["evidence"] if source["account_key"] == "twitter:beta")]
        elif defect == "organization_value":
            row["organization"] = "X" * 101
        elif defect == "claim_value":
            row["claim"] = "不允许输出 URL https://example.org"
        return {"affiliation_candidates": [row]}

    monkeypatch.setattr(relationships, "configured_ai_json", model)
    original = [account(), account("beta")]
    result = run(relationships.analyze_relationships_with_ai(original, posts(1), [], use_ai=True))
    assert len(calls) == 2 and result["ai"]["complete"] and all(row["status"] == "candidate" for row in original)
    assert all(row["status"] != "verified" for row in result["affiliations"])


def test_paid_valid_response_checkpoint_resumes_with_zero_model_requests(monkeypatch):
    checkpoints, calls = [], []

    async def model(system, data, **options):
        calls.append(data)
        return {"affiliation_candidates": [good_candidate(data)]}

    async def checkpoint(value):
        checkpoints.append(copy.deepcopy(value))
        if (value["ai"].get("pending_review") or {}).get("status") == "response_received":
            raise asyncio.CancelledError()

    monkeypatch.setattr(relationships, "configured_ai_json", model)
    with pytest.raises(asyncio.CancelledError):
        run(relationships.analyze_relationships_with_ai([account()], posts(1), [], use_ai=True, checkpoint=checkpoint))

    async def forbidden(*args, **kwargs):
        raise AssertionError("A received paid valid final JSON must not be requested again")

    monkeypatch.setattr(relationships, "configured_ai_json", forbidden)
    result = run(relationships.analyze_relationships_with_ai([account()], posts(1), [], use_ai=True, previous_analysis=checkpoints[-1]))
    assert len(calls) == 1 and result["ai"]["complete"] and result["ai"]["calls_made"] == 0
    assert any(row["method"] == "configured_ai" and row["requires_review"] for row in result["affiliations"])


def test_cancelled_expanded_output_resumes_at_larger_limit_without_short_repeat(monkeypatch):
    checkpoints, calls = [], []
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-flash")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    monkeypatch.delenv("DEEPSEEK_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.delenv("DEEPSEEK_MAX_OUTPUT_TOKENS", raising=False)

    async def model(system, data, **options):
        calls.append(options["max_tokens"])
        if len(calls) == 1:
            error = PipelineError("Output stopped at length")
            error.ai_diagnostic = {"finish_reason": "length", "requested_max_tokens": options["max_tokens"]}
            raise error
        raise asyncio.CancelledError()

    async def checkpoint(value):
        checkpoints.append(copy.deepcopy(value))

    monkeypatch.setattr(relationships, "configured_ai_json", model)
    with pytest.raises(asyncio.CancelledError):
        run(relationships.analyze_relationships_with_ai([account()], posts(1), [], use_ai=True, checkpoint=checkpoint))
    assert calls == [32768, 65536]
    restored_calls = []

    async def resumed(system, data, **options):
        restored_calls.append(options["max_tokens"])
        return {"affiliation_candidates": []}

    monkeypatch.setattr(relationships, "configured_ai_json", resumed)
    result = run(relationships.analyze_relationships_with_ai([account()], posts(1), [], use_ai=True, previous_analysis=checkpoints[-1]))
    assert restored_calls == [65536] and result["ai"]["complete"]


def test_known_exhausted_output_capacity_does_not_repeat_the_same_short_request(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_MODEL", "fixture-model")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://fixture.example")
    monkeypatch.setenv("DEEPSEEK_CONTEXT_WINDOW_TOKENS", "131072")
    monkeypatch.setenv("DEEPSEEK_MAX_OUTPUT_TOKENS", "16384")
    calls = []

    async def model(system, data, **options):
        calls.append(options["max_tokens"])
        error = PipelineError("AI 输出未完整结束；未采用截断内容。")
        error.ai_diagnostic = {"finish_reason": "length", "requested_max_tokens": options["max_tokens"]}
        raise error

    monkeypatch.setattr(relationships, "configured_ai_json", model)
    partial = run(relationships.analyze_relationships_with_ai([account()], posts(1), [], use_ai=True))
    assert calls == [16384] and partial["ai"]["pending_review"]["output_growth_required"]
    result = run(relationships.analyze_relationships_with_ai([account()], posts(1), [], use_ai=True, previous_analysis=partial))
    assert calls == [16384] and not result["ai"]["complete"] and result["ai"]["calls_made"] == 0
    # A real increase in configured public capacity can advance the same work.
    monkeypatch.setenv("DEEPSEEK_MAX_OUTPUT_TOKENS", "32768")

    async def recovered(system, data, **options):
        calls.append(options["max_tokens"])
        return {"affiliation_candidates": []}

    monkeypatch.setattr(relationships, "configured_ai_json", recovered)
    recovered_result = run(relationships.analyze_relationships_with_ai([account()], posts(1), [], use_ai=True, previous_analysis=result))
    assert calls == [16384, 32768] and recovered_result["ai"]["complete"]


def test_final_json_with_secret_or_hidden_reasoning_fields_is_omitted(monkeypatch):
    async def model(system, data, **options):
        return {"affiliation_candidates": [], "reasoning_content": "fixture-hidden-text", "authorization": "fixture-secret"}

    monkeypatch.setattr(relationships, "configured_ai_json", model)
    result = run(relationships.analyze_relationships_with_ai([account()], posts(1), [], use_ai=True))
    state = result["ai"]["pending_review"]
    assert state["raw_response_omitted"] and state["raw_response_omission_reason"] == "sensitive_response_fields"
    assert "fixture-hidden-text" not in json.dumps(result) and "fixture-secret" not in json.dumps(result)
    assert not result["ai"]["complete"] and result["ai"]["input_evidence_count"] == 0


def test_provider_exception_values_cannot_leak_into_durable_diagnostics(monkeypatch):
    async def model(system, data, **options):
        error = PipelineError("HTTP 503 Authorization fixture-secret-value")
        error.ai_diagnostic = {"headers": {"Authorization": "fixture-secret-value"}, "finish_reason": ["untrusted"], "reasoning_content": "fixture-hidden-value"}
        raise error

    monkeypatch.setattr(relationships, "configured_ai_json", model)
    result = run(relationships.analyze_relationships_with_ai([account()], posts(1), [], use_ai=True))
    assert not result["ai"]["complete"] and result["ai"]["provider_diagnostic"]["http_status"] == 503
    assert "fixture-secret-value" not in json.dumps(result) and "fixture-hidden-value" not in json.dumps(result)


def test_maximum_source_hashes_plus_bounded_invalid_raw_fit_analysis_checkpoint(monkeypatch):
    calls = []
    malformed = {"affiliation_candidates": [], "extra": "X" * 250000}

    async def model(system, data, **options):
        calls.append(data)
        if len(calls) <= 126:
            return {"affiliation_candidates": []}
        return malformed

    monkeypatch.setattr(relationships, "configured_ai_json", model)
    result = run(relationships.analyze_relationships_with_ai([account()], posts(10000), [], use_ai=True, time_budget_seconds=600))
    assert not result["ai"]["complete"] and result["ai"]["completed_batches"] == 126
    assert result["ai"]["pending_review"]["raw_response"] == malformed
    compact = compact_analysis_catalog(result)
    assert len(json.dumps(compact, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= 1500000
    assert compact["ai"]["pending_review"]["raw_response"] == malformed
    assert len(compact["ai"]["review_attempts"]) == 32
    assert compact["ai"]["review_attempts_omitted_count"] == len(calls) - 32
    assert set(compact["ai"]["pending_review"]["evidence_source_keys"]) == set(compact["ai"]["pending_review"]["evidence_hashes"])


def test_transport_failure_during_repair_keeps_raw_and_reuses_accepted_eighty(monkeypatch):
    calls = []
    malformed = {"affiliation_candidates": [], "extra": "paid draft"}

    async def model(system, data, **options):
        calls.append(copy.deepcopy(data))
        if len(calls) == 1:
            return {"affiliation_candidates": []}
        if len(calls) == 2:
            return malformed
        raise PipelineError("HTTP 503 transport failed")

    monkeypatch.setattr(relationships, "configured_ai_json", model)
    partial = run(relationships.analyze_relationships_with_ai([account()], posts(100), [], use_ai=True))
    assert partial["ai"]["input_evidence_count"] == 80 and len(calls) == 3
    assert partial["ai"]["pending_review"]["repair_attempts"] == 0  # No corrected response was received.
    assert partial["ai"]["pending_review"]["raw_response"] == malformed
    resumed_calls = []

    async def resumed(system, data, **options):
        resumed_calls.append(copy.deepcopy(data))
        assert data["previous_response"] == malformed
        return {"affiliation_candidates": []}

    monkeypatch.setattr(relationships, "configured_ai_json", resumed)
    result = run(relationships.analyze_relationships_with_ai([account()], posts(100), [], use_ai=True, previous_analysis=partial))
    assert result["ai"]["resumed_batches"] == 1 and len(resumed_calls) == 1 and result["ai"]["complete"]


def test_untrusted_attempt_metadata_is_filtered_and_bad_source_mapping_never_reuses_raw(monkeypatch):
    calls = []

    async def model(system, data, **options):
        calls.append(data)
        if len(calls) == 1:
            return {"affiliation_candidates": []}
        if len(calls) == 2:
            return {"affiliation_candidates": [], "extra": True}
        raise PipelineError("HTTP 503")

    monkeypatch.setattr(relationships, "configured_ai_json", model)
    partial = run(relationships.analyze_relationships_with_ai([account()], posts(100), [], use_ai=True))
    pending = partial["ai"]["pending_review"]
    pending["evidence_source_keys"][next(iter(pending["evidence_source_keys"]))] = "https://unrelated.example/source"
    partial["ai"]["review_attempts"] = [{"mode": [], "status": {}, "error_category": [], "secret": "fixture-imported-secret"}]
    recovered_calls = []

    async def recovered(system, data, **options):
        recovered_calls.append(data)
        assert "previous_response" not in data
        return {"affiliation_candidates": []}

    monkeypatch.setattr(relationships, "configured_ai_json", recovered)
    result = run(relationships.analyze_relationships_with_ai([account()], posts(100), [], use_ai=True, previous_analysis=partial))
    assert result["ai"]["resumed_batches"] == 1 and len(recovered_calls) == 1 and result["ai"]["complete"]
    assert result["ai"]["pending_review_invalidation"]["reason"] == "source_mapping_changed"
    assert "fixture-imported-secret" not in json.dumps(result)
