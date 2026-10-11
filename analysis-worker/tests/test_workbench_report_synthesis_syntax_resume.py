"""Offline stop/syntax recovery through the real strict HTTP JSON boundary.

Only synthetic evidence, credentials and an in-process MockTransport are used.
These cases certify checkpoint/validation behavior, not live model accuracy.
"""
import asyncio
import copy
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from app.workbench import query_plan, report_synthesis as synthesis
from app.workbench.ai_capacity import ModelCapacity, assess_messages
from app.workbench.pipeline import PipelineError
from test_workbench_report_compression_review import SMALL, review
from test_workbench_report_note_patch import base_note
from test_workbench_report_synthesis_capacity import packet, compressed_note, SYSTEM, OUTPUT


RAW = '{"findings": [}\n日本😀'
SECRET = "synthetic-opaque-credential"
_NATIVE_CLIENT = httpx.AsyncClient


def final(value, *, finish="stop", status=200):
    content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, allow_nan=False)
    return httpx.Response(status, json={"choices": [{"finish_reason": finish, "message": {
        "content": content, "reasoning_content": "hidden-provider-envelope"}}],
        "headers": {"Authorization": "hidden-provider-authorization"}})


def transport(monkeypatch, handler, *, capacity=SMALL):
    calls = []

    def mocked(request):
        assert request.url.host == "synthetic-provider.invalid"
        payload = json.loads(request.content)
        supplied = json.loads(payload["messages"][1]["content"])
        assert assess_messages(payload["messages"], max_output_tokens=payload["max_tokens"], capacity=capacity).fits
        calls.append({"supplied": copy.deepcopy(supplied), "payload": copy.deepcopy(payload)})
        return handler(supplied, len(calls), payload)

    monkeypatch.setattr(query_plan.httpx, "AsyncClient", lambda **options: _NATIVE_CLIENT(
        transport=httpx.MockTransport(mocked), **options))
    monkeypatch.setattr(query_plan, "configured_model_capacity", lambda **options: capacity)
    monkeypatch.setattr(synthesis, "configured_model_capacity", lambda: capacity)
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://synthetic-provider.invalid")
    monkeypatch.setenv("DEEPSEEK_MODEL", capacity.model)
    return calls


def valid(supplied):
    if supplied.get("compression_review"):
        return review(supplied)
    if supplied.get("compression_patch_revision"):
        candidate = supplied["compression_revision"]["previous_notes"]
        return {"replacements": [{"finding_index": index, "findings": [copy.deepcopy(candidate["findings"][index])]}
            for index in supplied["compression_patch_revision"]["finding_indexes"]],
            "limitations": copy.deepcopy(candidate["limitations"])}
    return compressed_note(supplied)


def run(*, data=None, previous=None, saved=None, checkpoint=None, capacity=SMALL, **options):
    if checkpoint is None and saved is not None:
        checkpoint = lambda value: saved.append(copy.deepcopy(value))
    return asyncio.run(synthesis.fit_report_input(packet(40, reading_characters=5000) if data is None else data,
        SYSTEM, max_output_tokens=OUTPUT, capacity=capacity, previous_state=previous, checkpoint=checkpoint, **options))


def first_row(state):
    return next(iter(state["batches"].values()))


@pytest.mark.parametrize("raw", [RAW, '```json\n{"findings":[],"limitations":[]}\n```',
    '{"findings":[],"findings":[],"limitations":[]}', '{"findings":NaN,"limitations":[]}',
    '{"findings":Infinity,"limitations":[]}'])
