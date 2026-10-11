"""Offline full-report regressions for truncated output and paid-progress recovery.

Every HTTP response is synthetic. No credentials, real services, formal stores,
browser profiles or packaged applications are consulted by this test module.
"""
import asyncio
import copy
import json
from dataclasses import dataclass
from types import SimpleNamespace

import httpx
import pytest

from app.workbench import pipeline
from app.workbench import report_generation as generation
from app.workbench.ai_capacity import assess_messages, compact_json, configured_model_capacity
from app.workbench.pipeline import PipelineError


VALID = '# 保存来源分析\n\n来源称计划进行公开讨论；计划不能证明实际举行，执行情况不足以判断。[S001]'
PARAMS = {'title': '保存来源分析', 'mode': 'ai',
          'start_date': '2026-10-01', 'end_date': '2026-10-09'}
ITEMS = [{'id': 'public-statement', 'type': 'news', 'title': '公開討論の計画',
          'content': '公開声明：10月10日に公開討論を計画すると述べた。実施されたかは未確認です。',
          'url': 'https://public.example/statement', 'source_name': '保存公开来源',
          'published_at': '2026-10-08T00:00:00Z', 'metadata': {'content_kind': 'body'}}]
REASONING_MARKER = 'synthetic-private-reasoning'
TRUNCATED_MARKER = 'synthetic-incomplete-output'


@dataclass
class End:
    reason: object = 'length'
    content: str = TRUNCATED_MARKER
    completion_tokens: object = None
    prompt_tokens: int = 100


class OfflineModel:
    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []
        self.raw_read_units = []

    async def handle(self, request):
        payload = json.loads(request.content)
        data = json.loads(payload['messages'][1]['content'])
        mode = ('compression_review' if data.get('compression_review') else
                'compression' if 'compression_level' in data and 'records' in data else
                'review' if 'candidate_report' in data else
                'read' if payload.get('response_format') and 'sources' in data else 'generation')
        capacity = configured_model_capacity(model=payload['model'], base_url=str(request.url))
        assessment = assess_messages(payload['messages'], max_output_tokens=payload['max_tokens'], capacity=capacity)
        if not assessment.fits:
            raise AssertionError('An impossible provider request reached the transport')
        self.calls.append({'mode': mode, 'max_tokens': payload['max_tokens'],
                           'input_capacity': assessment.as_dict(), 'data': copy.deepcopy(data)})
        if mode == 'compression_review':
            value = {'accepted': True, 'issues': [],
                'reviewed_finding_indexes': list(range(len(data['candidate_notes']['findings']))),
                'reviewed_unit_ids': [row['unit_id'] for row in data['evidence_units']]}
        elif mode == 'compression':
            references = [{'unit_id': row['unit_id'], 'citation_id': row['citation_id'],
                           'quote': row['reading']['quote']} for row in data['evidence_units']]
            identities = list(dict.fromkeys(ref['citation_id'] for ref in references))
            value = {'findings': [{'statement': '来源主张尚待独立核验。', 'citation_ids': identities,
                                  'kind': 'uncertainty', 'support_refs': references}],
                     'limitations': ['所有保存原文保留在快照；压缩可能丢失语义细节。']}
        elif mode == 'read':
            rows = data['sources']
            self.raw_read_units.extend(row['citation_id'] for row in rows)
            value = {'source_reviews': [{'citation_id': row['citation_id'],
                'quote': row['content_fragments'][0]['text'][:45],
                'reading': '来源自称计划公开讨论，实际举行尚未核验。',
                'limitations': '保存文本和原样引用不能独立证明外部真相。'} for row in rows]}
        elif mode == 'review' and (not self.steps or self.steps[0][0] != 'review'):
            value = {'accepted': True, 'issues': []}
        else:
            if not self.steps:
                raise AssertionError('Unexpected additional paid task')
            expected, value = self.steps.pop(0)
            if expected != mode:
                raise AssertionError('A completed paid phase was repeated')
        if value == 'timeout':
            raise httpx.ReadTimeout('synthetic timeout', request=request)
        if value == 'cancel':
            raise asyncio.CancelledError()
        if isinstance(value, int):
            return httpx.Response(value, json={'error': {'message': 'synthetic service failure'}})
        if isinstance(value, End):
            reason, content = value.reason, value.content
            tokens = payload['max_tokens'] if value.completion_tokens is None else value.completion_tokens
            prompt_tokens = value.prompt_tokens
        else:
            reason, content, tokens = 'stop', json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value, 50
            prompt_tokens = 100
        return httpx.Response(200, json={'choices': [{'finish_reason': reason,
            'message': {'content': content, 'reasoning_content': REASONING_MARKER}}],
            'usage': {'prompt_tokens': prompt_tokens, 'completion_tokens': tokens, 'total_tokens': prompt_tokens + tokens}})

    def generations(self):
        return [row for row in self.calls if row['mode'] == 'generation']


