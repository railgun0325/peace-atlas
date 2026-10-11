"""Output includes thinking; exact full wire and saved evidence remain bounded."""
import copy
import json

import httpx
import pytest

from app.workbench import harness_runtime as runtime
from app.workbench.ai_capacity import ModelCapacity, assess_messages, compact_json, PER_MESSAGE_RESERVE_TOKENS
from app.workbench.harness_evidence import EvidenceGateway
from app.workbench.pipeline import PipelineError
import test_workbench_harness_runtime as base
import test_workbench_harness_final_binding as binding

synthetic_configuration = base.synthetic_configuration
FLASH = ModelCapacity("fixture-model", 1048576, 393216, "official_metadata_fixture")
LENGTH_ERROR = "Harness 回复因单轮输出或模型上下文上限截断，输出未完整结束；未采用截断内容，证据已保存，可续跑继续。"


@pytest.mark.parametrize("output", [65536, 131072, 393216])
def test_configured_large_output_reaches_http_and_preserves_response_diagnostic(monkeypatch, output):
    gateway = EvidenceGateway(*base.snapshot())
    value = base.payload(gateway)
    value["max_tokens"] = output
    original = copy.deepcopy(value)
    sent = []
    def provider(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=base.response(reason="length"))
    base.transport(monkeypatch, provider)
    with pytest.raises(PipelineError, match="未完整结束") as caught:
        base.request_model(value, gateway, capacity=FLASH)
    assert len(sent) == 1 and sent[0]["max_tokens"] == output
    assert value == original
    diagnostic = caught.value.harness_response_diagnostic
    assert diagnostic["code"] == "unfinished" and diagnostic["requested_max_tokens"] == output
    assert runtime._safe_response_diagnostic(diagnostic) == diagnostic
    assert diagnostic["finish_reason"] == "length"


def test_known_large_output_allows_long_thinking_and_complete_bounded_content(monkeypatch):
    gateway = EvidenceGateway(*base.snapshot())
    value = base.payload(gateway)
    value["max_tokens"] = FLASH.max_output_tokens
    def provider(request):
        wire = json.loads(request.content)
        assert wire["max_tokens"] == 393216
        body = base.response(content=json.dumps(base.final_findings()), reasoning_content="fixture thinking")
        body["usage"] = {"prompt_tokens": 1000, "completion_tokens": 70000, "total_tokens": 71000}
        return httpx.Response(200, json=body)
    base.transport(monkeypatch, provider)
    result = base.request_model(value, gateway, capacity=FLASH)
    assert result["usage"]["completion_tokens"] == 70000
    assert json.loads(result["choices"][0]["message"]["content"]) == base.final_findings()


@pytest.mark.parametrize("case", ["length", "invalid_tool"])
def test_length_has_precise_public_error_while_tool_failure_keeps_existing_message(monkeypatch, case):
    gateway = EvidenceGateway(*base.snapshot())
    sends = []
    def provider(request):
        sends.append(request)
        return httpx.Response(200, json=base.response(reason="length" if case == "length" else "tool_calls"))
    base.transport(monkeypatch, provider)
    with pytest.raises(PipelineError) as caught: base.request_model(base.payload(gateway), gateway)
    assert str(caught.value) == (LENGTH_ERROR if case == "length" else "Harness 模型输出未完整结束或工具调用无效，证据已保存。")
    assert len(sends) == 1 and not hasattr(caught.value, "harness_transport_diagnostic")


@pytest.mark.parametrize("capacity", [ModelCapacity("fixture-model", 131072, 32768, "unknown_fixture"),
    ModelCapacity("fixture-model", 8000, 1024, "explicit_small_fixture")])
def test_above_configured_output_rejected_before_any_http(monkeypatch, capacity):
    gateway = EvidenceGateway(*base.snapshot())
    value = base.payload(gateway)
    value["max_tokens"] = capacity.max_output_tokens + 1
    def forbidden(request): pytest.fail("Over-configured request must never send")
    base.transport(monkeypatch, forbidden)
    with pytest.raises(PipelineError, match="单轮输出预留"):
        base.request_model(value, gateway, capacity=capacity)


def test_shared_context_reduces_output_without_losing_complete_wire_or_mutating_request(monkeypatch):
    gateway = EvidenceGateway(*base.snapshot())
    value = base.payload(gateway)
    value.update(max_tokens=FLASH.max_output_tokens, temperature=1)
    value["messages"].append({"role": "assistant", "content": "x" * (FLASH.context_window - 100000),
        "reasoning_content": "synthetic saved reasoning", "tool_calls": []})
    original = copy.deepcopy(value)
    budgets, sent = [], []
    def provider(request):
        wire = json.loads(request.content)
        sent.append(wire)
        assert wire["temperature"] == 0.2 and wire["reasoning_effort"] == "high"
        assert 65536 < wire["max_tokens"] < FLASH.max_output_tokens
        assessment = assess_messages([{"role": "user", "content": compact_json(wire)
            + " " * (PER_MESSAGE_RESERVE_TOKENS * len(wire["messages"]))}],
            max_output_tokens=wire["max_tokens"], capacity=FLASH)
        assert assessment.fits
        assert assessment.input_tokens_upper_bound + wire["max_tokens"] <= FLASH.context_window
        assert budgets and budgets[-1]["output_reserved_tokens"] == wire["max_tokens"]
        return httpx.Response(200, json=base.response())
    def event(kind, data):
        if kind == "output_budget": budgets.append(data)
    base.transport(monkeypatch, provider)
    base.request_model(value, gateway, capacity=FLASH, _transport_event=event)
    assert len(sent) == len(budgets) == 1 and value == original
    assert budgets[0]["context_limited"]
    assert budgets[0]["requested_max_tokens"] == 393216
    assert budgets[0]["output_reserved_tokens"] == sent[0]["max_tokens"]
    assert "reasoning" not in json.dumps(budgets) and base.SYNTHETIC_KEY not in json.dumps(budgets)


