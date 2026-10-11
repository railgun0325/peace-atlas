"""Fast classification uses saved profile fixtures and MockGateway only."""
import asyncio
import copy
import json

from fastapi.testclient import TestClient
import httpx
import pytest

from app.workbench import fast_account_classification as fast
from app.workbench.account_identity import correct_identity, describe_identity, research_registry_context
from app.workbench.jobs import JobRunner
from app.workbench.pipeline import PipelineError
from app.workbench.review import ReviewConflict, preview_batch, snapshot
from app.workbench.server import create_app
from app.workbench.store import Store
from test_workbench_agent_api import local_client, grant, preview, execution


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / 'fast.sqlite3')
    yield value
    value.close()


def seed(store, handle='ab123x', *, name='Public 123', bio='私の趣味と日常を投稿します', status='candidate'):
    account = store.save_account({'platform': 'twitter', 'handle': handle, 'name': name,
        'url': f'https://x.com/{handle}', 'status': status, 'organization': '原组织字段保留',
        'evidence_url': 'https://public.example.org/about' if status == 'verified' else '',
        'metadata': {'unrelated_public_notes': '不得发送给分类模型'}})
    store.import_social([], [{'platform': 'twitter', 'handle': handle, 'name': name,
        'url': f'https://x.com/{handle}', 'profile': {'verified_in_dom': True, 'url': f'https://x.com/{handle}', 'bio': bio}}], [])
    return store.get_account(account['id'])


def output(rows, type='individual'):
    return [{'id': row['id'], 'type': type, 'confidence': 'medium', 'reason': '已保存简介显示个人发布内容'} for row in rows]


class MockGateway:
    def __init__(self, type='individual', after=None):
        self.calls = []
        self.type = type
        self.after = after

    async def classify(self, profiles):
        self.calls.append(copy.deepcopy(profiles))
        if self.after:
            self.after()
        return {'classifications': [{'id': row['id'], 'type': self.type, 'confidence': 'medium',
                                    'reason': '已保存简介提供发布主体线索'} for row in profiles]}


def job_for(store, **options):
    estimate = fast.estimate_classification(store, options)
    job = fast.start_classification(store, options, expected_digest=estimate['input_digest'])
    store.update_job(job['id'], status='running')
    return store.get_job(job['id'])


def run(store, job, gateway, *, checkpoint=None, cancelled=lambda: False):
    async def saved(value):
        store.update_job(job['id'], result=value)
    return asyncio.run(fast.run_classification(store, job, gateway=gateway,
        checkpoint=checkpoint or saved, cancelled=cancelled))