@pytest.fixture
def model(monkeypatch):
    for name in ('DEEPSEEK_CONTEXT_WINDOW_TOKENS', 'DEEPSEEK_MAX_OUTPUT_TOKENS', 'DEEPSEEK_TIMEOUT_SECONDS'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'fake-key')
    monkeypatch.setenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com')
    monkeypatch.setenv('DEEPSEEK_MODEL', 'deepseek-flash')
    native = httpx.AsyncClient

    def install(steps):
        scripted = OfflineModel(steps)
        monkeypatch.setattr(pipeline.httpx, 'AsyncClient', lambda **options:
            native(transport=httpx.MockTransport(scripted.handle), **options))
        return scripted
    return install


def build(*, previous=None, saved=None, checkpoint=None, seconds=60):
    async def persist(state):
        if saved is not None:
            saved.append(copy.deepcopy(state))
        if checkpoint is not None:
            checkpoint(state)
    return asyncio.run(pipeline.build_report(copy.deepcopy(ITEMS), copy.deepcopy(PARAMS),
        previous_review=previous, checkpoint=persist, time_budget_seconds=seconds))


def held_generation(saved):
    state = saved[-1].get('report_generation')
    if not isinstance(state, dict):
        raise AssertionError('A failed provider request did not preserve a report-generation checkpoint')
    return state


def assert_safe(state):
    serialized = json.dumps(state, ensure_ascii=False)
    if any(value in serialized for value in ('fake-key', 'Authorization', REASONING_MARKER, TRUNCATED_MARKER)):
        raise AssertionError('A checkpoint leaked credentials, headers, reasoning or incomplete provider text')


