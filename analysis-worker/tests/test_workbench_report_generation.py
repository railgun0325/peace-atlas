"""Behavior regressions for bounded drafts and resumable source-based review.

Every transport response is a fixture; these tests do not certify external
facts or contact the model, a formal DB, a browser session or a packaged app.
"""
import asyncio
import copy
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from app.workbench import report_analysis as reports
from app.workbench import report_generation as generation
from app.workbench import report_synthesis as synthesis
from app.workbench.ai_capacity import ModelCapacity, assess_messages, compact_json, configured_model_capacity
from app.workbench.pipeline import PipelineError


VALID = (
    '# 保存来源研究\n\n## 身份与简介\n\n'
    'peace_a 简介自称全学連（矢嶋委員長）関西，并介绍現代革命労働者党機関紙「碇星」；'
    '简介文本出现 @peace_b，仅支持单向简介提及，身份仍为候选。[S001]\n\n'
    '观察时点记录为 peace_a 关注 peace_b；不证明互认、背书或隶属。[S002]\n\n'
    '帖子发布日期按 JST 为2026-10-09，来源称计划在10月10日讨论，计划不证明实际举行。[S003]\n\n'
    '材料不足以独立核验全面关系；本次已访问2个账号，3个预算跳过。[S001,S002,S003]'
)
NO_MENTION = '# 保存来源研究\n\npeace_a和peace_b之间没有提及记录。[S001,S002]\n\n计划仍待核验。[S003]'
ACCEPT = {'accepted': True, 'issues': []}
MACHINE_MARKER = '## 运行元数据〔程序生成〕'
FAKE_RUNTIME = ('程序统计声称已访问9999个账号，并证实全部团体相互背书。[S999]\n\n'
                '预算停止直接导致零关系；虚构证据见 https://unprovided.example/fabricated 。')


def ended_completion(finish_reason='length', content=''):
    """Raw provider completion, separate from a reviewer JSON object."""
    return SimpleNamespace(finish_reason=finish_reason, content=content,
                           reasoning_content='fixture-hidden-reasoning-must-not-be-saved')


def review_issue(code='target_coverage', identities=None, excerpt='没有提及记录'):
    return {'accepted': False, 'issues': [{'code': code,
        'explanation': '保存简介明确包含单向句柄提及，应与原帖及双向关系区别，并保留自称限定。',
        'citation_ids': ['S001'] if identities is None else identities, 'excerpt': excerpt}]}


def case(*, goal='核对简介提及、单向关注和计划时间', profile_suffix=''):
    items = [
        {'citation_id': 'S001', 'title': 'peace_a 公開プロフィール',
         'url': 'https://x.com/peace_a', 'source_name': 'twitter:peace_a',
         'content': '全学連（矢嶋委員長）関西。現代革命労働者党機関紙「碇星」。@peace_b に言及。' + profile_suffix,
         'metadata': {'research_report_source_role': 'public_profile', 'account_key': 'twitter:peace_a',
                      'account_status': 'candidate', 'body_completeness': 'saved_public_profile'}},
        {'citation_id': 'S002', 'title': '公开关注列表观察',
         'url': 'https://x.com/peace_a/following', 'source_name': 'twitter:peace_a',
         'content': '观察时点列表显示 peace_a 关注 peace_b。',
         'metadata': {'evidence_kind': 'visible_follow_list', 'relation_type': 'follows',
                      'source_key': 'twitter:peace_a', 'target_key': 'twitter:peace_b',
                      'observed_at': '2026-10-09T00:40:00+09:00'}},
        {'citation_id': 'S003', 'title': '计划公开讨论',
         'url': 'https://x.com/peace_a/status/3', 'source_name': 'twitter:peace_a',
         'content': '主催者は10月10日に公開討論を計画すると述べた。',
         'published_at': '2026-10-08T16:30:00+00:00',
         'publication_day': '2026-10-09',
         'metadata': {'content_kind': 'public_post_body', 'account_key': 'twitter:peace_a'}},
    ]
    context = {'run_id': 'fixture-run', 'status': 'partial', 'research_goal': goal,
               'accounts': [{'account_key': 'twitter:peace_a', 'name': '甲団体', 'status': 'candidate'},
                            {'account_key': 'twitter:peace_b', 'name': '乙団体', 'status': 'candidate'}],
               'summary': {'visited_count': 2, 'account_budget_skipped': 3},
               'diagnostics': [{'platform': 'twitter', 'account_key': 'twitter:peace_a',
                   'stage': 'following', 'source_url': 'https://x.com/peace_a/following',
                   'ok': True, 'raw_count': 8, 'accepted_count': 1}]}
    sources, coverage = reports.source_samples(items)
    response = {'source_reviews': [{'citation_id': source['citation_id'],
        'quote': source['content_fragments'][0]['text'][:35],
        'reading': '保存文本已阅读，来源身份和关系限定仍须区分。',
        'limitations': '原样引用仅证明保存文本匹配，不能独立核验外部真相。'} for source in sources]}
    reviews = reports.validate_source_reviews(response, sources)
    data = {'title': '来源研究', 'scope': {'start_date': '2026-10-09', 'end_date': '2026-10-09'},
            'sources': reports.synthesis_sources(sources), 'source_reviews': reviews,
            'coverage': coverage, 'analysis_context': reports.report_context(sources, coverage, context)}
    payload = {'model': 'deepseek-flash', 'max_tokens': 32768, 'reasoning_effort': 'high',
               'temperature': 0.2, 'messages': [{'role': 'system', 'content': '根据保存来源写报告，引用用[S001]；不能补造事实。'},
                                              {'role': 'user', 'content': compact_json(data)}]}
    return payload, data, items, context


class ScriptedModel:
    def __init__(self, steps, allow_compression=False):
        self.steps = list(steps)
        self.calls = []
        self.allow_compression = allow_compression

    async def handle(self, request):
        assert request.headers['Authorization'] == 'Bearer fixture-key'
        payload = json.loads(request.content)
        data = json.loads(payload['messages'][1]['content'])
        mode = ('compression' if 'compression_level' in data and 'records' in data
                else 'review' if 'candidate_report' in data else 'generation')
        if data.get('compression_review') and self.allow_compression:
            value = {'accepted': True, 'issues': [],
                     'reviewed_finding_indexes': list(range(len(data['candidate_notes']['findings']))),
                     'reviewed_unit_ids': [row['unit_id'] for row in data['evidence_units']]}
        elif mode == 'compression' and self.allow_compression:
            identities = sorted({identity for row in data['records'] for identity in row['represented_citation_ids']})
            value = {'findings': [{'statement': '已保存来源的限定说法，外部真相仍待核验。',
                'citation_ids': [identity], 'kind': 'uncertainty',
                'support_refs': [{'unit_id': row['unit_id'], 'citation_id': identity, 'quote': row['reading']['quote']}
                                 for row in data['evidence_units'] if row['citation_id'] == identity]} for identity in identities],
                'limitations': ['模拟压缩仅用于检查续跑与字段保留。']}
        else:
            assert self.steps, f'Unexpected extra {mode} call'
            expected, value = self.steps.pop(0)
            assert mode == expected
        capacity = configured_model_capacity(model=payload['model'], base_url=str(request.url))
        assert assess_messages(payload['messages'], max_output_tokens=payload['max_tokens'], capacity=capacity).fits
        self.calls.append({'mode': mode, 'payload': copy.deepcopy(payload), 'data': data})
        if value == 'cancel':
            raise asyncio.CancelledError()
        if isinstance(value, int):
            return httpx.Response(value, json={'error': {'message': 'synthetic service failure'}})
        if isinstance(value, SimpleNamespace):
            return httpx.Response(200, json={'choices': [{'finish_reason': value.finish_reason,
                'message': {'content': value.content, 'reasoning_content': value.reasoning_content}}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': payload['max_tokens'],
                          'total_tokens': 100 + payload['max_tokens']}})
        content = json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': content}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150}})


