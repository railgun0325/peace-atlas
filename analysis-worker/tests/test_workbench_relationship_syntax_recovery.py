"""Strict offline recovery of completed final content; no live credentials/calls."""
import asyncio
import copy
import hashlib
import json
import sqlite3

import httpx
import pytest

from app.workbench import query_plan
from app.workbench import relationship_analysis as relationships
from app.workbench.pipeline import PipelineError
from app.workbench.research_engine import compact_analysis_catalog
from app.workbench.store import Store
from app.workbench.research_repository import source_url_key


def accounts():
    return [{"account_key": "twitter:alpha", "platform": "twitter", "handle": "alpha", "name": "alpha", "status": "candidate", "profile": {
        "verified_in_dom": True, "url": "https://x.com/alpha/", "bio": "所属：公开团体A。",
        "observed_at": "2026-10-09T00:00:00Z"}}]


def posts(count=1):
    return [{"account_key": "twitter:alpha", "url": f"https://x.com/alpha/status/{index + 1}",
        "content": f"公开团体A 的公开材料 {index + 1}", "published_at": "2026-10-08T00:00:00Z",
        "metadata": {"collected_at": "2026-10-09T00:00:00Z"}} for index in range(count)]


def transport(monkeypatch, handler):
    native = httpx.AsyncClient
    calls = []

    def mocked(request):
        data = json.loads(request.content)
        calls.append(json.loads(data["messages"][1]["content"]))
        return handler(calls[-1], len(calls), request)

    monkeypatch.setattr(query_plan.httpx, "AsyncClient", lambda **options: native(
        transport=httpx.MockTransport(mocked), **options))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-secret-marker")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-flash")
    return calls


def final(content, *, finish="stop", status=200):
    return httpx.Response(status, json={"choices": [{"finish_reason": finish, "message": {
        "content": content, "reasoning_content": "fixture-hidden-envelope"}}],
        "headers": {"Authorization": "fixture-envelope-secret"}, "settings": "fixture-envelope-settings"})


def test_completed_non_json_transport_retains_only_exact_final_content(monkeypatch):
    raw = '{"affiliation_candidates": [}\n日本😀'
    calls = transport(monkeypatch, lambda data, number, request: final(raw))
    with pytest.raises(PipelineError) as caught:
        asyncio.run(query_plan.configured_ai_json("JSON", {"public": "公开"}))
    retained = caught.value.final_response
    assert retained["raw_response"] == raw and retained["raw_response_format"] == "json_text"
    assert retained["raw_response_sha256"] == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert retained["raw_response_bytes"] == len(raw.encode("utf-8"))
    assert caught.value.code == "ai_json_syntax" and len(calls) == 1
    assert caught.value.__context__ is None
    saved = str(caught.value) + json.dumps(vars(caught.value), ensure_ascii=False)
    assert all(marker not in saved for marker in (
        "fixture-hidden-envelope", "fixture-envelope-secret", "fixture-envelope-settings", "synthetic-secret-marker"))


