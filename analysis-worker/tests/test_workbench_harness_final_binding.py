"""Synthetic findings and real isolated Node stdio; no provider or profile access."""
import asyncio
import copy
import hashlib
import json

import pytest

from app.workbench import harness_runtime as runtime
from app.workbench.ai_capacity import compact_json
from app.workbench.harness_evidence import EvidenceGateway
from app.workbench.pipeline import PipelineError
import test_workbench_harness_runtime as base

synthetic_configuration = base.synthetic_configuration


def detail(code="unbound_quote", subtype="quote_schema", finding=1, quote=1):
    return {"version": 1, "code": code, "subtype": subtype,
            "finding_index": finding, "quote_index": quote}


def inputs():
    items, context, reviews = base.snapshot()
    items.append({"citation_id": "S002", "content": "另一份公开材料。", "title": "另一材料", "type": "news"})
    return items, context, reviews


@pytest.mark.parametrize("case,subtype", [("missing_field", "quote_schema"),
    ("extra_field", "quote_schema"), ("non_object", "quote_schema"),
    ("wrong_finding", "citation_not_in_finding"), ("unknown_id", "citation_not_in_finding"),
    ("unread", "citation_not_read")])
def test_binding_failure_has_exact_safe_finding_quote_location(case, subtype):
    gateway = EvidenceGateway(*inputs())
    value = base.final_findings()
    row = copy.deepcopy(value["findings"][0])
    row["quotes"].append(copy.deepcopy(row["quotes"][0]))
    value["findings"].append(row)
    quote = row["quotes"][1]
    if case == "missing_field": quote.pop("quote")
    elif case == "extra_field": quote[base.SYNTHETIC_KEY] = base.REASONING
    elif case == "non_object": row["quotes"][1] = base.SYNTHETIC_KEY
    elif case == "wrong_finding": quote["citation_id"] = "S002"
    elif case == "unknown_id": quote["citation_id"] = base.SYNTHETIC_KEY
    else:
        row["citation_ids"].append("S002")
        quote["citation_id"] = "S002"
    before = copy.deepcopy(value)
    with pytest.raises(PipelineError, match="摘录未绑定") as caught:
        runtime.validate_findings(value, gateway, base.valid_trace(gateway))
    assert caught.value.harness_validation_detail == detail(subtype=subtype, finding=2, quote=2)
    code, feedback = runtime._final_repair_feedback(caught.value)
    assert code == "unbound_quote" and subtype in feedback
    assert "finding[2].quotes[2]" in feedback
    assert base.SYNTHETIC_KEY not in feedback and base.REASONING not in feedback
    assert base.QUOTE not in json.dumps(caught.value.harness_validation_detail)
    assert value == before


@pytest.mark.parametrize("case,expected", [("unchecked", "逐字核对"),
    ("outside_window", "实际读取的原文窗口"), ("changed_source", "再次核对失败")])
def test_precise_binding_feedback_does_not_relax_later_exact_quote_gates(monkeypatch, case, expected):
    gateway = EvidenceGateway(*base.snapshot())
    value = base.final_findings()
    trace = base.valid_trace(gateway, limit=3 if case == "outside_window" else 12000)
    if case == "unchecked":
        # An unrelated valid pair must not grant permission to use this quote.
        trace[1]["args"]["exact_quote"] = "公开资料"
        trace[1]["result"] = gateway.call("check_citation", trace[1]["args"])
    if case == "changed_source": monkeypatch.setattr(gateway, "call", lambda *args: {"valid": False})
    before = copy.deepcopy(value)
    with pytest.raises(PipelineError, match=expected): runtime.validate_findings(value, gateway, trace)
    assert value == before


@pytest.mark.parametrize("change,value", [("version", True), ("subtype", "unsafe"),
    ("code", base.SYNTHETIC_KEY), ("finding_index", True), ("finding_index", 25),
    ("quote_index", 7), ("quote_index", "1"), ("raw", base.REASONING)])
