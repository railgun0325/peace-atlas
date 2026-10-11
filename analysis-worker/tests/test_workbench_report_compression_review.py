"""Offline fragment support, consistency review and checkpoint regressions.

All models below are local fixtures. Passing these cases certifies the recovery
and evidence contract; it does not establish external facts or model accuracy.
"""
import asyncio
import copy

import pytest

from app.workbench import report_compression_review as checking
from app.workbench import report_synthesis as synthesis
from app.workbench.ai_capacity import ModelCapacity, assess_messages, compact_json
from app.workbench.pipeline import PipelineError
from test_workbench_report_synthesis_capacity import packet, compressed_note, SYSTEM, OUTPUT, messages, JSON_SYSTEM_SUFFIX


SMALL = ModelCapacity("offline-review", 131072, 32768, "explicit_test_metadata")
LARGE = ModelCapacity("offline-compressor", 1048576, 393216, "explicit_test_metadata")


def review(supplied, *, accepted=True):
    units = supplied["evidence_units"]
    return {"accepted": accepted, "issues": [] if accepted else [{"code": "negation",
        "explanation": "原片段否定活动已经举行；草稿反向声称已经举行，必须保留取消限定。",
        "citation_ids": [units[0]["citation_id"]], "unit_ids": [units[0]["unit_id"]], "finding_indexes": [0]}],
        "reviewed_finding_indexes": list(range(len(supplied["candidate_notes"]["findings"]))),
        "reviewed_unit_ids": [unit["unit_id"] for unit in units]}


def install(monkeypatch, model, provider=SMALL):
    monkeypatch.setattr(synthesis, "configured_model_capacity", lambda: provider)
    async def checked(system, supplied, **options):
        assert options["max_input_chars"] is None
        assert assess_messages(messages(supplied, system + JSON_SYSTEM_SUFFIX),
            max_output_tokens=options["max_tokens"], capacity=provider).fits
        result = await model(system, supplied, **options)
        # These older providers generate one synthetic finding. Emit the new
        # wire contract explicitly; multi-finding patch behavior is exercised
        # separately without this fixture adapter.
        if supplied.get("compression_patch_revision") and isinstance(result, dict) and set(result) == {"findings", "limitations"}:
            assert supplied["compression_patch_revision"]["finding_indexes"] == [0]
            result = {"replacements": [{"finding_index": 0, "findings": result["findings"]}], "limitations": result["limitations"]}
        return result
    monkeypatch.setattr(synthesis, "configured_ai_json", checked)


def run(data, *, previous=None, saved=None, capacity=SMALL):
    return asyncio.run(synthesis.fit_report_input(data, SYSTEM, max_output_tokens=OUTPUT, capacity=capacity,
        previous_state=previous, checkpoint=None if saved is None else lambda value: saved.append(copy.deepcopy(value))))


def small_units():
    return {"S001~P1": {"citation_id": "S001", "text": "计划举行。"},
            "S001~P2": {"citation_id": "S001", "text": "后来撤回，未举行。"}}


def supported_note():
    return {"findings": [{"statement": "来源先计划，后来撤回，未举行。", "kind": "source_claim", "citation_ids": ["S001"],
        "support_refs": [{"unit_id": "S001~P1", "citation_id": "S001", "quote": "计划"},
                         {"unit_id": "S001~P2", "citation_id": "S001", "quote": "撤回"}]}], "limitations": []}


@pytest.mark.parametrize("defect", ["unknown_unit", "borrowed_quote", "wrong_citation", "missing_late_fragment",
    "duplicate_ref", "duplicate_citation", "empty_quote", "oversize_whitespace"])