def test_actual_transport_syntax_repair_keeps_accepted_eighty_and_candidate_boundary(monkeypatch):
    raw = '{"affiliation_candidates": [],}'
    checkpoints = []
    accepted_source_hashes = {}

    def handler(data, number, request):
        if number == 1:
            return final('{"affiliation_candidates":[]}')
        if number == 2:
            accepted_source_hashes.update(checkpoints[-1]["ai"]["review_batches"][0]["evidence_hashes"])
            return final(raw)
        assert data["previous_response"] == raw
        assert data["previous_response_format"] == "json_text"
        assert data["validation_issues"] == [{"code": "json_syntax"}]
        profile = next(row["id"] for row in data["evidence"] if row["type"] == "profile")
        return final(json.dumps({"affiliation_candidates": [{"account_key": "twitter:alpha",
            "organization": "公开团体A", "claim": "本人简介自述，仍须核对。", "evidence_ids": [profile]}]}, ensure_ascii=False))

    calls = transport(monkeypatch, handler)

    async def checkpoint(value):
        checkpoints.append(copy.deepcopy(value))

    result = asyncio.run(relationships.analyze_relationships_with_ai(accounts(), posts(100), [],
        use_ai=True, checkpoint=checkpoint))
    retained = next(row["ai"]["pending_review"] for row in checkpoints
        if row["ai"].get("pending_review") and row["ai"]["pending_review"].get("raw_response") == raw)
    assert len(calls) == 3 and result["ai"]["complete"] and result["ai"]["schema_repair_calls"] == 1
    assert retained["raw_response_format"] == "json_text" and retained["status"] in {"response_received", "validation_failed", "requesting"}
    assert result["ai"]["review_batches"][0]["evidence_hashes"] == accepted_source_hashes
    assert len(accepted_source_hashes) == 80
    candidate = next(row for row in result["affiliations"] if row["method"] == "configured_ai")
    assert candidate["status"] == "candidate" and candidate["requires_review"]
    assert result["ai"]["input_evidence_count"] == 101
    assert "fixture-hidden-envelope" not in json.dumps(checkpoints)


def test_cancelled_syntax_repair_roundtrips_store_then_ordinary_retry_uses_same_string(tmp_path, monkeypatch):
    raw = '```json\n{"affiliation_candidates": [],}\n```'
    checkpoints = []

    def handler(data, number, request):
        if number == 1:
            return final('{"affiliation_candidates":[]}')
        if number == 2:
            return final(raw)
        assert data["previous_response"] == raw
        raise asyncio.CancelledError()

    calls = transport(monkeypatch, handler)

    async def checkpoint(value):
        checkpoints.append(copy.deepcopy(value))

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(relationships.analyze_relationships_with_ai(accounts(), posts(100), [],
            use_ai=True, checkpoint=checkpoint))
    partial = checkpoints[-1]
    assert len(calls) == 3 and partial["ai"]["input_evidence_count"] == 80
    pending = partial["ai"]["pending_review"]
    assert pending["raw_response"] == raw and pending["repair_attempts"] == 0
    original_hashes = copy.deepcopy(partial["ai"]["review_batches"][0]["evidence_hashes"])
    store = Store(tmp_path / "syntax.sqlite3")
    try:
        job = store.create_job("research", {})
        research = store.create_research_run(job["id"], {})
        store.checkpoint_research(research["id"], items=posts(100), accounts=accounts(),
            analysis=compact_analysis_catalog(partial))
        restored = store.get_research_run(research["id"])["analysis"]
    finally:
        store.close()
    assert restored["ai"]["pending_review"]["raw_response"] == raw
    retries = []

    async def corrected(system, data, **options):
        retries.append(copy.deepcopy(data))
        assert data["previous_response"] == raw and data["previous_response_format"] == "json_text"
        return {"affiliation_candidates": []}

    monkeypatch.setattr(relationships, "configured_ai_json", corrected)
    result = asyncio.run(relationships.analyze_relationships_with_ai(accounts(), posts(100), [],
        use_ai=True, previous_analysis=restored))
    assert result["ai"]["complete"] and result["ai"]["resumed_batches"] == 1 and len(retries) == 1
    assert result["ai"]["review_batches"][0]["evidence_hashes"] == original_hashes


