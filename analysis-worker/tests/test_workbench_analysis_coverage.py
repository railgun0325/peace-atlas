import asyncio
import copy
from types import SimpleNamespace

import pytest

from app.workbench import relationship_analysis as relationships
from app.workbench.pipeline import PipelineError


def profile(handle):
    return {'account_key': 'twitter:' + handle, 'name': handle, 'profile': {
        'verified_in_dom': True, 'url': f'https://x.com/{handle}/',
        'bio': '所属：公开团体A。', 'observed_at': '2026-10-09T00:00:00Z'}}


def posts(count, handle='alpha'):
    return [{'account_key': 'twitter:' + handle, 'url': f'https://x.com/{handle}/status/{index+1}',
        'content': f'公开材料 {index+1}', 'published_at': '2026-10-08T00:00:00Z',
        'metadata': {'collected_at': '2026-10-09T00:00:00Z'}} for index in range(count)]


def run(coroutine):
    return asyncio.run(coroutine)


def test_more_than_80_entries_are_reviewed_in_bounded_batches(monkeypatch):
    inputs = []
    async def model(system, data, **options):
        inputs.append(copy.deepcopy(data['evidence']))
        assert len(data['evidence']) <= 80
        return {'affiliation_candidates': []}
    monkeypatch.setattr(relationships, 'configured_ai_json', model)
    accounts, items = [profile('alpha'), profile('beta')], posts(120) + posts(1, 'beta')
    result = run(relationships.analyze_relationships_with_ai(accounts, items, [], use_ai=True))
    expected = {row['id'] for row in result['evidence']}
    supplied = {row['id'] for batch in inputs for row in batch}
    assert supplied == expected, f'Unreviewed entries: {len(expected - supplied)}'
    assert len(inputs) >= 2
    assert result['ai']['complete'] and result['ai']['input_evidence_count'] == len(expected)
    assert result['ai']['pending_evidence_count'] == 0


def test_relationship_batch_uses_provider_capacity_instead_of_old_character_quota(monkeypatch):
    import json
    import httpx
    from app.workbench.ai_capacity import assess_messages, compact_json, configured_model_capacity
    from app.workbench import query_plan
    accounts = [profile(f'org_{index}') for index in range(20)]
    for account in accounts:
        account['profile']['bio'] = '公开组织的来源自称。' * 125
        account['profile']['links'] = ['https://public.example/' + ('a' * 650) + f'/{index}' for index in range(10)]
    items = posts(60, 'org_0')
    for item in items:
        item['content'] = '公开声明的文字仅说明来源自称，关系和外部事实未独立核验。' * 50
    original_client = httpx.AsyncClient
    calls = []
    def handle(request):
        payload = json.loads(request.content)
        data = json.loads(payload['messages'][1]['content'])
        calls.append(data)
        assert len(data['evidence']) <= 80
        assert assess_messages(payload['messages'], max_output_tokens=payload['max_tokens'],
            capacity=configured_model_capacity()).fits
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
            'message': {'content': '{"affiliation_candidates":[]}'}}]})
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'fixture-key')
    monkeypatch.setenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com')
    monkeypatch.setenv('DEEPSEEK_MODEL', 'deepseek-flash')
    monkeypatch.delenv('DEEPSEEK_CONTEXT_WINDOW_TOKENS', raising=False)
    monkeypatch.delenv('DEEPSEEK_MAX_OUTPUT_TOKENS', raising=False)
    monkeypatch.setattr(relationships, 'configured_ai_json', query_plan.configured_ai_json)
    monkeypatch.setattr(query_plan.httpx, 'AsyncClient', lambda **options:
        original_client(transport=httpx.MockTransport(handle), **options))
    result = run(relationships.analyze_relationships_with_ai(accounts, items, [], use_ai=True))
    assert calls and max(len(compact_json(data)) for data in calls) > 180000
    assert result['ai']['complete'] and result['ai']['pending_evidence_count'] == 0
    assert result['ai']['input_evidence_count'] == result['ai']['available_evidence_count']


