"""Review mutations, evidence gates and recovery use temporary public fixtures."""
import asyncio
import copy
import json

import pytest

from app.workbench import automated_review as auto
from app.workbench.account_identity import correct_identity
from app.workbench.jobs import JobRunner
from app.workbench.pipeline import PipelineError
from app.workbench.review import apply_batch, preview_batch, review_queue, snapshot, review_estimate, start_review, ReviewConflict
from app.workbench.store import Store


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / 'review.sqlite3')
    yield value
    value.close()


def seed(store):
    account = store.save_account({'name': 'Public A', 'platform': 'twitter', 'handle': 'peace_a', 'url': 'https://x.com/peace_a'})
    job = store.create_job('research', {'keywords': ['公開平和']})
    store.create_research_run(job['id'], job['params'])
    store.checkpoint_research(job['id'], nodes=[{'account_key': 'twitter:peace_a', 'name': 'Public A', 'depth': 0, 'status': 'visited'}],
        claims=[{'subject_key': 'twitter:peace_a', 'relationship': 'affiliation', 'object_label': '公開団体', 'basis': 'inferred',
                 'statement': '所属を示す独立証拠がない', 'evidence_urls': ['https://x.com/peace_a']}])
    item_id = store.import_items([{'type': 'news', 'url': 'https://news.example.org/activity', 'title': '公開集会',
        'content': '2026年10月5日、東京都で平和集会が開催された。\n2026年10月6日、京都府で反戦デモが行われた。'}])['ids'][0]
    return account, job, store.get_item(item_id)


def decisions(rows):
    result = {'accounts': [], 'claims': [], 'activities': []}
    for row in rows:
        kind, record = row['kind'], row['record']
        base = {'id': row['id'], 'expected_sha256': row['expected_sha256']}
        if kind == 'accounts':
            base['identity'] = {'type': 'unknown', 'note': '不足以判断', 'manual_locked': False}
        elif kind == 'claims':
            base.update(run_id=record['run_id'], status='inconclusive', note='独立证据不足')
        else:
            base.update(item_id=record['item_id'], status='inconclusive', title=record['title'], quote=record['quote'], note='缺少完整举办依据')
        result[kind].append(base)
    return result


def save(store, values, **kwargs):
    proof = preview_batch(store, values)
    return apply_batch(store, values, expected_digest=proof['digest'], **kwargs)


def test_mixed_batch_is_atomic_and_inconclusive_is_reviewed_not_confirmed(store):
    account, run, item = seed(store)
    rows = snapshot(store, ['accounts', 'claims', 'activities'])
    values = decisions(rows)
    before = len(store.list_jobs()['items'])
    proof = preview_batch(store, values)
    assert proof['count'] == 4 and proof['created_jobs'] == 0
    assert not store.get_research_run(run['id'])['claims'][0]['reviewed_at']
    result = apply_batch(store, values, expected_digest=proof['digest'])
    assert result['applied'] == 4 and result['atomic'] and result['original_material_retained']
    assert len(store.list_jobs()['items']) == before
    assert snapshot(store, ['accounts', 'claims', 'activities']) == []
    claim = store.get_research_run(run['id'])['claims'][0]
    assert claim['status'] == 'candidate' and claim['review_outcome'] == 'inconclusive'
    assert store.get_account(account['id'])['status'] == 'candidate'
    data = store.activities(status='candidate')
    assert data['pending'] == 0 and data['inconclusive'] == 2 and data['denominator'] == 0
    assert store.get_item(item['id'])['content'] == item['content']


def test_stale_one_record_leaves_entire_batch_unchanged(store):
    account, run, _ = seed(store)
    values = decisions(snapshot(store, ['accounts', 'claims']))
    proof = preview_batch(store, values)
    correct_identity(store, account['id'], type='unknown', note='new evidence', _schedule=False)
    with pytest.raises(ReviewConflict):
        apply_batch(store, values, expected_digest=proof['digest'])
    assert not store.get_research_run(run['id'])['claims'][0]['reviewed_at']


def test_sibling_activity_reviews_do_not_invalidate_other_paragraphs(store):
    seed(store)
    rows = snapshot(store, ['activities'])
    assert len(rows) == 2 and rows[0]['record']['item_id'] == rows[1]['record']['item_id']
    for row in rows:
        save(store, decisions([row]))
    assert review_queue(store, kind='activities')['total'] == 0