@pytest.fixture
def install_model(monkeypatch):
    for name in ('DEEPSEEK_CONTEXT_WINDOW_TOKENS', 'DEEPSEEK_MAX_OUTPUT_TOKENS', 'DEEPSEEK_TIMEOUT_SECONDS'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'fixture-key')
    monkeypatch.setenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com')
    monkeypatch.setenv('DEEPSEEK_MODEL', 'deepseek-flash')
    native = httpx.AsyncClient

    def install(steps, *, allow_compression=False):
        scripted = ScriptedModel(steps, allow_compression=allow_compression)
        monkeypatch.setattr(generation.httpx, 'AsyncClient', lambda **options: native(
            transport=httpx.MockTransport(scripted.handle), **options))
        return scripted

    return install


def run(value, *, previous=None, checkpoints=None, **options):
    payload, data, items, context = value
    return asyncio.run(generation.generate_checked_report(payload, data, items, context,
        previous_state=previous, checkpoint=None if checkpoints is None else checkpoints.append, **options))


def reverse_mapping_order(value):
    """Change recursive dict insertion order while preserving all list order."""
    if isinstance(value, dict):
        return {key: reverse_mapping_order(value[key]) for key in reversed(value)}
    if isinstance(value, list):
        return [reverse_mapping_order(row) for row in value]
    if isinstance(value, tuple):
        return tuple(reverse_mapping_order(row) for row in value)
    return copy.deepcopy(value)


def news_case():
    payload, data, items, _ = case()
    item = copy.deepcopy(items[-1])
    item.update(source_name='news:fixture', content='原公告计划10月10日举行公开讨论。后续更正：本次活动取消，尚未举行。')
    item['metadata'] = {'content_kind': 'public_news_body'}
    sources, coverage = reports.source_samples([item])
    reviews = reports.validate_source_reviews({'source_reviews': [{
        'citation_id': 'S003', 'quote': '后续更正：本次活动取消，尚未举行。',
        'reading': '计划已撤回，应保留取消更正。', 'limitations': '保存来源主张，外部事实尚未核验。'}]}, sources)
    data.update(sources=reports.synthesis_sources(sources), source_reviews=reviews, coverage=coverage,
                analysis_context=reports.report_context(sources, coverage, {}))
    payload['messages'][1]['content'] = compact_json(data)
    return payload, data, [item], {}


def test_news_only_draft_must_pass_source_review_and_preserve_withdrawal(install_model):
    bad = '# 新闻来源研究\n\n公开讨论已按原计划举行，所有公告均无撤回。[S003]'
    good = '# 新闻来源研究\n\n来源后续更正活动取消，尚未举行；原计划不能证明实际活动。[S003]'
    rejection = {'accepted': False, 'issues': [{'code': 'source_support',
        'explanation': '正文更正已取消，报告遗漏撤回并将计划当作已举行。',
        'citation_ids': ['S003'], 'excerpt': '已按原计划举行'}]}
    model = install_model([('generation', bad), ('review', rejection), ('generation', good), ('review', ACCEPT)])
    result = run(news_case())
    assert [call['mode'] for call in model.calls] == ['generation', 'review', 'generation', 'review']
    assert result['markdown'] == good and result['source_review_requested']
    assert result['state']['attempts'][0]['source_review']['accepted'] is False
    assert result['state']['attempts'][1]['source_review']['accepted'] is True
    assert not result['review_is_independent_fact_verification']


def test_old_news_cache_without_review_is_reopened_without_draft_regeneration(install_model):
    markdown = '# 新闻来源研究\n\n保存来源更正活动取消，尚未举行。[S003]'
    install_model([('generation', markdown), ('review', ACCEPT)])
    previous = run(news_case())['state']
    previous.pop('review_policy_fingerprint', None)
    previous['attempts'][-1].pop('source_review', None)
    previous['attempts'][-1].pop('source_review_attempts', None)
    unchanged = copy.deepcopy(previous)
    model = install_model([('review', ACCEPT)])
    result = run(news_case(), previous=previous)
    assert [call['mode'] for call in model.calls] == ['review']
    assert result['markdown'] == markdown and result['state']['complete']
    assert len(result['state']['attempts']) == 1
    assert result['state']['attempts'][0]['source_review']['accepted']
    assert previous == unchanged


def test_semantic_review_input_compression_is_checkpointed_and_resumed_separately(install_model, monkeypatch):
    value = news_case()
    markdown = '# 新闻来源研究\n\n保存来源更正活动取消，尚未举行。[S003]'
    held_compression = {'version': 3, 'input_fingerprint': 'fixture-review-input',
        'complete': False, 'batches': {'accepted-original-batch': {'phase': 'reviewed'}}}
    calls = []
    fail_once = [True]
    async def fit(data, system, **options):
        candidate = data.get('candidate_report')
        if candidate:
            calls.append({'previous_state': copy.deepcopy(options.get('previous_state')),
                          'candidate_report': candidate})
            assert options.get('checkpoint') is not None
            if fail_once[0]:
                fail_once[0] = False
                await options['checkpoint'](copy.deepcopy(held_compression))
                raise PipelineError('模拟审核输入压缩中断，已通过批次必须保留。')
            assert options['previous_state'] == held_compression
            finished = copy.deepcopy(held_compression)
            finished['complete'] = True
            await options['checkpoint'](finished)
        assessment = assess_messages([{'role': 'system', 'content': system},
            {'role': 'user', 'content': compact_json(data)}], max_output_tokens=options['max_output_tokens'],
            capacity=options['capacity']).as_dict()
        return data, {'final_capacity': assessment}, None
    monkeypatch.setattr(generation, 'fit_report_input', fit)
    install_model([('generation', markdown)])
    checkpoints = []
    with pytest.raises(PipelineError, match='审核输入压缩中断'):
        run(value, checkpoints=checkpoints)
    held = copy.deepcopy(checkpoints[-1])
    assert held['attempts'][-1]['source_review_input_synthesis'] == held_compression
    model = install_model([('review', ACCEPT)])
    result = run(value, previous=held)
    assert [call['mode'] for call in model.calls] == ['review']
    assert len(calls) == 2 and calls[1]['previous_state'] == held_compression
    assert len(result['state']['attempts']) == 1
    assert result['state']['attempts'][-1]['source_review_input_synthesis']['complete']
    assert 'source_review_input_synthesis' not in result['state']['attempts'][-1].get('input_compression', {})


@pytest.mark.parametrize('invalid', [
    '# 报告\n\n来源说法。[S099]',
    '# 报告\n\n来源说法。[S001-S099]',
    VALID + '\n\n详细证据见 https://unprovided.example/fabricated 。[S001]',
], ids=['unknown_id', 'invalid_range', 'unprovided_url'])
def test_invalid_source_reference_requests_bounded_full_report_repair_and_saves_correction(install_model, invalid):
    model = install_model([('generation', invalid), ('generation', VALID), ('review', ACCEPT)])
    checkpoints = []
    result = run(case(), checkpoints=checkpoints)
    assert result['markdown'] == VALID
    assert [call['mode'] for call in model.calls] == ['generation', 'generation', 'review']
    assert model.calls[1]['data']['revision_request']['previous_markdown'] == invalid
    assert model.calls[1]['data']['revision_request']['issues']
    assert len(result['state']['attempts']) == 2
    assert not result['state']['attempts'][0]['accepted']
    assert result['state']['attempts'][1]['accepted']
    assert checkpoints[-1]['draft'] == VALID and checkpoints[-1]['complete']
    assert result['provider_usage']['total_tokens'] == 300
    assert result['review_is_independent_fact_verification'] is False