def test_bulk_one_call_saved_fields_only_and_all_non_type_fields_retained(store, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('No public source, identity check or harness call allowed')
    from app.workbench import account_verification, automated_review
    monkeypatch.setattr(account_verification, 'run_checks', forbidden)
    monkeypatch.setattr(automated_review, 'run_evidence_harness', forbidden)
    monkeypatch.setattr(account_verification.pipeline, '_fetch_public_feed', forbidden)
    accounts = [seed(store, 'ab123x', status='verified'), seed(store, 'random777', bio='平和団体の共同運営アカウント')]
    material = store.import_social([], [], [{'source_key': 'twitter:ab123x', 'target_key': 'twitter:random777',
        'relation': 'follows', 'evidence_url': 'https://x.com/ab123x/following', 'observed_at': '2026-10-11T00:00:00Z'}])
    assert material['relations_inserted'] == 1
    edges = copy.deepcopy(store.registry_graph()['edges'])
    before = [store.get_account(row['id']) for row in accounts]
    before_jobs = store.list_jobs()['total']
    job = job_for(store)
    gateway = MockGateway(type='organization')
    result = run(store, job, gateway)
    assert result['completed'] == 2 and result['model_calls'] == 1 and result['tool_calls'] == 0
    assert len(gateway.calls) == 1
    assert all(set(row) == {'id', 'display_name', 'handle', 'public_bio'} for row in gateway.calls[0])
    assert {row['public_bio'] for row in gateway.calls[0]} == {row['metadata']['last_public_profile']['bio'] for row in before}
    for original in before:
        saved = store.get_account(original['id'])
        assert {key: saved[key] for key in ('status', 'organization', 'evidence_url', 'name', 'handle', 'url')} == {
            key: original[key] for key in ('status', 'organization', 'evidence_url', 'name', 'handle', 'url')}
        assert {key: value for key, value in saved['metadata'].items() if key != 'account_identity'} == original['metadata']
        info = describe_identity(saved)
        assert info['account_type'] == 'organization'  # even a random alphanumeric name can represent a group
        assert info['account_type_details']['basis'] == fast.BASIS
        assert info['account_type_details']['confidence'] == 'medium'
        assert info['account_type_details']['evidence_urls'] == []
        assert not saved['metadata'].get('verification_sources')
        context = research_registry_context([saved])[f"twitter:{saved['handle']}"]['metadata']['account_identity']
        assert context['basis'] == fast.BASIS and context['confidence'] == 'medium'
    assert store.list_jobs()['total'] == before_jobs + 1  # classification never schedules a verification job
    assert store.registry_graph()['edges'] == edges


def test_manual_locked_skipped_and_unknown_prior_review_can_be_rough_classified(store):
    locked = seed(store)
    unknown = seed(store, 'other')
    correct_identity(store, locked['id'], type='individual', manual_locked=True, _schedule=False)
    correct_identity(store, unknown['id'], type='unknown', manual_locked=False, _schedule=False)
    proof = fast.estimate_classification(store, {})
    assert proof['total'] == 1 and proof['model_calls_estimate'] == 1 and not proof['may_read_public_network']
    assert proof['created_jobs'] == 0
    assert fast.estimate_classification(store, {'account_ids': [locked['id']], 'recheck': True})['total'] == 0
    result = run(store, job_for(store), MockGateway())
    assert {row['id'] for row in result['results']} == {unknown['id']}
    assert describe_identity(store.get_account(locked['id']))['account_type_details']['manual_locked']


def test_dict_transport_attempt_callbacks_count_three_reserved_requests(store, monkeypatch):
    seed(store)
    job = job_for(store)
    checkpoints = []

    async def mock_request(system, payload, *, transport_event, **kwargs):
        assert kwargs['transport_retry'] is True
        profiles = payload['accounts']
        # Match query_plan._configured_json_request's actual callback contract.
        for attempt in (1, 2, 3):
            await transport_event('attempt', {'attempt': attempt, 'max_attempts': 3, 'request_reserved': True})
            if attempt < 3:
                await transport_event('retry', {'attempt': attempt, 'max_attempts': 3})
        await transport_event('complete', {'attempt': 3, 'max_attempts': 3})
        return {'classifications': output(profiles)}

    async def checkpoint(value):
        checkpoints.append(copy.deepcopy(value))
        store.update_job(job['id'], result=value)

    monkeypatch.setattr(fast, 'configured_ai_json', mock_request)
    result = run(store, job, None, checkpoint=checkpoint)
    assert result['completed'] == 1 and result['model_calls'] == 3
    assert result['transport_attempts'] == 3 and result['transport_retries'] == 2
    assert result['tool_calls'] == 0
    assert any(value.get('transport_attempts') == 3 and value['model_calls'] == 3 for value in checkpoints)
    assert store.get_job(job['id'])['result']['transport_retries'] == 2


def test_fast_gateway_wire_disables_thinking_and_preserves_other_calls_default(monkeypatch):
    from app.workbench import query_plan
    native = httpx.AsyncClient
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        data = json.loads(payload['messages'][1]['content'])
        value = {'classifications': output(data['accounts'])} if 'accounts' in data else {'ok': True}
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
            'message': {'content': json.dumps(value)}}]})

    monkeypatch.setattr(query_plan.httpx, 'AsyncClient', lambda **options: native(
        transport=httpx.MockTransport(handler), **options))
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'synthetic-fast-test-only')
    monkeypatch.setenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com')
    monkeypatch.setenv('DEEPSEEK_MODEL', 'deepseek-flash')
    for key in ('DEEPSEEK_CONTEXT_WINDOW_TOKENS', 'DEEPSEEK_MAX_OUTPUT_TOKENS'):
        monkeypatch.delenv(key, raising=False)

    async def scenario():
        for count in (61, 200):
            profiles = [{'id': f'account_{index}', 'display_name': '公开账号',
                         'handle': f'handle_{index}', 'public_bio': '已保存公开简介'} for index in range(count)]
            result = await fast.ClassificationGateway().classify(profiles)
            assert len(result['classifications']) == count
        assert await query_plan.configured_ai_json('仅输出 JSON', {'public': '已保存来源'}) == {'ok': True}

    asyncio.run(scenario())
    assert [request['reasoning_effort'] for request in requests] == ['none', 'none', 'low']
    assert [request['max_tokens'] for request in requests] == [19520, 64000, 1800]
    assert all(request['response_format'] == {'type': 'json_object'} for request in requests)
    assert all('tools' not in request and 'thinking' not in request for request in requests)