def test_strict_bad_stop_is_never_accepted_and_repaired_note_still_gets_source_review(monkeypatch, raw):
    saved = []

    def handler(supplied, number, payload):
        if number == 1:
            return final(raw)
        if number == 2:
            feedback = supplied["json_format_repair"]
            envelope = feedback["final_response"]
            assert envelope["raw_response"] == raw
            assert envelope["raw_response_sha256"] == hashlib.sha256(raw.encode("utf-8")).hexdigest()
            assert envelope["raw_response_bytes"] == len(raw.encode("utf-8"))
            assert not feedback["previous_response_accepted"] and feedback["repair_number"] == 1
            assert "compression_revision" not in supplied
        return final(valid(supplied))

    calls = transport(monkeypatch, handler)
    fitted, receipt, state = run(saved=saved)
    row = first_row(state)
    assert state["complete"] and fitted["compression"]["semantic_consistency_reviewed"]
    assert row["revision_count"] == 0 and row["phase"] == "reviewed"
    assert row["compression_json_format_recovery"]["status"] == "repaired"
    assert row["compression_attempts"][0]["complete"] is False
    assert row["compression_attempts"][0]["error_code"] == "ai_json_syntax"
    assert len(row["compression_attempts"]) == 2 and len(row["review_attempts"]) == 1
    assert calls[2]["supplied"].get("compression_review") is True
    assert receipt["omitted_source_count"] == 0 and len(state["evidence_unit_ledger"]) == 40
    assert all(b["phase"] == "reviewed" for b in state["batches"].values())
    assert all(marker not in json.dumps(saved, ensure_ascii=False) for marker in
        (SECRET, "hidden-provider-envelope", "hidden-provider-authorization"))


def test_syntax_repaired_json_with_bad_source_quote_requires_semantic_patch_and_full_review(monkeypatch):
    def handler(supplied, number, payload):
        if number == 1:
            return final(RAW)
        if number == 2:
            note = valid(supplied)
            note["findings"][0]["support_refs"][0]["quote"] = "NOT_IN_ORIGINAL_FRAGMENT"
            return final(note)
        if supplied.get("compression_patch_revision"):
            assert supplied["compression_revision"]["revision_number"] == 1
            assert supplied["compression_revision"]["review_issues"]
            assert "json_format_repair" not in supplied
            note = compressed_note(supplied)
            return final({"replacements": [{"finding_index": 0, "findings": note["findings"]}],
                "limitations": note["limitations"]})
        return final(valid(supplied))

    calls = transport(monkeypatch, handler)
    _, _, state = run()
    row = first_row(state)
    assert row["revision_count"] == 1 and row["phase"] == "reviewed"
    assert row["draft_history"][0]["note"]["findings"][0]["support_refs"][0]["quote"] == "NOT_IN_ORIGINAL_FRAGMENT"
    assert any(call["supplied"].get("compression_review") for call in calls)
    assert row["compression_json_format_recovery_history"][0]["completed_format_repair_count"] == 1


def test_review_syntax_recovery_keeps_paid_note_and_review_coverage(monkeypatch):
    broken = [False]

    def handler(supplied, number, payload):
        if supplied.get("compression_review") and not broken[0]:
            broken[0] = True
            return final(RAW)
        if supplied.get("json_format_repair"):
            assert supplied["compression_review"] and supplied["candidate_notes"]["findings"]
        return final(valid(supplied))

    calls = transport(monkeypatch, handler)
    _, _, state = run()
    row = first_row(state)
    assert len(row["compression_attempts"]) == 1 and len(row["review_attempts"]) == 2
    assert row["review_json_format_recovery"]["completed_format_repair_count"] == 1
    assert row.get("review_format_revision_count", 0) == 0
    assert row["review"]["reviewed_unit_ids"] == row["represented_unit_ids"]
    assert len([call for call in calls if call["supplied"].get("json_format_repair")]) == 1