@pytest.mark.parametrize("raw", [
    '{"authorization": "fixture-secret-value",',
    '{"reasoning_content": "fixture-hidden-value",',
    '{"provider_headers": {"X-Anything": "fixture-secret-value"},',
    '{"app_settings": "fixture-secret-value",',
    '{"nested": "DEEPSEEK_API_KEY=fixture-secret-value",',
    '{"nested": "Bearer fixture-secret-value",',
    '{"nested": "sk-1234567890abcdefghijklmnop",',
    '{"nested": "https://public.example/?access_token=fixture-secret-value",',
    '{"nested": "synthetic-secret-marker",',
])
def test_secret_suspected_final_content_is_hash_only_and_never_repaired(monkeypatch, raw):
    calls = transport(monkeypatch, lambda data, number, request: final(raw))
    result = asyncio.run(relationships.analyze_relationships_with_ai(accounts(), posts(), [], use_ai=True))
    state = result["ai"]["pending_review"]
    assert state["raw_response_omitted"] and "raw_response" not in state
    assert state["raw_response_omission_reason"] == "sensitive_response_fields"
    assert state["raw_response_sha256"] == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert len(calls) == 1 and not result["ai"]["complete"]
    assert "fixture-secret-value" not in json.dumps(result)
    assert "fixture-hidden-value" not in json.dumps(result)


@pytest.mark.parametrize("value", [
    {"extra": "password = fixture-secret-value"},
    {"extra": "Bearer fixture-secret-value"},
    {"extra": "sk-1234567890abcdefghijklmnop"},
    {"provider_headers": {"X-Anything": "fixture-secret-value"}},
    {"app_settings": "fixture-secret-value"},
])
def test_parsed_and_string_raw_have_the_same_sensitive_content_gate(value):
    parsed = relationships._bounded_response(value)
    text = relationships._bounded_response(json.dumps(value), response_format="json_text")
    assert parsed["raw_response_omitted"] and text["raw_response_omitted"]
    assert parsed["raw_response_omission_reason"] == text["raw_response_omission_reason"] == "sensitive_response_fields"


@pytest.mark.parametrize("byte_count, omitted", [(262143, False), (262144, False), (262145, True)])
def test_syntax_utf8_byte_limit_hashes_original_bytes(monkeypatch, byte_count, omitted):
    raw = "日" * 87381 + "X" * (byte_count - 262143)
    calls = transport(monkeypatch, lambda data, number, request: final(raw))
    with pytest.raises(PipelineError) as caught:
        asyncio.run(query_plan.configured_ai_json("JSON", {}))
    retained = caught.value.final_response
    assert retained["raw_response_bytes"] == byte_count
    assert retained["raw_response_sha256"] == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert retained["raw_response_omitted"] is omitted
    assert ("raw_response" in retained) is not omitted and len(calls) == 1
    if omitted:
        assert retained["raw_response_omission_reason"] == "raw_response_byte_limit"


@pytest.mark.parametrize("finish, status", [("length", 200), ("content_filter", 200), ("stop", 503)])
def test_incomplete_or_http_response_never_becomes_retained_syntax(monkeypatch, finish, status):
    calls = transport(monkeypatch, lambda data, number, request: final("private-final-marker", finish=finish, status=status))
    with pytest.raises(PipelineError) as caught:
        asyncio.run(query_plan.configured_ai_json("JSON", {}))
    assert not hasattr(caught.value, "final_response") and len(calls) == 1
    assert "private-final-marker" not in str(caught.value) + str(vars(caught.value))


@pytest.mark.parametrize("raw", [
    '{"affiliation_candidates":[{"evidence_ids":["unknown"]}],"affiliation_candidates":[]}',
    '{"affiliation_candidates": [], "extra": NaN}',
])
def test_nonfinite_and_duplicate_fields_are_not_silently_accepted(monkeypatch, raw):
    calls = transport(monkeypatch, lambda data, number, request: final(raw))
    result = asyncio.run(relationships.analyze_relationships_with_ai(accounts(), posts(), [], use_ai=True))
    assert not result["ai"]["complete"] and result["ai"]["input_evidence_count"] == 0
    assert result["ai"]["pending_review"]["raw_response"] == raw
    assert result["ai"]["pending_review"]["validation_issues"] == [{"code": "json_syntax"}]
    assert len(calls) == 3 and result["ai"]["pending_review"]["repair_attempts"] == 2