def test_late_material_is_not_excluded_by_a_1000_item_catalogue_limit():
    items = posts(1005)
    items[-1]['content'] = '最后一条公开正文明确提及 @beta。'
    result = relationships.analyze_relationships([profile('alpha'), profile('beta')], items, [])
    assert len([row for row in result['evidence'] if row['type'] == 'post']) == 1005
    assert any(row['relation'] == 'mentions' and row['target_key'] == 'twitter:beta'
        and items[-1]['url'] in row['evidence_urls'] for row in result['findings'])


def test_failed_second_batch_retains_first_and_retry_reviews_only_pending(monkeypatch):
    accounts, items = [profile('alpha')], posts(121)
    attempted, checkpoints = [], []
    async def model(system, data, **options):
        attempted.append({row['id'] for row in data['evidence']})
        if len(attempted) == 2:
            raise PipelineError('可控的第二批请求失败。')
        source = next(row for row in data['evidence'] if row['type'] == 'profile')
        return {'affiliation_candidates': [{'account_key': 'twitter:alpha', 'organization': '公开团体A',
            'claim': '公开简介有所属自述，仍待独立核对。', 'evidence_ids': [source['id']]}]}
    async def checkpoint(result):
        checkpoints.append(copy.deepcopy(result))
    monkeypatch.setattr(relationships, 'configured_ai_json', model)
    partial = run(relationships.analyze_relationships_with_ai(accounts, items, [], use_ai=True, checkpoint=checkpoint))
    assert partial['ai']['used'] and not partial['ai']['complete']
    assert partial['ai']['outcome'] == 'partial' and partial['ai']['pending_evidence_count'] > 0
    assert len([row for row in partial['affiliations'] if row['method'] == 'configured_ai']) == 1 and checkpoints
    checkpointed = checkpoints[-1]
    attempted.clear()
    async def resumed_model(system, data, **options):
        attempted.append({row['id'] for row in data['evidence']})
        return {'affiliation_candidates': []}
    monkeypatch.setattr(relationships, 'configured_ai_json', resumed_model)
    complete = run(relationships.analyze_relationships_with_ai(accounts, items, [], use_ai=True,
        previous_analysis=checkpointed))
    assert complete['ai']['complete'] and complete['ai']['pending_evidence_count'] == 0
    assert len(attempted) == 1 and len([row for row in complete['affiliations'] if row['method'] == 'configured_ai']) == 1
    assert complete['ai']['resumed_batches'] == 1


def test_changed_body_invalidates_saved_review_even_when_post_id_is_same(monkeypatch):
    calls = []
    async def model(system, data, **options):
        calls.append(copy.deepcopy(data['evidence']))
        return {'affiliation_candidates': []}
    monkeypatch.setattr(relationships, 'configured_ai_json', model)
    accounts, items = [profile('alpha')], posts(3)
    previous = run(relationships.analyze_relationships_with_ai(accounts, items, [], use_ai=True))
    items[-1]['content'] = '原帖更正为另一段公开内容。'
    calls.clear()
    result = run(relationships.analyze_relationships_with_ai(accounts, items, [], use_ai=True, previous_analysis=previous))
    assert calls and result['ai']['complete'] and result['ai']['resumed_batches'] == 0
    assert any(row['excerpt'] == items[-1]['content'] for row in calls[0])


def test_budget_boundary_preserves_reviewed_batches_and_reports_pending(monkeypatch):
    clock = SimpleNamespace(value=0.0)
    monkeypatch.setattr(relationships, 'time', SimpleNamespace(monotonic=lambda: clock.value))
    calls = []
    async def model(system, data, **options):
        calls.append(data)
        clock.value += 45
        return {'affiliation_candidates': []}
    monkeypatch.setattr(relationships, 'configured_ai_json', model)
    result = run(relationships.analyze_relationships_with_ai([profile('alpha')], posts(121), [],
        use_ai=True, time_budget_seconds=50))
    assert len(calls) == 1 and result['ai']['used'] and not result['ai']['complete']
    assert result['ai']['pending_evidence_count'] > 0 and result['ai']['stop_reason'] == 'time_budget'