def test_nonstop_failure_persists_only_safe_diagnostics_and_no_account_writes(store, monkeypatch):
    from app.workbench import query_plan
    accounts = [seed(store, status='verified'), seed(store, 'second')]
    before = [store.get_account(row['id']) for row in accounts]
    job = job_for(store)
    native = httpx.AsyncClient

    def handler(request):
        payload = json.loads(request.content)
        assert payload['reasoning_effort'] == 'none'
        return httpx.Response(200, json={'choices': [{'finish_reason': 'length',
            'message': {'content': 'private-response-marker', 'reasoning_content': 'private-reasoning-marker'}}],
            'usage': {'completion_tokens': payload['max_tokens']}})

    original = fast.ClassificationGateway

    class UntrustedDiagnosticGateway(original):
        async def classify(self, profiles):
            try:
                return await super().classify(profiles)
            except PipelineError as error:
                # Even unexpected provider additions cannot enter the saved job.
                error.ai_diagnostic.update(provider_body='private-response-marker',
                    reasoning_content='private-reasoning-marker', api_key='synthetic-fast-test-only')
                raise

    monkeypatch.setattr(fast, 'ClassificationGateway', UntrustedDiagnosticGateway)
    monkeypatch.setattr(query_plan.httpx, 'AsyncClient', lambda **options: native(
        transport=httpx.MockTransport(handler), **options))
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'synthetic-fast-test-only')
    monkeypatch.setenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com')
    monkeypatch.setenv('DEEPSEEK_MODEL', 'deepseek-flash')
    asyncio.run(JobRunner(store)._execute(job))
    saved = store.get_job(job['id'])
    assert saved['status'] == 'failed' and saved['progress'] == 0
    assert saved['error'] == 'AI 输出未完整结束；未采用截断内容。'
    assert saved['result']['completed'] == 0 and saved['result']['batches'] == [] and saved['result']['results'] == []
    assert saved['result']['model_calls'] == saved['result']['transport_attempts'] == 1
    assert saved['result']['tool_calls'] == 0
    assert saved['result']['ai_diagnostic'] == {'finish_reason': 'length', 'requested_max_tokens': 4096,
        'completion_tokens': 4096, 'reasoning_effort': 'none'}
    assert not any(marker in json.dumps(saved, ensure_ascii=False) for marker in (
        'private-response-marker', 'private-reasoning-marker', 'synthetic-fast-test-only'))
    assert [store.get_account(row['id']) for row in accounts] == before


@pytest.mark.parametrize('mutation', ['duplicate', 'missing', 'invented', 'non_string', 'extra', 'empty_reason', 'invalid_type', 'legacy_unknown'])
def test_invalid_structured_response_is_not_coerced_or_partially_saved(store, mutation):
    seed(store); seed(store, 'second')
    rows = fast.classification_snapshot(store, fast.ClassificationArgs())
    values = output(rows)
    if mutation == 'duplicate': values[1]['id'] = values[0]['id']
    elif mutation == 'missing': values.pop()
    elif mutation == 'invented': values[0]['id'] = 'invented'
    elif mutation == 'non_string': values[0]['reason'] = ['some explanation']
    elif mutation == 'extra': values[0]['evidence_url'] = 'https://example.org'
    elif mutation == 'empty_reason': values[0]['reason'] = ' '
    elif mutation == 'invalid_type': values[0]['type'] = 'verified'
    elif mutation == 'legacy_unknown': values[0]['type'] = 'unknown'
    with pytest.raises(PipelineError):
        fast.apply_classifications(store, rows, values)
    assert all(not store.get_account(row['id'])['metadata'].get('account_identity') for row in rows)