@pytest.mark.parametrize("defect", ["unknown_evidence", "unknown_account", "self_evidence", "unsupported_org"])
def test_syntax_repair_still_applies_all_evidence_and_fact_boundaries(monkeypatch, defect):
    raw = '{"affiliation_candidates": [],}'

    def handler(data, number, request):
        if number == 1:
            return final(raw)
        profile = next(row["id"] for row in data["evidence"] if row["type"] == "profile" and row["account_key"] == "twitter:alpha")
        other_profile = next(row["id"] for row in data["evidence"] if row["type"] == "profile" and row["account_key"] == "twitter:beta")
        row = {"account_key": "twitter:alpha", "organization": "公开团体A", "claim": "待核对", "evidence_ids": [profile]}
        if defect == "unknown_evidence":
            row["evidence_ids"] = ["unknown-evidence"]
        elif defect == "unknown_account":
            row["account_key"] = "twitter:outside"
        elif defect == "self_evidence":
            row["evidence_ids"] = [other_profile]
        else:
            row["organization"] = "没有本人支持的团体"
        return final(json.dumps({"affiliation_candidates": [row]}, ensure_ascii=False))

    calls = transport(monkeypatch, handler)
    extra = copy.deepcopy(accounts()[0])
    extra["account_key"] = "twitter:beta"
    extra["profile"]["url"] = "https://x.com/beta/"
    result = asyncio.run(relationships.analyze_relationships_with_ai(accounts() + [extra], posts(), [], use_ai=True))
    assert not any(row.get("method") == "configured_ai" for row in result["affiliations"])
    if defect == "unsupported_org":
        assert result["ai"]["complete"] and result["ai"]["rejected_candidate_count"] == 1 and len(calls) == 2
    else:
        assert not result["ai"]["complete"] and result["ai"]["input_evidence_count"] == 0 and len(calls) == 3


def test_valid_json_echo_of_runtime_credential_is_never_retained(monkeypatch):
    raw = '{"affiliation_candidates": [], "extra": "synthetic-secret-marker"}'
    calls = transport(monkeypatch, lambda data, number, request: final(raw))
    result = asyncio.run(relationships.analyze_relationships_with_ai(accounts(), posts(), [], use_ai=True))
    state = result["ai"]["pending_review"]
    assert state["raw_response_omitted"] and state["raw_response_omission_reason"] == "sensitive_response_fields"
    assert state["raw_response_sha256"] == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert len(calls) == 1 and "synthetic-secret-marker" not in json.dumps(result)


@pytest.mark.parametrize("raw", [
    '{"\\u0061uthorization": "fixture-secret-value",',
    '{"nested": "\\u0042earer fixture-secret-value",',
    '{"nested": "\\u0073k-1234567890abcdefghijklmnop",',
    "api_key = fixture-secret-value",
    "access_token: fixture-secret-value",
    "'api-key': 'fixture-secret-value'",
    "Authorization: Basic Zml4dHVyZTpwYXNzd29yZA==",
    '{"client_secret_value": "fixture-secret-value",',
    '{"password_hash": "fixture-secret-value",',
    '{"hidden_reasoning_notes": "fixture-hidden-value",',
    '{"env_variables": "fixture-secret-value",',
    '{"apiKeyValue": "fixture-secret-value",',
    '{"session_cookie": "fixture-secret-value",',
])
def test_escaped_and_non_json_credential_forms_are_hash_only(monkeypatch, raw):
    calls = transport(monkeypatch, lambda data, number, request: final(raw))
    result = asyncio.run(relationships.analyze_relationships_with_ai(accounts(), posts(), [], use_ai=True))
    assert result["ai"]["pending_review"]["raw_response_omitted"]
    assert len(calls) == 1 and "fixture-secret-value" not in json.dumps(result)


def test_public_api_and_token_budget_words_do_not_trigger_the_credential_gate():
    response = {"affiliation_candidates": [], "extra": "API 技术新闻，token 预算 32768，token_count: 32768。公开讨论密码政策。"}
    assert not relationships._bounded_response(response)["raw_response_omitted"]
    assert not relationships._bounded_response(json.dumps(response, ensure_ascii=False), response_format="json_text")["raw_response_omitted"]
    assert not relationships._bounded_response({"reasoning_effort": "low", "token_budget": 32768})["raw_response_omitted"]


