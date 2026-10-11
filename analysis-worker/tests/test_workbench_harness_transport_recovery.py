"""Synthetic wire failures plus real isolated stdio; no live AI or user data."""
import asyncio
import copy
import hashlib
import json

import httpx
import pytest

from app.workbench import harness_runtime as runtime
from app.workbench.harness_evidence import EvidenceGateway
from app.workbench.pipeline import PipelineError
from test_workbench_harness_runtime import (
    CAPACITY, QUOTE, REASONING, SYNTHETIC_KEY, fake_sidecar, final_findings,
    payload, request_model, response, run_host, snapshot, transport,
)


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", SYNTHETIC_KEY)
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://synthetic-transport.example/v1")
    monkeypatch.setenv("DEEPSEEK_MODEL", "fixture-model")


@pytest.mark.parametrize("failure", ["empty_body", "invalid_json", "read_error", "remote_protocol", "503", "429"])
def test_one_transient_wire_failure_recovers_same_pending_request(monkeypatch, failure):
    gateway, requests = EvidenceGateway(*snapshot()), []

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) > 1:
            return httpx.Response(200, json=response())
        if failure == "empty_body":
            return httpx.Response(200, content=b" \r\n")
        if failure == "invalid_json":
            return httpx.Response(200, text="not JSON " + SYNTHETIC_KEY + REASONING)
        if failure == "read_error":
            raise httpx.ReadError(SYNTHETIC_KEY, request=request)
        if failure == "remote_protocol":
            raise httpx.RemoteProtocolError(REASONING, request=request)
        return httpx.Response(int(failure), text=SYNTHETIC_KEY)

    transport(monkeypatch, handler)
    result = request_model(payload(gateway), gateway)
    assert result["choices"][0]["finish_reason"] == "stop"
    assert len(requests) == 2 and requests[0] == requests[1]


def test_repeated_non_json_is_bounded_hash_only_and_never_success(monkeypatch):
    gateway, calls = EvidenceGateway(*snapshot()), []
    raw = ("invalid body " + SYNTHETIC_KEY + REASONING).encode()

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=raw, headers={"content-type": "text/html"})

    transport(monkeypatch, handler)
    with pytest.raises(PipelineError, match="响应无效") as caught:
        request_model(payload(gateway), gateway)
    diagnostic = caught.value.harness_transport_diagnostic
    assert len(calls) == 3 and diagnostic["attempt"] == diagnostic["max_attempts"] == 3
    assert diagnostic["code"] == "invalid_json" and diagnostic["http_status"] == 200
    assert diagnostic["body_bytes"] == len(raw)
    assert diagnostic["body_sha256"] == hashlib.sha256(raw).hexdigest()
    assert diagnostic["content_type"] == "html" and diagnostic["body_retained"] is False
    assert runtime._safe_transport_diagnostic(diagnostic) == diagnostic
    assert SYNTHETIC_KEY not in json.dumps(diagnostic) and REASONING not in json.dumps(diagnostic)


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 413, 422])
def test_permanent_http_failure_is_never_retried_and_diagnosed(monkeypatch, status):
    gateway, calls = EvidenceGateway(*snapshot()), []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text=SYNTHETIC_KEY)

    transport(monkeypatch, handler)
    with pytest.raises(PipelineError) as caught:
        request_model(payload(gateway), gateway)
    diagnostic = caught.value.harness_transport_diagnostic
    assert len(calls) == 1 and diagnostic["http_status"] == status
    assert diagnostic["code"] == "http_status" and not diagnostic["retryable"]
    assert SYNTHETIC_KEY not in json.dumps(diagnostic) and SYNTHETIC_KEY not in str(caught.value)


def tool_then_model_sidecar(monkeypatch, tmp_path):
    directory, processes = fake_sidecar(monkeypatch, tmp_path, "model")
    script = r'''
import readline from 'node:readline';
const lines=readline.createInterface({input:process.stdin});
const emit=value=>process.stdout.write(JSON.stringify(value)+'\n');
let start;
const tool=(id,name,args)=>emit({kind:'tool_request',id,name,args,run_binding:start.run_binding});
lines.on('line',line=>{
  const msg=JSON.parse(line);
  if(msg.kind==='cancel'){process.exit(0);return;}
  if(msg.kind==='start'){
    start=msg;
    emit({kind:'runtime_roster',runtime:'0.2.0-rc.2',names:start.tool_schemas.map(row=>row.name)});
    tool('read-once','read_evidence',{citation_id:'S001'});return;
  }
  if(msg.kind==='tool_response' && msg.id==='read-once'){
    tool('check-once','check_citation',{citation_id:'S001',exact_quote:__QUOTE__});return;
  }
  if(msg.kind==='tool_response' && msg.id==='check-once'){
    emit({kind:'model_request',id:'pending-model',run_binding:start.run_binding,
      request:{model:start.model.id,messages:[{role:'user',content:'Use saved tool results'}],
        tools:start.tool_schemas.map(row=>({type:'function',function:row})),max_tokens:2048,tool_choice:'auto'}});return;
  }
  if(msg.kind==='model_response'){
    emit({kind:'complete',session_id:start.session_id,run_binding:start.run_binding,
      runtime_closed:true,finish_reason:'stop',final_response:msg.response.choices[0].message.content});return;
  }
});
'''
    (directory / "sidecar.mjs").write_text(script.replace("__QUOTE__", json.dumps(QUOTE)), encoding="utf-8")
    return processes