@pytest.mark.parametrize("legacy", [False, True])
def test_twelfth_semantic_patch_resume_keeps_reviewed_batch_and_unlisted_finding(monkeypatch, legacy):
    saved = []
    second_id = [None]

    def initial(supplied, number, payload):
        identity = supplied["evidence_units"][0]["unit_id"]
        if supplied.get("compression_review"):
            if len(saved[-1]["batches"]) == 1:
                return final(review(supplied))
            second_id[0] = identity
            return final(review(supplied, accepted=False))
        return final(base_note(supplied))

    transport(monkeypatch, initial)

    def checkpoint(value):
        saved.append(copy.deepcopy(value))
        if any(row.get("revision_kind") == "semantic" and row["phase"] == "revision_required"
                for row in value["batches"].values()):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        run(checkpoint=checkpoint)
    previous = saved[-1]
    keys = list(previous["batches"])
    assert len(keys) == 2 and previous["batches"][keys[0]]["phase"] == "reviewed"
    paid_first = copy.deepcopy(previous["batches"][keys[0]])
    row = previous["batches"][keys[1]]
    row.update(revision_count=11, phase="revision_pending")
    old_candidate, old_issues = copy.deepcopy(row["candidate_draft"]), copy.deepcopy(row["review"]["issues"])
    if legacy:
        row["compression_attempts"].append({"requested_max_tokens": 32768, "complete": False,
            "error": synthesis._LEGACY_JSON_SYNTAX_ERROR, "ai_diagnostic": {}})
    patch_calls = []

    def resume(supplied, number, payload):
        assert supplied["evidence_units"][0]["unit_id"] != paid_first["represented_unit_ids"][0]
        if supplied.get("compression_patch_revision"):
            assert supplied["evidence_units"][0]["unit_id"] == second_id[0]
            patch_calls.append(copy.deepcopy(supplied))
            assert supplied["compression_revision"]["revision_number"] == 12
            assert supplied["compression_revision"]["previous_notes"] == old_candidate
            assert supplied["compression_revision"]["review_issues"] == old_issues
            assert supplied["compression_patch_revision"]["finding_indexes"] == [0]
            if not legacy and len(patch_calls) == 1:
                return final(RAW)
            feedback = supplied["json_format_repair"]
            if legacy:
                assert feedback["previous_response_available"] is False
                assert feedback["audit"] == "legacy_checkpoint_final_response_not_retained"
                assert "final_response" not in feedback
            else:
                assert feedback["final_response"]["raw_response"] == RAW
        return final(valid(supplied))

    calls = transport(monkeypatch, resume)
    _, _, state = run(previous=previous)
    current = state["batches"][keys[1]]
    assert state["complete"] and state["batches"][keys[0]] == paid_first
    assert current["revision_count"] == 12 and current["phase"] == "reviewed"
    assert current["note"]["findings"][1] == old_candidate["findings"][1]
    local_calls = [call for call in calls if call["supplied"]["evidence_units"][0]["unit_id"] == second_id[0]]
    assert len(patch_calls) == (1 if legacy else 2) and len(local_calls) == (2 if legacy else 3)
    assert current["draft_history"][-1]["review"]["issues"] == old_issues
    assert current["compression_json_format_recovery"]["completed_format_repair_count"] == 1


@pytest.mark.parametrize("cancel_at", ["failed_response_checkpoint", "repair_call", "repaired_candidate_checkpoint"])
def test_cancel_resume_preserves_same_format_slot_and_paid_candidate(monkeypatch, cancel_at):
    saved, cancelled = [], [False]

    def handler(supplied, number, payload):
        if number == 1:
            return final(RAW)
        if cancel_at == "repair_call" and not cancelled[0]:
            cancelled[0] = True
            raise asyncio.CancelledError()
        return final(valid(supplied))

    initial_calls = transport(monkeypatch, handler)

    def checkpoint(value):
        saved.append(copy.deepcopy(value))
        row = first_row(value)
        recovery = row.get("compression_json_format_recovery", {})
        stop = (cancel_at == "failed_response_checkpoint" and recovery.get("status") == "retry_pending"
            or cancel_at == "repaired_candidate_checkpoint" and row["phase"] == "draft_validation_pending"
                and recovery.get("status") == "repaired")
        if stop and not cancelled[0]:
            cancelled[0] = True
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        run(checkpoint=checkpoint)
    partial = saved[-1]
    original = first_row(partial)["compression_attempts"][0]["final_response"]
    assert original["raw_response"] == RAW
    calls = transport(monkeypatch, lambda supplied, number, payload: final(valid(supplied)))
    _, _, state = run(previous=partial)
    assert state["complete"]
    if cancel_at == "repaired_candidate_checkpoint":
        assert calls[0]["supplied"].get("compression_review")
        assert len(first_row(state)["compression_attempts"]) == len(first_row(partial)["compression_attempts"])
    else:
        assert calls[0]["supplied"]["json_format_repair"]["repair_number"] == 1
    assert first_row(state)["compression_json_format_recovery"]["completed_format_repair_count"] == 1
    assert len(initial_calls) == (1 if cancel_at == "failed_response_checkpoint" else 2)