def test_note_requires_exact_original_unit_support_not_only_id_union(defect):
    units, note = small_units(), supported_note()
    refs = note["findings"][0]["support_refs"]
    if defect == "unknown_unit":
        refs[0]["unit_id"] = "S001~P9"
    elif defect == "borrowed_quote":
        refs[0]["quote"] = "撤回"
    elif defect == "wrong_citation":
        refs[0]["citation_id"] = "S002"
    elif defect == "missing_late_fragment":
        refs.pop()
    elif defect == "duplicate_ref":
        refs.append(copy.deepcopy(refs[0]))
    elif defect == "duplicate_citation":
        note["findings"][0]["citation_ids"].append("S001")
    elif defect == "empty_quote":
        refs[0]["quote"] = " "
    else:
        refs[0]["quote"] = " " * 500 + "计划"
    unchanged = copy.deepcopy(note)
    with pytest.raises(PipelineError):
        checking.validate_note(note, {"S001"}, units)
    assert note == unchanged


@pytest.mark.parametrize("code", [[], {}, 0, None])
def test_review_nested_bad_code_is_recoverable_pipeline_error(code):
    units, note = small_units(), supported_note()
    supplied = {"evidence_units": [{"unit_id": key, **value} for key, value in units.items()], "candidate_notes": note}
    result = review(supplied, accepted=False)
    result["issues"][0]["code"] = code
    with pytest.raises(PipelineError, match="复核问题字段无效"):
        checking.validate_review(result, note, units)


def test_all_coverage_and_valid_quotes_do_not_override_semantic_rejection(monkeypatch):
    data = packet(40, reading_characters=5000)
    calls, saved = [], []
    async def model(system, supplied, **options):
        calls.append(copy.deepcopy(supplied))
        if supplied.get("compression_review"):
            wrong = supplied["candidate_notes"]["findings"][0]["statement"] == "活动已经举行。"
            return review(supplied, accepted=not wrong)
        if supplied.get("compression_revision"):
            revision = supplied["compression_revision"]
            assert revision["previous_notes"]["findings"][0]["statement"] == "活动已经举行。"
            assert revision["review_issues"][0]["code"] == "negation"
            return compressed_note(supplied, "保存记录只支持计划；不能判断活动已经举行。")
        return compressed_note(supplied, "活动已经举行。")
    install(monkeypatch, model)
    _, receipt, state = run(data, saved=saved)
    assert state["complete"] and receipt["semantic_consistency_reviewed"]
    assert any(value["phase"] == "revision_required" for snapshot in saved for value in snapshot["batches"].values())
    for batch in state["batches"].values():
        assert batch["revision_count"] == 1 and batch["phase"] == "reviewed"
        assert len(batch["draft_history"]) == 1 and batch["draft_history"][0]["review"]["accepted"] is False
        assert batch["review"]["accepted"] is True
    assert receipt["semantic_consistency_review_is_independent_fact_verification"] is False


@pytest.mark.parametrize("failure", ["transport", "format", "cancel"])
def test_review_failure_reuses_draft_without_recompressing(monkeypatch, failure):
    data, saved, calls = packet(40, reading_characters=5000), [], []
    async def failing(system, supplied, **options):
        calls.append(copy.deepcopy(supplied))
        if not supplied.get("compression_review"):
            return compressed_note(supplied)
        if failure == "cancel":
            raise asyncio.CancelledError()
        if failure == "format":
            if len(calls) > 2:
                raise PipelineError("fixture format-repair service failure")
            return {"accepted": True, "issues": []}
        raise PipelineError("fixture HTTP 503")
    install(monkeypatch, failing)
    with pytest.raises(asyncio.CancelledError if failure == "cancel" else PipelineError):
        run(data, saved=saved)
    previous = saved[-1]
    first = next(iter(previous["batches"].values()))
    assert first["phase"] in {"review_pending", "review_format_pending"} and first["note"] is not None
    retained = copy.deepcopy(first["note"])
    first_records = calls[0]["records"]
    retry_calls = []
    async def resumed(system, supplied, **options):
        retry_calls.append(copy.deepcopy(supplied))
        if supplied.get("compression_review"):
            return review(supplied)
        assert supplied["records"] != first_records
        return compressed_note(supplied)
    install(monkeypatch, resumed)
    _, _, state = run(data, previous=previous)
    assert retry_calls[0]["compression_review"] is True
    assert next(iter(state["batches"].values()))["note"] == retained
    assert state["complete"]


