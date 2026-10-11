"""Receipt selection is an explicit wire extension; saved quotes remain literal."""
import copy
import json

import pytest

from app.workbench import harness_runtime as runtime
from app.workbench.harness_evidence import EvidenceGateway
from app.workbench.pipeline import PipelineError
import test_workbench_harness_runtime as base
import test_workbench_harness_final_binding as binding
import test_workbench_harness_quote_readiness as ready

synthetic_configuration = base.synthetic_configuration


def receipt_inputs():
    source = ready.gateway()
    trace = base.valid_trace(source)
    trace.append(ready.tool_row(source, "read_evidence", {"citation_id": "S002"}))
    trace.append(ready.check(source))
    return source, trace


def selected(source, trace):
    catalog = runtime._quote_receipt_catalog(source, trace)
    receipt = next(row for row in catalog["entries"] if row["citation_id"] == "S002")
    value = {"findings": [{"claim": "材料保留未核验边界。", "citation_ids": ["S002"],
        "quotes": [{"citation_id": "S002", "receipt_id": receipt["receipt_id"]}]}],
        "focus_accounts": [], "gaps": [], "next_steps": []}
    return receipt, value


def test_catalog_is_current_exact_read_checked_pair_and_stable_without_mutation():
    source, trace = receipt_inputs()
    before = copy.deepcopy(trace)
    catalog = runtime._quote_receipt_catalog(source, trace)
    assert runtime.QUOTE_RECEIPT_WIRE_VERSION == catalog["version"] == 1
    assert catalog["scope_sha256"] == source.manifest["input_fingerprint"]
    assert len(catalog["entries"]) == 2
    receipt, value = selected(source, trace)
    assert receipt["quote"] == ready.QUOTE
    assert receipt["read_window"]["fragment_sha256"] == trace[2]["result"]["fragment_sha256"]
    assert receipt["source_sha256"] == trace[3]["result"]["source_sha256"]
    assert receipt["quote_sha256"] == trace[3]["result"]["quote_sha256"]
    projected = ready.project(source, trace, trace[3])
    assert projected["receipt_id"] == receipt["receipt_id"] and projected["quote_receipt_wire_version"] == 1
    assert runtime._quote_receipt_catalog(source, trace + copy.deepcopy(trace)) == catalog
    assert trace == before and all("receipt_id" not in row["result"] for row in trace)


@pytest.mark.parametrize("change", ["no_read", "no_check", "wrong_window", "source_hash", "fragment_hash", "quote_hash",
    "read_citation", "read_total", "read_returned", "read_returned_bool"])
def test_incomplete_or_corrupt_pair_produces_no_receipt(change):
    source, trace = receipt_inputs()
    if change == "no_read": trace.pop(2)
    elif change == "no_check": trace.pop(3)
    elif change == "wrong_window": trace[2] = ready.tool_row(source, "read_evidence", {"citation_id": "S002", "limit": 3})
    elif change == "source_hash": trace[2]["result"]["source_sha256"] = "0" * 64
    elif change == "fragment_hash": trace[2]["result"]["fragment_sha256"] = "0" * 64
    elif change == "read_citation": trace[2]["result"]["citation_id"] = "S001"
    elif change == "read_total": trace[2]["result"]["total_characters"] += 1
    elif change == "read_returned": trace[2]["result"]["returned_characters"] += 1
    elif change == "read_returned_bool": trace[2]["result"]["returned_characters"] = True
    else: trace[3]["result"]["quote_sha256"] = "0" * 64
    before = copy.deepcopy(trace)
    catalog = runtime._quote_receipt_catalog(source, trace)
    assert all(row["citation_id"] != "S002" for row in catalog["entries"])
    assert trace == before


def test_receipt_resolves_losslessly_then_original_validator_accepts():
    source, trace = receipt_inputs()
    receipt, value = selected(source, trace)
    before = copy.deepcopy(value)
    resolved = runtime._resolve_quote_receipts(value, source, trace)
    assert resolved["findings"][0]["quotes"] == [{"citation_id": "S002", "quote": ready.QUOTE}]
    assert runtime.validate_findings(resolved, source, trace) == resolved
    assert value == before and "receipt_id" not in json.dumps(resolved)