def test_format_exhaustion_stays_bounded_across_resume_and_keeps_all_failed_responses(monkeypatch):
    saved = []
    calls = transport(monkeypatch, lambda supplied, number, payload: final(RAW + str(number)))
    with pytest.raises(query_plan.AIJSONSyntaxError):
        run(saved=saved)
    row = first_row(saved[-1])
    assert len(calls) == 3 and row["compression_json_format_recovery"]["status"] == "exhausted"
    assert row["revision_count"] == 0 and row["note"] is None
    assert [attempt.get("json_format_repair_number", 0) for attempt in row["compression_attempts"]] == [0, 1, 2]
    assert all(attempt["final_response"]["raw_response"] == RAW + str(index)
        for index, attempt in enumerate(row["compression_attempts"], 1))
    resumed_calls = transport(monkeypatch, lambda supplied, number, payload: final(valid(supplied)))
    with pytest.raises(PipelineError, match="达到上限"):
        run(previous=saved[-1])
    assert resumed_calls == []


@pytest.mark.parametrize("raw,reason", [('{"api_key":"fixture-value",}', "sensitive_response_fields"),
    ('{"value":"' + SECRET + '",}', "sensitive_response_fields"),
    ('{"reasoning_content":"fixture-reasoning"}', "sensitive_response_fields"),
    ("x" * (query_plan.AI_FINAL_RESPONSE_BYTE_LIMIT + 1), "raw_response_byte_limit")],
    ids=["sensitive_field", "opaque_key", "hidden_reasoning", "over_byte_limit"])
def test_sensitive_and_over_byte_final_responses_are_hash_only_and_never_regenerated(monkeypatch, raw, reason):
    saved = []
    calls = transport(monkeypatch, lambda supplied, number, payload: final(raw))
    with pytest.raises(query_plan.AIJSONFinalResponseError):
        run(saved=saved)
    row = first_row(saved[-1])
    envelope = row["compression_attempts"][0]["final_response"]
    assert len(calls) == 1 and envelope["raw_response_omitted"] is True
    assert envelope["raw_response_omission_reason"] == reason and "raw_response" not in envelope
    assert envelope["raw_response_sha256"] == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert row["compression_json_format_recovery"]["status"] == "blocked"
    assert raw not in json.dumps(saved, ensure_ascii=False)
    resumed_calls = transport(monkeypatch, lambda supplied, number, payload: final(valid(supplied)))
    with pytest.raises(PipelineError):
        run(previous=saved[-1])
    assert resumed_calls == []


@pytest.mark.parametrize("defect", ["changed_raw", "wrong_hash", "hash_only"])
def test_pending_response_integrity_is_rechecked_before_resending(monkeypatch, defect):
    saved = []
    transport(monkeypatch, lambda supplied, number, payload: final(RAW))

    def checkpoint(value):
        saved.append(copy.deepcopy(value))
        if first_row(value).get("compression_json_format_recovery"):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        run(checkpoint=checkpoint)
    partial = saved[-1]
    envelope = first_row(partial)["compression_json_format_recovery"]["final_response"]
    if defect == "changed_raw":
        envelope["raw_response"] += "changed"
    elif defect == "wrong_hash":
        envelope["raw_response_sha256"] = "0" * 64
    else:
        envelope.pop("raw_response")
        envelope["raw_response_omitted"] = True
    calls = transport(monkeypatch, lambda supplied, number, payload: final(valid(supplied)))
    with pytest.raises(PipelineError, match="无法安全完整保留"):
        run(previous=partial, saved=saved)
    assert not calls and first_row(saved[-1])["compression_json_format_recovery"]["status"] == "blocked"
    assert "raw_response" not in first_row(saved[-1])["compression_attempts"][0]["final_response"]