def test_cancellation_propagates_after_successful_batch_checkpoint(monkeypatch):
    checkpoints, calls = [], []
    async def model(system, data, **options):
        calls.append(data)
        if len(calls) == 2:
            raise asyncio.CancelledError()
        return {'affiliation_candidates': []}
    async def checkpoint(result):
        checkpoints.append(copy.deepcopy(result))
    monkeypatch.setattr(relationships, 'configured_ai_json', model)
    with pytest.raises(asyncio.CancelledError):
        run(relationships.analyze_relationships_with_ai([profile('alpha')], posts(121), [],
            use_ai=True, checkpoint=checkpoint))
    assert checkpoints and checkpoints[-1]['ai']['input_evidence_count'] > 0
    assert checkpoints[-1]['ai']['pending_evidence_count'] > 0


def test_completed_saved_review_makes_no_repeat_model_request(monkeypatch):
    async def model(system, data, **options):
        return {'affiliation_candidates': []}
    monkeypatch.setattr(relationships, 'configured_ai_json', model)
    accounts, items = [profile('alpha')], posts(90)
    previous = run(relationships.analyze_relationships_with_ai(accounts, items, [], use_ai=True))
    async def forbidden(*args, **kwargs):
        raise AssertionError('Same completed evidence was submitted again')
    monkeypatch.setattr(relationships, 'configured_ai_json', forbidden)
    result = run(relationships.analyze_relationships_with_ai(accounts, items, [], use_ai=True, previous_analysis=previous))
    assert result['ai']['complete'] and result['ai']['pending_evidence_count'] == 0


def test_change_after_excerpt_limit_invalidates_the_original_batch(monkeypatch):
    calls = []
    async def model(system, data, **options):
        calls.append(data)
        return {'affiliation_candidates': []}
    monkeypatch.setattr(relationships, 'configured_ai_json', model)
    accounts, items = [profile('alpha')], posts(1)
    items[0]['content'] = '公开正文' * 500 + '原版'
    previous = run(relationships.analyze_relationships_with_ai(accounts, items, [], use_ai=True))
    items[0]['content'] = '公开正文' * 500 + '更正版'
    calls.clear()
    result = run(relationships.analyze_relationships_with_ai(accounts, items, [], use_ai=True, previous_analysis=previous))
    assert calls and result['ai']['resumed_batches'] == 0 and result['ai']['complete']


def test_full_body_rule_mentions_after_the_excerpt_are_preserved():
    items = posts(1)
    items[0]['content'] = '公开活动正文' * 500 + ' @beta'
    result = relationships.analyze_relationships([profile('alpha'), profile('beta')], items, [])
    assert any(row['relation'] == 'mentions' and row['target_key'] == 'twitter:beta' for row in result['findings'])


def test_follows_only_candidate_is_filtered_with_an_explanation(monkeypatch):
    async def model(system, data, **options):
        source = next(row for row in data['evidence'] if row['type'] == 'visible_follow_list')
        return {'affiliation_candidates': [{'account_key': 'twitter:alpha', 'organization': '公开团体B',
            'claim': '仅凭关注列表不能判断所属。', 'evidence_ids': [source['id']]}]}
    monkeypatch.setattr(relationships, 'configured_ai_json', model)
    result = run(relationships.analyze_relationships_with_ai([profile('alpha'), profile('beta')], [],
        [{'source_key': 'twitter:alpha', 'target_key': 'twitter:beta', 'relation': 'follows',
          'evidence_url': 'https://x.com/alpha/following/'}], use_ai=True))
    assert result['ai']['complete'] and result['ai']['rejected_candidate_count'] == 1
    assert not any(row['method'] == 'configured_ai' for row in result['affiliations'])
    assert result['ai']['rejected_candidates'][0]['reason']


