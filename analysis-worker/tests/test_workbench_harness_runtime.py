"""Host protocol fixtures: synthetic HTTP and real, isolated Node stdio.

These tests do not establish real DSH SDK, model or platform acceptance.
No real key, user profile or application database is opened.
"""
import asyncio
import copy
import json
from pathlib import Path
import shutil

import httpx
import pytest

from app.workbench import harness_runtime as runtime
from app.workbench.ai_capacity import ModelCapacity
from app.workbench.harness_evidence import EvidenceGateway
from app.workbench.pipeline import PipelineError


SYNTHETIC_KEY = "synthetic-host-protocol-key-DO-NOT-PERSIST"
REASONING = "synthetic-private-reasoning-DO-NOT-PERSIST"
QUOTE = "会议仍属计划，尚未举行。"
CAPACITY = ModelCapacity("fixture-model", 1000000, 32768, "test_fixture")


def test_actual_audited_runtime_manifest_matches_host_capability(monkeypatch):
    directory = Path(__file__).resolve().parents[2] / 'desktop' / 'harness'
    monkeypatch.setattr(runtime, 'runtime_paths', lambda: (directory, 'isolated-node-fixture'))
    assert runtime.capability()['available'] is True


@pytest.mark.parametrize('changed', [{'dsh_sdk_version': '0.0.1-rc.1'},
    {'dsh_runtime_version': '0.1.5-rc.2'}, {'protocol': 2},
    {'dsh_version': runtime.DSH_VERSION, 'dsh_runtime_version': None, 'dsh_sdk_version': None}])