def test_transport_timeout_never_retains_final_content_or_automatically_retries(monkeypatch):
    def handler(data, number, request):
        raise httpx.ReadTimeout("fixture-provider-private-message", request=request)

    calls = transport(monkeypatch, handler)
    result = asyncio.run(relationships.analyze_relationships_with_ai(accounts(), posts(), [], use_ai=True))
    assert len(calls) == 1 and not result["ai"]["complete"] and result["ai"]["input_evidence_count"] == 0
    assert "raw_response" not in result["ai"]["pending_review"]
    assert "fixture-provider-private-message" not in json.dumps(result)


def test_http402_has_explicit_safe_balance_message_without_retry_or_progress_loss(monkeypatch):
    def handler(data, number, request):
        if number == 1:
            return final('{"affiliation_candidates":[]}')
        return final("fixture-provider-private-value", status=402)

    calls = transport(monkeypatch, handler)
    result = asyncio.run(relationships.analyze_relationships_with_ai(accounts(), posts(100), [], use_ai=True))
    assert len(calls) == 2 and result["ai"]["input_evidence_count"] == 80 and not result["ai"]["complete"]
    assert result["ai"]["provider_diagnostic"]["http_status"] == 402
    assert result["ai"]["message"] == "DeepSeek 余额不足；已完成批次和待处理证据保留，可补充余额后重试。"
    assert "raw_response" not in result["ai"]["pending_review"] and result["ai"]["schema_repair_calls"] == 0
    assert len(result["ai"]["review_batches"][0]["evidence_hashes"]) == 80
    assert "fixture-provider-private-value" not in json.dumps(result)


def test_exhausted_saved_syntax_does_not_buy_more_repairs_on_ordinary_retry(monkeypatch):
    raw = '{"affiliation_candidates": [],}'
    calls = transport(monkeypatch, lambda data, number, request: final(raw))
    partial = asyncio.run(relationships.analyze_relationships_with_ai(accounts(), posts(), [], use_ai=True))
    assert len(calls) == 3 and partial["ai"]["pending_review"]["repair_attempts"] == 2

    async def forbidden(*args, **options):
        pytest.fail("Exhausted syntax corrections must not repeat automatically")

    monkeypatch.setattr(relationships, "configured_ai_json", forbidden)
    result = asyncio.run(relationships.analyze_relationships_with_ai(accounts(), posts(), [], use_ai=True, previous_analysis=partial))
    assert result["ai"]["calls_made"] == 0 and not result["ai"]["complete"]
    assert result["ai"]["pending_review"]["raw_response"] == raw
    assert result["ai"]["pending_review"]["status"] == "repair_exhausted"


def test_imported_omission_format_metadata_cannot_leak_or_crash(monkeypatch):
    calls = transport(monkeypatch, lambda data, number, request: final('{"api_key": "fixture-secret-value",'))
    partial = asyncio.run(relationships.analyze_relationships_with_ai(accounts(), posts(), [], use_ai=True))
    partial["ai"]["pending_review"]["raw_response_format"] = ["fixture-imported-secret"]
    result = asyncio.run(relationships.analyze_relationships_with_ai(accounts(), posts(), [], use_ai=True, previous_analysis=partial))
    assert len(calls) == 1 and result["ai"]["pending_review"]["raw_response_omitted"]
    assert "fixture-imported-secret" not in json.dumps(result)


def test_legacy_parsed_json_canonical_hash_stays_identical():
    response = {"extra": "公开草稿", "affiliation_candidates": []}
    expected = json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    retained = relationships._bounded_response(response)
    assert retained["raw_response_sha256"] == hashlib.sha256(expected).hexdigest()
    assert retained["raw_response_bytes"] == len(expected) and retained["raw_response"] == response