def test_maximum_public_snapshot_review_state_fits_storage_budget(monkeypatch):
    import json
    from app.workbench.research_engine import compact_analysis_catalog
    async def model(system, data, **options):
        return {'affiliation_candidates': []}
    monkeypatch.setattr(relationships, 'configured_ai_json', model)
    result = run(relationships.analyze_relationships_with_ai([profile(f'author{i}') for i in range(40)],
        posts(10000, 'author0'), [], use_ai=True, time_budget_seconds=600))
    compact = compact_analysis_catalog(result)
    assert result['ai']['complete']
    assert len(json.dumps(compact, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) <= 1500000


def test_conflicting_subject_or_direction_cannot_overwrite_a_citation():
    relations = [{'source_key': 'twitter:alpha', 'target_key': 'twitter:beta', 'relation': 'follows',
                  'evidence_id': 'shared-id', 'evidence_url': 'https://x.com/alpha/following/'},
                 {'source_key': 'twitter:beta', 'target_key': 'twitter:alpha', 'relation': 'follows',
                  'evidence_id': 'shared-id', 'evidence_url': 'https://x.com/beta/following/'}]
    with pytest.raises(PipelineError, match='证据 ID.*冲突'):
        relationships.analyze_relationships([profile('alpha'), profile('beta')], [], relations)


def test_reverse_follow_finding_is_rejected_at_the_checkpoint_boundary():
    from app.workbench.research_export import checkpoint_payload
    accounts = [profile('alpha'), profile('beta')]
    analysis = relationships.analyze_relationships(accounts, [], [{'source_key': 'twitter:alpha',
        'target_key': 'twitter:beta', 'relation': 'follows', 'evidence_url': 'https://x.com/alpha/following/'}])
    analysis['findings'][0].update(source_key='twitter:beta', target_key='twitter:alpha')
    result = checkpoint_payload({'nodes': [{'account_key': row['account_key']} for row in accounts], 'analysis': analysis})
    assert not result['edges'] and not result['analysis']['findings']
    assert any(row['outcome'] == 'invalid_finding_evidence' and row['ok'] is False for row in result['diagnostics'])


def test_rejected_candidates_keep_their_audit_when_review_resumes(monkeypatch):
    calls = []
    async def model(system, data, **options):
        calls.append(data)
        if len(calls) == 2:
            raise PipelineError('可控后续批失败')
        source = next(row for row in data['evidence'] if row['type'] == 'profile')
        return {'affiliation_candidates': [{'account_key': 'twitter:alpha', 'organization': '未提及的团体B',
            'claim': '该名称缺少本人文本支持。', 'evidence_ids': [source['id']]}]}
    monkeypatch.setattr(relationships, 'configured_ai_json', model)
    accounts, items = [profile('alpha')], posts(100)
    partial = run(relationships.analyze_relationships_with_ai(accounts, items, [], use_ai=True))
    assert partial['ai']['rejected_candidate_count'] == 1
    rejected = partial['ai']['rejected_candidates'][0]
    assert rejected['claim'] == '该名称缺少本人文本支持。'
    assert rejected['evidence_urls'] == [accounts[0]['profile']['url']]
    assert rejected['review_batch_id'] == partial['ai']['review_batches'][0]['id']
    async def resumed_model(system, data, **options):
        return {'affiliation_candidates': []}
    monkeypatch.setattr(relationships, 'configured_ai_json', resumed_model)
    completed = run(relationships.analyze_relationships_with_ai(accounts, items, [], use_ai=True, previous_analysis=partial))
    assert completed['ai']['complete'] and completed['ai']['rejected_candidate_count'] == 1
    assert completed['ai']['rejected_candidates'] == partial['ai']['rejected_candidates']


def test_profile_links_are_not_reworded_as_independently_official(monkeypatch):
    account = profile('wptr_cassiopeia')
    account['profile']['bio'] = '現代革命労働者党機関紙『碇星』／ご購入はBASEから／碇星放送局から公開情報'
    original = '简介自述为现代革命劳动者党机关报《碇星》，并附官方店铺与频道链接标识。'
    async def model(system, data, **options):
        return {'affiliation_candidates': [{'account_key': account['account_key'],
            'organization': '現代革命労働者党機関紙『碇星』', 'claim': original,
            'evidence_ids': [next(row['id'] for row in data['evidence'] if row['type'] == 'profile')]}]}
    monkeypatch.setattr(relationships, 'configured_ai_json', model)
    result = run(relationships.analyze_relationships_with_ai([account], [], [], use_ai=True))
    candidate = next(row for row in result['affiliations'] if row['method'] == 'configured_ai')
    assert '官方店铺' not in candidate['claim'] and candidate['model_claim'] == original
    assert candidate['claim_corrections'] and candidate['requires_review']
    resumed = run(relationships.analyze_relationships_with_ai([account], [], [], use_ai=True, previous_analysis=result))
    restored = next(row for row in resumed['affiliations'] if row['method'] == 'configured_ai')
    assert restored['model_claim'] == original and resumed['ai']['calls_made'] == 0


def test_invalidated_batch_does_not_keep_its_rejection_audit(monkeypatch):
    calls = []
    async def model(system, data, **options):
        calls.append(data)
        if len(calls) == 1:
            return {'affiliation_candidates': []}
        source = next(row for row in data['evidence'] if row['type'] == 'profile')
        return {'affiliation_candidates': [{'account_key': 'twitter:alpha', 'organization': '无支持的组织B',
            'claim': '缺少本人文本支持。', 'evidence_ids': [source['id']]}]}
    monkeypatch.setattr(relationships, 'configured_ai_json', model)
    accounts, items = [profile('alpha')], posts(100)
    previous = run(relationships.analyze_relationships_with_ai(accounts, items, [], use_ai=True))
    assert previous['ai']['rejected_candidate_count'] == 1
    changed = next(row for row in calls[1]['evidence'] if row['type'] == 'post')
    next(row for row in items if row['url'] == changed['url'])['content'] += ' 新正文'
    async def resumed_model(system, data, **options):
        return {'affiliation_candidates': []}
    monkeypatch.setattr(relationships, 'configured_ai_json', resumed_model)
    current = run(relationships.analyze_relationships_with_ai(accounts, items, [], use_ai=True, previous_analysis=previous))
    assert current['ai']['resumed_batches'] == 1 and current['ai']['calls_made'] == 1
    assert current['ai']['rejected_candidate_count'] == 0 and current['ai']['rejected_candidates'] == []


def test_introducing_publication_pages_is_only_a_content_association(monkeypatch):
    account = profile('zenshinch')
    account['profile']['bio'] = '正在YouTube上介绍中核派机关报《前進》的版面。'
    original = '简介介绍机关报版面，这是组织机关报署名，可能存在隶属关系。'
    async def model(system, data, **options):
        return {'affiliation_candidates': [{'account_key': account['account_key'], 'organization': '中核派',
            'claim': original, 'evidence_ids': [next(row['id'] for row in data['evidence'] if row['type'] == 'profile')]}]}
    monkeypatch.setattr(relationships, 'configured_ai_json', model)
    result = run(relationships.analyze_relationships_with_ai([account], [], [], use_ai=True))
    candidate = next(row for row in result['affiliations'] if row['method'] == 'configured_ai')
    assert candidate['support_kind'] == 'publication_content_reference'
    assert '仅是内容关联线索' in candidate['claim'] and '不足以判断组织隶属' in candidate['claim']
    assert '署名' not in candidate['claim'] and candidate['model_claim'] == original
    assert candidate['requires_review'] and candidate['status'] == 'candidate'
    resumed = run(relationships.analyze_relationships_with_ai([account], [], [], use_ai=True, previous_analysis=result))
    restored = next(row for row in resumed['affiliations'] if row['method'] == 'configured_ai')
    assert restored['model_claim'] == original and resumed['ai']['calls_made'] == 0