def test_untrusted_saved_validation_detail_is_not_echoed(change, value):
    unsafe = detail()
    unsafe[change] = value
    assert runtime._safe_validation_detail(unsafe) is None


_SIDECAR = r'''
import readline from 'node:readline';
import {writeFileSync} from 'node:fs';
const mode=__MODE__, capture=__CAPTURE__, result=__FINAL__, quote=__QUOTE__, secret=__SECRET__;
let start, number=0, feedbacks=[];
const emit=x=>process.stdout.write(JSON.stringify(x)+'\n');
const save=()=>writeFileSync(capture,JSON.stringify({start,feedbacks}));
const candidate=()=>{
  const value=JSON.parse(JSON.stringify(result));
  if(mode==='bounded' || mode==='resume_bad'){
    if(number<2 && mode==='bounded')value.findings[0].quotes=[{citation_id:'S001',unexpected:secret}];
    else value.findings[0].quotes=[{citation_id:secret,quote:secret}];
  }
  emit({kind:'final_candidate',protocol:1,id:'candidate-'+(++number),session_id:start.session_id,
    run_binding:start.run_binding,final_response:JSON.stringify(value)});
};
const complete=()=>emit({kind:'complete',session_id:start.session_id,run_binding:start.run_binding,
  runtime_closed:true,finish_reason:'stop',final_response:JSON.stringify(result)});
readline.createInterface({input:process.stdin}).on('line',line=>{
  const msg=JSON.parse(line);
  if(msg.kind==='cancel'){process.exit(0);return;}
  if(msg.kind==='start'){
    start=msg;save();
    emit({kind:'runtime_roster',runtime:'0.2.0-rc.2',names:start.tool_schemas.map(x=>x.name)});
    if(mode==='bounded')emit({kind:'tool_request',id:'read',name:'read_evidence',args:{citation_id:'S001'},run_binding:start.run_binding});
    else if(mode==='budget')emit({kind:'model_request',id:'budget-model',run_binding:start.run_binding,request:{}});
    else if(mode!=='wait')candidate();
  }else if(msg.kind==='tool_response'){
    if(msg.id==='read')emit({kind:'tool_request',id:'check',name:'check_citation',args:{citation_id:'S001',exact_quote:quote},run_binding:start.run_binding});
    else candidate();
  }else if(msg.kind==='candidate_response'){
    if(msg.accepted)complete();
    else {feedbacks.push(msg.feedback);save();candidate();}
  }
});
'''


def sidecar(monkeypatch, tmp_path, mode):
    directory, processes = base.fake_sidecar(monkeypatch, tmp_path, "success")
    capture = tmp_path / (mode + "-start.json")
    script = _SIDECAR.replace("__MODE__", json.dumps(mode)).replace("__CAPTURE__", json.dumps(str(capture)))
    script = script.replace("__FINAL__", json.dumps(base.final_findings(), ensure_ascii=False))
    script = script.replace("__QUOTE__", json.dumps(base.QUOTE)).replace("__SECRET__", json.dumps(base.SYNTHETIC_KEY))
    (directory / "sidecar.mjs").write_text(script, encoding="utf-8")
    return capture, processes


def fingerprint(gateway, system=None):
    return hashlib.sha256(compact_json({"evidence": gateway.manifest["input_fingerprint"],
        "system": system or runtime.SYSTEM, "tools": gateway.tools, "model": "fixture-model",
        "runtime_version": runtime.DSH_VERSION, "protocol": runtime.PROTOCOL}).encode("utf-8")).hexdigest()


def failed_parent():
    gateway = EvidenceGateway(*base.snapshot())
    trace = base.valid_trace(gateway)
    return {"input_fingerprint": fingerprint(gateway), "complete": False, "phase": "failed",
        "model_calls": 25, "transport_attempts": 25, "transport_retries": 1,
        "tool_calls": 130, "trace": trace * 65, "final_repairs_used": 2,
        "final_validation": {"code": "invalid_final", "repair_number": 2},
        "error": "Harness 摘录未绑定实际读取的材料，未生成正式报告。"}