@pytest.mark.parametrize("failed_attempts", [1, 2])
def test_host_recovery_reserves_every_attempt_without_replaying_completed_tools(monkeypatch, tmp_path, failed_attempts):
    processes = tool_then_model_sidecar(monkeypatch, tmp_path)
    checkpoints, observed = [], []

    def handler(request):
        observed.append((checkpoints[-1]["model_calls"], checkpoints[-1]["tool_calls"], copy.deepcopy(checkpoints[-1]["trace"])))
        if len(observed) == 1:
            if failed_attempts == 1:
                raise httpx.RemoteProtocolError(SYNTHETIC_KEY, request=request)
            return httpx.Response(200, content=b"")
        if len(observed) <= failed_attempts:
            return httpx.Response(200, content=b"broken JSON " + SYNTHETIC_KEY.encode())
        return httpx.Response(200, json=response(content=json.dumps(final_findings(), ensure_ascii=False)))

    transport(monkeypatch, handler)
    result = run_host(checkpoints)
    assert result["complete"] and result["model_calls"] == result["transport_attempts"] == failed_attempts + 1
    assert result["transport_retries"] == failed_attempts and result["tool_calls"] == 2
    assert [(row[0], row[1]) for row in observed] == [(index, 2) for index in range(1, failed_attempts + 2)]
    assert all(row[2] == result["trace"] for row in observed)
    assert len(processes) == 1 and all(process.returncode == 0 for process in processes)
    assert [row["code"] for row in result["transport_diagnostics"]] == (["remote_protocol"] if failed_attempts == 1 else ["empty_body", "invalid_json"])
    assert SYNTHETIC_KEY not in json.dumps(checkpoints) and REASONING not in json.dumps(checkpoints)


def test_host_attempt_budget_stops_retry_before_another_http_request(monkeypatch, tmp_path):
    processes = tool_then_model_sidecar(monkeypatch, tmp_path)
    checkpoints, observed = [], []

    def handler(request):
        observed.append(checkpoints[-1]["model_calls"])
        return httpx.Response(200, content=b"")

    transport(monkeypatch, handler)
    with pytest.raises(PipelineError, match="决策轮数上限"):
        run_host(checkpoints, max_model_calls=2)
    assert observed == [1, 2] and checkpoints[-1]["model_calls"] == 2
    assert checkpoints[-1]["complete"] is False and checkpoints[-1]["tool_calls"] == 2
    assert len(checkpoints[-1]["transport_diagnostics"]) == 2
    assert all(process.returncode is not None for process in processes)


def test_total_transport_deadline_is_not_reset_on_retry(monkeypatch):
    gateway, calls = EvidenceGateway(*snapshot()), []

    async def handler(request):
        calls.append(request)
        if len(calls) == 1:
            await asyncio.sleep(0.035)
            return httpx.Response(200, content=b"")
        await asyncio.sleep(0.08)
        return httpx.Response(200, json=response())

    transport(monkeypatch, handler)
    with pytest.raises(PipelineError, match="超时") as caught:
        request_model(payload(gateway), gateway, seconds=0.06)
    assert 1 <= len(calls) <= 2
    assert caught.value.harness_transport_diagnostic["code"] == "timeout"


@pytest.mark.parametrize("change", ["extra", "private_code", "hash", "bool_count", "raw_body"])
def test_untrusted_transport_diagnostic_is_not_checkpoint_safe(monkeypatch, change):
    gateway = EvidenceGateway(*snapshot())
    transport(monkeypatch, lambda request: httpx.Response(401, text=SYNTHETIC_KEY))
    with pytest.raises(PipelineError) as caught:
        request_model(payload(gateway), gateway)
    diagnostic = copy.deepcopy(caught.value.harness_transport_diagnostic)
    if change == "extra": diagnostic["credentials"] = SYNTHETIC_KEY
    elif change == "private_code": diagnostic["code"] = SYNTHETIC_KEY
    elif change == "hash": diagnostic["body_sha256"] = SYNTHETIC_KEY
    elif change == "bool_count": diagnostic["body_bytes"] = True
    else: diagnostic["body_retained"] = True
    assert runtime._safe_transport_diagnostic(diagnostic) is None


