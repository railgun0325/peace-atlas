"""Standard report wire readiness must never rewrite saved pure tool evidence."""
import copy
import json

import pytest

from app.workbench import harness_runtime as runtime
from app.workbench.harness_evidence import EvidenceGateway
from app.workbench.pipeline import PipelineError
import test_workbench_harness_runtime as base
import test_workbench_harness_final_binding as binding

synthetic_configuration = base.synthetic_configuration
QUOTE = "未核验组织关系。🕊️ e\u0301  两个空格"


def gateway():
    items, context, reviews = base.snapshot()
    items.append({"citation_id": "S002", "content": "固定前缀。" + QUOTE + "\n结尾。", "type": "news"})
    return EvidenceGateway(items, context, reviews)


def tool_row(gateway, name, args):
    return {"name": name, "args": copy.deepcopy(args), "result": gateway.call(name, args), "ok": True}


def check(gateway, quote=QUOTE, **args):
    return tool_row(gateway, "check_citation", {"citation_id": "S002", "exact_quote": quote, **args})


def project(gateway, trace, row):
    return runtime._citation_result_for_model(row["name"], row["args"], row["result"], gateway, trace)


@pytest.mark.parametrize("reading,gap", [("missing", "citation_not_read"), ("wrong_window", "unread_window"), ("full", "ready")])
def test_valid_exact_match_is_distinct_from_actual_readiness(reading, gap):
    source = gateway()
    trace = base.valid_trace(source)
    if reading != "missing":
        trace.append(tool_row(source, "read_evidence", {"citation_id": "S002", **({"limit": 3} if reading == "wrong_window" else {})}))
    checked = check(source)
    before = copy.deepcopy((trace, checked))
    result = project(source, trace, checked)
    assert checked["result"]["valid"] is result["valid"] is True
    assert result["exact_match"] is True and result["truth_verified"] is False
    assert result["report_quote_ready"] is (gap == "ready")
    assert result["report_quote_gap"] == gap
    if gap == "ready":
        assert result["next_read"] is None
    else:
        request = result["next_read"]
        assert set(request) == {"citation_id", "offset", "limit"}
        assert request["citation_id"] == "S002"
        assert request["offset"] == checked["result"]["offset"] and request["limit"] == len(QUOTE)
        assert source.call("read_evidence", request)["text"] == QUOTE
    assert (trace, checked) == before


@pytest.mark.parametrize("field,value", [("offset", 99999999), ("end", 99999999),
    ("source_sha256", "0" * 64), ("quote_sha256", "1" * 64), ("valid", 1)])
def test_forged_saved_check_never_grants_readiness_or_guessed_read(field, value):
    source = gateway()
    trace = base.valid_trace(source)
    checked = check(source)
    checked["result"][field] = value
    before = copy.deepcopy(checked)
    result = project(source, trace, checked)
    assert result["report_quote_ready"] is False and result["report_quote_gap"] == "check_not_current"
    assert result["next_read"] is None and checked == before


@pytest.mark.parametrize("quote", ["不存在的逐字内容", "未核验组织关系。🕊️ é 两个空格"])
def test_absent_or_normalized_quote_does_not_get_a_read_request(quote):
    source = gateway()
    checked = check(source, quote)
    assert checked["result"]["valid"] is False
    result = project(source, base.valid_trace(source), checked)
    assert result["report_quote_ready"] is False and result["report_quote_gap"] == "quote_mismatch"
    assert result["next_read"] is None


def test_long_tool_match_is_not_eligible_for_final_1200_character_quote():
    quote = "逐字" * 601
    source = EvidenceGateway([{"citation_id": "S002", "content": quote, "type": "news"}])
    checked = check(source, quote)
    result = project(source, [], checked)
    assert result["valid"] is True and result["report_quote_ready"] is False
    assert result["report_quote_gap"] == "quote_not_eligible" and result["next_read"] is None


@pytest.mark.parametrize("change", ["unknown_id", "missing_quote", "bool_offset"])
def test_invalid_legacy_check_args_get_fixed_gap_without_an_exception(change):
    source = gateway()
    checked = check(source)
    if change == "unknown_id": checked["args"]["citation_id"] = "S999"
    elif change == "missing_quote": checked["args"].pop("exact_quote")
    else: checked["args"]["offset"] = True
    before = copy.deepcopy(checked)
    result = project(source, base.valid_trace(source), checked)
    assert result["report_quote_ready"] is False and result["report_quote_gap"] == "check_not_current"
    assert result["next_read"] is None and checked == before


def test_invalid_legacy_read_args_do_not_grant_a_ready_window():
    source = gateway()
    read = tool_row(source, "read_evidence", {"citation_id": "S002"})
    read["args"]["offset"] = True
    trace = [*base.valid_trace(source), read]
    before = copy.deepcopy(trace)
    result = project(source, trace, check(source))
    assert result["report_quote_ready"] is False and result["report_quote_gap"] == "unread_window"
    assert source.call("read_evidence", result["next_read"])["text"] == QUOTE
    assert trace == before