@pytest.mark.parametrize('draft,issue', [
    (NO_MENTION, review_issue()),
    (VALID.replace('JST 为2026-10-09', 'JST 为2026-10-08'), review_issue('date', ['S003'], 'JST 为2026-10-08')),
])
def test_source_reviewer_feedback_revises_profile_or_date_before_acceptance(install_model, draft, issue):
    model = install_model([('generation', draft), ('review', issue), ('generation', VALID), ('review', ACCEPT)])
    checkpoints = []
    result = run(case(), checkpoints=checkpoints)
    assert result['markdown'] == VALID and result['state']['complete']
    assert model.calls[2]['data']['revision_request']['issues'] == issue['issues']
    assert model.calls[2]['data']['revision_request']['previous_markdown'] == draft
    assert result['state']['attempts'][0]['source_review'] == issue
    assert result['state']['attempts'][0]['source_review_is_independent_fact_verification'] is False
    assert result['state']['attempts'][1]['accepted']
    if draft == NO_MENTION:
        advisory = model.calls[1]['data']['deterministic_review_advisories'][0]
        assert advisory['blocking'] is False and advisory['semantic_truth_established'] is False


def test_review_503_retry_reuses_checkpointed_draft_without_regeneration_or_reading(install_model, monkeypatch):
    async def forbidden_read(*args, **options):
        raise AssertionError('Already completed reading must not be repeated by draft recovery')
    monkeypatch.setattr(reports, 'read_sources', forbidden_read)
    first = install_model([('generation', VALID)] + [('review', 503)] * 3)
    checkpoints = []
    value = case()
    with pytest.raises(PipelineError, match='503'):
        run(value, checkpoints=checkpoints)
    held = copy.deepcopy(checkpoints[-1])
    assert held['draft'] == VALID and held['phase'] == 'review_pending' and not held['complete']
    assert len(held['attempts']) == 1 and not held['attempts'][0]['accepted']
    retry = install_model([('review', ACCEPT)])
    result = run(value, previous=held, checkpoints=checkpoints)
    assert result['state']['complete'] and result['markdown'] == VALID
    assert len(result['state']['attempts']) == 1
    assert [row['mode'] for row in first.calls] == ['generation', 'review', 'review', 'review']
    assert [row['mode'] for row in retry.calls] == ['review']
    assert held['phase'] == 'review_pending' and not held['complete']


@pytest.mark.parametrize('review', [
    review_issue(identities=['S999']),
    {'accepted': True, 'issues': review_issue()['issues']},
    {'accepted': False, 'issues': []},
    {'accepted': 'true', 'issues': []},
    {'accepted': True, 'issues': [], 'verified': True},
    {'accepted': False, 'issues': [{'code': [], 'explanation': '格式反例', 'citation_ids': ['S001'], 'excerpt': '原句'}]},
    {'accepted': False, 'issues': [{'code': {}, 'explanation': '格式反例', 'citation_ids': ['S001'], 'excerpt': '原句'}]},
])
def test_unknown_review_citation_and_invalid_schema_hold_draft_without_false_success(install_model, review):
    model = install_model([('generation', VALID), ('review', review)])
    checkpoints = []
    with pytest.raises(PipelineError, match='复核'):
        run(case(), checkpoints=checkpoints)
    assert len(model.calls) == 2
    assert checkpoints[-1]['draft'] == VALID
    assert not checkpoints[-1]['complete']
    assert not any(attempt['accepted'] for attempt in checkpoints[-1]['attempts'])


def test_three_noncooperative_drafts_end_held_and_keep_all_completed_attempts(install_model):
    invalid = '# 报告\n\n模型始终写输入之外的来源。[S999]'
    model = install_model([('generation', invalid)] * 3)
    checkpoints = []
    with pytest.raises(PipelineError, match='尚未通过'):
        run(case(), checkpoints=checkpoints)
    held = checkpoints[-1]
    assert len(model.calls) == len(held['attempts']) == 3
    assert held['phase'] == 'correction_required' and not held['complete']
    assert held['draft'] == invalid and held['revision_issues']
    assert not any(row['accepted'] for row in held['attempts'])


@pytest.mark.parametrize('change', ['same', 'goal', 'source'])
def test_accepted_cache_has_zero_calls_but_source_or_goal_change_invalidates_it(install_model, change):
    value = case()
    install_model([('generation', VALID), ('review', ACCEPT)])
    previous = run(value)['state']
    untouched = copy.deepcopy(previous)
    if change == 'goal':
        changed = case(goal='新目标：比较地区与刊物自述限定')
    elif change == 'source':
        changed = case(profile_suffix=' 保存来源新增说明。')
    else:
        changed = value
    model = install_model([] if change == 'same' else [('generation', VALID), ('review', ACCEPT)])
    result = run(changed, previous=previous)
    assert result['state']['complete']
    assert previous == untouched
    assert len(model.calls) == (0 if change == 'same' else 2)
    assert len(result['state']['attempts']) == 1
    assert (result['state']['input_fingerprint'] == previous['input_fingerprint']) == (change == 'same')


def test_actual_requests_fit_capacity_and_keep_critical_profile_literals_without_identity_promotion(install_model):
    value = case(profile_suffix=' 保存された限定。' * 1000 + ' 最終限定 @outside 未核验。')
    unchanged = copy.deepcopy(value)
    model = install_model([('generation', VALID), ('review', ACCEPT)])
    result = run(value)
    assert result['compression_receipt']['final_capacity']['fits']
    assert result['compression_receipt']['final_capacity']['exact_token_count'] is False
    for call in model.calls:
        inventory = call['data']['report_inventory']
        profile = inventory['profile_sources'][0]
        assert profile['saved_profile_text'] == value[2][0]['content']
        assert '矢嶋委員長' in profile['saved_profile_text'] and '現代革命労働者党機関紙「碇星」' in profile['saved_profile_text']
        mentions = {row['target_handle']: row for row in inventory['profile_handle_mentions']}
        assert mentions['@peace_b']['target_key'] == 'twitter:peace_b'
        assert mentions['@outside']['target_key'] is None
        assert inventory['semantic_truth_established'] is False
        assert profile['profile_self_description_is_independent_verification'] is False
        assert 'verified' not in profile and 'affiliation' not in profile
        assert inventory['collection_scope']['visited_count'] == 2
        assert call['data']['sources'][1]['relation_type'] == 'follows'
        assert call['data']['sources'][2]['publication_day'] == '2026-10-09'
    assert value == unchanged
    assert value[3]['accounts'][0]['status'] == 'candidate'


def test_generation_capacity_follows_actual_payload_model_instead_of_environment_alias(install_model):
    value = case(profile_suffix='A' * 200000)
    value[0]['model'] = 'unknown-small-model'
    model = install_model([], allow_compression=True)
    with pytest.raises(PipelineError):
        run(value)
    assert not any(call['mode'] == 'generation' for call in model.calls)