def test_cancellation_during_revision_resumes_revision_and_keeps_accepted_batches(monkeypatch):
    data, saved, calls = packet(40, reading_characters=5000), [], []
    async def failing(system, supplied, **options):
        calls.append(copy.deepcopy(supplied))
        if supplied.get("compression_review"):
            return review(supplied, accepted=supplied["records"][0]["represented_citation_ids"][0] == "S001")
        if supplied.get("compression_revision"):
            raise asyncio.CancelledError()
        return compressed_note(supplied)
    install(monkeypatch, failing)
    with pytest.raises(asyncio.CancelledError):
        run(data, saved=saved)
    previous = saved[-1]
    accepted = {key: copy.deepcopy(value) for key, value in previous["batches"].items() if value["phase"] == "reviewed"}
    assert accepted and any(value["phase"] == "revision_pending" for value in previous["batches"].values())
    seen = []
    async def resumed(system, supplied, **options):
        seen.append(copy.deepcopy(supplied))
        assert supplied["records"][0]["represented_citation_ids"][0] != "S001"
        return review(supplied) if supplied.get("compression_review") else compressed_note(supplied)
    install(monkeypatch, resumed)
    _, _, state = run(data, previous=previous)
    assert seen[0]["compression_revision"]["revision_number"] == 1
    assert all(state["batches"][key] == value for key, value in accepted.items())
    assert state["complete"]


def test_repeated_semantic_rejection_has_bounded_revisions_and_retains_audit(monkeypatch):
    data, saved, drafts = packet(40, reading_characters=5000), [], []
    async def model(system, supplied, **options):
        if supplied.get("compression_review"):
            return review(supplied, accepted=False)
        drafts.append(copy.deepcopy(supplied))
        return compressed_note(supplied)
    install(monkeypatch, model)
    with pytest.raises(PipelineError, match="修正次数达到上限"):
        run(data, saved=saved)
    assert len(drafts) == 13
    batch = next(iter(saved[-1]["batches"].values()))
    assert batch["phase"] == "revision_required" and batch["revision_count"] == 12 and len(batch["draft_history"]) == 12
    assert not saved[-1]["complete"]
    async def forbidden(*args, **kwargs):
        pytest.fail("A rejected batch at its revision ceiling must not loop paid calls")
    install(monkeypatch, forbidden)
    with pytest.raises(PipelineError, match="修正次数达到上限"):
        run(data, previous=saved[-1])


def test_extension_eight_to_twelve_retains_counts_fingerprint_and_accepted_batches(monkeypatch):
    data, saved = packet(40, reading_characters=5000), []
    async def rejecting(system, supplied, **options):
        if supplied.get("compression_review"):
            return review(supplied, accepted=supplied["records"][0]["represented_citation_ids"][0] == "S001")
        return compressed_note(supplied)
    monkeypatch.setattr(synthesis, "_NOTE_REVISION_LIMIT", 8)
    install(monkeypatch, rejecting)
    with pytest.raises(PipelineError, match="修正次数达到上限"):
        run(data, saved=saved)
    previous = saved[-1]
    accepted = {key: copy.deepcopy(value) for key, value in previous["batches"].items() if value["phase"] == "reviewed"}
    rejected = next(value for value in previous["batches"].values() if value["phase"] == "revision_required")
    assert accepted and rejected["revision_count"] == 8
    monkeypatch.setattr(synthesis, "_NOTE_REVISION_LIMIT", 12)
    calls = []
    async def resumed(system, supplied, **options):
        calls.append(copy.deepcopy(supplied))
        assert supplied["records"][0]["represented_citation_ids"][0] != "S001"
        return review(supplied) if supplied.get("compression_review") else compressed_note(supplied)
    install(monkeypatch, resumed)
    _, receipt, state = run(data, previous=previous)
    assert calls[0]["compression_revision"]["revision_number"] == 9
    assert calls[0]["compression_revision"]["previous_notes"] == rejected["note"]
    assert state["input_fingerprint"] == previous["input_fingerprint"]
    assert state["revision_limits"]["note"] == receipt["revision_limits"]["note"] == 12
    assert all(state["batches"][key] == value for key, value in accepted.items())
    assert state["complete"]