def assert_complete(result):
    snapshot = result['snapshot']
    assert snapshot['report_generation']['complete']
    assert '执行情况不足以判断' in result['markdown']
    assert snapshot['analysis_review']['reading_state']['complete']
    assert snapshot['provider_usage'] == {'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150}
    assert 'failed_or_truncated_calls' in snapshot['provider_usage_scope']
    assert_safe(snapshot)


def test_full_build_grows_only_length_output_and_keeps_completed_generation_usage(model):
    scripted = model([('generation', End()), ('generation', VALID)])
    saved = []
    result = build(saved=saved)
    assert_complete(result)
    assert result['snapshot']['initial_max_output_tokens'] == 32768
    assert result['snapshot']['max_output_tokens'] == 65536
    assert result['snapshot']['provider_input']['capacity']['output_reserved_tokens'] == 65536
    assert [row['max_tokens'] for row in scripted.generations()] == [32768, 65536]
    assert len(scripted.raw_read_units) == len(set(scripted.raw_read_units)) == 1
    assert [row['mode'] for row in scripted.calls] == ['read', 'generation', 'generation', 'review']
    assert_safe(saved)


@pytest.mark.parametrize('reason', ['content_filter', 'tool_calls', 'function_call',
    'insufficient_system_resource', None, 'unknown-provider-reason'])
def test_other_non_stop_never_accepts_even_valid_known_cited_markdown(model, reason):
    scripted = model([('generation', End(reason, VALID))])
    saved = []
    with pytest.raises(PipelineError):
        build(saved=saved)
    assert len(scripted.generations()) == 1
    assert not any(row['mode'] == 'review' for row in scripted.calls)
    state = held_generation(saved)
    assert not state['complete'] and not state.get('draft')
    assert state['draft_generation_attempts'][-1]['failure_diagnostic'].get('finish_reason') != 'stop'
    assert_safe(saved)


@pytest.mark.parametrize('failure', [402, 'timeout', 'cancel'], ids=['http402', 'timeout', 'cancel'])
def test_service_failure_or_cancel_stops_once_and_resume_never_rereads_sources(model, failure):
    scripted = model([('generation', failure)])
    saved = []
    expected = asyncio.CancelledError if failure == 'cancel' else PipelineError
    with pytest.raises(expected) as raised:
        build(saved=saved)
    assert len(scripted.generations()) == 1
    prior = copy.deepcopy(saved[-1])
    state = held_generation(saved)
    assert not state['complete'] and not state.get('draft')
    assert not state['draft_generation_attempts'][-1]['request_complete']
    if failure == 402:
        assert '余额不足' in str(raised.value) and '补充余额后继续' in str(raised.value)
        assert state['draft_generation_attempts'][-1]['failure_diagnostic']['http_status'] == 402
    assert_safe(saved)
    reading_units = list(scripted.raw_read_units)
    resumed = model([('generation', VALID)])
    result = build(previous=prior)
    assert_complete(result)
    assert reading_units and resumed.raw_read_units == []
    assert [row['mode'] for row in resumed.calls] == ['generation', 'review']


@pytest.mark.parametrize('failure', [402, 'timeout', 'cancel'], ids=['http402', 'timeout', 'cancel'])
def test_expanded_failed_request_retains_budget_and_resume_does_not_repeat_short_budget(model, failure):
    scripted = model([('generation', End()), ('generation', failure)])
    saved = []
    expected = asyncio.CancelledError if failure == 'cancel' else PipelineError
    with pytest.raises(expected):
        build(saved=saved)
    assert [row['max_tokens'] for row in scripted.generations()] == [32768, 65536]
    prior = copy.deepcopy(saved[-1])
    state = held_generation(saved)
    assert state['draft_output_budget'] == 65536 and not state['complete']
    assert_safe(saved)
    resumed = model([('generation', VALID)])
    assert_complete(build(previous=prior))
    assert resumed.raw_read_units == []
    assert [row['max_tokens'] for row in resumed.generations()] == [65536]


@pytest.mark.parametrize('after_growth', [False, True], ids=['before_first_request', 'before_enlarged_request'])
def test_checkpoint_cancel_before_request_persists_budget_and_resume_correctly(model, after_growth):
    scripted = model([('generation', End())] if after_growth else [])
    saved = []
    desired = 65536 if after_growth else 32768

    def cancel_before_request(state):
        current = state.get('report_generation') or {}
        attempts = current.get('draft_generation_attempts') or []
        if attempts and attempts[-1]['requested_max_tokens'] == desired and not attempts[-1]['request_complete']:
            if not attempts[-1].get('failure_diagnostic'):
                raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        build(saved=saved, checkpoint=cancel_before_request)
    assert len(scripted.generations()) == int(after_growth)
    prior = copy.deepcopy(saved[-1])
    assert held_generation(saved)['draft_output_budget'] == desired
    assert_safe(saved)
    resumed = model([('generation', VALID)])
    assert_complete(build(previous=prior))
    assert resumed.raw_read_units == []
    assert [row['max_tokens'] for row in resumed.generations()] == [desired]


@pytest.mark.parametrize('context,output,expected_cap', [(1048576, 40000, 'model'), (45000, 45000, 'context')])
def test_length_growth_stops_at_actual_model_or_wire_context_limit(model, monkeypatch, context, output, expected_cap):
    monkeypatch.setenv('DEEPSEEK_CONTEXT_WINDOW_TOKENS', str(context))
    monkeypatch.setenv('DEEPSEEK_MAX_OUTPUT_TOKENS', str(output))
    scripted = model([('generation', End()), ('generation', End())])
    saved = []
    with pytest.raises(PipelineError):
        build(saved=saved)
    calls = scripted.generations()
    assert len(calls) == 2 and not scripted.steps
    available = context - calls[0]['input_capacity']['input_tokens_upper_bound']
    assert calls[1]['max_tokens'] == min(65536, output, available)
    assert calls[1]['max_tokens'] > calls[0]['max_tokens']
    assert calls[1]['input_capacity']['fits']
    assert (output <= available) == (expected_cap == 'model')
    assert not held_generation(saved)['complete']
    assert_safe(saved)


def test_no_growth_at_provider_cap_makes_exactly_one_generation_request(model, monkeypatch):
    monkeypatch.setenv('DEEPSEEK_MAX_OUTPUT_TOKENS', '32768')
    scripted = model([('generation', End())])
    saved = []
    with pytest.raises(PipelineError):
        build(saved=saved)
    assert len(scripted.generations()) == 1 and not scripted.steps
    assert not held_generation(saved)['complete']


def test_initial_generation_budget_respects_smaller_explicit_model_output_limit(model, monkeypatch):
    monkeypatch.setenv('DEEPSEEK_MAX_OUTPUT_TOKENS', '16384')
    scripted = model([('generation', VALID)])
    result = build()
    assert_complete(result)
    assert result['snapshot']['initial_max_output_tokens'] == result['snapshot']['max_output_tokens'] == 16384
    assert [row['max_tokens'] for row in scripted.generations()] == [16384]


def test_capacity_change_reuses_complete_report_with_historical_actual_and_initial_budgets(model, monkeypatch):
    model([('generation', End()), ('generation', VALID)])
    saved = []
    initial = build(saved=saved)
    assert initial['snapshot']['initial_max_output_tokens'] == 32768
    assert initial['snapshot']['max_output_tokens'] == 65536
    prior = copy.deepcopy(saved[-1])
    monkeypatch.setenv('DEEPSEEK_MAX_OUTPUT_TOKENS', '16384')
    resumed = model([])
    result = build(previous=prior)
    assert_complete(result)
    assert resumed.calls == []
    assert result['snapshot']['initial_max_output_tokens'] == 32768
    assert result['snapshot']['max_output_tokens'] == 65536


def test_lower_current_cap_clamps_expanded_failed_request_without_rereading(model, monkeypatch):
    model([('generation', End()), ('generation', 402)])
    saved = []
    with pytest.raises(PipelineError, match='402'):
        build(saved=saved)
    prior = copy.deepcopy(saved[-1])
    assert held_generation(saved)['draft_output_budget'] == 65536
    monkeypatch.setenv('DEEPSEEK_MAX_OUTPUT_TOKENS', '32768')
    resumed = model([('generation', VALID)])
    result = build(previous=prior)
    assert_complete(result)
    assert resumed.raw_read_units == []
    assert [row['max_tokens'] for row in resumed.generations()] == [32768]
    assert result['snapshot']['initial_max_output_tokens'] == 32768
    assert result['snapshot']['max_output_tokens'] == 32768


@pytest.mark.parametrize('defect', ['missing_key', 'invalid_base'])
def test_configuration_failure_before_transport_is_not_classified_as_deadline(model, monkeypatch, defect):
    scripted = model([])
    saved = []

    def change_configuration_after_durable_reserve(state):
        current = state.get('report_generation') or {}
        if current.get('draft_generation_attempts'):
            if defect == 'missing_key':
                monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
            else:
                monkeypatch.setenv('DEEPSEEK_BASE_URL', 'not-a-provider-url')

    with pytest.raises(PipelineError) as raised:
        build(saved=saved, checkpoint=change_configuration_after_durable_reserve)
    assert raised.value.code == 'report_configuration'
    assert [row['mode'] for row in scripted.calls] == ['read']
    assert not scripted.generations()
    state = held_generation(saved)
    diagnostic = state['draft_generation_attempts'][-1]['failure_diagnostic']
    assert diagnostic['failure_kind'] == 'configuration'
    assert diagnostic['failure_kind'] != 'time_budget'
    assert not state['complete'] and not state.get('draft')
    assert_safe(saved)


@pytest.mark.parametrize('phase', ['complete', 'review_pending'])
def test_legacy_generation_fields_absent_preserves_compatible_draft_cache(model, phase):
    scripted = model([('generation', VALID)] + ([('review', 402)] if phase == 'review_pending' else []))
    saved = []
    if phase == 'review_pending':
        with pytest.raises(PipelineError):
            build(saved=saved)
    else:
        assert_complete(build(saved=saved))
    prior = copy.deepcopy(saved[-1])
    state = prior['report_generation']
    state.pop('draft_generation_attempts', None)
    state.pop('draft_output_budget', None)
    for attempt in state['attempts']:
        attempt.pop('generation_requested_max_tokens', None)
        attempt.pop('generation_finish_reason', None)
    resumed = model([])
    result = build(previous=prior)
    assert_complete(result)
    assert result['snapshot']['max_output_tokens'] == 32768
    assert resumed.raw_read_units == [] and resumed.generations() == []
    assert [row['mode'] for row in resumed.calls] == ([] if phase == 'complete' else ['review'])
    assert len(scripted.raw_read_units) == 1


def test_changed_generation_system_invalidates_draft_but_retains_accepted_source_reading(model, monkeypatch):
    first = model([('generation', VALID)])
    saved = []
    assert_complete(build(saved=saved))
    prior = copy.deepcopy(saved[-1])
    unchanged = copy.deepcopy(prior)
    original = generation.generate_checked_report

    async def changed_system(payload, *args, **options):
        payload = copy.deepcopy(payload)
        payload['messages'][0]['content'] += ' 新写作策略：先陈述保存来源的限定。'
        return await original(payload, *args, **options)

    monkeypatch.setattr(generation, 'generate_checked_report', changed_system)
    second = model([('generation', VALID)])
    result = build(previous=prior)
    assert_complete(result)
    assert first.raw_read_units and second.raw_read_units == []
    assert [row['mode'] for row in second.calls] == ['generation', 'review']
    assert result['snapshot']['report_generation']['input_fingerprint'] != prior['report_generation']['input_fingerprint']
    assert prior == unchanged


def test_legacy_semantic_correction_checkpoint_keeps_revision_and_accepted_reading(model):
    invalid = '# 保存来源分析\n\n来源来自未提供的证据。[S999]'
    first = model([('generation', invalid)] * 3)
    saved = []
    with pytest.raises(PipelineError):
        build(saved=saved)
    prior = copy.deepcopy(saved[-1])
    state = prior['report_generation']
    assert state['phase'] == 'correction_required' and state['revision_issues']
    state.pop('draft_generation_attempts', None)
    state.pop('draft_output_budget', None)
    for attempt in state['attempts']:
        attempt.pop('generation_requested_max_tokens', None)
        attempt.pop('generation_finish_reason', None)
    second = model([('generation', VALID)])
    result = build(previous=prior)
    assert result['snapshot']['report_generation']['complete']
    assert second.raw_read_units == [] and first.raw_read_units
    assert second.generations()[0]['data']['revision_request']['previous_markdown'] == invalid
    assert second.generations()[0]['data']['revision_request']['issues'] == state['revision_issues']
    assert [row['mode'] for row in second.calls] == ['generation', 'review']
    assert result['snapshot']['provider_usage']['completion_tokens'] == 200
    assert_safe(result['snapshot'])


@pytest.mark.parametrize('completion,prompt', [(32769, 100), (50, 1048576)], ids=['output_limit', 'context_limit'])
def test_stop_with_provider_usage_over_capacity_is_held_without_review(model, completion, prompt):
    scripted = model([('generation', End('stop', VALID, completion, prompt))])
    saved = []
    with pytest.raises(PipelineError):
        build(saved=saved)
    assert len(scripted.generations()) == 1
    assert not any(row['mode'] == 'review' for row in scripted.calls)
    state = held_generation(saved)
    assert not state['complete'] and not state.get('draft')
    assert state['draft_generation_attempts'][-1]['failure_diagnostic']['failure_kind'] == 'output_capacity'
    assert_safe(saved)


def test_length_failure_checkpoint_consuming_shared_deadline_blocks_next_request(model, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(generation, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    scripted = model([('generation', End())])
    saved = []

    def consume_deadline(state):
        current = state.get('report_generation') or {}
        attempts = current.get('draft_generation_attempts') or []
        if attempts and attempts[-1].get('failure_diagnostic', {}).get('finish_reason') == 'length':
            clock[0] = 100.0

    with pytest.raises(PipelineError) as raised:
        build(saved=saved, checkpoint=consume_deadline, seconds=10)
    assert raised.value.code == 'report_time_budget'
    assert raised.value.ai_diagnostic['failure_kind'] == 'time_budget'
    assert len(scripted.generations()) == 1 and not scripted.steps
    state = held_generation(saved)
    assert not state['complete'] and not state.get('draft')
    assert state['draft_generation_attempts'][-1]['failure_diagnostic']['finish_reason'] == 'length'
    assert_safe(saved)


def test_real_compression_notes_survive_length_http402_and_grown_budget_resume(model, monkeypatch):
    # Reuse only a pure input factory; this test runs the real compression and
    # consistency-review implementations through the local HTTP transport.
    from test_workbench_report_synthesis_capacity import packet
    data = packet(30, body_characters=4000, reading_characters=4000)
    for source in data['sources']:
        source['metadata'] = {'content_kind': 'body'}
        source.pop('evidence_kind', None)
    items = [{'citation_id': row['citation_id'], 'title': row['title'],
        'url': 'https://public.example/' + row['citation_id'], 'source_name': row['source_name'],
        'type': 'news', 'content': row['content_fragments'][0]['text'],
        'publication_day': row['publication_day'], 'published_at': row['published_at'],
        'metadata': {'content_kind': 'body'}} for row in data['sources']]
    monkeypatch.setenv('DEEPSEEK_CONTEXT_WINDOW_TOKENS', '131072')
    monkeypatch.setenv('DEEPSEEK_MAX_OUTPUT_TOKENS', '90000')
    payload = {'model': 'deepseek-flash', 'reasoning_effort': 'high', 'max_tokens': 32768,
        'messages': [{'role': 'system', 'content': '依据全部保存来源写报告，用[S001]引用。'},
                     {'role': 'user', 'content': compact_json(data)}]}
    saved, compressed = [], []
    first = model([('generation', End()), ('generation', 402)])
    with pytest.raises(PipelineError, match='402'):
        asyncio.run(generation.generate_checked_report(payload, data, items,
            checkpoint=lambda state: saved.append(copy.deepcopy(state)),
            synthesis_checkpoint=lambda state: compressed.append(copy.deepcopy(state))))
    assert [row['max_tokens'] for row in first.generations()] == [32768, 65536]
    assert any(row['mode'] == 'compression' for row in first.calls)
    assert any(row['mode'] == 'compression_review' for row in first.calls)
    assert compressed[-1]['complete'] and compressed[-1]['batches']
    held, accepted_notes = copy.deepcopy(saved[-1]), copy.deepcopy(compressed[-1])
    second = model([('generation', VALID)])
    resumed = []

    def stop_after_durable_draft(state):
        resumed.append(copy.deepcopy(state))
        if state['phase'] == 'review_pending' and state.get('draft'):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(generation.generate_checked_report(payload, data, items,
            previous_state=held, previous_synthesis=accepted_notes,
            checkpoint=stop_after_durable_draft,
            synthesis_checkpoint=lambda state: compressed.append(copy.deepcopy(state))))
    assert [row['mode'] for row in second.calls] == ['generation']
    assert second.generations()[0]['max_tokens'] == 65536
    assert resumed[-1]['draft'] == VALID and not resumed[-1]['complete']
    assert accepted_notes['batches'] == compressed[-1]['batches']
    assert_safe(saved + resumed + compressed)