def test_full_input_leaving_no_output_stops_before_any_http(monkeypatch):
    gateway = EvidenceGateway(*base.snapshot())
    value = base.payload(gateway)
    value.update(max_tokens=FLASH.max_output_tokens)
    value["messages"][0]["content"] = "x" * FLASH.context_window
    def forbidden(request): pytest.fail("Over-context input must never send")
    base.transport(monkeypatch, forbidden)
    with pytest.raises(PipelineError, match="超过模型上下文"):
        base.request_model(value, gateway, capacity=FLASH)


@pytest.mark.parametrize("capacity", [FLASH, ModelCapacity("fixture-model", 131072, 32768, "unknown_fixture"),
    ModelCapacity("fixture-model", 8192, 1024, "explicit_small_fixture")])
def test_runtime_initialization_uses_configured_output_and_keeps_saved_fingerprint_trace_and_limit(monkeypatch, tmp_path, capacity):
    capture, _ = binding.sidecar(monkeypatch, tmp_path, "resume_valid")
    monkeypatch.setattr(runtime, "configured_model_capacity", lambda **options: capacity)
    parent = binding.failed_parent()
    before = copy.deepcopy(parent)
    state = base.run_host([], previous_state=parent)
    start = json.loads(capture.read_text(encoding="utf-8"))["start"]
    assert start["model"]["max_output_tokens"] == capacity.max_output_tokens
    assert state["complete"] and state["final_repairs_used"] == 2
    assert state["trace"] == parent["trace"] and state["input_fingerprint"] == parent["input_fingerprint"]
    assert state["tool_calls"] == 130 and parent == before


def test_length_failure_keeps_paid_reservation_budget_and_saved_tools_then_recovery_reuses(monkeypatch, tmp_path):
    base.fake_sidecar(monkeypatch, tmp_path, "model")
    monkeypatch.setattr(runtime, "configured_model_capacity", lambda **options: FLASH)
    parent = binding.failed_parent()
    before = copy.deepcopy(parent)
    sends, checkpoints = [], []
    def provider(request):
        sends.append(json.loads(request.content))
        return httpx.Response(200, json=base.response(reason="length"))
    base.transport(monkeypatch, provider)
    with pytest.raises(PipelineError, match="未完整结束"):
        base.run_host(checkpoints, previous_state=parent, max_model_calls=0)
    failed = checkpoints[-1]
    assert len(sends) == 1 and failed["phase"] == "failed"
    assert failed["model_calls"] == failed["transport_attempts"] == 26
    assert failed["transport_retries"] == 1 and failed["final_repairs_used"] == 2
    assert failed["failure_diagnostic"]["stage"] == "model_transport"
    assert failed["model_output_budget"]["output_reserved_tokens"] == 2048
    assert failed["trace"] == before["trace"] and parent == before
    binding.sidecar(monkeypatch, tmp_path, "resume_valid")
    recovered = base.run_host([], previous_state=failed)
    assert recovered["complete"] and recovered["trace"] == before["trace"]
    assert recovered["model_calls"] == 26 and recovered["tool_calls"] == 130
    assert recovered["final_repairs_used"] == 2 and len(sends) == 1


@pytest.mark.parametrize("field,value", [("raw", base.SYNTHETIC_KEY), ("requested_max_tokens", True),
    ("output_reserved_tokens", 0), ("input_tokens_upper_bound", -1), ("context_limited", "yes")])
def test_output_budget_rejects_untrusted_fields_and_invalid_counts(field, value):
    budget = {"version": 1, "requested_max_tokens": 393216, "max_output_tokens": 393216,
        "context_window": 1048576, "input_tokens_upper_bound": 8000, "output_reserved_tokens": 393216,
        "context_limited": False}
    budget[field] = value
    assert runtime._safe_output_budget(budget) is None


def test_saved_budget_cannot_claim_output_capacity_larger_than_shared_context():
    budget = {"version": 1, "requested_max_tokens": 10, "max_output_tokens": 1000,
        "context_window": 100, "input_tokens_upper_bound": 20, "output_reserved_tokens": 10,
        "context_limited": False}
    assert runtime._safe_output_budget(budget) is None