def test_hierarchical_compression_retains_revision_candidate_inventory_and_advisories(install_model):
    _, data, items, context = case()
    from app.workbench.report_quality import deterministic_report_inventory
    data['report_inventory'] = deterministic_report_inventory(items, context)
    data['revision_request'] = {'previous_markdown': NO_MENTION, 'issues': review_issue()['issues']}
    data['candidate_report'] = NO_MENTION
    data['deterministic_review_advisories'] = [{'code': 'fixture_advisory', 'blocking': False,
                                               'semantic_truth_established': False}]
    data['program_generated_sections'] = {'runtime_metadata': {'rendered_by_program': True,
        'source': 'saved_run', 'collection_scope': {'visited_count': 2, 'account_budget_skipped': 3}}}
    for source, row in zip(data['sources'], data['source_reviews']):
        row['reading'] = 'A' * 3000
        row['fragment_reviews'] = [{'quote': row['quote'], 'reading': 'A' * 3000,
                                   'limitations': row['limitations'], 'start': 0, 'end': source['content_characters']}]
    unchanged = copy.deepcopy(data)
    capacity = ModelCapacity('fixture-capacity', 12000, 1024, 'fixture_metadata')
    model = install_model([], allow_compression=True)
    reduced, receipt, state = asyncio.run(synthesis.fit_report_input(data, '引用须有来源，不新增事实。',
        max_output_tokens=1024, capacity=capacity))
    assert receipt['semantic_compression'] and state['complete']
    assert receipt['final_capacity']['fits']
    assert model.calls and all(call['mode'] == 'compression' for call in model.calls)
    for field in ('revision_request', 'candidate_report', 'report_inventory', 'program_generated_sections', 'deterministic_review_advisories'):
        assert reduced[field] == unchanged[field]
    represented = {identity for row in reduced['compressed_evidence'] for identity in row['represented_citation_ids']}
    assert represented == {'S001', 'S002', 'S003'}
    assert data == unchanged


def test_large_report_correction_and_final_review_reuse_one_evidence_note_set(install_model, monkeypatch):
    value = case()
    value[0]['max_tokens'] = 1000
    for index in range(3):
        identity = f'S{index + 4:03d}'
        for key in ('sources', 'source_reviews'):
            copied = copy.deepcopy(value[1][key][index])
            copied['citation_id'] = identity
            value[1][key].append(copied)
        item = copy.deepcopy(value[2][index])
        item['citation_id'] = identity
        value[2].append(item)
    for row in value[1]['source_reviews']:
        row['reading'] = 'R' * 22000
    monkeypatch.setenv('DEEPSEEK_CONTEXT_WINDOW_TOKENS', '131072')
    monkeypatch.setenv('DEEPSEEK_MAX_OUTPUT_TOKENS', '32768')
    model = install_model([('generation', NO_MENTION), ('review', review_issue()),
                           ('generation', VALID), ('review', ACCEPT)], allow_compression=True)
    saved, states = [], []
    result = run(value, synthesis_checkpoint=saved.append, checkpoints=states)
    assert result['state']['complete'] and result['markdown'] == VALID
    attempts = result['state']['attempts']
    assert len(attempts) == 2
    assert attempts[0]['source_review']['accepted'] is False
    assert attempts[1]['source_review']['accepted'] is True
    assert all(not state['revision_issues'] for state in states
               if state['phase'] == 'review_pending' and len(state['attempts']) >= 2)
    batch_count = attempts[0]['input_compression']['completed_compression_batches']
    assert batch_count > 0
    # Each original batch is drafted and reviewed once. The new report still
    # receives a separate semantic review after the rejected first report.
    assert sum(call['mode'] == 'compression' for call in model.calls) == batch_count * 2
    assert [call['mode'] for call in model.calls if call['mode'] != 'compression'] == [
        'generation', 'review', 'generation', 'review']
    assert attempts[1]['input_compression']['reused_reviewed_compression_batches'] == batch_count
    assert all(state['evidence_unit_ledger_sha256'] == saved[0]['evidence_unit_ledger_sha256'] for state in saved)