def test_second_occurrence_read_is_ready_when_check_found_first_occurrence():
    text = QUOTE + "\n中间正文\n" + QUOTE
    source = EvidenceGateway([{"citation_id": "S002", "content": text, "type": "news"}])
    checked = check(source)
    assert checked["result"]["offset"] == 0
    second = text.rfind(QUOTE)
    trace = [tool_row(source, "read_evidence", {"citation_id": "S002", "offset": second})]
    result = project(source, trace, checked)
    assert result["report_quote_ready"] is True and result["report_quote_gap"] == "ready"
    assert result["next_read"] is None


def test_restore_projects_missing_read_without_rewriting_140_pure_results():
    source = gateway()
    old = base.valid_trace(source) * 65
    missing = check(source)
    trace = old + [old[1]] * 7 + [missing] + [old[1]] * 2
    before = copy.deepcopy(trace)
    context = json.loads(runtime._resume_tool_context(trace, gateway=source))
    assert context["saved_call_count"] == 140 and context["unique_tool_results"] == 3
    projected = [row for row in context["tool_results"] if row["name"] == "check_citation"]
    assert {row["args"]["citation_id"]: row["result"]["report_quote_ready"] for row in projected} == {"S001": True, "S002": False}
    assert projected[-1]["result"]["report_quote_gap"] == "citation_not_read"
    assert trace == before and all("report_quote_ready" not in row["result"] for row in trace)
    custom = json.loads(runtime._resume_tool_context(trace))
    assert all("report_quote_ready" not in row["result"] for row in custom["tool_results"])


def test_explicit_next_read_then_check_closes_strict_validator_with_parent_unchanged():
    source = gateway()
    parent = {"trace": base.valid_trace(source) * 65 + [check(source)] * 10, "final_repairs_used": 2}
    before = copy.deepcopy(parent)
    candidate = {"findings": [{"claim": "材料保留未核验边界。", "citation_ids": ["S002"],
        "quotes": [{"citation_id": "S002", "quote": QUOTE}]}], "focus_accounts": [], "gaps": [], "next_steps": []}
    with pytest.raises(PipelineError) as failed:
        runtime.validate_findings(candidate, source, parent["trace"])
    assert failed.value.harness_validation_detail["subtype"] == "citation_not_read"
    pending = project(source, parent["trace"], parent["trace"][-1])
    child = copy.deepcopy(parent)
    child["trace"].append(tool_row(source, "read_evidence", pending["next_read"]))
    child["trace"].append(check(source))
    ready = project(source, child["trace"], child["trace"][-1])
    assert ready["report_quote_ready"] is True
    assert runtime.validate_findings(candidate, source, child["trace"]) == candidate
    assert len(child["trace"]) == 142 and child["final_repairs_used"] == 2 and parent == before


@pytest.mark.parametrize("custom", [False, True])
def test_live_tool_wire_is_standard_only_and_durable_result_stays_pure(monkeypatch, tmp_path, custom):
    capture, processes = binding.sidecar(monkeypatch, tmp_path, "bounded")
    script_path = tmp_path / "harness" / "sidecar.mjs"
    # Locate the fake runtime created by the existing isolated stdio helper.
    directory, _ = runtime.runtime_paths()
    script_path = directory / "sidecar.mjs"
    script = script_path.read_text(encoding="utf-8")
    script = script.replace("feedbacks=[]", "feedbacks=[], tools=[]")
    script = script.replace("{start,feedbacks}", "{start,feedbacks,tools}")
    script = script.replace("}else if(msg.kind==='tool_response'){", "}else if(msg.kind==='tool_response'){tools.push(msg.result);save();")
    # This test is about tool output projections, so submit the valid candidate
    # immediately rather than the helper's intentionally invalid candidates.
    script = script.replace("mode==='bounded' || mode==='resume_bad'", "mode==='resume_bad'")
    script_path.write_text(script, encoding="utf-8")
    saved = []
    options = {"_validator": lambda value, *_: value} if custom else {}
    result = base.run_host(saved, **options)
    wire = json.loads(capture.read_text(encoding="utf-8"))
    assert result["complete"] and len(result["trace"]) == 2
    check_wire = wire["tools"][1]
    assert ("report_quote_ready" in check_wire) is (not custom)
    if not custom:
        assert check_wire["report_quote_ready"] is True and check_wire["report_quote_gap"] == "ready"
    assert "report_quote_ready" not in result["trace"][1]["result"]
    assert result["trace"][1]["result"] == EvidenceGateway(*base.snapshot()).call("check_citation", result["trace"][1]["args"])
    assert all(process.returncode is not None for process in processes)