def test_stale_manual_change_aborts_whole_batch_and_keeps_lock(store):
    first = seed(store); second = seed(store, 'second')
    rows = fast.classification_snapshot(store, fast.ClassificationArgs())
    correct_identity(store, second['id'], type='organization', manual_locked=True, note='人工纠正', _schedule=False)
    with pytest.raises(ReviewConflict): fast.apply_classifications(store, rows, output(rows))
    assert not store.get_account(first['id'])['metadata'].get('account_identity')
    value = describe_identity(store.get_account(second['id']))
    assert value['account_type'] == 'organization' and value['account_type_details']['manual_locked']


def test_failed_atomic_progress_save_rolls_back_all_types(store):
    seed(store); seed(store, 'second')
    rows = fast.classification_snapshot(store, fast.ClassificationArgs())
    def fail(connection, saved): raise OSError('injected progress write failure')
    with pytest.raises(OSError): fast.apply_classifications(store, rows, output(rows), after_apply=fail)
    assert all(not store.get_account(row['id'])['metadata'].get('account_identity') for row in rows)


def test_cancel_after_model_and_resume_reuses_saved_decisions_without_new_call(store):
    seed(store); seed(store, 'second')
    job = job_for(store)
    cancelled = False
    async def checkpoint(value):
        nonlocal cancelled
        store.update_job(job['id'], result=value)
        if value['phase'] == 'saving': cancelled = True
    gateway = MockGateway()
    with pytest.raises(asyncio.CancelledError): run(store, job, gateway, checkpoint=checkpoint, cancelled=lambda: cancelled)
    assert len(gateway.calls) == 1 and all(not row['metadata'].get('account_identity') for row in store.list_accounts())
    store.update_job(job['id'], status='cancelled')
    cancelled_child = store.retry_job(job['id'])
    store.request_cancel(cancelled_child['id'])
    retried = store.retry_job(cancelled_child['id'])
    result = run(store, retried, gateway)
    assert result['completed'] == 2 and len(gateway.calls) == 1


def test_resume_completed_batch_only_calls_unfinished_batch_and_job_runner_finishes(store, monkeypatch):
    seed(store); seed(store, 'second'); seed(store, 'third')
    job = job_for(store, batch_size=2)
    async def checkpoint(value):
        store.update_job(job['id'], result=value)
        if value['completed'] == 2: raise asyncio.CancelledError()
    gateway = MockGateway()
    with pytest.raises(asyncio.CancelledError): run(store, job, gateway, checkpoint=checkpoint)
    assert store.get_job(job['id'])['result']['completed'] == 2
    retried = store.create_job('collect', {**job['params'], '_workbench_retry': {'parent_job_id': job['id']}})
    monkeypatch.setattr(fast, 'ClassificationGateway', lambda **kwargs: gateway)
    asyncio.run(JobRunner(store)._execute(retried))
    saved = store.get_job(retried['id'])
    assert saved['status'] == 'succeeded' and saved['progress'] == 100
    assert saved['result']['completed'] == 3 and len(gateway.calls) == 2
    assert len(gateway.calls[-1]) == 1


def test_manual_quick_correction_unlock_and_estimate_are_network_free(store, monkeypatch):
    account = seed(store, status='verified')
    monkeypatch.setenv('DEEPSEEK_API_KEY', '')
    with TestClient(create_app(store=store, run_worker=False)) as client:
        before = store.list_jobs()['total']
        response = client.patch(f"/api/v2/accounts/{account['id']}/identity", json={'type': 'individual', 'manual_locked': True})
        assert response.status_code == 200
        assert response.json()['status'] == 'verified'
        identity = copy.deepcopy(store.get_account(account['id'])['metadata']['account_identity'])
        assert client.post('/api/v2/accounts/classify/estimate', json={'recheck': True}).json()['total'] == 0
        unlocked = client.patch(f"/api/v2/accounts/{account['id']}/type-lock", json={'manual_locked': False})
        assert unlocked.status_code == 200 and unlocked.json()['account_type'] == 'individual'
        current = store.get_account(account['id'])['metadata']['account_identity']
        assert {key: current[key] for key in identity if key not in {'manual_locked', 'revision'}} == {
            key: identity[key] for key in identity if key not in {'manual_locked', 'revision'}}
        assert client.post('/api/v2/accounts/classify/estimate', json={'recheck': True}).json()['total'] == 1
        assert store.list_jobs()['total'] == before