def test_length_grows_wire_reserve_without_syntax_repair_or_retaining_truncated_content(monkeypatch):
    capacity = ModelCapacity("offline-length", 131072, 65536, "explicit_test_metadata")

    def handler(supplied, number, payload):
        assert "json_format_repair" not in supplied
        return final(RAW + SECRET, finish="length") if number == 1 else final(valid(supplied))

    calls = transport(monkeypatch, handler, capacity=capacity)
    _, _, state = run(capacity=capacity)
    row = first_row(state)
    assert state["complete"] and calls[1]["payload"]["max_tokens"] > calls[0]["payload"]["max_tokens"]
    assert row["compression_attempts"][0]["ai_diagnostic"]["finish_reason"] == "length"
    assert "final_response" not in row["compression_attempts"][0]
    assert "compression_json_format_recovery" not in row and SECRET not in json.dumps(state)


@pytest.mark.parametrize("failure", ["length_at_provider_max", "transport", "content_filter"])
def test_non_stop_or_transport_failure_does_not_start_format_repair(monkeypatch, failure):
    saved = []

    def handler(supplied, number, payload):
        if failure == "transport":
            raise httpx.ConnectError("synthetic transport failure")
        return final(RAW, finish="length" if failure == "length_at_provider_max" else "content_filter")

    calls = transport(monkeypatch, handler)
    with pytest.raises(PipelineError):
        run(saved=saved)
    assert len(calls) == 1 and "compression_json_format_recovery" not in first_row(saved[-1])
    assert "final_response" not in first_row(saved[-1])["compression_attempts"][0]


def test_transport_cancel_during_pending_repair_keeps_same_budget_on_resume(monkeypatch):
    saved = []
    calls = transport(monkeypatch, lambda supplied, number, payload:
        final(RAW) if number == 1 else httpx.Response(503))
    with pytest.raises(PipelineError, match="HTTP 503"):
        run(saved=saved)
    row = first_row(saved[-1])
    assert len(calls) == 2 and row["compression_json_format_recovery"]["repair_number"] == 1
    assert row["compression_json_format_recovery"]["completed_format_repair_count"] == 0
    resumed_calls = transport(monkeypatch, lambda supplied, number, payload: final(valid(supplied)))
    _, _, state = run(previous=saved[-1])
    assert state["complete"] and resumed_calls[0]["supplied"]["json_format_repair"]["repair_number"] == 1


def test_full_repair_wire_over_capacity_keeps_original_evidence_and_does_not_truncate(monkeypatch):
    saved = []
    raw = "x" * query_plan.AI_FINAL_RESPONSE_BYTE_LIMIT
    calls = transport(monkeypatch, lambda supplied, number, payload: final(raw))
    with pytest.raises(PipelineError, match="超出实际模型上下文"):
        run(saved=saved)
    row = first_row(saved[-1])
    assert len(calls) == 1 and row["compression_attempts"][-1]["error"] == "actual_wire_capacity"
    assert row["compression_attempts"][0]["final_response"]["raw_response"] == raw
    assert row["compression_json_format_recovery"]["completed_format_repair_count"] == 0
    assert len(saved[-1]["evidence_unit_ledger"]) == 40 and row["note"] is None


def test_deadline_after_request_checkpoint_stops_before_provider(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(synthesis, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    saved = []
    calls = transport(monkeypatch, lambda supplied, number, payload: final(valid(supplied)))

    def checkpoint(value):
        saved.append(copy.deepcopy(value))
        if any(row["compression_attempts"] for row in value["batches"].values()):
            clock[0] = 11.0

    with pytest.raises(PipelineError, match="最长运行时间"):
        run(checkpoint=checkpoint, time_budget_seconds=10)
    assert calls == [] and first_row(saved[-1])["compression_attempts"][-1]["complete"] is False
