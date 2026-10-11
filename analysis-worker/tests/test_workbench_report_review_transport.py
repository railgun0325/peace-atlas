"""Opt-in final source-review transport recovery; fixture HTTP only."""
import asyncio
import copy
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from app.workbench import query_plan, report_generation as generation
from app.workbench.pipeline import PipelineError
from test_workbench_report_generation import ACCEPT, VALID, case, run

NATIVE_CLIENT = httpx.AsyncClient


@pytest.fixture(autouse=True)
def fixture_configuration(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-private-key-not-real")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-flash")
    for name in ("DEEPSEEK_TIMEOUT_SECONDS", "DEEPSEEK_CONTEXT_WINDOW_TOKENS", "DEEPSEEK_MAX_OUTPUT_TOKENS"):
        monkeypatch.delenv(name, raising=False)


def install(monkeypatch, handler):
    client = NATIVE_CLIENT
    monkeypatch.setattr(query_plan.httpx, "AsyncClient", lambda **options: client(
        transport=httpx.MockTransport(handler), **options))
    delays = []
    async def delay(seconds):
        delays.append(seconds)
    monkeypatch.setattr(query_plan.asyncio, "sleep", delay)
    return delays


def success(value=None):
    return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
        "content": value if isinstance(value, str) else json.dumps(ACCEPT if value is None else value)}}]})


def call(**options):
    return asyncio.run(query_plan.configured_ai_json("Return JSON.", {"fixture": True},
        max_tokens=65536, **options))


@pytest.mark.parametrize("failure", ["empty_body", "invalid_json", "connect_error", "read_error", "write_error", "remote_protocol", "decoding_error", 408, 429, 500, 502, 503, 504])
def test_opt_in_recovers_same_request_with_safe_events(monkeypatch, failure):
    requests, events = [], []
    async def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 3:
            return success()
        if failure == "empty_body":
            return httpx.Response(200, content=b" \r\n")
        if failure == "invalid_json":
            return httpx.Response(200, text="bad fixture-private-key-not-real https://private.invalid")
        errors = {"connect_error": httpx.ConnectError, "read_error": httpx.ReadError,
            "write_error": httpx.WriteError, "remote_protocol": httpx.RemoteProtocolError,
            "decoding_error": httpx.DecodingError}
        if failure in errors:
            raise errors[failure]("fixture-private-key-not-real", request=request)
        return httpx.Response(failure, text="fixture-private-key-not-real")
    delays = install(monkeypatch, handler)
    assert call(transport_retry=True, transport_event=lambda kind, value: events.append((kind, copy.deepcopy(value)))) == ACCEPT
    assert len(requests) == 3 and requests[0] == requests[1] == requests[2]
    assert delays == [0.5, 1.0]
    assert [kind for kind, _ in events] == ["attempt", "failure", "attempt", "failure", "attempt", "complete"]
    encoded = json.dumps(events)
    assert "fixture-private-key-not-real" not in encoded and "private.invalid" not in encoded
    assert all(row["body_retained"] is False for kind, row in events if kind == "failure")


def test_default_remains_one_request_and_diagnoses_outer_parse(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, text="non JSON")
    delays = install(monkeypatch, handler)
    with pytest.raises(PipelineError) as caught:
        call()
    assert len(requests) == 1 and delays == []
    assert caught.value.ai_transport_diagnostic["code"] == "invalid_json"


@pytest.mark.parametrize("failure", [400, 401, 402, 403, 404, 413, 422, "read_timeout", "outer_shape", "inner_json", "length"])
def test_non_transient_or_parsed_response_failure_never_retries(monkeypatch, failure):
    requests, events = [], []
    def handler(request):
        requests.append(request)
        if isinstance(failure, int):
            return httpx.Response(failure, text="fixture-private-key-not-real")
        if failure == "read_timeout":
            raise httpx.ReadTimeout("fixture-private-key-not-real", request=request)
        if failure == "outer_shape":
            return httpx.Response(200, json={"choices": []})
        if failure == "inner_json":
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "bad JSON"}}]})
        return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": ""}}]})
    delays = install(monkeypatch, handler)
    with pytest.raises(PipelineError) as caught:
        call(transport_retry=True, transport_event=lambda kind, value: events.append((kind, value)))
    assert len(requests) == 1 and delays == []
    assert "fixture-private-key-not-real" not in str(caught.value)
    if failure == "outer_shape":
        assert caught.value.ai_transport_diagnostic["code"] == "outer_shape"
    if failure == "inner_json":
        assert caught.value.code == "ai_json_syntax"