def test_agent_fast_preview_execute_schema_needs_review_scope_and_not_harness(tmp_path, monkeypatch):
    async def scenario():
        async with local_client(tmp_path, monkeypatch) as (app, client):
            seed(app.state.store)
            headers, _ = await grant(client, tmp_path, scopes=('read', 'review'))
            monkeypatch.setattr(fast, 'capabilities', lambda: {'llm': {'model': 'MockGateway', 'configured': True}})
            from app.workbench import server
            monkeypatch.setattr(server.pipeline, 'capabilities', lambda: {'llm': {'model': 'MockGateway', 'configured': True}, 'analysis_harness': {'available': False}})
            schema = (await client.get('/agents/v1/schema')).json()['operations']['classify.start']
            assert schema['scope'] == 'review' and schema['may_read_public_network'] is False and schema['identity_verification'] is False
            assert schema['classification_types'] == ['organization', 'individual']
            plan = await preview(client, headers, 'classify.start', {'batch_size': 100})
            assert plan['preview']['total'] == 1 if 'preview' in plan else plan['result']['total'] == 1
            response = await client.post('/agents/v1/execute', headers=headers, json=execution(plan, 'fast-type-001'))
            assert response.status_code == 201, response.text
            job = response.json()
            assert job['params']['_account_classification'] is True
            cancelled = await client.post('/agents/v1/preview', headers=headers, json={'operation': 'job.cancel', 'arguments': {'id': job['id']}})
            assert cancelled.status_code == 200
    asyncio.run(scenario())


def test_strict_evidence_review_still_requires_evidence_and_does_not_accept_fast_basis(store):
    seed(store)
    row = snapshot(store, ['accounts'])[0]
    values = {'accounts': [{'id': row['id'], 'expected_sha256': row['expected_sha256'],
                          'identity': {'type': 'organization', 'note': 'Just a model guess'}}]}
    with pytest.raises(ValueError, match='独立公开来源'): preview_batch(store, values)
    assert not store.get_account(row['id'])['metadata'].get('account_identity')