@pytest.mark.parametrize("failure", ["http", "timeout", "incomplete"])
def test_failed_transport_repair_keeps_safe_syntax_then_ordinary_retry_repairs(monkeypatch, failure):
    raw = '{"affiliation_candidates": [],}'

    def handler(data, number, request):
        if number == 1:
            return final('{"affiliation_candidates":[]}')
        if number == 2:
            return final(raw)
        assert data["previous_response"] == raw
        if failure == "timeout":
            raise httpx.ReadTimeout("fixture-unusable-private-value", request=request)
        return final("fixture-unusable-private-value", status=503 if failure == "http" else 200,
            finish="content_filter" if failure == "incomplete" else "stop")

    calls = transport(monkeypatch, handler)
    partial = asyncio.run(relationships.analyze_relationships_with_ai(accounts(), posts(100), [], use_ai=True))
    assert len(calls) == 3 and partial["ai"]["input_evidence_count"] == 80 and not partial["ai"]["complete"]
    pending = partial["ai"]["pending_review"]
    assert pending["raw_response"] == raw and pending["repair_attempts"] == 0
    assert pending["raw_response_sha256"] == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert "fixture-unusable-private-value" not in json.dumps(partial)
    retries = []

    async def corrected(system, data, **options):
        retries.append(data)
        assert data["previous_response"] == raw
        return {"affiliation_candidates": []}

    monkeypatch.setattr(relationships, "configured_ai_json", corrected)
    recovered = asyncio.run(relationships.analyze_relationships_with_ai(accounts(), posts(100), [], use_ai=True, previous_analysis=partial))
    assert recovered["ai"]["complete"] and recovered["ai"]["resumed_batches"] == 1 and len(retries) == 1
    assert recovered["ai"]["review_batches"][0]["evidence_hashes"] == partial["ai"]["review_batches"][0]["evidence_hashes"]


@pytest.mark.parametrize("change", ["raw", "raw_hash", "raw_bytes", "format", "batch", "context", "source_key", "source_hash"])
def test_changed_saved_syntax_is_invalidated_without_rebuying_accepted_batch(monkeypatch, change):
    checkpoints = []

    def handler(data, number, request):
        if number == 1:
            return final('{"affiliation_candidates":[]}')
        if number == 2:
            return final('{"affiliation_candidates": [],}')
        raise asyncio.CancelledError()

    transport(monkeypatch, handler)

    async def checkpoint(value):
        checkpoints.append(copy.deepcopy(value))

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(relationships.analyze_relationships_with_ai(accounts(), posts(100), [], use_ai=True, checkpoint=checkpoint))
    partial = checkpoints[-1]
    pending = partial["ai"]["pending_review"]
    accepted = copy.deepcopy(partial["ai"]["review_batches"][0])
    if change == "raw":
        pending["raw_response"] += "changed"
    elif change == "raw_hash":
        pending["raw_response_sha256"] = "f" * 64
    elif change == "raw_bytes":
        pending["raw_response_bytes"] += 1
    elif change == "format":
        pending["raw_response_format"] = "parsed_json"
    elif change == "batch":
        pending["batch_id"] = "f" * 64
    elif change == "context":
        pending["context_fingerprint"] = "f" * 64
    elif change == "source_key":
        pending["evidence_source_keys"][next(iter(pending["evidence_hashes"]))] = "https://unrelated.example/"
    else:
        pending["evidence_hashes"][next(iter(pending["evidence_hashes"]))] = "f" * 64
    retries = []

    async def fresh(system, data, **options):
        retries.append(copy.deepcopy(data))
        assert "previous_response" not in data
        return {"affiliation_candidates": []}

    monkeypatch.setattr(relationships, "configured_ai_json", fresh)
    result = asyncio.run(relationships.analyze_relationships_with_ai(accounts(), posts(100), [], use_ai=True, previous_analysis=partial))
    assert result["ai"]["complete"] and result["ai"]["resumed_batches"] == 1 and len(retries) == 1
    assert result["ai"]["review_batches"][0]["evidence_hashes"] == accepted["evidence_hashes"]
    assert result["ai"]["pending_review_invalidation"]["reason"] in {
        "raw_response_changed", "batch_identity_changed", "context_changed", "source_mapping_changed", "evidence_changed"}