def test_extended_revision_budget_still_stops_at_deadline_and_resumes_without_reset(monkeypatch):
    data, saved = packet(40, reading_characters=5000), []
    clock = [0.0]
    monkeypatch.setattr(synthesis, "time", type("Clock", (), {"monotonic": staticmethod(lambda: clock[0])}))
    async def rejecting(system, supplied, **options):
        if supplied.get("compression_review"):
            clock[0] = 2.0
            return review(supplied, accepted=False)
        return compressed_note(supplied)
    install(monkeypatch, rejecting)
    with pytest.raises(PipelineError, match="最长运行时间"):
        asyncio.run(synthesis.fit_report_input(data, SYSTEM, max_output_tokens=OUTPUT, capacity=SMALL,
            checkpoint=lambda value: saved.append(copy.deepcopy(value)), time_budget_seconds=1))
    previous = saved[-1]
    first = next(iter(previous["batches"].values()))
    assert first["revision_count"] == 0 and first["note"] and not previous["complete"]
    clock[0] = 0
    calls = []
    async def corrected(system, supplied, **options):
        calls.append(copy.deepcopy(supplied))
        return review(supplied) if supplied.get("compression_review") else compressed_note(supplied)
    install(monkeypatch, corrected)
    state = run(data, previous=previous)[2]
    assert calls[0]["compression_revision"]["revision_number"] == 1
    assert state["complete"] and state["input_fingerprint"] == previous["input_fingerprint"]


def test_cancel_in_third_revision_retains_old_budget_history_and_retries_same_slot(monkeypatch):
    data, saved = packet(40, reading_characters=5000), []
    async def model(system, supplied, **options):
        if supplied.get("compression_review"):
            return review(supplied, accepted=False)
        if supplied.get("compression_revision", {}).get("revision_number") == 3:
            raise asyncio.CancelledError()
        return compressed_note(supplied)
    install(monkeypatch, model)
    with pytest.raises(asyncio.CancelledError):
        run(data, saved=saved)
    previous = saved[-1]
    first = next(iter(previous["batches"].values()))
    assert first["revision_count"] == 2 and len(first["draft_history"]) == 2 and first["phase"] == "revision_pending"
    calls = []
    async def corrected(system, supplied, **options):
        calls.append(copy.deepcopy(supplied))
        return review(supplied) if supplied.get("compression_review") else compressed_note(supplied)
    install(monkeypatch, corrected)
    state = run(data, previous=previous)[2]
    assert calls[0]["compression_revision"]["revision_number"] == 3
    assert calls[0]["compression_revision"]["previous_notes"] == first["note"]
    assert state["complete"]


def test_invalid_paid_candidate_is_checkpointed_and_corrected_with_exact_ref_feedback(monkeypatch):
    data, saved, seen = packet(40, reading_characters=5000), [], []
    async def model(system, supplied, **options):
        seen.append(copy.deepcopy(supplied))
        if supplied.get("compression_review"):
            return review(supplied)
        result = compressed_note(supplied)
        if supplied.get("compression_revision"):
            revision = supplied["compression_revision"]
            assert revision["previous_notes"]["findings"][0]["support_refs"][0]["quote"] == "source plans...action."
            issue = revision["review_issues"][0]
            assert issue["finding_index"] == 0 and issue["support_ref_index"] == 0
            assert issue["code"] == "exact_quote" and issue["allowed_quote_examples"]
            return result
        result["findings"][0]["support_refs"][0]["quote"] = "source plans...action."
        return result
    install(monkeypatch, model)
    _, _, state = run(data, saved=saved)
    rejected_snapshots = [batch for value in saved for batch in value["batches"].values()
        if batch.get("phase") == "revision_required" and batch.get("revision_kind") == "structure"]
    assert rejected_snapshots and all(batch["candidate_draft"] for batch in rejected_snapshots)
    assert all(batch["phase"] == "reviewed" and batch["draft_history"][0]["revision_kind"] == "structure"
        for batch in state["batches"].values())
    assert state["complete"]