def test_failed_atomic_checkpoint_rolls_back_both_review_and_progress(store):
    account, _, _ = seed(store)
    values = decisions(snapshot(store, ['accounts']))
    def fail(connection, result):
        raise OSError('injected storage failure')
    with pytest.raises(OSError):
        save(store, values, _after_apply=fail)
    assert not store.get_account(account['id'])['metadata'].get('account_identity')
    assert review_queue(store, kind='accounts')['total'] == 1


def test_auto_range_preserves_manual_lock_and_start_rejects_changed_input(store, monkeypatch):
    account, _, _ = seed(store)
    correct_identity(store, account['id'], type='individual', note='manual source', manual_locked=True, _schedule=False)
    assert snapshot(store, ['accounts'], pending_only=False) == []
    rows = snapshot(store, ['claims'])
    from app.workbench.review import digest
    save(store, decisions(rows))
    with pytest.raises(ReviewConflict):
        start_review(store, {'kinds': ['claims']}, expected_digest=digest(rows))


def test_harness_batch_commits_and_retry_does_not_repeat_completed_model_calls(store, monkeypatch):
    seed(store)
    calls = []
    async def harness(items, context, reviews, **options):
        gateway = options['_gateway']
        calls.append(gateway.rows)
        rows = [{'kind': row['kind'], 'id': row['id'], 'outcome': 'unknown' if row['kind'] == 'accounts' else 'inconclusive',
                 'note': '公开依据不足，保留原记录', 'evidence': []} for row in gateway.rows]
        return {'findings': options['_validator']({'decisions': rows}, gateway, []), 'trace': [], 'model_calls': 1, 'tool_calls': 0}
    monkeypatch.setattr(auto, 'run_evidence_harness', harness)
    from app.workbench.review import digest
    rows = snapshot(store, ['accounts', 'claims', 'activities'])
    job = start_review(store, {'batch_size': 1, 'use_public_network': False}, expected_digest=digest(rows))
    store.update_job(job['id'], status='running')
    job = store.get_job(job['id'])
    assert job['params']['_evidence_review']
    asyncio.run(JobRunner(store)._execute(job))
    saved = store.get_job(job['id'])
    assert saved['status'] == 'succeeded' and saved['progress'] == 100
    assert saved['result']['completed'] == 4 and len(calls) == 4
    assert snapshot(store, ['accounts', 'claims', 'activities']) == []
    retried = store.create_job('collect', {**job['params'], '_workbench_retry': {'parent_job_id': job['id']}})
    store.update_job(retried['id'], status='running')
    async def checkpoint(value):
        store.update_job(retried['id'], result=value)
    again = asyncio.run(auto.run_auto_review(store, retried, checkpoint=checkpoint, cancelled=lambda: False))
    assert again['completed'] == 4 and len(calls) == 4


@pytest.mark.parametrize('name,args', [('read_evidence', {'citation_id': []}), ('compare_evidence', {'citation_ids': [{}]}),
    ('inspect_account', {'account_key': {}}), ('list_evidence', {'limit': True})])
def test_invalid_tool_inputs_fail_without_network(name, args):
    gateway = auto.ReviewGateway([], use_public_network=False)
    from app.workbench.harness_evidence import EvidenceGatewayError
    with pytest.raises(EvidenceGatewayError):
        asyncio.run(gateway.call(name, args))


def test_exact_owner_backlink_and_read_quote_allow_type_but_model_guess_does_not(store, monkeypatch):
    account, _, _ = seed(store)
    row = snapshot(store, ['accounts'])[0]
    gateway = auto.ReviewGateway([row])
    async def fetch(url, **kwargs):
        return '<header>公開団体の公式ページ<a href="https://x.com/peace_a">公式 X</a></header>'.encode(), url, 200
    monkeypatch.setattr(auto.pipeline, '_fetch_public_feed', fetch)
    evidence = {'citation_id': 'https://official.example.org/about', 'quote': '公開団体の公式ページ'}
    proof = asyncio.run(gateway.call('check_citation', {'citation_id': evidence['citation_id'], 'exact_quote': evidence['quote']}))
    trace = [{'name': 'check_citation', 'ok': True, 'result': proof}]
    answer = {'decisions': [{'kind': 'accounts', 'id': row['id'], 'outcome': 'organization', 'note': '独立主体説明と正確回链', 'evidence': [evidence]}]}
    with pytest.raises(PipelineError):
        auto.validate_decisions(answer, gateway, [])
    accepted = auto.validate_decisions(answer, gateway, trace)
    assert auto.validate_decisions(accepted, gateway, trace) == accepted
    save(store, auto._batch([row], accepted['decisions']), reviewer='deepseek_auto_review')
    assert store.get_account(account['id'])['status'] == 'candidate'  # host check is separate
    from app.workbench.account_verification import run_checks
    checks = asyncio.run(run_checks([store.get_account(account['id'])]))
    store.apply_account_checks(checks)
    assert store.get_account(account['id'])['status'] == 'verified'