def test_three_non_json_attempts_are_bounded_and_hash_only(monkeypatch):
    raw = b"fixture-private-key-not-real invalid response"
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, content=raw)
    install(monkeypatch, handler)
    with pytest.raises(PipelineError) as caught:
        call(transport_retry=True)
    assert len(requests) == 3
    detail = caught.value.ai_transport_diagnostic
    assert detail["attempt"] == detail["max_attempts"] == 3
    assert detail["body_sha256"] == hashlib.sha256(raw).hexdigest()
    assert detail["body_bytes"] == len(raw) and detail["body_retained"] is False


@pytest.mark.parametrize("error_type", [httpx.ReadError, asyncio.TimeoutError])
def test_callback_failure_is_not_transport_retry_and_never_sends(monkeypatch, error_type):
    requests = []
    install(monkeypatch, lambda request: requests.append(request) or success())
    error = error_type("fixture callback failure")
    async def event(kind, value):
        raise error
    with pytest.raises(error_type) as caught:
        call(transport_retry=True, transport_event=event)
    assert caught.value is error and requests == []


def test_cancellation_stops_without_retry(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        raise asyncio.CancelledError()
    delays = install(monkeypatch, handler)
    with pytest.raises(asyncio.CancelledError):
        call(transport_retry=True)
    assert len(requests) == 1 and delays == []


def test_shared_deadline_not_restarted_after_backoff(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(query_plan, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(503)
    install(monkeypatch, handler)
    async def delay(seconds):
        clock[0] = 2.0
    monkeypatch.setattr(query_plan.asyncio, "sleep", delay)
    with pytest.raises(PipelineError, match="超时"):
        call(transport_retry=True, timeout_limit=1)
    assert len(requests) == 1


def test_final_source_review_recovery_retains_one_draft_and_complete_cache(monkeypatch):
    modes, requests, checkpoints = [], [], []
    def handler(request):
        payload = json.loads(request.content)
        data = json.loads(payload["messages"][1]["content"])
        mode = "review" if "candidate_report" in data else "generation"
        modes.append(mode)
        requests.append(payload)
        if mode == "generation":
            return success(VALID)
        if modes.count("review") <= 2:
            return httpx.Response(503, text="fixture-private-key-not-real")
        return success()
    install(monkeypatch, handler)
    result = run(case(), checkpoints=checkpoints)
    assert modes == ["generation", "review", "review", "review"]
    state = result["state"]
    assert state["complete"] and len(state["attempts"]) == 1 and state["draft"] == VALID
    attempt = state["attempts"][0]["source_review_attempts"][0]
    assert attempt["complete"] and len(attempt["transport_attempts"]) == 3
    assert all(row["request_reserved"] is True for row in attempt["transport_attempts"])
    assert sum(row.get("complete") is True for row in attempt["transport_attempts"]) == 1
    assert requests[1] == requests[2] == requests[3]
    assert "fixture-private-key-not-real" not in json.dumps(state)
    parent = copy.deepcopy(state)
    cached = run(case(), previous=state)
    assert modes == ["generation", "review", "review", "review"] and cached["state"]["complete"]
    assert state == parent


def test_exhausted_final_review_preserves_paid_candidate_and_review_only_resume(monkeypatch):
    modes, checkpoints = [], []
    def failed(request):
        data = json.loads(json.loads(request.content)["messages"][1]["content"])
        mode = "review" if "candidate_report" in data else "generation"
        modes.append(mode)
        return success(VALID) if mode == "generation" else httpx.Response(503)
    install(monkeypatch, failed)
    with pytest.raises(PipelineError, match="503"):
        run(case(), checkpoints=checkpoints)
    held = copy.deepcopy(checkpoints[-1])
    unchanged_parent = copy.deepcopy(held)
    assert held["phase"] == "review_pending" and not held["complete"] and held["draft"] == VALID
    assert modes == ["generation", "review", "review", "review"]
    saved = held["attempts"][0]["source_review_attempts"]
    assert len(saved) == 1 and len(saved[0]["transport_attempts"]) == 3
    assert saved[0]["failure_diagnostic"]["transport_code"] == "http_status"
    def resumed(request):
        data = json.loads(json.loads(request.content)["messages"][1]["content"])
        assert data["candidate_report"] == VALID
        modes.append("review")
        return success()
    install(monkeypatch, resumed)
    result = run(case(), previous=held)
    assert result["state"]["complete"] and len(result["state"]["attempts"]) == 1
    assert len(result["state"]["attempts"][0]["source_review_attempts"]) == 2
    assert modes == ["generation", "review", "review", "review", "review"]
    assert held == unchanged_parent


def test_failure_checkpoint_error_stops_recovery_without_second_http(monkeypatch):
    requests = []
    install(monkeypatch, lambda request: requests.append(request) or httpx.Response(503))
    error = httpx.ReadError("fixture checkpoint failure")
    def event(kind, value):
        if kind == "failure":
            raise error
    with pytest.raises(httpx.ReadError) as caught:
        call(transport_retry=True, transport_event=event)
    assert caught.value is error and len(requests) == 1


def test_actual_transport_clock_expiration_does_not_retry(monkeypatch):
    requests = []
    async def handler(request):
        requests.append(request)
        await asyncio.Future()
    install(monkeypatch, handler)
    with pytest.raises(PipelineError, match="超时") as caught:
        call(transport_retry=True, timeout_limit=0.01)
    assert len(requests) == 1 and caught.value.ai_transport_diagnostic["code"] == "timeout"


@pytest.mark.parametrize("body", [[], {"choices": [{"finish_reason": "stop"}]}, {"choices": {"0": {}}},
    {"choices": [{"finish_reason": {}, "message": {"content": "{}"}}]},
    {"choices": [{"finish_reason": [], "message": {"content": "{}"}}]},
    {"choices": [{"finish_reason": "stop", "message": {"content": []}}]}])
def test_parsed_outer_schema_errors_have_fixed_classification(monkeypatch, body):
    requests = []
    install(monkeypatch, lambda request: requests.append(request) or httpx.Response(200, json=body))
    with pytest.raises(PipelineError) as caught:
        call(transport_retry=True)
    assert len(requests) == 1 and caught.value.ai_transport_diagnostic["code"] == "outer_shape"


def test_missing_finish_reason_keeps_original_unfinished_gate(monkeypatch):
    requests = []
    install(monkeypatch, lambda request: requests.append(request) or httpx.Response(200, json={"choices": [{}]}))
    with pytest.raises(PipelineError, match="未完整结束") as caught:
        call(transport_retry=True)
    assert len(requests) == 1 and caught.value.ai_diagnostic["finish_reason"] == "other"


def test_source_review_reservation_saved_before_each_http_send(monkeypatch):
    checkpoints, modes = [], []
    def handler(request):
        data = json.loads(json.loads(request.content)["messages"][1]["content"])
        if "candidate_report" not in data:
            modes.append("generation")
            return success(VALID)
        latest = checkpoints[-1]["attempts"][-1]["source_review_attempts"][-1]
        reservations = latest["transport_attempts"]
        assert len(reservations) == modes.count("review") + 1
        assert reservations[-1]["request_reserved"] is True and reservations[-1]["complete"] is False
        modes.append("review")
        return httpx.Response(503) if modes.count("review") == 1 else success()
    install(monkeypatch, handler)
    assert run(case(), checkpoints=checkpoints)["state"]["complete"]
    assert modes == ["generation", "review", "review"]


def test_source_review_cancellation_after_transient_keeps_draft_and_send_history(monkeypatch):
    checkpoints, modes = [], []
    def handler(request):
        data = json.loads(json.loads(request.content)["messages"][1]["content"])
        mode = "review" if "candidate_report" in data else "generation"
        modes.append(mode)
        if mode == "generation":
            return success(VALID)
        if modes.count("review") == 1:
            return httpx.Response(503)
        raise asyncio.CancelledError()
    install(monkeypatch, handler)
    with pytest.raises(asyncio.CancelledError):
        run(case(), checkpoints=checkpoints)
    held = checkpoints[-1]
    assert held["draft"] == VALID and held["phase"] == "review_pending" and not held["complete"]
    row = held["attempts"][-1]["source_review_attempts"][-1]
    assert not row["complete"] and len(row["transport_attempts"]) == 2
    assert row["transport_attempts"][0]["failure_diagnostic"]["http_status"] == 503
    assert row["transport_attempts"][-1]["complete"] is False
    assert modes == ["generation", "review", "review"]


def test_checkpoint_consumed_deadline_prevents_first_http(monkeypatch):
    clock, requests = [0.0], []
    monkeypatch.setattr(query_plan, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    install(monkeypatch, lambda request: requests.append(request) or success())
    def event(kind, value):
        if kind == "attempt":
            clock[0] = 2.0
    with pytest.raises(PipelineError, match="超时"):
        call(transport_retry=True, timeout_limit=1, transport_event=event)
    assert requests == []


@pytest.mark.parametrize("option", [{"transport_retry": 1}, {"transport_retry": "yes"}, {"transport_event": {}}])
def test_invalid_opt_in_options_never_send(monkeypatch, option):
    requests = []
    install(monkeypatch, lambda request: requests.append(request) or success())
    with pytest.raises(PipelineError, match="预算无效"):
        call(**option)
    assert not requests