def test_cancelled_structural_revision_keeps_invalid_candidate_and_resumes_with_feedback(monkeypatch):
    data, saved, calls = packet(40, reading_characters=5000), [], []
    async def failing(system, supplied, **options):
        calls.append(copy.deepcopy(supplied))
        assert not supplied.get("compression_review")
        if supplied.get("compression_revision"):
            raise asyncio.CancelledError()
        result = compressed_note(supplied)
        result["findings"][0]["support_refs"][0]["quote"] = "omitted ... intervening lines"
        return result
    install(monkeypatch, failing)
    with pytest.raises(asyncio.CancelledError):
        run(data, saved=saved)
    retained = saved[-1]
    first = next(iter(retained["batches"].values()))
    assert first["phase"] == "revision_pending" and first["revision_kind"] == "structure"
    assert first["candidate_draft"]["findings"][0]["support_refs"][0]["quote"] == "omitted ... intervening lines"
    assert first["revision_count"] == 0 and first["note"] is None
    resumed_calls = []
    async def resumed(system, supplied, **options):
        resumed_calls.append(copy.deepcopy(supplied))
        return review(supplied) if supplied.get("compression_review") else compressed_note(supplied)
    install(monkeypatch, resumed)
    _, _, state = run(data, previous=retained)
    assert resumed_calls[0]["compression_revision"]["previous_notes"] == first["candidate_draft"]
    assert resumed_calls[0]["compression_revision"]["review_issues"][0]["code"] == "exact_quote"
    assert state["complete"]


def test_cancel_at_raw_candidate_checkpoint_resumes_validation_then_review(monkeypatch):
    data, saved, drafted = packet(40, reading_characters=5000), [], []
    async def model(system, supplied, **options):
        drafted.append(copy.deepcopy(supplied))
        return compressed_note(supplied)
    install(monkeypatch, model)
    def checkpoint(value):
        saved.append(copy.deepcopy(value))
        if any(batch["phase"] == "draft_validation_pending" for batch in value["batches"].values()):
            raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(synthesis.fit_report_input(data, SYSTEM, max_output_tokens=OUTPUT, capacity=SMALL, checkpoint=checkpoint))
    retained = saved[-1]
    first_records, resumed_calls = drafted[0]["records"], []
    assert next(iter(retained["batches"].values()))["candidate_draft"] is not None
    async def resumed(system, supplied, **options):
        resumed_calls.append(copy.deepcopy(supplied))
        if supplied.get("compression_review"):
            return review(supplied)
        assert supplied["records"] != first_records
        return compressed_note(supplied)
    install(monkeypatch, resumed)
    assert run(data, previous=retained)[2]["complete"]
    assert resumed_calls[0]["compression_review"] is True