def test_runtime_client_protocol_mismatch_is_unavailable(tmp_path, monkeypatch, changed):
    manifest = {'dsh_runtime_version': runtime.DSH_VERSION, 'dsh_sdk_version': runtime.DSH_VERSION, 'protocol': 1}
    manifest.update(changed)
    (tmp_path / 'runtime-manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    monkeypatch.setattr(runtime, 'runtime_paths', lambda: (tmp_path, 'isolated-node-fixture'))
    assert runtime.capability()['available'] is False


@pytest.fixture(autouse=True)
def synthetic_configuration(monkeypatch):
    # Replace, rather than inspect, actual environment credentials.
    monkeypatch.setenv("DEEPSEEK_API_KEY", SYNTHETIC_KEY)
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://host-route.example/v1")
    monkeypatch.setenv("DEEPSEEK_MODEL", "fixture-model")
    monkeypatch.delenv("DEEPSEEK_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.delenv("DEEPSEEK_MAX_OUTPUT_TOKENS", raising=False)


def snapshot():
    items = [{"citation_id": "S001", "title": "公开资料", "content": "公开资料：" + QUOTE,
        "url": "https://public.example/source", "type": "news", "account_key": "twitter:alpha"}]
    context = {"run_id": "fixture-run", "run_revision": 4, "research_goal": "比较计划与实际发生",
        "accounts": [{"account_key": "twitter:alpha", "status": "candidate"}]}
    return items, context, []


def payload(gateway):
    return {"model": "fixture-model", "messages": [{"role": "user", "content": "读取保存证据"}],
        "tools": gateway.tools, "tool_choice": "auto", "max_tokens": 2048}


def response(*, content="{}", reason="stop", calls=None, **extra):
    message = {"role": "assistant", "content": content, **extra}
    if calls is not None:
        message["tool_calls"] = calls
    return {"choices": [{"message": message, "finish_reason": reason}],
        "usage": {"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300}}


def tool_response():
    return response(reason="tool_calls", calls=[{"id":"owned-call-1", "type":"function",
        "function":{"name":"list_evidence", "arguments":'{"limit":10}'}}], role="assistant",
        reasoning_content=REASONING)


@pytest.mark.parametrize('indexed',[False,True])
def test_standard_nonstream_tool_response_is_accepted_with_reasoning(monkeypatch,indexed):
    gateway = EvidenceGateway(*snapshot())
    body=tool_response()
    if indexed: body['choices'][0]['message']['tool_calls'][0]['index']=0
    transport(monkeypatch, lambda request: httpx.Response(200, json=body))
    result = request_model(payload(gateway), gateway)
    assert result["choices"][0]["finish_reason"] == "tool_calls"
    assert result["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "list_evidence"
    assert 'index' not in result["choices"][0]["message"]["tool_calls"][0]


@pytest.mark.parametrize('index',[-1,True,1,'0',0.5])
def test_optional_tool_index_must_be_exact_integer_ordinal(monkeypatch,index):
    gateway=EvidenceGateway(*snapshot())
    body=tool_response()
    body['choices'][0]['message']['tool_calls'][0]['index']=index
    transport(monkeypatch,lambda request:httpx.Response(200,json=body))
    with pytest.raises(PipelineError) as caught: request_model(payload(gateway),gateway)
    assert caught.value.harness_response_diagnostic['code']=='tool_call_fields'


def test_multiple_optional_indices_are_validated_and_normalized(monkeypatch):
    gateway=EvidenceGateway(*snapshot())
    body=tool_response()
    first=body['choices'][0]['message']['tool_calls'][0]
    first['index']=0
    second=copy.deepcopy(first)
    second.update(id='owned-call-2',index=1)
    body['choices'][0]['message']['tool_calls'].append(second)
    transport(monkeypatch,lambda request:httpx.Response(200,json=body))
    result=request_model(payload(gateway),gateway)
    assert len(result['choices'][0]['message']['tool_calls'])==2
    assert all('index' not in call for call in result['choices'][0]['message']['tool_calls'])
    assert first['index']==0 and second['index']==1


@pytest.mark.parametrize('case,expected', [
    ('empty_choices','response_shape'), ('length','unfinished'), ('extra_message','message_fields'),
    ('secret_public','sensitive_public_output'), ('many_calls','tool_finish_mismatch'),
    ('call_index','tool_call_fields'), ('unknown_name','tool_call_name'),
    ('object_arguments','tool_call_arguments'), ('duplicate_id','tool_call_fields'),
    ('usage_overrun','usage_bounds')])
def test_model_failure_diagnostics_have_only_fixed_enums_and_counts(monkeypatch, case, expected):
    gateway = EvidenceGateway(*snapshot())
    body = tool_response()
    message = body['choices'][0]['message']
    if case == 'empty_choices': body['choices'] = []
    elif case == 'length': body['choices'][0]['finish_reason'] = 'length'
    elif case == 'extra_message': message['private_unknown_' + SYNTHETIC_KEY] = REASONING
    elif case == 'secret_public': message['content'] = SYNTHETIC_KEY
    elif case == 'many_calls': message['tool_calls'] = [copy.deepcopy(message['tool_calls'][0]) for _ in range(runtime.MAX_TOOL_CALLS_PER_RESPONSE+1)]
    elif case == 'call_index': message['tool_calls'][0]['index'] = 1
    elif case == 'unknown_name': message['tool_calls'][0]['function']['name'] = 'shell'
    elif case == 'object_arguments': message['tool_calls'][0]['function']['arguments'] = {'limit':10}
    elif case == 'duplicate_id': message['tool_calls'].append(copy.deepcopy(message['tool_calls'][0]))
    else: body['usage']['completion_tokens'] = 3000
    transport(monkeypatch, lambda request: httpx.Response(200, json=body))
    with pytest.raises(PipelineError) as caught:
        request_model(payload(gateway), gateway)
    diagnostic = caught.value.harness_response_diagnostic
    assert diagnostic['code'] == expected
    assert runtime._safe_response_diagnostic(diagnostic) == diagnostic
    encoded = json.dumps(diagnostic)
    assert SYNTHETIC_KEY not in encoded and REASONING not in encoded and 'owned-call-1' not in encoded
    assert diagnostic['tool_call_has_index'] is (case == 'call_index')
    assert diagnostic['tool_arguments_are_objects'] is (case == 'object_arguments')


@pytest.mark.parametrize('count',[8,9,10,64])
def test_parallel_tool_response_accepts_each_valid_call_through_explicit_bound(monkeypatch,count):
    gateway=EvidenceGateway(*snapshot())
    body=tool_response()
    seed=body['choices'][0]['message']['tool_calls'][0]
    body['choices'][0]['message']['tool_calls']=[{**copy.deepcopy(seed),'id':'parallel-'+str(i),'index':i} for i in range(count)]
    transport(monkeypatch,lambda request:httpx.Response(200,json=body))
    answer=request_model(payload(gateway),gateway)
    calls=answer['choices'][0]['message']['tool_calls']
    assert len(calls)==count and len({c['id'] for c in calls})==count
    assert all('index' not in c and c['function']['name']=='list_evidence' for c in calls)


def test_parallel_response_still_rejects_duplicate_id_inside_large_batch(monkeypatch):
    gateway=EvidenceGateway(*snapshot())
    body=tool_response()
    seed=body['choices'][0]['message']['tool_calls'][0]
    body['choices'][0]['message']['tool_calls']=[{**copy.deepcopy(seed),'id':'parallel-'+str(i),'index':i} for i in range(10)]
    body['choices'][0]['message']['tool_calls'][-1]['id']='parallel-0'
    transport(monkeypatch,lambda request:httpx.Response(200,json=body))
    with pytest.raises(PipelineError) as caught:request_model(payload(gateway),gateway)
    assert caught.value.harness_response_diagnostic['code']=='tool_call_fields'


@pytest.mark.parametrize('field,value', [('code',[]), ('code','private_secret'), ('finish_reason',{}),
    ('requested_max_tokens',True), ('completion_tokens',True), ('tool_call_count',-1),
    ('tool_call_has_index',1), ('extra_message_field_count','credentials')])
def test_untrusted_diagnostic_fields_are_not_persistable(field, value):
    diagnostic = runtime._response_diagnostic(tool_response(), 'tool_call_fields', 2048)
    diagnostic[field] = value
    assert runtime._safe_response_diagnostic(diagnostic) is None


def transport(monkeypatch, handler):
    native = httpx.AsyncClient
    monkeypatch.setattr(runtime.httpx, "AsyncClient", lambda **options:
        native(transport=httpx.MockTransport(handler), **options))


def request_model(value, gateway, **options):
    return asyncio.run(runtime.model_request(value, gateway=gateway, model="fixture-model",
        capacity=options.pop("capacity", CAPACITY), seconds=options.pop("seconds", 2), **options))


def final_findings():
    return {"findings": [{"claim": "保存材料仅说会议仍属计划。", "citation_ids": ["S001"],
        "quotes": [{"citation_id": "S001", "quote": QUOTE}]}],
        "focus_accounts": [{"account_key": "twitter:alpha", "reason": "保存来源主体", "citation_ids": ["S001"]}],
        "gaps": [{"question": "是否已实际举行仍需其他公开资料。", "citation_ids": []}],
        "next_steps": [{"account_key": "twitter:alpha", "reason": "继续核对已知公开来源", "citation_ids": ["S001"]}]}


def valid_trace(gateway, *, limit=12000):
    rows = []
    for name, args in [("read_evidence", {"citation_id": "S001", "limit": limit}),
            ("check_citation", {"citation_id": "S001", "exact_quote": QUOTE})]:
        rows.append({"name": name, "args": args, "result": gateway.call(name, args), "ok": True})
    return rows


def test_model_transport_keeps_host_route_exact_tools_and_synthetic_key(monkeypatch):
    gateway = EvidenceGateway(*snapshot())
    captured = []
    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=response(reasoning_content=REASONING))
    transport(monkeypatch, handler)
    answer = request_model(payload(gateway), gateway)
    assert len(captured) == 1
    assert str(captured[0].url) == "https://host-route.example/v1/chat/completions"
    assert captured[0].headers["Authorization"] == "Bearer " + SYNTHETIC_KEY
    wire = json.loads(captured[0].content)
    assert wire["tools"] == gateway.tools and wire["model"] == "fixture-model"
    assert wire["reasoning_effort"] == "high" and wire["temperature"] == 0.2
    assert answer["choices"][0]["message"]["reasoning_content"] == REASONING
    assert SYNTHETIC_KEY not in json.dumps(answer)


def test_model_tool_round_with_reasoning_toolcalls_and_tool_id_is_supported(monkeypatch):
    gateway = EvidenceGateway(*snapshot())
    value = payload(gateway)
    value["messages"] += [{"role": "assistant", "content": None, "reasoning_content": REASONING,
        "tool_calls": [{"id": "read-1", "type": "function", "function": {
            "name": "read_evidence", "arguments": '{"citation_id":"S001"}'}}]},
        {"role": "tool", "content": json.dumps(gateway.call("read_evidence", {"citation_id": "S001"}), ensure_ascii=False),
            "tool_call_id": "read-1"}]
    captured = []
    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=response())
    transport(monkeypatch, handler)
    assert request_model(value, gateway)["choices"][0]["finish_reason"] == "stop"
    assert captured[0]["messages"] == value["messages"]


@pytest.mark.parametrize("change", ["model", "extra_route", "extra_tool", "tool_schema", "tool_choice", "boolean_output", "oversized_output", "secret_content"])
def test_model_rejects_route_schema_budget_and_secret_before_http(monkeypatch, change):
    gateway = EvidenceGateway(*snapshot())
    value = payload(gateway)
    if change == "model": value["model"] = "unselected-model"
    elif change == "extra_route": value["base_url"] = "https://outside.example"
    elif change == "extra_tool": value["tools"].append({"type": "function", "function": {"name": "shell"}})
    elif change == "tool_schema": value["tools"][0]["function"]["parameters"]["additionalProperties"] = True
    elif change == "tool_choice": value["tool_choice"] = {"type": "function", "function": {"name": "shell"}}
    elif change == "boolean_output": value["max_tokens"] = True
    elif change == "oversized_output": value["max_tokens"] = 32769
    else: value["messages"][0]["content"] = SYNTHETIC_KEY
    def forbidden(request):
        raise AssertionError("Rejected model request reached HTTP")
    transport(monkeypatch, forbidden)
    with pytest.raises(PipelineError): request_model(value, gateway)


@pytest.mark.parametrize("failure,match", [("402", "余额不足"), ("503", "HTTP 503"), ("timeout", "超时"),
    ("unfinished", "未完整结束"), ("wrong_finish", "未完整结束"), ("secret_output", "无效"), ("malformed", "无效")])
def test_model_errors_become_bounded_pipeline_failures_without_secrets(monkeypatch, failure, match):
    gateway = EvidenceGateway(*snapshot())
    def handler(request):
        if failure in {"402", "503"}: return httpx.Response(int(failure), text=SYNTHETIC_KEY)
        if failure == "timeout": raise httpx.ReadTimeout(SYNTHETIC_KEY, request=request)
        if failure == "unfinished": return httpx.Response(200, json=response(reason="length"))
        if failure == "wrong_finish": return httpx.Response(200, json=response(reason="tool_calls"))
        if failure == "secret_output": return httpx.Response(200, json=response(content=SYNTHETIC_KEY))
        return httpx.Response(200, text="not JSON " + SYNTHETIC_KEY)
    transport(monkeypatch, handler)
    with pytest.raises(PipelineError, match=match) as caught: request_model(payload(gateway), gateway)
    assert SYNTHETIC_KEY not in str(caught.value)


def test_final_findings_only_accept_read_window_and_exact_checked_quotes():
    gateway = EvidenceGateway(*snapshot())
    original = final_findings()
    assert runtime.validate_findings(original, gateway, valid_trace(gateway)) == original
    with pytest.raises(PipelineError, match="实际读取的原文窗口"):
        runtime.validate_findings(original, gateway, valid_trace(gateway, limit=3))
    trace = valid_trace(gateway)
    trace[1]["result"]["valid"] = False
    with pytest.raises(PipelineError): runtime.validate_findings(original, gateway, trace)


@pytest.mark.parametrize("change", ["extra_field", "unknown_id", "duplicate_id", "unknown_account", "unread_id", "forged_quote", "extra_quote_field", "empty_findings_quote"])
def test_final_findings_unknown_scope_and_unchecked_quotes_fail_closed(change):
    items, context, reviews = snapshot()
    items.append({"citation_id": "S002", "content": "另一材料", "title": "另一材料", "type": "news"})
    gateway = EvidenceGateway(items, context, reviews)
    value, trace = final_findings(), valid_trace(gateway)
    if change == "extra_field": value["findings"][0]["verified"] = True
    elif change == "unknown_id": value["findings"][0]["citation_ids"] = ["S099"]
    elif change == "duplicate_id": value["findings"][0]["citation_ids"] *= 2
    elif change == "unknown_account": value["focus_accounts"][0]["account_key"] = "twitter:outside"
    elif change == "unread_id":
        value["findings"][0]["citation_ids"] = ["S002"]
        value["findings"][0]["quotes"] = [{"citation_id": "S002", "quote": "另一材料"}]
    elif change == "forged_quote": value["findings"][0]["quotes"][0]["quote"] = "已经举行"
    elif change == "extra_quote_field": value["findings"][0]["quotes"][0]["url"] = "https://outside.example"
    else: value["findings"][0]["quotes"] = []
    with pytest.raises(PipelineError): runtime.validate_findings(value, gateway, trace)


_SIDECAR = r'''
import readline from 'node:readline';
const mode = __MODE__;
const quote = __QUOTE__;
const result = __FINAL__;
const reasoning = __REASONING__;
const secret = __SECRET__;
const lines = readline.createInterface({input:process.stdin});
let start, phase=0, candidateRound=0;
const emit = value => process.stdout.write(JSON.stringify(value)+'\n');
const sendTool = (name,args,id='tool-'+(++phase),binding=start.run_binding) =>
  emit({kind:'tool_request',id,name,args,run_binding:binding});
const complete = () => emit({kind:'complete',session_id:start.session_id,
  run_binding:mode==='wrong_complete_binding'?{...start.run_binding,revision:999}:start.run_binding,
  runtime_closed:mode==='not_closed'?false:true,finish_reason:mode==='unfinished'?'length':'stop',
  final_response:mode==='final_fence'?'```json\n'+JSON.stringify(result)+'\n```':
    mode==='bad_final'?secret+' malformed reply':
    mode==='candidate_tamper'?JSON.stringify({...result,gaps:[]}):JSON.stringify(result)});
const candidate = () => {
  const value=JSON.parse(JSON.stringify(result));
  if(candidateRound===0 || mode==='candidate_repeat') value.findings[0].quotes=[{citation_id:'S001',unexpected:secret}];
  const id='candidate-'+(++candidateRound);
  const message={kind:'final_candidate',protocol:1,id,session_id:start.session_id,
    run_binding:mode==='candidate_wrong_binding'?{...start.run_binding,revision:999}:start.run_binding,
    final_response:JSON.stringify(value)};
  emit(message);
  if(mode==='candidate_duplicate')emit(message);
};
const finish = () => mode.startsWith('candidate_')?candidate():complete();
lines.on('line', line => {
  const msg = JSON.parse(line);
  if(msg.kind==='cancel'){process.exit(0);return;}
  if(msg.kind==='start'){
    start=msg;
    if(mode==='resume_context' && (!start.goal.includes('"saved_call_count":41')
      || !start.goal.includes('"unique_tool_results":2') || !start.goal.includes('"name":"read_evidence"'))) throw new Error('earlier saved read lost');
    if(process.env.DEEPSEEK_API_KEY || process.env.DEEPSEEK_BASE_URL ||
      process.env.APPDATA !== process.env.DSH_HOME || process.env.HOME !== process.env.DSH_HOME ||
      JSON.stringify(start).includes(secret)) throw new Error('isolation failed');
    if(mode==='no_roster'){sendTool('read_evidence',{citation_id:'S001'});return;}
    const names=start.tool_schemas.map(row=>row.name);
    if(mode==='bad_roster') names.push('shell');
    emit({kind:'runtime_roster',runtime:'0.2.0-rc.2',names});
    if(mode==='bad_roster')return;
    if(mode==='wait_cancel')return;
    if(mode==='abrupt_exit'){process.exit(0);return;}
    if(mode==='wrong_binding'){sendTool('read_evidence',{citation_id:'S001'},'wrong-1',{...start.run_binding,revision:999});return;}
    if(mode==='unknown_tool'){sendTool('shell-'+secret,{command:secret});return;}
    if(mode==='model'){
      emit({kind:'model_request',id:'model-1',run_binding:start.run_binding,request:{model:start.model.id,
        messages:[{role:'user',content:'read saved evidence'}],tools:start.tool_schemas.map(row=>({type:'function',function:row})),
        max_tokens:2048,tool_choice:'auto'}});return;
    }
    sendTool('read_evidence',{citation_id:'S001',...(mode==='unread_tail'?{limit:3}:{})},mode==='duplicate'?'duplicate-id':'read-1');return;
  }
  if(msg.kind==='model_response'){
    if(msg.id==='repair-model'){sendTool('read_evidence',{citation_id:'S001'},'repair-read');return;}
    emit({kind:'checkpoint',session_id:start.session_id,reasoning_content:reasoning,credentials:secret});
    sendTool('read_evidence',{citation_id:'S001'},'read-1');return;
  }
  if(msg.kind==='tool_response'){
    if(msg.id.startsWith('bulk-check-')){
      const count=Number(msg.id.slice('bulk-check-'.length));
      if(count<257)sendTool('check_citation',{citation_id:'S001',exact_quote:quote},'bulk-check-'+(count+1));
      else finish();return;
    }
    if(msg.id==='repair-read'){sendTool('check_citation',{citation_id:'S001',exact_quote:quote},'repair-check');return;}
    if(msg.id==='repair-check'){candidate();return;}
    if(mode==='unknown_tool'){process.exit(0);return;}
    if(msg.id==='read-1' || msg.id==='duplicate-id'){
      if(mode==='partial_exit'){process.exit(0);return;}
      sendTool('check_citation',{citation_id:'S001',exact_quote:quote},mode==='duplicate'?'duplicate-id':'check-1');return;
    }
    if(msg.id==='check-1'){
      if(mode==='unlimited_tools'){sendTool('check_citation',{citation_id:'S001',exact_quote:quote},'bulk-check-1');return;}
      emit({kind:'checkpoint',session_id:start.session_id,reasoning_content:reasoning,credentials:secret});
      finish();return;
    }
  }
  if(msg.kind==='candidate_response'){
    if(msg.accepted){complete();return;}
    if(!msg.feedback.includes('read_evidence') || msg.feedback.includes(secret))throw new Error('unsafe feedback');
    if(mode==='candidate_repeat'){candidate();return;}
    emit({kind:'model_request',id:'repair-model',run_binding:start.run_binding,request:{model:start.model.id,
      messages:[{role:'user',content:msg.feedback}],tools:start.tool_schemas.map(row=>({type:'function',function:row})),
      max_tokens:2048,tool_choice:'auto'}});
  }
});
'''


def fake_sidecar(monkeypatch, tmp_path, mode="success"):
    node = shutil.which("node")
    if not node: pytest.skip("Node required for the host stdio protocol fixture")
    directory = tmp_path / ("runtime-" + mode)
    directory.mkdir(exist_ok=True)
    script = _SIDECAR.replace("__MODE__", json.dumps(mode)).replace("__QUOTE__", json.dumps(QUOTE))
    script = script.replace("__FINAL__", json.dumps(final_findings(), ensure_ascii=False))
    script = script.replace("__REASONING__", json.dumps(REASONING)).replace("__SECRET__", json.dumps(SYNTHETIC_KEY))
    (directory / "sidecar.mjs").write_text(script, encoding="utf-8")
    (directory / "runtime-manifest.json").write_text(json.dumps({"dsh_runtime_version": runtime.DSH_VERSION,
        "dsh_sdk_version": runtime.DSH_VERSION, "protocol": 1}), encoding="utf-8")
    monkeypatch.setattr(runtime, "runtime_paths", lambda: (directory, node))
    monkeypatch.setattr(runtime, "configured_model_capacity", lambda **options: CAPACITY)
    native = asyncio.create_subprocess_exec
    processes = []
    async def create(*args, **kwargs):
        process = await native(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(runtime.asyncio, "create_subprocess_exec", create)
    return directory, processes


def run_host(checkpoints, **options):
    return asyncio.run(runtime.run_evidence_harness(*snapshot(), checkpoint=lambda state: checkpoints.append(state),
        time_budget_seconds=4, **options))


def test_real_stdio_host_success_roster_trace_isolation_and_reasoning_not_persisted(monkeypatch, tmp_path):
    _, processes = fake_sidecar(monkeypatch, tmp_path)
    checkpoints = []
    state = run_host(checkpoints)
    assert state["complete"] is True and state["phase"] == "complete"
    assert [row["name"] for row in state["trace"]] == ["read_evidence", "check_citation"]
    assert len(state["tool_roster"]) == 7
    assert state["findings"] == final_findings()
    assert REASONING not in json.dumps(checkpoints, ensure_ascii=False)


@pytest.mark.parametrize('seconds', [None, 0])
def test_zero_harness_quotas_complete_beyond_old_tool_cap_without_total_deadline(monkeypatch, tmp_path, seconds):
    _, processes = fake_sidecar(monkeypatch, tmp_path, 'unlimited_tools')
    checkpoints = []
    async def exercise():
        return await asyncio.wait_for(runtime.run_evidence_harness(*snapshot(),
            max_model_calls=0, max_tool_calls=0, time_budget_seconds=seconds,
            checkpoint=lambda state: checkpoints.append(state)), timeout=10)
    state = asyncio.run(exercise())
    assert state['complete'] and state['tool_calls'] == 259
    assert state['findings'] == final_findings()
    assert all(process.returncode == 0 for process in processes)
    assert len(checkpoints[-1]['trace']) == 259
    assert SYNTHETIC_KEY not in json.dumps(checkpoints, ensure_ascii=False)
    assert SYNTHETIC_KEY not in json.dumps(checkpoints, ensure_ascii=False)
    assert processes and all(process.returncode is not None for process in processes)


def test_candidate_repair_reserves_then_reuses_real_stdio_and_only_saves_valid_final(monkeypatch,tmp_path):
    _, processes=fake_sidecar(monkeypatch,tmp_path,'candidate_repair')
    checkpoints=[]
    async def model(payload,**options):
        assert checkpoints[-1]['final_repairs_used']==1
        assert checkpoints[-1]['model_calls']==1
        assert 'unbound_quote' in payload['messages'][0]['content']
        return response()
    monkeypatch.setattr(runtime,'model_request',model)
    state=run_host(checkpoints)
    assert state['complete'] and state['findings']==final_findings()
    assert state['final_repairs_used']==1 and state['model_calls']==1 and state['tool_calls']==4
    assert len(processes)==1 and processes[0].returncode==0
    assert all('findings' not in row for row in checkpoints[:-1])
    assert SYNTHETIC_KEY not in json.dumps(checkpoints) and REASONING not in json.dumps(checkpoints)


def test_repeated_bad_candidate_is_bounded_and_repair_count_survives_resume(monkeypatch,tmp_path):
    _,processes=fake_sidecar(monkeypatch,tmp_path,'candidate_repeat')
    checkpoints=[]
    with pytest.raises(PipelineError,match='摘录未绑定'):run_host(checkpoints)
    saved=checkpoints[-1]
    assert saved['final_repairs_used']==2 and not saved['complete'] and 'findings' not in saved
    assert saved['failure_diagnostic']['stage']=='final_validate'
    resumed=[]
    with pytest.raises(PipelineError,match='摘录未绑定'):run_host(resumed,previous_state=saved)
    assert resumed[-1]['final_repairs_used']==2 and resumed[-1]['phase']=='failed'
    assert all(process.returncode==0 for process in processes)
    assert SYNTHETIC_KEY not in json.dumps(checkpoints+resumed)


@pytest.mark.parametrize('mode,match',[('candidate_wrong_binding','本次会话'),
    ('candidate_duplicate','重复提交'),('candidate_tamper','已确认结束回复')])
def test_candidate_binding_duplicates_and_post_accept_change_are_rejected(monkeypatch,tmp_path,mode,match):
    _,processes=fake_sidecar(monkeypatch,tmp_path,mode)
    checkpoints=[]
    async def model(*args,**kwargs):return response()
    monkeypatch.setattr(runtime,'model_request',model)
    with pytest.raises(PipelineError,match=match):run_host(checkpoints)
    assert checkpoints[-1]['complete'] is False and 'findings' not in checkpoints[-1]
    assert all(process.returncode==0 for process in processes)
    assert SYNTHETIC_KEY not in json.dumps(checkpoints)


@pytest.mark.parametrize('count',[True,-1,7,'1'])
def test_invalid_cumulative_final_repair_counter_is_rejected_before_launch(monkeypatch,tmp_path,count):
    _,processes=fake_sidecar(monkeypatch,tmp_path)
    saved=run_host([])
    saved.update(complete=False,final_repairs_used=count)
    with pytest.raises(PipelineError,match='结束结果纠正次数'):run_host([],previous_state=saved)
    assert len(processes)==1


def test_resume_context_keeps_different_read_windows_and_only_removes_exact_duplicate_results():
    gateway=EvidenceGateway(*snapshot())
    full=valid_trace(gateway)
    short=valid_trace(gateway,limit=3)
    trace=[*full,*copy.deepcopy(full),*short]
    before=copy.deepcopy(trace)
    context=json.loads(runtime._resume_tool_context(trace))
    assert context['saved_call_count']==6 and context['unique_tool_results']==3
    windows=[row['result']['text'] for row in context['tool_results'] if row['name']=='read_evidence']
    assert windows==[snapshot()[0][0]['content'],snapshot()[0][0]['content'][:3]]
    assert trace==before


def test_stdio_resume_retains_earlier_read_outside_last_thirtytwo_records(monkeypatch,tmp_path):
    _,processes=fake_sidecar(monkeypatch,tmp_path,'partial_exit')
    checkpoints=[]
    with pytest.raises(PipelineError):run_host(checkpoints)
    previous=copy.deepcopy(checkpoints[-1])
    check=valid_trace(EvidenceGateway(*snapshot()))[1]
    previous['trace'].extend(copy.deepcopy(check) for _ in range(40))
    previous['tool_calls']=41
    fake_sidecar(monkeypatch,tmp_path,'resume_context')
    state=run_host([],previous_state=previous)
    assert state['complete'] and state['tool_calls']==43
    assert state['trace'][:41]==previous['trace']


@pytest.mark.parametrize('wrapper', [lambda value:value, lambda value:'```json\n'+value+'\n```',
    lambda value:'```JSON\r\n'+value+'\r\n```', lambda value:'```\n'+value+'\n```'])
def test_one_complete_final_object_accepts_only_plain_or_whole_fence(wrapper):
    value=final_findings()
    parsed=runtime._parse_findings_response(wrapper(json.dumps(value,ensure_ascii=False)))
    gateway=EvidenceGateway(*snapshot())
    assert runtime.validate_findings(parsed,gateway,valid_trace(gateway))==value


@pytest.mark.parametrize('text', ['prefix {"findings":[]}', '{} trailing', '{}{}', '```json\n{}',
    '```python\n{}\n```', '```json\n{}\n```\nprose', '[]', 'null', '{"x":1,"x":2}',
    '{"x":{"y":1,"y":2}}', '{"x":NaN}', '{"x":Infinity}', 'secret-invalid-json', '{'])
def test_incomplete_or_ambiguous_final_json_is_rejected_without_echoing_text(text):
    with pytest.raises(PipelineError) as caught: runtime._parse_findings_response(text)
    assert text not in str(caught.value)


def test_fenced_final_object_does_not_accept_unknown_fields_or_forged_quotes():
    value=final_findings()
    value['findings'][0]['quotes'][0]['quote']='已经举行'
    parsed=runtime._parse_findings_response('```json\n'+json.dumps(value)+'\n```')
    gateway=EvidenceGateway(*snapshot())
    with pytest.raises(PipelineError): runtime.validate_findings(parsed,gateway,valid_trace(gateway))


def test_real_stdio_fenced_final_still_checks_actual_reading_and_owned_exit(monkeypatch,tmp_path):
    _,processes=fake_sidecar(monkeypatch,tmp_path,'final_fence')
    checkpoints=[]
    state=run_host(checkpoints)
    assert state['complete'] and state['findings']==final_findings()
    assert len(state['trace'])==2 and all(p.returncode is not None for p in processes)


def test_real_stdio_invalid_final_keeps_trace_and_safe_stage_diagnostic(monkeypatch,tmp_path):
    _,processes=fake_sidecar(monkeypatch,tmp_path,'bad_final')
    checkpoints=[]
    with pytest.raises(PipelineError,match='单个 JSON 对象'):run_host(checkpoints)
    state=checkpoints[-1]
    assert state['phase']=='failed' and not state['complete'] and len(state['trace'])==2
    assert state['failure_diagnostic']['stage']=='final_decode'
    assert state['failure_diagnostic']['final_prefix_kind']=='other'
    assert SYNTHETIC_KEY not in json.dumps(checkpoints) and REASONING not in json.dumps(checkpoints)
    assert all(p.returncode is not None for p in processes)


@pytest.mark.parametrize('error,expected', [(json.JSONDecodeError('private-secret','private-secret',0),'json_decode'),
    (OSError('private-secret'),'os_error'),(BrokenPipeError('private-secret'),'broken_pipe'),
    (asyncio.TimeoutError('private-secret'),'timeout'),(PipelineError('private-secret'),'pipeline')])
def test_stage_diagnostic_only_retains_host_enums_and_counts(error,expected):
    value=runtime._failure_diagnostic('model_transport',error,None,'```json\nprivate-secret\n```')
    assert value['exception']==expected and value['final_prefix_kind']=='markdown_fence'
    assert set(value)=={'version','stage','exception','runtime_exit_code','final_text_characters','final_prefix_kind'}
    assert 'private-secret' not in json.dumps(value)


@pytest.mark.parametrize("mode,match", [("no_roster", "尚未确认"), ("bad_roster", "工具名单"),
    ("wrong_binding", "证据快照"), ("duplicate", "重复执行"), ("abrupt_exit", "未正常结束"),
    ("unfinished", "未完整结束"), ("not_closed", "未完整结束"),
    ("wrong_complete_binding", "未完整结束"), ("unread_tail", "实际读取的原文窗口")])
def test_real_stdio_host_protocol_rejects_invalid_roster_binding_duplicate_and_completion(monkeypatch, tmp_path, mode, match):
    _, processes = fake_sidecar(monkeypatch, tmp_path, mode)
    checkpoints = []
    with pytest.raises(PipelineError, match=match): run_host(checkpoints)
    assert checkpoints[-1]["complete"] is False and checkpoints[-1]["phase"] == "failed"
    assert all(process.returncode is not None for process in processes)
    assert REASONING not in json.dumps(checkpoints)


def test_rejected_unknown_tool_does_not_persist_unsafe_name_or_args(monkeypatch, tmp_path):
    fake_sidecar(monkeypatch, tmp_path, "unknown_tool")
    checkpoints = []
    with pytest.raises(PipelineError): run_host(checkpoints)
    assert checkpoints[-1]["trace"][-1]["ok"] is False
    assert checkpoints[-1]["trace"][-1]["args"] == {}
    assert SYNTHETIC_KEY not in json.dumps(checkpoints)


def test_host_model_failure_reserves_call_before_http_and_keeps_tool_progress(monkeypatch, tmp_path):
    _, processes = fake_sidecar(monkeypatch, tmp_path, "model")
    checkpoints, observed_reserved = [], []
    def handler(request):
        observed_reserved.append(checkpoints[-1]["model_calls"])
        return httpx.Response(402, text=SYNTHETIC_KEY)
    transport(monkeypatch, handler)
    with pytest.raises(PipelineError, match="余额不足"): run_host(checkpoints)
    assert observed_reserved == [1]
    assert checkpoints[-1]["model_calls"] == 1 and checkpoints[-1]["complete"] is False
    assert SYNTHETIC_KEY not in json.dumps(checkpoints)
    assert all(process.returncode is not None for process in processes)


def test_host_saves_bounded_structural_diagnostic_and_closes_owned_child(monkeypatch, tmp_path):
    _, processes = fake_sidecar(monkeypatch, tmp_path, "model")
    checkpoints = []
    body = tool_response()
    body['choices'][0]['message']['tool_calls'][0]['index'] = 1
    transport(monkeypatch, lambda request: httpx.Response(200, json=body))
    with pytest.raises(PipelineError, match="工具调用无效"):
        run_host(checkpoints)
    state = checkpoints[-1]
    assert state['phase'] == 'failed' and state['model_calls'] == 1 and state['tool_calls'] == 0
    assert state['response_diagnostic']['code'] == 'tool_call_fields'
    assert state['response_diagnostic']['tool_call_has_index'] is True
    assert REASONING not in json.dumps(checkpoints) and SYNTHETIC_KEY not in json.dumps(checkpoints)
    assert all(process.returncode is not None for process in processes)


def test_host_refuses_extra_or_sensitive_diagnostic_fields(monkeypatch, tmp_path):
    _, processes = fake_sidecar(monkeypatch, tmp_path, "model")
    async def reject(*args, **kwargs):
        error = PipelineError('Controlled response failure')
        error.harness_response_diagnostic = {**runtime._response_diagnostic(tool_response(), 'tool_call_fields', 2048),
            'credentials':SYNTHETIC_KEY, 'reasoning_content':REASONING}
        raise error
    monkeypatch.setattr(runtime, 'model_request', reject)
    checkpoints = []
    with pytest.raises(PipelineError):
        run_host(checkpoints)
    assert 'response_diagnostic' not in checkpoints[-1]
    assert SYNTHETIC_KEY not in json.dumps(checkpoints) and REASONING not in json.dumps(checkpoints)
    assert all(process.returncode is not None for process in processes)


def test_completed_checkpoint_reuses_without_sidecar_but_changed_model_invalidates(monkeypatch, tmp_path):
    _, processes = fake_sidecar(monkeypatch, tmp_path)
    previous = run_host([])
    reused = run_host([], previous_state=previous)
    assert reused["reused_completed_checkpoint"] is True and len(processes) == 1
    monkeypatch.setenv("DEEPSEEK_MODEL", "different-fixture-model")
    changed = run_host([], previous_state=previous)
    assert "reused_completed_checkpoint" not in changed
    assert changed["input_fingerprint"] != previous["input_fingerprint"] and len(processes) == 2


@pytest.mark.parametrize("change", ["body", "goal", "revision", "reviews"])
def test_changed_snapshot_context_or_reviews_invalidates_completed_reuse(monkeypatch, tmp_path, change):
    _, processes = fake_sidecar(monkeypatch, tmp_path)
    previous = run_host([])
    items, context, reviews = snapshot()
    if change == "body": items[0]["content"] += "保存补充。"
    elif change == "goal": context["research_goal"] += "进一步比较"
    elif change == "revision": context["run_revision"] += 1
    else: reviews.append({"citation_id": "S001", "quote": QUOTE, "reading": "保存审阅", "limitations": "非外部核验"})
    checkpoints = []
    changed = asyncio.run(runtime.run_evidence_harness(items, context, reviews, previous_state=previous,
        checkpoint=lambda state: checkpoints.append(state), time_budget_seconds=4))
    assert changed["input_fingerprint"] != previous["input_fingerprint"] and len(processes) == 2
    assert checkpoints[0]["trace"] == [] and checkpoints[0]["model_calls"] == 0 and checkpoints[0]["tool_calls"] == 0


def test_partial_exit_checkpoint_resumes_saved_tools_and_never_marks_failure_complete(monkeypatch, tmp_path):
    fake_sidecar(monkeypatch, tmp_path, "partial_exit")
    failed = []
    with pytest.raises(PipelineError): run_host(failed)
    previous = failed[-1]
    assert len(previous["trace"]) == 1 and previous["trace"][0]["name"] == "read_evidence"
    assert previous["complete"] is False
    fake_sidecar(monkeypatch, tmp_path, "success")
    resumed = run_host([], previous_state=previous)
    assert resumed["complete"] is True and len(resumed["trace"]) == 3
    assert resumed["trace"][0] == previous["trace"][0]


def test_cancel_is_saved_and_real_child_process_exits(monkeypatch, tmp_path):
    _, processes = fake_sidecar(monkeypatch, tmp_path, "wait_cancel")
    checkpoints = []
    async def exercise():
        ready = asyncio.Event()
        async def checkpoint(state):
            checkpoints.append(state)
            if state.get("tool_roster"): ready.set()
        task = asyncio.create_task(runtime.run_evidence_harness(*snapshot(), checkpoint=checkpoint,
            time_budget_seconds=4))
        await asyncio.wait_for(ready.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(exercise())
    assert checkpoints[-1]["phase"] == "cancelled" and checkpoints[-1]["complete"] is False
    assert processes and all(process.returncode is not None for process in processes)