def test_event_date_cannot_be_filled_from_publication_year(store):
    seed(store)
    row = snapshot(store, ['activities'])[0]
    gateway = auto.ReviewGateway([row])
    quote = row['record']['quote']
    proof = asyncio.run(gateway.call('check_citation', {'citation_id': f"activities:{row['id']}", 'exact_quote': quote}))
    answer = {'decisions': [{'kind': 'activities', 'id': row['id'], 'outcome': 'confirmed', 'date': '2025-10-05', 'prefecture': '東京都',
        'title': row['record']['title'], 'note': 'incorrect year', 'evidence': [{'citation_id': proof['citation_id'], 'quote': quote}]}]}
    with pytest.raises(PipelineError, match='年份'):
        auto.validate_decisions(answer, gateway, [{'name': 'check_citation', 'ok': True, 'result': proof}])


def test_explicit_retry_skips_committed_batch_after_process_failure(store, monkeypatch):
    seed(store)
    calls = []
    async def harness(items, context, reviews, **options):
        gateway = options['_gateway']
        calls.append(gateway.rows[0]['id'])
        value = {'decisions': [{'kind': row['kind'], 'id': row['id'], 'outcome': 'unknown' if row['kind'] == 'accounts' else 'inconclusive',
            'note': '不足以判断', 'evidence': []} for row in gateway.rows]}
        return {'findings': options['_validator'](value, gateway, []), 'model_calls': 1, 'tool_calls': 0, 'trace': []}
    monkeypatch.setattr(auto, 'run_evidence_harness', harness)
    from app.workbench.review import digest
    rows = snapshot(store, ['accounts', 'claims', 'activities'])
    job = start_review(store, {'batch_size': 1, 'use_public_network': False}, expected_digest=digest(rows))
    async def failing_checkpoint(value):
        if value['completed'] == 1:
            raise OSError('process interruption after atomic commit')
        store.update_job(job['id'], result=value)
    with pytest.raises(OSError):
        asyncio.run(auto.run_auto_review(store, job, checkpoint=failing_checkpoint, cancelled=lambda: False))
    assert store.get_job(job['id'])['result']['completed'] == 1
    retried = store.create_job('collect', {**job['params'], '_workbench_retry': {'parent_job_id': job['id']}})
    async def checkpoint(value):
        store.update_job(retried['id'], result=value)
    result = asyncio.run(auto.run_auto_review(store, retried, checkpoint=checkpoint, cancelled=lambda: False))
    assert result['completed'] == 4 and len(calls) == len(set(calls)) == 4


def test_agent_review_scope_preview_apply_and_replay_are_shared_atomic(store, tmp_path, monkeypatch):
    from test_workbench_agent_api import local_client, grant, execution
    async def scenario():
        async with local_client(tmp_path, monkeypatch) as (app, client):
            seed(app.state.store)
            read_headers, _ = await grant(client, tmp_path, scopes=('read',), name='read')
            body = {'operation': 'review.queue', 'arguments': {'kind': 'claims'}}
            found = await client.post('/agents/v1/read', headers=read_headers, json=body)
            assert found.status_code == 200
            values = decisions(found.json()['items'])
            request = {'operation': 'review.batch', 'arguments': values}
            assert (await client.post('/agents/v1/preview', headers=read_headers, json=request)).status_code == 403
            headers, _ = await grant(client, tmp_path, scopes=('read', 'review'), name='review')
            prior = await client.post('/agents/v1/preview', headers=headers, json=request)
            assert prior.status_code == 200 and prior.json()['preview']['created_jobs'] == 0
            payload = execution(prior.json(), 'review-action-001')
            applied = await client.post('/agents/v1/execute', headers=headers, json=payload)
            assert applied.status_code == 200 and applied.json()['applied'] == 1
            repeated = await client.post('/agents/v1/execute', headers=headers, json=payload)
            assert repeated.headers['x-peace-atlas-replayed'] == 'true' and repeated.json() == applied.json()
            assert review_queue(app.state.store, kind='claims')['total'] == 0
    asyncio.run(scenario())