def test_invalid_review_extra_index_is_saved_repaired_and_keeps_semantic_issues(monkeypatch):
    data, saved, calls = packet(40, reading_characters=5000), [], []
    async def model(system, supplied, **options):
        calls.append(copy.deepcopy(supplied))
        if not supplied.get("compression_review"):
            return compressed_note(supplied, "已经举行。" if not supplied.get("compression_revision") else "尚不能判断已经举行。")
        if supplied["candidate_notes"]["findings"][0]["statement"] == "尚不能判断已经举行。":
            return review(supplied)
        if supplied.get("compression_review_repair"):
            repair = supplied["compression_review_repair"]
            assert repair["previous_invalid_review"]["issues"][0]["code"] == "negation"
            assert repair["format_issues"][0]["extra_values"] == [1]
            assert repair["expected_finding_indexes"] == [0]
            return review(supplied, accepted=False)
        result = review(supplied, accepted=False)
        result["reviewed_finding_indexes"].append(1)
        return result
    install(monkeypatch, model)
    _, _, state = run(data, saved=saved)
    invalid = [row for checkpoint in saved for row in checkpoint["batches"].values() if row.get("phase") == "review_format_required"]
    assert invalid and invalid[0]["raw_review"]["reviewed_finding_indexes"] == [0, 1]
    assert invalid[0]["note"] is not None and not invalid[0].get("review")
    for batch in state["batches"].values():
        history = batch["draft_history"][0]
        assert history["review"]["accepted"] is False and history["review"]["issues"][0]["code"] == "negation"
        assert history["review_history"][0]["raw_review"]["reviewed_finding_indexes"] == [0, 1]
    assert state["complete"]


def test_cancelled_review_format_repair_resumes_only_review_not_legal_note(monkeypatch):
    data, saved, calls = packet(40, reading_characters=5000), [], []
    async def failing(system, supplied, **options):
        calls.append(copy.deepcopy(supplied))
        if not supplied.get("compression_review"):
            return compressed_note(supplied)
        if supplied.get("compression_review_repair"):
            raise asyncio.CancelledError()
        result = review(supplied)
        result["reviewed_unit_ids"].pop()
        return result
    install(monkeypatch, failing)
    with pytest.raises(asyncio.CancelledError):
        run(data, saved=saved)
    previous = saved[-1]
    first_records = calls[0]["records"]
    retained = next(iter(previous["batches"].values()))
    assert retained["phase"] == "review_format_pending" and retained["raw_review"]
    assert retained.get("review_format_revision_count", 0) == 0
    seen = []
    async def resumed(system, supplied, **options):
        seen.append(copy.deepcopy(supplied))
        if supplied.get("compression_review"):
            return review(supplied)
        assert supplied["records"] != first_records
        return compressed_note(supplied)
    install(monkeypatch, resumed)
    assert run(data, previous=previous)[2]["complete"]
    assert seen[0]["compression_review_repair"]["previous_invalid_review"] == retained["raw_review"]
    assert seen[0]["candidate_notes"] == retained["note"]


def test_repeated_review_format_failure_is_bounded_without_recompressing(monkeypatch):
    data, saved, tasks = packet(40, reading_characters=5000), [], []
    async def model(system, supplied, **options):
        tasks.append("review" if supplied.get("compression_review") else "draft")
        if supplied.get("compression_review"):
            result = review(supplied)
            result["reviewed_finding_indexes"].append(1)
            return result
        return compressed_note(supplied)
    install(monkeypatch, model)
    with pytest.raises(PipelineError, match="复核格式修正次数达到上限"):
        run(data, saved=saved)
    assert tasks == ["draft", "review", "review", "review"]
    retained = next(iter(saved[-1]["batches"].values()))
    assert retained["note"] and len(retained["review_history"]) == 2 and retained["review_format_revision_count"] == 2
    assert not saved[-1]["complete"]


def test_cancel_after_complete_raw_review_checkpoint_resumes_without_repeating_it(monkeypatch):
    data, saved, calls = packet(40, reading_characters=5000), [], []
    async def model(system, supplied, **options):
        calls.append(copy.deepcopy(supplied))
        return review(supplied) if supplied.get("compression_review") else compressed_note(supplied)
    install(monkeypatch, model)
    def checkpoint(value):
        saved.append(copy.deepcopy(value))
        if any(batch["phase"] == "review_validation_pending" for batch in value["batches"].values()):
            raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(synthesis.fit_report_input(data, SYSTEM, max_output_tokens=OUTPUT, capacity=SMALL, checkpoint=checkpoint))
    assert len(calls) == 2 and saved[-1]["batches"]
    previous, first_records, resumed_calls = saved[-1], calls[0]["records"], []
    assert next(iter(previous["batches"].values()))["raw_review"]["accepted"] is True
    async def resumed(system, supplied, **options):
        resumed_calls.append(copy.deepcopy(supplied))
        assert supplied["records"] != first_records
        return review(supplied) if supplied.get("compression_review") else compressed_note(supplied)
    install(monkeypatch, resumed)
    assert run(data, previous=previous)[2]["complete"]