@pytest.mark.parametrize("case", ["unknown", "wrong_version", "bool", "mixed", "extra_field", "cross_citation", "cross_scope", "changed_window"])
def test_invalid_receipt_selections_fail_closed_with_safe_position(case):
    source, trace = receipt_inputs()
    receipt, value = selected(source, trace)
    quote = value["findings"][0]["quotes"][0]
    if case == "unknown": quote["receipt_id"] = "qr1_" + "0" * 64
    elif case == "wrong_version": quote["receipt_id"] = quote["receipt_id"].replace("qr1_", "qr2_")
    elif case == "bool": quote["receipt_id"] = True
    elif case == "mixed": quote["quote"] = ready.QUOTE
    elif case == "extra_field": quote[base.SYNTHETIC_KEY] = base.REASONING
    elif case == "cross_citation":
        quote["citation_id"] = "S001"
        value["findings"][0]["citation_ids"] = ["S001"]
    elif case == "cross_scope":
        items, context, reviews = base.snapshot()
        items.append({"citation_id": "S002", "content": "改变保存范围。" + ready.QUOTE, "type": "news"})
        source = EvidenceGateway(items, context, reviews)
    else:
        trace[2] = ready.tool_row(source, "read_evidence", {"citation_id": "S002", "offset": len("固定前缀。")})
    before = copy.deepcopy(value)
    with pytest.raises(PipelineError) as caught:
        runtime._resolve_quote_receipts(value, source, trace)
    detail = runtime._validation_detail(caught.value)
    assert detail["finding_index"] == detail["quote_index"] == 1
    assert detail["code"] in {"unbound_quote", "invalid_quote_receipt"}
    assert base.SYNTHETIC_KEY not in str(caught.value) and base.REASONING not in str(caught.value)
    assert ready.QUOTE not in json.dumps(detail) and value == before


def test_legacy_raw_short_quote_is_not_silently_replaced_by_a_receipt():
    source, trace = receipt_inputs()
    value = base.final_findings()
    value["findings"][0]["quotes"][0]["quote"] = "会议仍属计划"
    before = copy.deepcopy(value)
    resolved = runtime._resolve_quote_receipts(value, source, trace)
    assert resolved == before
    with pytest.raises(PipelineError) as caught:
        runtime.validate_findings(resolved, source, trace)
    assert runtime._validation_detail(caught.value) == {"version": 1, "code": "unchecked_quote", "subtype": None,
        "finding_index": 1, "quote_index": 1}


def test_resume_has_full_evidence_plus_flat_catalog_without_changing_trace():
    source, trace = receipt_inputs()
    full = trace * 35
    before = copy.deepcopy(full)
    context = json.loads(runtime._resume_tool_context(full, gateway=source))
    assert context["saved_call_count"] == 140 and context["unique_tool_results"] == 4
    assert context["quote_receipt_catalog"] == runtime._quote_receipt_catalog(source, full)
    assert len(context["quote_receipt_catalog"]["entries"]) == 2 and full == before
    custom = json.loads(runtime._resume_tool_context(full))
    assert "quote_receipt_catalog" not in custom


@pytest.mark.parametrize("custom", [False, True])
def test_actual_stdio_candidate_complete_receipt_resolution_is_standard_only(monkeypatch, tmp_path, custom):
    gateway = EvidenceGateway(*base.snapshot())
    parent = binding.failed_parent()
    receipt = runtime._quote_receipt_catalog(gateway, parent["trace"])["entries"][0]
    wire_value = base.final_findings()
    wire_value["findings"][0]["quotes"] = [{"citation_id": "S001", "receipt_id": receipt["receipt_id"]}]
    original = copy.deepcopy(parent)
    monkeypatch.setattr(base, "final_findings", lambda: copy.deepcopy(wire_value))
    capture, processes = binding.sidecar(monkeypatch, tmp_path, "receipt_selection")
    saved = []
    validator = {"_validator": lambda value, *_: value} if custom else {}
    result = base.run_host(saved, previous_state=parent, max_model_calls=0, max_tool_calls=0, **validator)
    assert result["complete"] and result["final_repairs_used"] == 2 and parent == original
    assert result["findings"]["findings"][0]["quotes"] == ([{"citation_id": "S001", "receipt_id": receipt["receipt_id"]}] if custom
        else [{"citation_id": "S001", "quote": base.QUOTE}])
    if not custom:
        assert result["quote_receipt_wire_version"] == 1
        completed = base.run_host([], previous_state=result, **validator)
        assert completed["reused_completed_checkpoint"] and len(processes) == 1
    assert all(process.returncode is not None for process in processes)
