"""Account walks must select deeper candidates using cited saved evidence."""
import asyncio
import copy

import pytest

from app.workbench import research_engine as engine
from app.workbench.relationship_analysis import analyze_relationships
from test_research_engine import adapter, following, params, prof, post


def prepare(adapter, monkeypatch):
    adapter.profiles['seed'] = prof('seed', '')
    adapter.post_results['seed'] = [post(text='公開資料から次のアカウントを調べる。')]
    first = following('seed', 'aa_outer')
    second = following('seed', 'zz_bridge')
    adapter.neighbor_results[('seed', 'following')] = {
        'accounts': first['accounts'] + second['accounts'],
        'relations': first['relations'] + second['relations'], 'diagnostics': []}
    adapter.neighbor_results[('zz_bridge', 'following')] = following('zz_bridge', 'deep_node')
    for handle in ['aa_outer', 'zz_bridge', 'deep_node']:
        adapter.profiles[handle] = prof(handle, '')

    async def final(accounts, items, relations, **kwargs):
        result = analyze_relationships(accounts, items, relations)
        result['ai'] = {'requested': True, 'used': True, 'complete': True, 'outcome': 'ok'}
        return result
    monkeypatch.setattr(engine, 'analyze_relationships_with_ai', final)


def test_account_walk_ai_prioritizes_next_layer_and_keeps_outer_candidates(adapter, monkeypatch):
    prepare(adapter, monkeypatch)
    calls, checkpoints = [], []
    async def model(system, data, **kwargs):
        calls.append(copy.deepcopy(data))
        chosen = next(row for row in data['candidate_accounts'] if row['account_key'] in {'twitter:zz_bridge','twitter:deep_node'})
        return {'priorities': [{'account_key': chosen['account_key'], 'priority': 99,
            'reason': '先核对已经观察到的公开关系，再查看下一层。',
            'evidence_ids': chosen['supporting_evidence_ids']} ]}
    monkeypatch.setattr(engine, 'configured_ai_json', model)
    result = asyncio.run(engine.run_research(params(mode='account', handle='seed', keyword='',
        analysis_mode='ai', time_budget_seconds=600, expand_relations=['following']), checkpoint=checkpoints.append))
    assert calls, 'Account-scoped research currently skips the AI selector entirely'
    profile_order = [row[2] for row in adapter.instances[0].calls if row[0] == 'profile']
    assert profile_order.index('zz_bridge') < profile_order.index('aa_outer')
    assert profile_order.index('deep_node') < profile_order.index('aa_outer')
    assert {row['account_key'] for row in result['nodes']} == {'twitter:seed','twitter:zz_bridge','twitter:aa_outer','twitter:deep_node'}
    chosen = next(row for row in result['nodes'] if row['account_key'] == 'twitter:zz_bridge')
    assert chosen['priority_method'] == 'configured_ai_account_scope' and chosen['priority_evidence_ids']
    assert any(row['action'] == 'reprioritize_account_frontier' and row['ai_used'] for row in result['decisions'])
    assert any(any(n.get('priority_method') == 'configured_ai_account_scope' for n in row['nodes']) for row in checkpoints)
    assert not any(row[0] == 'search' for row in adapter.instances[0].calls)
    assert all(row['status'] == 'candidate' and not row['organization'] for row in result['accounts'])


@pytest.mark.parametrize('mutation', ['unknown_account','unknown_evidence','other_candidate_evidence','duplicate_account','uncited','bad_priority'])
def test_account_selector_rejects_whole_invalid_decision_without_partial_writes(adapter, monkeypatch, mutation):
    prepare(adapter, monkeypatch)
    async def model(system, data, **kwargs):
        chosen = next(row for row in data['candidate_accounts'] if row['account_key'] == 'twitter:zz_bridge')
        row = {'account_key': chosen['account_key'], 'priority': 100, 'reason': '公开关系待核对。', 'evidence_ids': chosen['supporting_evidence_ids']}
        if mutation == 'unknown_account': row['account_key'] = 'twitter:invented'
        if mutation == 'unknown_evidence': row['evidence_ids'] = ['invented-source']
        if mutation == 'other_candidate_evidence':
            # A real input source cannot be used to explain an unrelated account.
            row['evidence_ids'] = [next(e['id'] for e in data['evidence'] if e.get('target_key') == 'twitter:aa_outer')]
        if mutation == 'uncited': row['evidence_ids'] = []
        if mutation == 'bad_priority': row['priority'] = True
        values = [row, copy.deepcopy(row)] if mutation == 'duplicate_account' else [row]
        return {'priorities': values}
    monkeypatch.setattr(engine, 'configured_ai_json', model)
    result = asyncio.run(engine.run_research(params(mode='account', handle='seed', keyword='', analysis_mode='ai',
        time_budget_seconds=600, expand_relations=['following'])))
    assert any(row['action'] == 'ai_decision_failed' for row in result['decisions'])
    assert not any(row.get('priority_method') == 'configured_ai_account_scope' for row in result['nodes'])
    assert 'twitter:invented' not in {row['account_key'] for row in result['accounts']}


def test_account_selector_is_not_used_in_rules_mode(adapter, monkeypatch):
    prepare(adapter, monkeypatch)
    async def forbidden(*args, **kwargs):
        raise AssertionError('No model requests in rules mode')
    monkeypatch.setattr(engine, 'configured_ai_json', forbidden)
    result = asyncio.run(engine.run_research(params(mode='account', handle='seed', keyword='',
        analysis_mode='rules', expand_relations=['following'])))
    assert not any(row['action'] == 'reprioritize_account_frontier' for row in result['decisions'])
