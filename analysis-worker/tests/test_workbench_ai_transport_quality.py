import asyncio

import httpx
import pytest

from app.workbench import query_plan
from app.workbench.pipeline import PipelineError


@pytest.mark.parametrize('finish', ['length', 'content_filter', 'unrecognized-provider-text'])
def test_incomplete_response_is_rejected_with_only_safe_budget_diagnostics(monkeypatch, finish):
    native = httpx.AsyncClient
    def handler(request):
        return httpx.Response(200, json={'choices': [{'finish_reason': finish,
            'message': {'content': 'private-response-marker', 'reasoning_content': 'private-reasoning-marker'}}],
            'usage': {'completion_tokens': 32768}})
    monkeypatch.setattr(query_plan.httpx, 'AsyncClient', lambda **options: native(transport=httpx.MockTransport(handler), **options))
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'synthetic-secret-marker')
    monkeypatch.setenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com')
    with pytest.raises(PipelineError) as caught:
        asyncio.run(query_plan.configured_ai_json('仅输出 JSON', {'public': '公开材料'}, max_tokens=32768))
    assert caught.value.ai_diagnostic == {'finish_reason': finish if finish in {'length', 'content_filter'} else 'other',
        'requested_max_tokens': 32768, 'reasoning_effort': 'low', 'completion_tokens': 32768}
    assert not any(marker in str(caught.value) + str(caught.value.ai_diagnostic)
        for marker in ('private-response-marker', 'private-reasoning-marker', 'synthetic-secret-marker'))


def test_completed_json_response_remains_usable(monkeypatch):
    native = httpx.AsyncClient
    monkeypatch.setattr(query_plan.httpx, 'AsyncClient', lambda **options: native(
        transport=httpx.MockTransport(lambda request: httpx.Response(200,
            json={'choices': [{'finish_reason': 'stop', 'message': {'content': '{"affiliation_candidates":[]}'}}]})), **options))
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'synthetic-secret-marker')
    monkeypatch.setenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com')
    result = asyncio.run(query_plan.configured_ai_json('仅输出 JSON', {'public': '公开材料'}, max_tokens=32768))
    assert result == {'affiliation_candidates': []}