def test_terminal_failure_saved_before_quota_and_last_feedback_remains_separate(monkeypatch, tmp_path):
    capture, processes = sidecar(monkeypatch, tmp_path, "bounded")
    saved = []
    with pytest.raises(PipelineError, match="摘录未绑定"):
        base.run_host(saved)
    final = saved[-1]
    assert final["final_repairs_used"] == runtime.MAX_FINAL_REPAIRS == 2
    assert final["final_validation_failure"] == detail(subtype="citation_not_in_finding")
    assert final["final_validation"]["code"] == "unbound_quote"
    assert final["final_validation"]["detail"] == detail()
    assert final["final_validation"]["repair_number"] == 2
    assert any(row.get("final_validation_failure") == final["final_validation_failure"]
        and row["phase"] != "failed" for row in saved)
    actual = json.loads(capture.read_text(encoding="utf-8"))
    assert len(actual["feedbacks"]) == 2
    assert all("finding[1].quotes[1]" in feedback for feedback in actual["feedbacks"])
    assert all("quote_schema" in feedback for feedback in actual["feedbacks"])
    assert "findings" not in final and not final["complete"]
    assert base.SYNTHETIC_KEY not in json.dumps(saved)
    assert all(process.returncode == 0 for process in processes)


@pytest.mark.parametrize("diagnosis", ["exact", "legacy", "unknown", "poisoned"])
def test_same_fingerprint_resume_injects_safe_guidance_without_replaying_tools_or_mutating_parent(monkeypatch, tmp_path, diagnosis):
    capture, processes = sidecar(monkeypatch, tmp_path, "resume_valid")
    parent = failed_parent()
    if diagnosis == "exact": parent["final_validation_failure"] = detail(subtype="citation_not_in_finding", finding=2, quote=2)
    elif diagnosis == "unknown": parent["error"] = "unrecognized private " + base.SYNTHETIC_KEY
    elif diagnosis == "poisoned":
        parent["final_validation_failure"] = {**detail(), "raw": base.SYNTHETIC_KEY}
        parent["error"] = base.REASONING
    before = copy.deepcopy(parent)
    saved = []
    state = base.run_host(saved, previous_state=parent)
    actual = json.loads(capture.read_text(encoding="utf-8"))
    assert state["complete"] and state["findings"] == base.final_findings()
    assert state["trace"] == before["trace"] and state["tool_calls"] == 130
    assert state["model_calls"] == state["transport_attempts"] == 25
    assert state["final_repairs_used"] == 2 and parent == before
    assert state["input_fingerprint"] == parent["input_fingerprint"]
    assert '"saved_call_count":130' in actual["start"]["goal"]
    assert "结束候选修正指导" in actual["start"]["goal"]
    assert "read_evidence" in actual["start"]["goal"] and "check_citation" in actual["start"]["goal"]
    if diagnosis == "exact":
        assert "citation_not_in_finding" in actual["start"]["goal"]
        assert "finding[2].quotes[2]" in actual["start"]["goal"]
    elif diagnosis == "legacy":
        assert "unbound_quote" in actual["start"]["goal"]
        assert "quote_schema" not in actual["start"]["goal"]
    else: assert "unknown" in actual["start"]["goal"]
    assert base.SYNTHETIC_KEY not in json.dumps(actual) and base.REASONING not in json.dumps(actual)
    assert base.SYNTHETIC_KEY not in json.dumps(saved) and base.REASONING not in json.dumps(saved)
    assert not actual["feedbacks"] and all(process.returncode == 0 for process in processes)