def test_binary_profile_fixture_distinguishes_collective_publisher_from_group_mention(store, monkeypatch):
    """Exercise the actual request and persistence contract, not real-model accuracy."""
    from app.workbench import query_plan
    profiles = [
        ('shunicirou', '吉美駿一郎', '', 'individual', 'medium', '姓名式名称，未见组织发布线索'),
        ('gfyyrnb0djrqflj', 'gfyyrnb0djrqflj', '', 'individual', 'low', '未见组织发布线索，暂归个人'),
        ('douroutiba', 'dorosien', '動労千葉を支援する会', 'individual', 'low', '支援团体的提及未说明账号以团体名义运营'),
        ('ab_random123', '公开资料研究会', '公开资料研究会が共同運営する団体の発信窓口です。',
         'organization', 'high', '团体名称与简介明确集体运营的发布渠道'),
    ]
    accounts = [seed(store, handle, name=name, bio=bio) for handle, name, bio, *_ in profiles]
    before = {row['id']: copy.deepcopy(store.get_account(row['id'])) for row in accounts}
    expected = {handle: (name, bio, kind, confidence, reason)
                for handle, name, bio, kind, confidence, reason in profiles}
    calls = []
    native = httpx.AsyncClient

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert payload['reasoning_effort'] == 'none' and 'tools' not in payload
        assert payload['messages'][0]['content'].startswith(fast.SYSTEM)
        assert '仅提到组织、支持某组织、自称成员或前成员' in payload['messages'][0]['content']
        sent = json.loads(payload['messages'][1]['content'])['accounts']
        assert len(sent) == len(profiles)
        result = []
        for row in sent:
            assert set(row) == {'id', 'display_name', 'handle', 'public_bio'}
            name, bio, kind, confidence, reason = expected[row['handle']]
            assert (row['display_name'], row['public_bio']) == (name, bio)
            result.append({'id': row['id'], 'type': kind, 'confidence': confidence, 'reason': reason})
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
            'message': {'content': json.dumps({'classifications': result})}}]})

    monkeypatch.setattr(query_plan.httpx, 'AsyncClient', lambda **options: native(
        transport=httpx.MockTransport(handler), **options))
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'synthetic-binary-fixture-only')
    monkeypatch.setenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com')
    monkeypatch.setenv('DEEPSEEK_MODEL', 'deepseek-flash')
    job = job_for(store)
    asyncio.run(JobRunner(store)._execute(job))
    saved = store.get_job(job['id'])
    assert saved['status'] == 'succeeded' and saved['result']['completed'] == 4
    assert len(calls) == 1 and saved['result']['tool_calls'] == 0
    assert saved['result']['outcomes'] == {'organization': 1, 'individual': 3}
    for account in accounts:
        current = store.get_account(account['id'])
        kind = expected[account['handle']][2]
        assert describe_identity(current)['account_type'] == kind
        assert current['metadata']['account_identity']['basis'] == fast.BASIS
        assert {key: value for key, value in current.items() if key not in {'metadata', 'updated_at'}} == {
            key: value for key, value in before[account['id']].items() if key not in {'metadata', 'updated_at'}}
        assert {key: value for key, value in current['metadata'].items() if key != 'account_identity'} == before[account['id']]['metadata']
    assert store.list_jobs()['total'] == 1  # No identity check, public-web read or membership grant.


def test_legacy_unknown_checkpoint_resumes_without_reclassifying_committed_or_saved_decisions(store):
    first, second = seed(store), seed(store, 'second')
    job = job_for(store, batch_size=1)
    rows = job['params']['classification_snapshot']
    initial, remaining = rows[:1], rows[1:]
    # An earlier three-type release could persist unknown in both completed and
    # not-yet-committed decisions. Keep that historical receipt unchanged.
    saved = fast.apply_classifications(store, initial, output(initial, type='unknown'), _saved_legacy=True)
    store.update_job(job['id'], status='cancelled', result={
        'classification_version': 1, 'classification_basis': fast.BASIS,
        'snapshot_digest': job['params']['snapshot_digest'], 'total': 2, 'completed': 1,
        'results': saved, 'batches': [{'input_digest': fast.digest(initial), 'count': 1, 'tool_calls': 0}],
        'model_calls': 2, 'tool_calls': 0, 'phase': 'saving',
        'current_classification': {'batch_digest': fast.digest(remaining), 'decisions': output(remaining, type='unknown')}})
    child = store.retry_job(job['id'])
    gateway = MockGateway()
    result = run(store, child, gateway)
    assert result['completed'] == 2 and result['complete'] is True and not gateway.calls
    assert all(row['outcome'] == 'unknown' for row in result['results'])
    assert all(describe_identity(store.get_account(row['id']))['account_type'] == 'unknown' for row in (first, second))
    # Legacy checkpoint acceptance never widens the new model-output contract.
    with pytest.raises(PipelineError):
        fast.validate_classifications({'classifications': output(remaining, type='unknown')}, remaining)


def test_invalid_resume_result_is_rejected_before_any_model_call(store):
    account = seed(store)
    job = job_for(store)
    store.update_job(job['id'], result={'classification_version': 1, 'snapshot_digest': job['params']['snapshot_digest'],
        'total': 1, 'completed': 1, 'results': [{'id': account['id'], 'outcome': 'verified', 'confidence': 'high',
            'note': 'invalid status masquerading as type', 'basis': fast.BASIS}], 'batches': []})
    retried = store.create_job('collect', {**job['params'], '_workbench_retry': {'parent_job_id': job['id']}})
    gateway = MockGateway()
    with pytest.raises(PipelineError, match='分类进度无效'): run(store, retried, gateway)
    assert not gateway.calls and not store.get_account(account['id'])['metadata'].get('account_identity')