@pytest.mark.parametrize("omit_catalogue", [False, True])
def test_syntax_string_obeys_related_cleanup_restore_and_purge(tmp_path, monkeypatch, omit_catalogue):
    store = Store(tmp_path / "syntax-cleanup.sqlite3")
    raw = '{"affiliation_candidates": [], "extra": "syntax_deleted_source_fragment",}'
    try:
        job = store.create_job("research", {})
        run = store.create_research_run(job["id"], {})
        store.checkpoint_research(run["id"], accounts=accounts(), items=posts())
        material = store.get_research_run(run["id"])["items"][0]
        catalogue = relationships.evidence_catalog(accounts(), [material], [])
        hashes = {row["id"]: relationships._review_hash(row) for row in catalogue}
        pending = {"batch_id": relationships._review_hash(hashes), "context_fingerprint": "0" * 64,
            "evidence_hashes": hashes, "evidence_source_keys": {row["id"]: source_url_key(row["url"]) for row in catalogue},
            "repair_attempts": 0, "requested_max_tokens": 32768, "status": "validation_failed",
            "validation_issues": [{"code": "json_syntax"}], **relationships._bounded_response(raw, response_format="json_text")}
        analysis = {"evidence": [] if omit_catalogue else catalogue, "findings": [], "affiliations": [],
            "ai": {"requested": True, "complete": False, "review_work_version": 1, "pending_review": pending,
                "review_batches": [], "input_evidence_count": 0, "available_evidence_count": len(catalogue),
                "pending_evidence_count": len(catalogue)}}
        store.checkpoint_research(run["id"], analysis=analysis, phase="finished", status="partial")
        store.update_job(job["id"], status="partial")

        def clean(item_id, action):
            preview = store.cleanup_preview([item_id], action)
            store.cleanup_apply([item_id], action, preview["preview_digest"], "Syntax string cleanup regression", confirm_purge=action == "purge")

        unrelated = store.import_items([{**material, "url": "https://x.com/alpha/status/999", "content": "无关公开正文"}])["resolved_ids"][0]
        for action in ("trash", "purge"):
            clean(unrelated, action)
            assert store.get_research_run(run["id"])["analysis"]["ai"]["pending_review"] == pending
        clean(material["id"], "trash")
        current = store.get_research_run(run["id"])["analysis"]
        assert current["ai"]["pending_review"] is None and "syntax_deleted_source_fragment" not in json.dumps(current)
        clean(material["id"], "restore")
        assert store.get_research_run(run["id"])["analysis"]["ai"]["pending_review"] == pending
        clean(material["id"], "trash")
        clean(material["id"], "purge")
        with sqlite3.connect(store.path) as database:
            saved = json.loads(database.execute("SELECT analysis FROM wb_research_runs WHERE id=?", (run["id"],)).fetchone()[0])
            assert saved["ai"]["pending_review"] is None and "syntax_deleted_source_fragment" not in json.dumps(saved)
            assert database.execute("PRAGMA foreign_key_check").fetchall() == []
        saved_run = store.get_research_run(run["id"])
        requests = []

        async def fresh(system, data, **options):
            requests.append(data)
            assert "previous_response" not in data and "syntax_deleted_source_fragment" not in json.dumps(data)
            return {"affiliation_candidates": []}

        monkeypatch.setattr(relationships, "configured_ai_json", fresh)
        result = asyncio.run(relationships.analyze_relationships_with_ai(accounts(), saved_run["items"], [],
            use_ai=True, previous_analysis=saved_run["analysis"]))
        assert result["ai"]["complete"] and "syntax_deleted_source_fragment" not in json.dumps(result)
    finally:
        store.close()