def test_standard_resume_adds_feedback_but_repeated_no_progress_still_rejects(monkeypatch, tmp_path):
    capture, _ = sidecar(monkeypatch, tmp_path, "resume_bad")
    parent, saved = failed_parent(), []
    before = copy.deepcopy(parent)
    with pytest.raises(PipelineError, match="摘录未绑定"):
        base.run_host(saved, previous_state=parent)
    assert saved[-1]["final_validation_failure"] == detail(subtype="citation_not_in_finding")
    assert saved[-1]["final_repairs_used"] == 3 and saved[-1]["trace"] == before["trace"]
    assert saved[-1]["final_repair_policy"]["total_limit"] == 6
    assert saved[-1]["final_repair_stop"]["reason"] == "no_progress"
    assert saved[-1]["final_validation"]["repair_number"] == 3 and parent == before
    feedbacks = json.loads(capture.read_text(encoding="utf-8"))["feedbacks"]
    assert len(feedbacks) == 1 and "citation_not_in_finding" in feedbacks[0]


def test_standard_initial_prompt_supplies_exact_quote_object_and_pair_example(monkeypatch, tmp_path):
    capture, _ = sidecar(monkeypatch, tmp_path, "resume_valid")
    base.run_host([], previous_state=failed_parent())
    system = json.loads(capture.read_text(encoding="utf-8"))["start"]["system"]
    assert '{"citation_id":"S001","quote":"原文中的完整逐字片段"}' in system
    assert 'check_citation({"citation_id":"S001","exact_quote":"原文中的完整逐字片段"})' in system
    assert "示例不是已读取证据" in system and "额外字段" in system


def test_custom_validator_and_prompt_do_not_receive_standard_report_schema(monkeypatch, tmp_path):
    capture, _ = sidecar(monkeypatch, tmp_path, "resume_valid")
    system = "custom review schema contract"
    parent = failed_parent()
    parent["input_fingerprint"] = fingerprint(EvidenceGateway(*base.snapshot()), system)
    state = base.run_host([], previous_state=parent, _system=system,
        _validator=lambda value, gateway, trace: copy.deepcopy(value))
    actual = json.loads(capture.read_text(encoding="utf-8"))["start"]
    assert actual["system"] == system and "结束候选修正指导" not in actual["goal"]
    assert state["final_repairs_used"] == 2 and state["trace"] == parent["trace"]


def test_cancel_after_resume_guidance_preserves_trace_and_cumulative_limit(monkeypatch, tmp_path):
    _, processes = sidecar(monkeypatch, tmp_path, "wait")
    parent, saved = failed_parent(), []
    parent["final_validation_failure"] = detail(subtype="citation_not_read", finding=2, quote=2)
    before = copy.deepcopy(parent)
    async def exercise():
        ready = asyncio.Event()
        def checkpoint(row):
            saved.append(row)
            if row.get("tool_roster"): ready.set()
        task = asyncio.create_task(runtime.run_evidence_harness(*base.snapshot(), previous_state=parent,
            checkpoint=checkpoint, time_budget_seconds=4))
        await asyncio.wait_for(ready.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(exercise())
    assert saved[-1]["phase"] == "cancelled" and saved[-1]["trace"] == before["trace"]
    assert saved[-1]["final_repairs_used"] == 2 and parent == before
    assert saved[-1]["final_validation_failure"] == before["final_validation_failure"]
    assert all(process.returncode is not None for process in processes)


def test_budget_before_next_decision_preserves_saved_validation_location_and_no_new_call(monkeypatch, tmp_path):
    _, processes = sidecar(monkeypatch, tmp_path, "budget")
    parent, saved = failed_parent(), []
    parent["final_validation_failure"] = detail(subtype="citation_not_read", finding=2, quote=2)
    before = copy.deepcopy(parent)
    async def forbidden(*args, **options): pytest.fail("Budget must stop before transport")
    monkeypatch.setattr(runtime, "model_request", forbidden)
    with pytest.raises(PipelineError, match="决策轮数上限"):
        base.run_host(saved, previous_state=parent, max_model_calls=25)
    assert saved[-1]["model_calls"] == saved[-1]["transport_attempts"] == 25
    assert saved[-1]["final_validation_failure"] == before["final_validation_failure"]
    assert saved[-1]["trace"] == before["trace"] and saved[-1]["final_repairs_used"] == 2
    assert parent == before and all(process.returncode is not None for process in processes)