@pytest.mark.parametrize("defect", ["top_fields", "index_type", "missing_unit", "duplicate_unit", "issue_code"])
def test_review_diagnostics_report_real_format_errors_without_fixing_output(defect):
    units, note = small_units(), supported_note()
    supplied = {"evidence_units": [{"unit_id": key, **value} for key, value in units.items()], "candidate_notes": note}
    result = review(supplied, accepted=False)
    if defect == "top_fields":
        result["unexpected"] = "ignored?"
    elif defect == "index_type":
        result["reviewed_finding_indexes"] = [True]
    elif defect == "missing_unit":
        result["reviewed_unit_ids"].pop()
    elif defect == "duplicate_unit":
        result["reviewed_unit_ids"].append(result["reviewed_unit_ids"][0])
    else:
        result["issues"][0]["code"] = []
    unchanged = copy.deepcopy(result)
    assert checking.review_structure_issues(result, note, units)
    assert result == unchanged
    with pytest.raises(PipelineError):
        checking.validate_review(result, note, units)


@pytest.mark.parametrize("failure_at", ["compression", "review"])
def test_length_expands_output_with_actual_wire_checks_and_diagnostics(monkeypatch, failure_at):
    data, saved, requests = packet(40, reading_characters=5000), [], []
    failed = False
    async def model(system, supplied, **options):
        nonlocal failed
        task = "review" if supplied.get("compression_review") else "compression"
        requests.append((task, options["max_tokens"]))
        if task == failure_at and not failed:
            failed = True
            error = PipelineError("fixture incomplete length")
            error.ai_diagnostic = {"finish_reason": "length", "requested_max_tokens": options["max_tokens"],
                                   "completion_tokens": options["max_tokens"], "reasoning_effort": "high"}
            raise error
        return review(supplied) if task == "review" else compressed_note(supplied)
    install(monkeypatch, model, LARGE)
    _, _, state = run(data, saved=saved)
    task_requests = [value for task, value in requests if task == failure_at]
    assert task_requests[:2] == [65536, 131072]
    attempt = next(iter(state["batches"].values()))[f"{failure_at}_attempts"][0]
    assert attempt["ai_diagnostic"]["finish_reason"] == "length" and not attempt["complete"]
    assert attempt["retry_requested_max_tokens"] == 131072 and state["complete"]


def test_length_at_provider_limit_keeps_draft_and_can_resume_review(monkeypatch):
    data, saved = packet(40, reading_characters=5000), []
    async def model(system, supplied, **options):
        if supplied.get("compression_review"):
            error = PipelineError("fixture incomplete length")
            error.ai_diagnostic = {"finish_reason": "length"}
            raise error
        return compressed_note(supplied)
    install(monkeypatch, model)
    with pytest.raises(PipelineError, match="length"):
        run(data, saved=saved)
    batch = next(iter(saved[-1]["batches"].values()))
    assert batch["phase"] == "review_pending" and batch["note"] is not None
    assert batch["review_attempts"][-1]["length_retry_unavailable"]["provider_max_output_tokens"] == SMALL.max_output_tokens
    async def resumed(system, supplied, **options):
        return review(supplied) if supplied.get("compression_review") else compressed_note(supplied)
    install(monkeypatch, resumed)
    assert run(data, previous=saved[-1])[2]["complete"]