@pytest.mark.parametrize("failure", ["choices", "length", "unknown_tool", "unsafe_output", "usage"])
def test_valid_json_with_invalid_model_semantics_is_never_transport_retried(monkeypatch, failure):
    gateway, calls = EvidenceGateway(*snapshot()), []
    body = response()
    if failure == "choices": body["choices"] = []
    elif failure == "length": body["choices"][0]["finish_reason"] = "length"
    elif failure == "unknown_tool":
        body = response(reason="tool_calls", calls=[{"id": "unsafe-call", "type": "function",
            "function": {"name": "shell", "arguments": "{}"}}])
    elif failure == "unsafe_output": body["choices"][0]["message"]["content"] = SYNTHETIC_KEY
    else: body["usage"]["completion_tokens"] = 9000

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=body)

    transport(monkeypatch, handler)
    with pytest.raises(PipelineError) as caught:
        request_model(payload(gateway), gateway)
    assert len(calls) == 1 and hasattr(caught.value, "harness_response_diagnostic")
    assert not hasattr(caught.value, "harness_transport_diagnostic")


def test_cancel_during_recovery_does_not_start_another_attempt(monkeypatch):
    gateway, requests, events = EvidenceGateway(*snapshot()), [], []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, content=b"")

    async def checkpoint(kind, value):
        events.append((kind, value))
        if kind == "failure":
            raise asyncio.CancelledError()

    transport(monkeypatch, handler)
    with pytest.raises(asyncio.CancelledError):
        request_model(payload(gateway), gateway, _transport_event=checkpoint)
    assert len(requests) == 1 and [event[0] for event in events] == ["attempt", "failure"]


@pytest.mark.parametrize("error_type", [OSError, httpx.ReadError, httpx.ReadTimeout, asyncio.TimeoutError])
def test_pre_attempt_checkpoint_failure_prevents_http(monkeypatch, error_type):
    gateway, requests = EvidenceGateway(*snapshot()), []
    transport(monkeypatch, lambda request: requests.append(request) or httpx.Response(200, json=response()))

    async def checkpoint(kind, value):
        raise error_type("synthetic checkpoint unavailable")

    with pytest.raises(error_type) as caught:
        request_model(payload(gateway), gateway, _transport_event=checkpoint)
    assert not requests
    assert not hasattr(caught.value, "harness_transport_diagnostic")


def test_completed_review_batch_preserves_attempt_history_and_counts(monkeypatch, tmp_path):
    from app.workbench import automated_review as auto
    from app.workbench.review import digest, snapshot as review_snapshot, start_review
    from app.workbench.store import Store
    from test_workbench_automated_review import seed

    store = Store(tmp_path / "review-accounting.sqlite3")
    seed(store)
    rows = review_snapshot(store, ["accounts"])
    job = start_review(store, {"kinds": ["accounts"], "batch_size": 1, "use_public_network": False}, expected_digest=digest(rows))
    store.update_job(job["id"], status="running")
    diagnostic = runtime._transport_diagnostic(httpx.Response(200, content=b""), "empty_body", 1, retryable=True)

    async def harness(items, context, reviews, **options):
        gateway = options["_gateway"]
        decisions = [{"kind": row["kind"], "id": row["id"], "outcome": "unknown",
            "note": "公开证据不足，保持未知", "evidence": []} for row in gateway.rows]
        state = {"findings": options["_validator"]({"decisions": decisions}, gateway, []), "trace": [],
            "model_calls": 2, "tool_calls": 0, "transport_attempts": 2, "transport_retries": 1,
            "transport_diagnostics": [diagnostic]}
        await options["checkpoint"](state)
        return state

    async def checkpoint(value):
        store.update_job(job["id"], result=value)

    monkeypatch.setattr(auto, "run_evidence_harness", harness)
    try:
        result = asyncio.run(auto.run_auto_review(store, store.get_job(job["id"]), checkpoint=checkpoint, cancelled=lambda: False))
        assert result["completed"] == result["total"] == 1
        assert result["transport_attempts"] == result["model_calls"] == 2 and result["transport_retries"] == 1
        assert result["batches"][0]["transport_diagnostics"] == [diagnostic]
        assert result["batches"][0]["transport_attempts"] == 2
        assert "current_harness" not in result
    finally:
        store.close()


def test_old_checkpoint_transport_baseline_and_saved_tools_survive_failure(monkeypatch, tmp_path):
    fake_sidecar(monkeypatch, tmp_path, "success")
    legacy = run_host([])
    legacy.update(complete=False, model_calls=7)
    for key in ("transport_attempts", "transport_retries", "transport_diagnostics"):
        legacy.pop(key, None)
    original_trace = copy.deepcopy(legacy["trace"])
    fake_sidecar(monkeypatch, tmp_path, "model")
    transport(monkeypatch, lambda request: httpx.Response(401, text=SYNTHETIC_KEY))
    checkpoints = []
    with pytest.raises(PipelineError, match="HTTP 401"):
        run_host(checkpoints, previous_state=legacy)
    state = checkpoints[-1]
    assert state["model_calls"] == state["transport_attempts"] == 8
    assert state["transport_retries"] == 0 and state["trace"] == original_trace
    assert state["tool_calls"] == 2 and not state["complete"]
