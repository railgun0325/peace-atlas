"""Versioned standard correction resources preserve exact citation contracts."""
import copy

import pytest

from app.workbench import harness_runtime as runtime
from app.workbench.pipeline import PipelineError
import test_workbench_harness_quote_receipts as receipts
import test_workbench_harness_quote_readiness as ready
import test_workbench_harness_runtime as base

synthetic_configuration = base.synthetic_configuration


@pytest.mark.parametrize("case,subtype", [("boolean", "shape"), ("malformed", "shape"), ("mixed", "shape"),
    ("unknown", "unknown"), ("version", "version"), ("citation", "citation_mismatch")])
def test_receipt_failures_have_fixed_specific_safe_diagnostics(case, subtype):
    gateway, trace = receipts.receipt_inputs()
    receipt, value = receipts.selected(gateway, trace)
    quote = value["findings"][0]["quotes"][0]
    if case == "boolean": quote["receipt_id"] = True
    elif case == "malformed": quote["receipt_id"] = base.SYNTHETIC_KEY
    elif case == "mixed": quote["quote"] = base.REASONING
    elif case == "unknown": quote["receipt_id"] = "qr1_" + "0" * 64
    elif case == "version": quote["receipt_id"] = receipt["receipt_id"].replace("qr1_", "qr2_")
    else: quote["citation_id"] = "S001"
    with pytest.raises(PipelineError) as caught:
        runtime._resolve_quote_receipts(value, gateway, trace)
    detail = runtime._validation_detail(caught.value)
    assert detail == {"version": 1, "code": "invalid_quote_receipt", "subtype": subtype, "finding_index": 1, "quote_index": 1}
    assert base.SYNTHETIC_KEY not in str(caught.value) and base.REASONING not in str(caught.value)


def test_standard_policy_expands_cumulative_limit_and_custom_keeps_two():
    expected = {"version": 1, "total_limit": 6, "consecutive_no_progress_limit": 2}
    assert runtime._final_repair_policy({}, standard=True) == expected
    assert runtime._final_repair_policy({"final_repairs_used": 2}, standard=True) == expected
    assert runtime._final_repair_policy({}, standard=False) is None
    assert runtime.MAX_FINAL_REPAIRS == 2 and runtime.STANDARD_FINAL_REPAIRS == 6


@pytest.mark.parametrize("field,value", [("version", 2), ("total_limit", 7), ("total_limit", True),
    ("consecutive_no_progress_limit", 0), ("extra", "payload")])
def test_forged_or_unknown_standard_policy_fails_closed(field, value):
    policy = {"version": 1, "total_limit": 6, "consecutive_no_progress_limit": 2}
    policy[field] = value
    with pytest.raises(PipelineError):
        runtime._final_repair_policy({"final_repair_policy": policy}, standard=True)


def test_duplicate_read_check_or_old_ready_pairs_cannot_manufacture_progress():
    gateway, trace = receipts.receipt_inputs()
    progress = runtime._initial_repair_progress({}, gateway, trace)
    before = copy.deepcopy(progress)
    next_progress, advanced = runtime._advance_repair_progress(progress, None, gateway, trace + copy.deepcopy(trace))
    assert not advanced and next_progress["consecutive_no_progress"] == 1
    assert next_progress["ready_binding_count"] == progress["ready_binding_count"] == 2 and progress == before
    final, advanced = runtime._advance_repair_progress(next_progress, None, gateway, trace)
    assert not advanced and final["consecutive_no_progress"] == 2
    assert runtime._initial_repair_progress({"final_repair_progress": final}, gateway, trace) == final


def test_actual_new_read_check_ready_pair_advances_once():
    gateway, trace = receipts.receipt_inputs()
    old = trace[:2]
    baseline = runtime._initial_repair_progress({}, gateway, old)
    next_progress, advanced = runtime._advance_repair_progress(baseline, None, gateway, trace)
    assert advanced and next_progress["ready_binding_count"] == 2 and next_progress["consecutive_no_progress"] == 0
    duplicate, advanced = runtime._advance_repair_progress(next_progress, None, gateway, trace + copy.deepcopy(trace))
    assert not advanced and duplicate["consecutive_no_progress"] == 1


def test_candidate_legal_pair_progress_is_a_deduplicated_union_not_text_changes():
    gateway, trace = receipts.receipt_inputs()
    _, value = receipts.selected(gateway, trace)
    progress = runtime._initial_repair_progress({}, gateway, trace)
    after, advanced = runtime._advance_repair_progress(progress, value, gateway, trace)
    assert advanced and len(after["bound_quote_hashes"]) == 1
    different_claim = copy.deepcopy(value)
    different_claim["findings"][0]["claim"] = "Changing wording cannot create evidence progress."
    repeated, advanced = runtime._advance_repair_progress(after, different_claim, gateway, trace)
    assert not advanced and repeated["consecutive_no_progress"] == 1
    literal = base.final_findings()
    accumulated, advanced = runtime._advance_repair_progress(repeated, literal, gateway, trace)
    assert advanced and len(accumulated["bound_quote_hashes"]) == 2 and accumulated["consecutive_no_progress"] == 0


@pytest.mark.parametrize("field,value", [("ready_binding_count", True), ("consecutive_no_progress", 3),
    ("version", 2), ("bound_quote_hashes", ["untrusted-payload"]), ("bound_quote_hashes", ["0" * 64] * 2)])
def test_progress_checkpoint_rejects_unbounded_or_untrusted_metadata(field, value):
    gateway, trace = receipts.receipt_inputs()
    progress = runtime._initial_repair_progress({}, gateway, trace)
    progress[field] = value
    with pytest.raises(PipelineError):
        runtime._initial_repair_progress({"final_repair_progress": progress}, gateway, trace)


def test_unknown_or_mixed_receipts_cannot_manufacture_legal_candidate_progress():
    gateway, trace = receipts.receipt_inputs()
    _, value = receipts.selected(gateway, trace)
    value["findings"][0]["quotes"][0]["receipt_id"] = "qr1_" + "0" * 64
    baseline = runtime._initial_repair_progress({}, gateway, trace)
    updated, advanced = runtime._advance_repair_progress(baseline, value, gateway, trace)
    assert not advanced and updated["bound_quote_hashes"] == []


def test_standard_feedback_safely_accepts_reserved_six_custom_default_stays_two():
    feedback = {"code": "invalid_quote_receipt", "repair_number": 6,
        "detail": {"version": 1, "code": "invalid_quote_receipt", "subtype": "unknown", "finding_index": 1, "quote_index": 1}}
    assert runtime._safe_final_feedback(feedback, maximum=6) == feedback
    assert runtime._safe_final_feedback(feedback) is None


def test_full_bound_union_stays_resumable_without_evicting_prior_progress():
    gateway, trace = receipts.receipt_inputs()
    _, value = receipts.selected(gateway, trace)
    progress = runtime._initial_repair_progress({}, gateway, trace)
    progress["bound_quote_hashes"] = [format(n, "064x") for n in range(1008)]
    before = copy.deepcopy(progress)
    result, advanced = runtime._advance_repair_progress(progress, value, gateway, trace)
    assert not advanced and result["bound_quote_hashes"] == before["bound_quote_hashes"]
    assert result["consecutive_no_progress"] == 1 and progress == before
    assert runtime._initial_repair_progress({"final_repair_progress": result}, gateway, trace) == result
