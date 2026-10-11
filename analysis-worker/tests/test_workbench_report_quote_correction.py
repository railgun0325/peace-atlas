"""Synthetic reading fixtures: exact quotes, bounded correction and durable resume."""
import asyncio
import copy
from types import SimpleNamespace

import pytest

from app.workbench import report_analysis as reports
from app.workbench.pipeline import PipelineError


def sources(count=12):
    return reports.source_samples([{'citation_id': f'S{index:03d}',
        'title': '标题中的单独句子不可替代正文',
        'content': f'公开正文编号{index:03d}。来源声明活动尚未发生，仍需独立核对。'}
        for index in range(1, count + 1)])[0]


def good(batch):
    return {'source_reviews': [{'citation_id': row['citation_id'],
        'quote': row['content_fragments'][0]['text'][:30],
        'reading': '所给正文记录来源自身说法。',
        'limitations': '原样摘录并不证明说法属实。'} for row in batch]}


def test_complete_batch_quote_only_error_gets_one_exact_correction(monkeypatch):
    material = sources()
    original = copy.deepcopy(material)
    calls, checkpoints = [], []
    async def provider(system, data, **options):
        calls.append(copy.deepcopy(data))
        if len(calls) == 2:
            assert checkpoints[-1]['quote_repairs_used'] == 1
            assert checkpoints[-1]['batches'][-1]['status'] == 'quote_correction_pending'
            assert checkpoints[-1]['coverage_progress']['reviewed_source_count'] == 0
        response = good(data['sources'])
        if len(calls) == 1:
            response['source_reviews'][0]['quote'] = '来源中没有提供的虚构引文'
        return response
    monkeypatch.setattr(reports, 'configured_ai_json', provider)
    state = asyncio.run(reports.read_sources(material, checkpoint=lambda value: checkpoints.append(value), return_state=True))
    assert state['complete'] and len(calls) == 2
    assert calls[0]['sources'] == calls[1]['sources']
    assert [(row['citation_id'], row['title'], row['content_fragments']) for row in calls[0]['sources']] == [
        (row['citation_id'], row['title'], row['content_fragments']) for row in original]
    assert material == original
    assert state['quote_repairs_used'] == 1
    assert state['batches'][0]['quote_corrected'] is True
    assert len(state['batches'][0]['attempts']) == 2
    assert '来源中没有提供的虚构引文' not in repr(state)
    for row, source in zip(state['fragment_reviews'], material):
        assert row['quote'] in source['content_fragments'][0]['text']


def test_second_unsupported_quote_still_fails_without_accepting_it(monkeypatch):
    calls = []
    async def provider(system, data, **options):
        calls.append(data)
        result = good(data['sources'])
        result['source_reviews'][0]['quote'] = '再一次未提供的虚构引文'
        return result
    monkeypatch.setattr(reports, 'configured_ai_json', provider)
    state = asyncio.run(reports.read_sources(sources(), return_state=True))
    assert len(calls) == 2 and not state['complete']
    assert state['quote_repairs_used'] == 1 and not state['fragment_reviews']
    assert state['stop_reason'] == 'source_read_error'
    assert state['batches'][0]['status'] == 'failed'


@pytest.mark.parametrize('other_error', ['unknown_id', 'missing_row', 'duplicate_id', 'extra_url', 'extra_field'])
def test_quote_plus_other_error_never_gets_automatic_correction(monkeypatch, other_error):
    calls = []
    async def provider(system, data, **options):
        calls.append(data)
        result = good(data['sources'])
        result['source_reviews'][0]['quote'] = '原文未提供的虚构摘录'
        if other_error == 'unknown_id': result['source_reviews'][-1]['citation_id'] = 'S999'
        if other_error == 'missing_row': result['source_reviews'].pop()
        if other_error == 'duplicate_id': result['source_reviews'][-1]['citation_id'] = 'S001'
        if other_error == 'extra_url': result['source_reviews'][-1]['reading'] = 'https://unknown.example/extra'
        if other_error == 'extra_field': result['source_reviews'][-1]['extra'] = 'not allowed'
        return result
    monkeypatch.setattr(reports, 'configured_ai_json', provider)
    state = asyncio.run(reports.read_sources(sources(), return_state=True))
    assert len(calls) == 1 and not state['complete'] and not state['fragment_reviews']
    assert state.get('quote_repairs_used', 0) == 0


@pytest.mark.parametrize('message', ['HTTP fixture failure', 'AI 输出未完整结束；未采用截断内容。',
                                     'AI 最终响应未通过安全保留校验；本次未使用 AI 结果。'])