def test_no_report_total_deadline_does_not_impose_a_hidden_compression_stage_cap(install_model, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(generation, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    model = install_model([('generation', VALID), ('review', ACCEPT)])
    real_fit = generation.fit_report_input
    budgets = []
    async def fit(data, system, **options):
        budgets.append(options['time_budget_seconds'])
        clock[0] += 1200
        return await real_fit(data, system, **options)
    monkeypatch.setattr(generation, 'fit_report_input', fit)
    result = run(case(), time_budget_seconds=None)
    assert result['state']['complete']
    assert budgets == [None, None]
    assert [call['mode'] for call in model.calls] == ['generation', 'review']


def test_deadline_after_draft_checkpoint_holds_completed_draft_before_review(install_model, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(generation, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    model = install_model([('generation', VALID)])
    checkpoints = []

    def checkpoint(state):
        checkpoints.append(state)
        if state['phase'] == 'review_pending':
            clock[0] = 11.0

    payload, data, items, context = case()
    with pytest.raises(PipelineError, match='最长运行时间'):
        asyncio.run(generation.generate_checked_report(payload, data, items, context,
            checkpoint=checkpoint, time_budget_seconds=10))
    assert len(model.calls) == 1
    assert checkpoints[-1]['draft'] == VALID and not checkpoints[-1]['complete']
    assert checkpoints[-1]['phase'] == 'review_pending'


@pytest.mark.parametrize('budget,stage_elapsed', [(7200, 650), (1800, 650), (900, 350)])
def test_multicall_compression_receives_full_shared_deadline_and_each_request_stays_bounded(
        install_model, monkeypatch, budget, stage_elapsed):
    clock = [0.0]
    monkeypatch.setattr(generation, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    model = install_model([('generation', VALID), ('review', ACCEPT)])
    stage_budgets, draft_timeouts, review_timeouts = [], [], []
    real_fit, real_request, real_json = generation.fit_report_input, generation._request, generation.configured_ai_json

    async def fit(data, system, **options):
        # Two long stages represent multiple bounded model calls, never sleeps.
        expected = budget - clock[0]
        stage_budgets.append(options['time_budget_seconds'])
        assert options['time_budget_seconds'] == expected
        clock[0] += stage_elapsed
        return await real_fit(data, system, **options)

    async def request(payload, timeout, **options):
        draft_timeouts.append(timeout)
        return await real_request(payload, timeout, **options)

    async def review(*args, **options):
        review_timeouts.append(options['timeout_limit'])
        return await real_json(*args, **options)

    monkeypatch.setattr(generation, 'fit_report_input', fit)
    monkeypatch.setattr(generation, '_request', request)
    monkeypatch.setattr(generation, 'configured_ai_json', review)
    result = run(case(), time_budget_seconds=budget)
    assert result['state']['complete'] and result['markdown'] == VALID
    assert stage_budgets == [budget, budget-stage_elapsed]
    assert draft_timeouts == [min(600, budget-stage_elapsed)]
    assert review_timeouts == [min(600, budget-2*stage_elapsed)]
    assert [call['mode'] for call in model.calls] == ['generation', 'review']


def test_expired_deadline_after_long_compression_preserves_draft_and_blocks_review(
        install_model, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(generation, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    model = install_model([('generation', VALID)])
    real_fit = generation.fit_report_input
    stages, checkpoints = [], []

    async def fit(data, system, **options):
        stages.append(options['time_budget_seconds'])
        assert options['time_budget_seconds'] == 900-clock[0]
        clock[0] += 650 if not data.get('candidate_report') else 251
        return await real_fit(data, system, **options)

    monkeypatch.setattr(generation, 'fit_report_input', fit)
    with pytest.raises(PipelineError) as error:
        run(case(), time_budget_seconds=900, checkpoints=checkpoints)
    assert error.value.code == 'report_time_budget'
    assert stages == [900, 250] and len(model.calls) == 1
    assert checkpoints[-1]['draft'] == VALID and not checkpoints[-1]['complete']
    assert checkpoints[-1]['phase'] == 'review_pending'


def test_cancellation_during_source_review_keeps_draft_for_review_only_resume(install_model):
    model = install_model([('generation', VALID), ('review', 'cancel')])
    checkpoints = []
    value = case()
    with pytest.raises(asyncio.CancelledError):
        run(value, checkpoints=checkpoints)
    assert checkpoints[-1]['draft'] == VALID and not checkpoints[-1]['complete']
    assert len(model.calls) == 2
    retry = install_model([('review', ACCEPT)])
    result = run(value, previous=checkpoints[-1])
    assert result['state']['complete']
    assert [row['mode'] for row in retry.calls] == ['review']


def assert_review_capacity_receipts(state, calls):
    reviews = [call for call in calls if call['mode'] == 'review']
    attempts = state['attempts'][-1]['source_review_attempts']
    # One logical source review can reserve several identical HTTP sends.
    physical_attempts = [attempt for attempt in attempts
        for _ in (attempt.get('transport_attempts') or [{}])]
    assert len(physical_attempts) == len(reviews)
    for attempt, call in zip(physical_attempts, reviews):
        requested = call['payload']['max_tokens']
        capacity = configured_model_capacity(model=call['payload']['model'])
        actual = assess_messages(call['payload']['messages'], max_output_tokens=requested, capacity=capacity)
        assert requested == attempt['requested_max_tokens'] <= capacity.max_output_tokens
        assert actual.fits and attempt['input_capacity']['fits']
        assert attempt['input_capacity']['output_reserved_tokens'] == requested
        assert attempt['input_capacity']['exact_token_count'] is False


def test_length_exhausted_review_increases_output_and_accepts_same_checkpointed_draft(install_model):
    model = install_model([('generation', VALID), ('review', ended_completion()), ('review', ACCEPT)])
    checkpoints = []
    result = run(case(), checkpoints=checkpoints)
    assert [call['mode'] for call in model.calls] == ['generation', 'review', 'review']
    assert [call['payload']['max_tokens'] for call in model.calls[1:]] == [65536, 131072]
    state = result['state']
    assert state['complete'] and state['draft'] == result['markdown'] == VALID
    assert len(state['attempts']) == 1 and state['attempts'][0]['accepted']
    reviews = state['attempts'][0]['source_review_attempts']
    assert [row['complete'] for row in reviews] == [False, True]
    assert reviews[0]['failure_diagnostic'] == {'finish_reason': 'length',
        'requested_max_tokens': 65536, 'reasoning_effort': 'high', 'completion_tokens': 65536}
    assert model.calls[1]['data'] == model.calls[2]['data']
    assert model.calls[2]['data']['candidate_report'] == VALID
    assert 'fixture-hidden-reasoning' not in compact_json(checkpoints)
    assert 'fixture-key' not in compact_json(checkpoints)
    assert any(row['phase'] == 'review_pending' and row['draft'] == VALID for row in checkpoints)
    assert_review_capacity_receipts(state, model.calls)


def test_three_length_exhausted_reviews_hold_draft_and_resume_without_regeneration(install_model):
    model = install_model([('generation', VALID)] + [('review', ended_completion())] * 3)
    checkpoints = []
    value = case()
    with pytest.raises(PipelineError, match='未完整结束'):
        run(value, checkpoints=checkpoints)
    held = copy.deepcopy(checkpoints[-1])
    assert [call['payload']['max_tokens'] for call in model.calls[1:]] == [65536, 131072, 262144]
    assert len(model.calls) == 4 and not model.steps
    assert held['phase'] == 'review_pending' and held['draft'] == VALID and not held['complete']
    assert len(held['attempts']) == 1 and not held['attempts'][0]['accepted']
    reviews = held['attempts'][0]['source_review_attempts']
    assert len(reviews) == 3 and all(not row['complete'] for row in reviews)
    assert all(row['failure_diagnostic']['finish_reason'] == 'length' for row in reviews)
    assert 'fixture-hidden-reasoning' not in compact_json(held)
    assert_review_capacity_receipts(held, model.calls)
    retry = install_model([('review', ACCEPT)])
    result = run(value, previous=held)
    assert result['state']['complete'] and result['markdown'] == VALID
    assert [call['mode'] for call in retry.calls] == ['review']
    assert len(result['state']['attempts']) == 1
    assert len(result['state']['attempts'][0]['source_review_attempts']) == 4
    assert not held['complete'] and len(held['attempts'][0]['source_review_attempts']) == 3


@pytest.mark.parametrize('failure,match', [
    (503, '503'),
    ({'accepted': 'true', 'issues': []}, '复核格式'),
    (review_issue(identities=['S999']), '复核字段或引用'),
], ids=['http_503', 'schema', 'unknown_citation'])
def test_length_retry_does_not_swallow_service_schema_or_unknown_citation_failure(install_model, failure, match):
    failures = [('review', failure)] * (3 if failure == 503 else 1)
    model = install_model([('generation', VALID), ('review', ended_completion())] + failures)
    checkpoints = []
    with pytest.raises(PipelineError, match=match):
        run(case(), checkpoints=checkpoints)
    assert len(model.calls) == (5 if failure == 503 else 3) and not model.steps
    held = checkpoints[-1]
    assert held['draft'] == VALID and held['phase'] == 'review_pending' and not held['complete']
    assert len(held['attempts']) == 1 and not held['attempts'][0]['accepted']
    assert len(held['attempts'][0]['source_review_attempts']) == 2
    assert_review_capacity_receipts(held, model.calls)


@pytest.mark.parametrize('end', ['accept', 'length'])
def test_unknown_model_review_never_exceeds_provider_fallback_output_maximum(install_model, monkeypatch, end):
    monkeypatch.setenv('DEEPSEEK_MODEL', 'unknown-small-model')
    value = case()
    value[0]['model'] = 'unknown-small-model'
    model = install_model([('generation', VALID), ('review', ACCEPT if end == 'accept' else ended_completion())])
    checkpoints = []
    if end == 'length':
        with pytest.raises(PipelineError, match='未完整结束'):
            run(value, checkpoints=checkpoints)
        state = checkpoints[-1]
        assert state['phase'] == 'review_pending' and not state['complete']
    else:
        state = run(value, checkpoints=checkpoints)['state']
        assert state['complete']
    assert len(model.calls) == 2 and not model.steps
    capacity = configured_model_capacity(model='unknown-small-model')
    assert capacity.source == 'unverified_provider_fallback'
    assert model.calls[-1]['payload']['max_tokens'] == capacity.max_output_tokens == 32768
    assert state['draft'] == VALID
    assert_review_capacity_receipts(state, model.calls)


def test_review_length_growth_stops_at_explicit_model_maximum(install_model, monkeypatch):
    monkeypatch.setenv('DEEPSEEK_MAX_OUTPUT_TOKENS', '70000')
    model = install_model([('generation', VALID), ('review', ended_completion()), ('review', ended_completion())])
    checkpoints = []
    with pytest.raises(PipelineError, match='未完整结束'):
        run(case(), checkpoints=checkpoints)
    assert [call['payload']['max_tokens'] for call in model.calls[1:]] == [65536, 70000]
    assert len(model.calls) == 3 and not model.steps
    assert checkpoints[-1]['draft'] == VALID and not checkpoints[-1]['complete']
    assert_review_capacity_receipts(checkpoints[-1], model.calls)


def test_review_length_growth_respects_shared_deadline_and_saves_exhausted_attempt(install_model, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(generation, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    model = install_model([('generation', VALID), ('review', ended_completion())])
    checkpoints = []

    def checkpoint(state):
        checkpoints.append(state)
        attempts = (state['attempts'][-1] if state['attempts'] else {}).get('source_review_attempts', [])
        if attempts and attempts[-1].get('failure_diagnostic', {}).get('finish_reason') == 'length':
            clock[0] = 11.0

    payload, data, items, context = case()
    with pytest.raises(PipelineError, match='最长运行时间'):
        asyncio.run(generation.generate_checked_report(payload, data, items, context,
            checkpoint=checkpoint, time_budget_seconds=10))
    assert len(model.calls) == 2 and not model.steps
    held = checkpoints[-1]
    assert held['draft'] == VALID and held['phase'] == 'review_pending' and not held['complete']
    assert len(held['attempts'][0]['source_review_attempts']) == 1
    assert_review_capacity_receipts(held, model.calls)


@pytest.mark.parametrize('field,replacement', [
    ('url', 'https://news.example.test/replaced-provenance'),
    ('id', 'material-id-replaced'),
    ('content_hash', 'b' * 64),
    ('content', '主催者は10月10日に公開討論を計画すると述べた。原始保存正文新增限定。'),
    ('metadata', {'content_kind': 'public_news_body', 'raw_capture_revision': 'new'}),
], ids=['news_url', 'material_id', 'content_hash', 'raw_content', 'raw_metadata'])
def test_news_provenance_change_invalidates_accepted_cache_when_prompt_body_is_identical(install_model, field, replacement):
    payload, data, items, _ = case()
    item = copy.deepcopy(items[-1])
    item.update(id='material-id-original', content_hash='a' * 64, source_name='news:fixture')
    item['metadata'] = {'content_kind': 'public_news_body'}
    sources, coverage = reports.source_samples([item])
    data.update(sources=reports.synthesis_sources(sources), source_reviews=[data['source_reviews'][-1]],
                coverage=coverage, analysis_context=reports.report_context(sources, coverage, {}))
    payload['messages'][1]['content'] = compact_json(data)
    value = (payload, data, [item], {})
    markdown = '# 新闻来源研究\n\n保存来源称计划于10月10日公开讨论；计划不证明已举行。[S003]'
    first = install_model([('generation', markdown), ('review', ACCEPT)])
    previous = run(value)['state']
    previous_unchanged = copy.deepcopy(previous)
    same = install_model([])
    assert run(value, previous=previous)['state']['complete'] and same.calls == []
    changed = copy.deepcopy(value)
    changed[2][0][field] = replacement
    assert changed[0] == value[0] and changed[1] == value[1]
    second = install_model([('generation', markdown), ('review', ACCEPT)])
    result = run(changed, previous=previous)
    assert [call['mode'] for call in second.calls] == ['generation', 'review']
    assert first.calls[0]['data'] == second.calls[0]['data']
    assert result['state']['input_fingerprint'] != previous['input_fingerprint']
    assert result['state']['complete'] and len(result['state']['attempts']) == 1
    assert result['source_review_requested'] is True
    assert previous == previous_unchanged
    if field == 'url':
        assert replacement not in compact_json(second.calls[0]['data'])


@pytest.mark.parametrize('kind', [[], {}], ids=['list', 'dict'])
def test_compression_finding_non_string_kind_is_controlled_pipeline_error(kind):
    note = {'findings': [{'statement': '仅保存来源说法。', 'citation_ids': ['S001'], 'kind': kind,
                         'support_refs': [{'unit_id': 'S001~P1', 'citation_id': 'S001', 'quote': '来源'}]}],
            'limitations': ['外部真相未独立核验。']}
    unchanged = copy.deepcopy(note)
    with pytest.raises(PipelineError, match='压缩字段、引用或片段支持无效'):
        synthesis._validate_note(note, {'S001'}, {'S001~P1': {'citation_id': 'S001', 'text': '来源'}})
    assert note == unchanged


def test_draft_held_only_by_old_deterministic_gate_is_rechecked_and_reviewed_without_generation(install_model):
    value = case()
    install_model([('generation', VALID)] + [('review', 503)] * 3)
    checkpoints = []
    with pytest.raises(PipelineError, match='503'):
        run(value, checkpoints=checkpoints)
    held = copy.deepcopy(checkpoints[-1])
    old_issue = {'code': 'uncited_assertion_paragraph', 'blocking': True,
                 'explanation': '模拟旧门禁把纯日期方法说明误判为未引证事实。'}
    held.update(phase='correction_required', revision_issues=[old_issue])
    held['attempts'][-1]['citation_audit']['issues'] = [old_issue]
    assert not held['attempts'][-1].get('source_review')
    assert not any(row['blocking'] for row in reports.report_audit(VALID, value[2])['issues'])
    unchanged = copy.deepcopy(held)
    model = install_model([('review', ACCEPT)])
    result = run(value, previous=held)
    assert [call['mode'] for call in model.calls] == ['review']
    assert result['markdown'] == VALID and result['state']['complete']
    assert len(result['state']['attempts']) == 1
    assert result['state']['attempts'][0]['held_citation_gate_rechecked'] is True
    assert result['state']['attempts'][0]['accepted']
    assert result['state']['revision_issues'] == []
    assert held == unchanged


@pytest.mark.parametrize('revision_kind', ['semantic', 'legacy_gate_with_semantic_rejection'])
def test_semantically_rejected_draft_requires_revision_even_when_current_citation_gate_passes(install_model, revision_kind):
    value = case()
    rejected = review_issue()
    install_model([('generation', NO_MENTION), ('review', rejected), ('generation', 'cancel')])
    checkpoints = []
    with pytest.raises(asyncio.CancelledError):
        run(value, checkpoints=checkpoints)
    held = copy.deepcopy(checkpoints[-1])
    assert held['phase'] == 'correction_required' and held['draft'] == NO_MENTION
    assert held['attempts'][-1]['source_review']['accepted'] is False
    assert not any(row['blocking'] for row in reports.report_audit(NO_MENTION, value[2])['issues'])
    if revision_kind == 'legacy_gate_with_semantic_rejection':
        held['revision_issues'] = [{'code': 'uncited_assertion_paragraph', 'blocking': True,
                                    'explanation': '旧机械门禁问题与已保存语义拒绝同时存在。'}]
    unchanged = copy.deepcopy(held)
    model = install_model([('generation', VALID), ('review', ACCEPT)])
    result = run(value, previous=held)
    assert [call['mode'] for call in model.calls] == ['generation', 'review']
    assert model.calls[0]['data']['revision_request']['previous_markdown'] == NO_MENTION
    assert model.calls[0]['data']['revision_request']['issues'] == held['revision_issues']
    assert result['state']['complete'] and len(result['state']['attempts']) == 2
    assert not result['state']['attempts'][0]['accepted']
    assert result['state']['attempts'][1]['accepted']
    assert not result['state']['attempts'][0].get('held_citation_gate_rechecked')
    assert held == unchanged


def test_changed_review_prompt_rechecks_completed_draft_without_regenerating_it(install_model, monkeypatch):
    value = case()
    install_model([('generation', VALID), ('review', ACCEPT)])
    previous = run(value)['state']
    unchanged = copy.deepcopy(previous)
    monkeypatch.setattr(generation, '_REVIEW_PROMPT', generation._REVIEW_PROMPT + ' 新策略：逐条核对明确归因及地点，不把共现解释为因果。')
    model = install_model([('review', ACCEPT)])
    checkpoints = []
    result = run(value, previous=previous, checkpoints=checkpoints)
    assert [call['mode'] for call in model.calls] == ['review']
    assert '新策略：' in model.calls[0]['payload']['messages'][0]['content']
    assert model.calls[0]['data']['candidate_report'] == VALID
    assert result['markdown'] == VALID and result['state']['complete']
    assert len(result['state']['attempts']) == 1
    assert result['state']['input_fingerprint'] == previous['input_fingerprint']
    assert result['state']['review_policy_fingerprint'] != previous['review_policy_fingerprint']
    assert any(row['phase'] == 'review_pending' and not row['complete'] for row in checkpoints)
    initial = checkpoints[0]['attempts'][-1]
    assert initial['accepted'] is False and initial['source_review_attempts'] == []
    assert 'source_review' not in initial
    prior = initial['prior_source_reviews'][-1]
    assert prior['review_policy_fingerprint'] == previous['review_policy_fingerprint']
    assert prior['accepted'] is True and prior['source_review'] == ACCEPT
    assert prior['source_review_attempts'] == previous['attempts'][-1]['source_review_attempts']
    assert previous == unchanged


def test_changed_review_prompt_rejection_revises_draft_and_does_not_keep_old_acceptance(install_model, monkeypatch):
    value = case()
    install_model([('generation', NO_MENTION), ('review', ACCEPT)])
    previous = run(value)['state']
    unchanged = copy.deepcopy(previous)
    monkeypatch.setattr(generation, '_REVIEW_PROMPT', generation._REVIEW_PROMPT + ' 新策略：重新核对简介字面提及。')
    model = install_model([('review', review_issue()), ('generation', VALID), ('review', ACCEPT)])
    result = run(value, previous=previous)
    assert [call['mode'] for call in model.calls] == ['review', 'generation', 'review']
    assert result['state']['complete'] and result['markdown'] == VALID
    assert len(result['state']['attempts']) == 2
    assert result['state']['attempts'][0]['source_review']['accepted'] is False
    assert result['state']['attempts'][0]['accepted'] is False
    assert result['state']['attempts'][1]['accepted'] is True
    assert previous == unchanged


def test_changed_review_prompt_failed_service_holds_draft_without_claiming_old_acceptance(install_model, monkeypatch):
    value = case()
    install_model([('generation', VALID), ('review', ACCEPT)])
    previous = run(value)['state']
    unchanged = copy.deepcopy(previous)
    monkeypatch.setattr(generation, '_REVIEW_PROMPT', generation._REVIEW_PROMPT + ' 新策略：逐条检查地点与扫描字段。')
    model = install_model([('review', 503)] * 3)
    checkpoints = []
    with pytest.raises(PipelineError, match='503'):
        run(value, previous=previous, checkpoints=checkpoints)
    held = checkpoints[-1]
    assert [call['mode'] for call in model.calls] == ['review', 'review', 'review']
    assert held['phase'] == 'review_pending' and not held['complete'] and held['draft'] == VALID
    assert held['review_policy_fingerprint'] != previous['review_policy_fingerprint']
    assert not held['attempts'][-1]['accepted']
    assert 'source_review' not in held['attempts'][-1]
    assert held['attempts'][-1]['prior_source_reviews'][-1]['accepted'] is True
    assert held['attempts'][-1]['prior_source_reviews'][-1]['review_policy_fingerprint'] == previous['review_policy_fingerprint']
    assert checkpoints[0]['attempts'][-1]['source_review_attempts'] == []
    assert previous == unchanged


def test_recursive_dict_key_reordering_reuses_complete_report_with_zero_requests(install_model):
    value = case()
    install_model([('generation', VALID), ('review', ACCEPT)])
    previous = run(value)['state']
    unchanged = copy.deepcopy(previous)
    changed = reverse_mapping_order(value)
    assert changed == value
    assert compact_json(changed[1]) != compact_json(value[1])
    assert [row['citation_id'] for row in changed[2]] == ['S001', 'S002', 'S003']
    model = install_model([])
    result = run(changed, previous=reverse_mapping_order(previous))
    assert model.calls == []
    assert result['state']['complete'] and result['markdown'] == VALID
    assert result['state']['input_fingerprint'] == previous['input_fingerprint']
    assert previous == unchanged


@pytest.mark.parametrize('field', ['topics', 'entities', 'locations'])
def test_only_derived_rule_tags_change_does_not_invalidate_source_provenance(install_model, field):
    value = case()
    value[2][0][field] = [{'label': 'old-rule-result'}]
    install_model([('generation', VALID), ('review', ACCEPT)])
    previous = run(value)['state']
    changed = copy.deepcopy(value)
    changed[2][0][field] = [{'label': 'new-derived-rule-result', 'confidence': 0.3}]
    assert changed[1] == value[1] and changed[2][0]['content'] == value[2][0]['content']
    model = install_model([])
    result = run(changed, previous=previous)
    assert model.calls == [] and result['state']['complete']
    assert result['state']['input_fingerprint'] == previous['input_fingerprint']


@pytest.mark.parametrize('field', ['sources', 'source_reviews'])
def test_canonical_report_cache_preserves_list_order_as_an_input_change(install_model, field):
    value = case()
    install_model([('generation', VALID), ('review', ACCEPT)])
    previous = run(value)['state']
    changed = copy.deepcopy(value)
    changed[1][field].reverse()
    model = install_model([('generation', VALID), ('review', ACCEPT)])
    result = run(changed, previous=previous)
    assert [call['mode'] for call in model.calls] == ['generation', 'review']
    assert result['state']['input_fingerprint'] != previous['input_fingerprint']
    assert result['state']['complete']


def test_compression_checkpoint_reuses_all_notes_after_recursive_dict_key_reordering(install_model, monkeypatch):
    _, data, _, _ = case()
    for source, row in zip(data['sources'], data['source_reviews']):
        row['reading'] = 'A' * 3000
        row['fragment_reviews'] = [{'quote': row['quote'], 'reading': 'A' * 3000,
                                   'limitations': row['limitations'], 'start': 0, 'end': source['content_characters']}]
    capacity = ModelCapacity('fixture-capacity', 12000, 1024, 'fixture_metadata')
    model = install_model([], allow_compression=True)
    reduced, receipt, previous = asyncio.run(synthesis.fit_report_input(data, '保留全部来源及限定。',
        max_output_tokens=1024, capacity=capacity))
    assert receipt['semantic_compression'] and previous['complete'] and previous['batches']
    assert model.calls and all(call['mode'] == 'compression' for call in model.calls)
    unchanged = copy.deepcopy(previous)
    changed = reverse_mapping_order(data)
    assert changed == data and compact_json(changed) != compact_json(data)
    unexpected = []

    async def forbidden_compression(*args, **options):
        unexpected.append(True)
        raise AssertionError('Canonical-equivalent input must reuse checkpointed compression notes')

    monkeypatch.setattr(synthesis, 'configured_ai_json', forbidden_compression)
    retry_reduced, retry_receipt, retry_state = asyncio.run(synthesis.fit_report_input(changed,
        '保留全部来源及限定。', max_output_tokens=1024, capacity=capacity,
        previous_state=reverse_mapping_order(previous)))
    assert unexpected == []
    assert retry_state['complete'] and retry_receipt['final_capacity']['fits']
    assert retry_state['input_fingerprint'] == previous['input_fingerprint']
    assert retry_state['batches'] == previous['batches']
    assert retry_reduced == reduced and previous == unchanged


def test_explicit_four_draft_attempts_accepts_fourth_draft_after_three_invalid_drafts(install_model):
    invalid = '# 报告\n\n引用输入之外的来源。[S999]'
    model = install_model([('generation', invalid)] * 3 + [('generation', VALID), ('review', ACCEPT)])
    result = run(case(), max_draft_attempts=4)
    assert [call['mode'] for call in model.calls] == ['generation'] * 4 + ['review']
    assert result['markdown'] == VALID and result['state']['complete']
    assert len(result['state']['attempts']) == 4
    assert [row['accepted'] for row in result['state']['attempts']] == [False, False, False, True]
    assert model.calls[3]['data']['revision_request']['previous_markdown'] == invalid


@pytest.mark.parametrize('maximum', [True, False, 4.0, '4', 0, 13],
                         ids=['true', 'false', 'float', 'string', 'zero', 'above_maximum'])
def test_invalid_max_draft_attempts_is_rejected_before_any_request(install_model, maximum):
    model = install_model([])
    with pytest.raises(PipelineError, match='1 至 12 的整数'):
        run(case(), max_draft_attempts=maximum)
    assert model.calls == []


def machine_draft():
    after = '## 继续分析\n\n保存简介字面提及只支持单向文本观察，不能授予核验身份。[S001]'
    return VALID + '\n\n' + MACHINE_MARKER + '\n\n' + FAKE_RUNTIME + '\n\n' + after, after


def assert_machine_audit_preserves_raw_ranges(state, raw):
    audit = state['machine_section_audit']
    clean = state['draft']
    assert state['raw_draft'] == raw
    assert audit['clean_markdown'] == clean
    assert audit['raw_sha256'] == hashlib.sha256(raw.encode('utf-8')).hexdigest()
    assert audit['clean_sha256'] == hashlib.sha256(clean.encode('utf-8')).hexdigest()
    assert audit['changed'] and audit['footer_render_required']
    assert audit['raw_truth_established'] is False
    assert len(audit['removed_sections']) == 1
    removed = audit['removed_sections'][0]
    assert raw[removed['start']:removed['end']] == removed['raw']
    assert removed['raw'].startswith(MACHINE_MARKER)
    assert FAKE_RUNTIME in removed['raw']
    assert removed['sha256'] == hashlib.sha256(removed['raw'].encode('utf-8')).hexdigest()
    assert removed['document_sha256'] == audit['raw_sha256']
    assert removed['offset_unit'] == 'unicode_codepoints'
    assert removed['raw_truth_established'] is False
    latest = state['attempts'][-1]
    assert latest['raw_markdown'] == raw and latest['markdown'] == clean
    assert latest['machine_section_audit'] == audit


def test_reserved_machine_slot_fake_statistics_unknown_citations_and_url_are_not_delivered(install_model):
    value = case()
    unchanged = copy.deepcopy(value)
    raw, after = machine_draft()
    assert any(row['blocking'] for row in reports.report_audit(raw, value[2])['issues'])
    model = install_model([('generation', raw), ('review', ACCEPT)])
    result = run(value)
    clean = result['markdown']
    assert clean.startswith(VALID) and clean.endswith(after)
    assert MACHINE_MARKER not in clean and FAKE_RUNTIME not in clean
    assert '[S999]' not in clean and 'https://unprovided.example' not in clean
    assert model.calls[1]['data']['candidate_report'] == clean
    assert '9999' not in model.calls[1]['data']['candidate_report']
    program = model.calls[1]['data']['program_generated_sections']['runtime_metadata']
    assert program['rendered_by_program'] is True and program['model_text_is_not_delivered'] is True
    assert program['collection_scope']['visited_count'] == 2
    assert result['state']['complete']
    assert_machine_audit_preserves_raw_ranges(result['state'], raw)
    assert value == unchanged


def test_machine_slot_review_503_and_completed_cache_preserve_original_audit_without_regeneration(install_model):
    value = case()
    raw, _ = machine_draft()
    model = install_model([('generation', raw)] + [('review', 503)] * 3)
    checkpoints = []
    with pytest.raises(PipelineError, match='503'):
        run(value, checkpoints=checkpoints)
    held = copy.deepcopy(checkpoints[-1])
    assert held['phase'] == 'review_pending' and not held['complete']
    assert_machine_audit_preserves_raw_ranges(held, raw)
    assert [call['mode'] for call in model.calls] == ['generation', 'review', 'review', 'review']
    original_audit = copy.deepcopy(held['machine_section_audit'])
    retry = install_model([('review', ACCEPT)])
    result = run(value, previous=held)
    assert [call['mode'] for call in retry.calls] == ['review']
    assert result['state']['machine_section_audit'] == original_audit
    assert_machine_audit_preserves_raw_ranges(result['state'], raw)
    cached = install_model([])
    reused = run(reverse_mapping_order(value), previous=reverse_mapping_order(result['state']))
    assert cached.calls == [] and reused['state']['complete']
    assert reused['state']['machine_section_audit'] == original_audit
    assert_machine_audit_preserves_raw_ranges(reused['state'], raw)
    assert not held['complete'] and held['machine_section_audit'] == original_audit


def test_same_fake_statistics_unknown_citations_and_url_in_ordinary_body_remain_blocked(install_model):
    raw = VALID + '\n\n## 普通正文\n\n' + FAKE_RUNTIME
    model = install_model([('generation', raw)] * 3)
    checkpoints = []
    with pytest.raises(PipelineError, match='尚未通过'):
        run(case(), checkpoints=checkpoints)
    assert [call['mode'] for call in model.calls] == ['generation'] * 3
    held = checkpoints[-1]
    assert not held['complete'] and held['draft'] == held['raw_draft'] == raw
    assert held['machine_section_audit']['removed_sections'] == []
    assert held['machine_section_audit']['changed'] is False
    assert any(row['code'] == 'unknown_citation' for row in held['revision_issues'])
    assert not any(row['accepted'] for row in held['attempts'])


def test_unsupported_body_fact_with_known_citation_still_requires_semantic_source_review(install_model):
    raw = VALID + '\n\npeace_a 已经得到20万人正式背书。[S001]'
    issue = review_issue('source_support', ['S001'], '已经得到20万人正式背书')
    model = install_model([('generation', raw), ('review', issue)] * 3)
    checkpoints = []
    with pytest.raises(PipelineError, match='尚未通过'):
        run(case(), checkpoints=checkpoints)
    assert [call['mode'] for call in model.calls] == ['generation', 'review'] * 3
    held = checkpoints[-1]
    assert held['machine_section_audit']['removed_sections'] == []
    assert not held['complete'] and not any(row['accepted'] for row in held['attempts'])
    assert all(row['source_review']['accepted'] is False for row in held['attempts'])


@pytest.mark.parametrize('marker', [
    '## 运行元数据（程序生成）', '## 运行元数据〔程序生成〕 ',
    '### 运行元数据〔程序生成〕', '##运行元数据〔程序生成〕',
], ids=['wrong_brackets', 'trailing_space', 'wrong_heading_level', 'missing_space'])
def test_near_match_machine_marker_cannot_hide_unsupported_body(install_model, marker):
    raw = VALID + '\n\n' + marker + '\n\n' + FAKE_RUNTIME
    model = install_model([('generation', raw)])
    checkpoints = []
    with pytest.raises(PipelineError, match='尚未通过'):
        run(case(), checkpoints=checkpoints, max_draft_attempts=1)
    assert len(model.calls) == 1
    held = checkpoints[-1]
    assert not held['complete'] and held['draft'] == raw
    assert held['machine_section_audit']['removed_sections'] == []


@pytest.mark.parametrize('raw', [
    MACHINE_MARKER + '\n\n' + FAKE_RUNTIME + '\n\n看似已知引用。[S001]',
    '', '# 只有标题', '# 只有标题但挂了来源 [S001]',
    '# 没有正文来源\n\n材料范围仍待判断。\n\n' + MACHINE_MARKER + '\n\n全部已核对。[S001]',
    '# 没有正文引用\n\n本次报告说明材料边界。',
    '# 仅标题有引用 [S001]\n\n本次报告说明材料边界。',
], ids=['only_slot', 'empty', 'heading_only', 'heading_only_with_citation', 'known_citation_inside_slot_only', 'uncited_body', 'uncited_body_with_heading_citation'])
def test_machine_slot_or_missing_cited_analysis_cannot_become_accepted_report(install_model, raw):
    model = install_model([('generation', raw)])
    checkpoints = []
    with pytest.raises(PipelineError, match='尚未通过'):
        run(case(), checkpoints=checkpoints, max_draft_attempts=1)
    assert [call['mode'] for call in model.calls] == ['generation']
    held = checkpoints[-1]
    assert not held['complete'] and not held['attempts'][-1]['accepted']
    assert any(row['code'] == 'missing_analysis_body_or_citations' for row in held['revision_issues'])