def test_length_growth_uses_remaining_wire_headroom_when_doubling_would_not_fit(monkeypatch):
    provider = ModelCapacity("offline-wire-ceiling", 300000, 200000, "explicit_test_metadata")
    data, budgets, expected = packet(40, reading_characters=5000), [], []
    async def model(system, supplied, **options):
        if supplied.get("compression_review"):
            return review(supplied)
        budgets.append(options["max_tokens"])
        if len(budgets) == 1:
            wire = assess_messages(messages(supplied, system + JSON_SYSTEM_SUFFIX), max_output_tokens=options["max_tokens"], capacity=provider)
            expected.append(provider.context_window-wire.input_tokens_upper_bound)
            assert budgets[0] < expected[0] < budgets[0]*2
            error = PipelineError("fixture incomplete length")
            error.ai_diagnostic = {"finish_reason": "length"}
            raise error
        return compressed_note(supplied)
    install(monkeypatch, model, provider)
    assert run(data)[2]["complete"]
    assert budgets[1] == expected[0]


def test_minimum_unit_support_preflight_fails_before_any_model_call(monkeypatch):
    data, saved = packet(1500, reading_characters=200), []
    async def forbidden(*args, **kwargs):
        pytest.fail("Minimum original-unit support cannot fit; no paid attempt is permitted")
    install(monkeypatch, forbidden, LARGE)
    with pytest.raises(PipelineError, match="未调用压缩模型"):
        run(data, saved=saved)
    assert saved[-1]["batches"] == {} and not saved[-1]["complete"]
    assert not saved[-1]["preflight"]["minimum_support_capacity"]["fits"]


@pytest.mark.parametrize("changed", ["compression_prompt", "review_prompt", "base_url", "provider_capacity"])
def test_prompt_and_actual_provider_change_invalidates_success_cache(monkeypatch, changed):
    data, calls = packet(40, reading_characters=5000), []
    async def model(system, supplied, **options):
        calls.append(copy.deepcopy(supplied))
        return review(supplied) if supplied.get("compression_review") else compressed_note(supplied)
    install(monkeypatch, model, LARGE)
    previous = run(data)[2]
    calls.clear()
    if changed == "compression_prompt":
        monkeypatch.setattr(synthesis, "_COMPRESS_PROMPT", synthesis._COMPRESS_PROMPT + "政策修订。")
    elif changed == "review_prompt":
        monkeypatch.setattr(synthesis, "COMPRESSION_REVIEW_PROMPT", synthesis.COMPRESSION_REVIEW_PROMPT + "政策修订。")
    elif changed == "base_url":
        monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://offline.example/v2")
    else:
        install(monkeypatch, model, ModelCapacity("different-compressor", 1048576, 393216, "explicit_test_metadata"))
    state = run(data, previous=previous)[2]
    assert calls and state["input_fingerprint"] != previous["input_fingerprint"] and state["complete"]


def test_every_higher_level_review_has_original_fragments_and_typed_dates(monkeypatch):
    data, calls = packet(40, reading_characters=5000), []
    original = copy.deepcopy(data)
    async def model(system, supplied, **options):
        calls.append(copy.deepcopy(supplied))
        for unit in supplied["evidence_units"]:
            source = next(row for row in data["sources"] if row["citation_id"] == unit["citation_id"])
            assert unit["text"] == source["content_fragments"][0]["text"]
            assert unit["source"]["published_at"] == source["published_at"]
            if source.get("relation_type"):
                assert unit["source"]["source_key"] == source["source_key"]
                assert unit["source"]["target_key"] == source["target_key"]
                assert unit["source"]["observed_at"] == source["observed_at"]
        if supplied.get("compression_review"):
            return review(supplied)
        return compressed_note(supplied, "verbose " * 18000 if supplied["compression_level"] == 1 else "保留各来源限定。")
    install(monkeypatch, model, LARGE)
    _, receipt, state = run(data)
    assert receipt["levels"] >= 2 and any(row.get("compression_review") and row["compression_level"] > 1 for row in calls)
    assert state["complete"] and data == original