def test_provider_failure_does_not_trigger_quote_correction(monkeypatch, message):
    calls = []
    async def provider(system, data, **options):
        calls.append(data)
        raise PipelineError(message)
    monkeypatch.setattr(reports, 'configured_ai_json', provider)
    state = asyncio.run(reports.read_sources(sources(), return_state=True))
    assert len(calls) == 1 and not state['complete']
    assert state.get('quote_repairs_used', 0) == 0


def test_title_only_with_body_requires_fresh_exact_body_quote(monkeypatch):
    calls = []
    async def provider(system, data, **options):
        calls.append(data)
        result = good(data['sources'])
        if len(calls) == 1:
            result['source_reviews'][0]['quote'] = data['sources'][0]['title']
        return result
    monkeypatch.setattr(reports, 'configured_ai_json', provider)
    state = asyncio.run(reports.read_sources(sources(1), return_state=True))
    assert state['complete'] and len(calls) == 2
    assert state['batches'][0]['attempts'][0]['rejections'][0]['reason'] == 'title_only_with_body'
    assert state['fragment_reviews'][0]['quote_basis'] == 'sent_body_fragment'


def test_correction_subtracts_elapsed_time_from_same_total_budget(monkeypatch):
    clock = SimpleNamespace(now=100.)
    calls, saved = [], []
    async def provider(system, data, **options):
        calls.append(copy.deepcopy(options))
        clock.now += 2.
        result = good(data['sources'])
        if len(calls) == 1: result['source_reviews'][0]['quote'] = '输入中不存在的虚构摘录'
        return result
    monkeypatch.setattr(reports, 'configured_ai_json', provider)
    monkeypatch.setattr(reports, 'time', SimpleNamespace(monotonic=lambda: clock.now))
    state = asyncio.run(reports.read_sources(sources(1), time_budget_seconds=10,
        checkpoint=lambda value: saved.append(value), return_state=True))
    assert state['complete'] and [row['timeout_limit'] for row in calls] == [10, 8]
    assert saved[0]['batches'][0]['status'] == 'quote_correction_pending'


def test_exhausted_time_stops_before_second_request(monkeypatch):
    clock = SimpleNamespace(now=100.)
    calls = []
    async def provider(system, data, **options):
        calls.append(data)
        clock.now += 11.
        result = good(data['sources'])
        result['source_reviews'][0]['quote'] = '输入中不存在的虚构摘录'
        return result
    monkeypatch.setattr(reports, 'configured_ai_json', provider)
    monkeypatch.setattr(reports, 'time', SimpleNamespace(monotonic=lambda: clock.now))
    state = asyncio.run(reports.read_sources(sources(1), time_budget_seconds=10, return_state=True))
    assert len(calls) == 1 and not state['complete']
    assert state['stop_reason'] == 'time_budget'


def test_failed_checkpoint_cancel_preserves_prior_good_batch_before_correction(monkeypatch):
    calls, saved = [], []
    async def provider(system, data, **options):
        calls.append(data)
        result = good(data['sources'])
        if len(calls) == 2: result['source_reviews'][0]['quote'] = '输入中不存在的虚构摘录'
        return result
    def checkpoint(state):
        saved.append(copy.deepcopy(state))
        if state['batches'][-1]['status'] == 'quote_correction_pending':
            raise asyncio.CancelledError()
    monkeypatch.setattr(reports, 'configured_ai_json', provider)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(reports.read_sources(sources(24), checkpoint=checkpoint, return_state=True))
    assert len(calls) == 2
    assert saved[-1]['coverage_progress'] == {'input_source_count': 24,
        'reviewed_source_count': 12, 'pending_source_count': 12}
    assert saved[-1]['quote_repairs_used'] == 1
    assert saved[-1]['batches'][-1]['attempts'][0]['failure_kind'] == 'quote_support'


def test_cumulative_quote_limit_survives_cancelled_checkpoint_resume(monkeypatch):
    calls = []
    async def bad(system, data, **options):
        calls.append(data)
        result = good(data['sources'])
        result['source_reviews'][0]['quote'] = '输入中不存在的虚构摘录'
        return result
    monkeypatch.setattr(reports, 'configured_ai_json', bad)
    previous = None
    for used in range(1, 13):
        calls.clear()
        previous = asyncio.run(reports.read_sources(sources(1), previous_review=previous, return_state=True))
        assert len(calls) == 2 and previous['quote_repairs_used'] == used
        assert not previous['complete']
    calls.clear()
    second = asyncio.run(reports.read_sources(sources(1), previous_review=previous, return_state=True))
    assert len(calls) == 1 and second['quote_repairs_used'] == 12
    assert second['batches'][0]['quote_repair_refused'] == 'cumulative_limit'
    calls.clear()
    with pytest.raises(PipelineError, match='纠正预算记录无效'):
        asyncio.run(reports.read_sources(sources(1), previous_review={**previous, 'quote_repairs_used': 13}, return_state=True))
    assert not calls
