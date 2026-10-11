import asyncio
import json

import httpx
import pytest

from app.workbench import query_plan
from app.workbench import ai_capacity
from app.workbench.pipeline import PipelineError


@pytest.fixture
def mock_ai(monkeypatch):
    for name in ('DEEPSEEK_CONTEXT_WINDOW_TOKENS', 'DEEPSEEK_MAX_OUTPUT_TOKENS'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv('DEEPSEEK_TIMEOUT_SECONDS', raising=False)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'synthetic-test-only')
    monkeypatch.setenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com')
    monkeypatch.setenv('DEEPSEEK_MODEL', 'deepseek-flash')
    calls = []
    native = httpx.AsyncClient

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [
            {'finish_reason': 'stop', 'message': {'content': '{"ok":true}'}}]})

    monkeypatch.setattr(query_plan.httpx, 'AsyncClient', lambda **options: native(
        transport=httpx.MockTransport(handler), **options))
    return calls


def test_flash_accepts_lossless_300k_characters_above_old_parameter_ceiling(mock_ai):
    text = 'A' * 300000
    result = asyncio.run(query_plan.configured_ai_json(
        'Return JSON.', {'source': text}, max_tokens=32768, max_input_chars=500000))
    assert result == {'ok': True}
    assert len(mock_ai) == 1
    assert json.loads(mock_ai[0]['messages'][1]['content']) == {'source': text}


def test_flash_default_budget_accepts_310k_japanese_characters_losslessly(mock_ai):
    text = '平' * 310000
    result = asyncio.run(query_plan.configured_ai_json(
        'Return JSON.', {'source': text}, max_tokens=32768))
    assert result == {'ok': True}
    assert json.loads(mock_ai[0]['messages'][1]['content'])['source'] == text


def test_http_json_escaping_does_not_consume_message_character_budget(mock_ai):
    data = {'source': '"' * 110000, 'other': ['日本', '\\', '\n', '😀']}
    result = asyncio.run(query_plan.configured_ai_json(
        'JSON', data, max_tokens=32768, max_input_chars=230000))
    assert result == {'ok': True}
    messages = mock_ai[0]['messages']
    assert sum(len(m['content']) + len(m['role']) for m in messages) < 230000
    assert len(json.dumps(mock_ai[0], ensure_ascii=False)) > 400000
    assert json.loads(messages[1]['content']) == data


@pytest.mark.parametrize('model', ['deepseek-flash', 'deepseek-v4-flash',
                                 'deepseek-v4-flash-vision-exp', 'deepseek-v4-pro'])
def test_official_known_models_have_1m_capacity_without_discovery(model):
    capacity = ai_capacity.configured_model_capacity(model=model, environ={})
    assert capacity.context_window == 1048576
    assert capacity.max_output_tokens == 393216
    assert capacity.source == ai_capacity.OFFICIAL_CAPACITY_SOURCE


@pytest.mark.parametrize('base,model', [
    ('https://other-provider.example/v1', 'deepseek-flash'),
    ('https://api.deepseek.com.evil.example', 'deepseek-flash'),
    ('https://api.deepseek.com', 'custom-model'),
    ('https://api.deepseek.com', 'deepseek-v4-pro-0813'),
    ('https://api.deepseek.com', 'deepseek-chat'),
    ('https://api.deepseek.com', 'deepseek-reasoner'),
    ('http://api.deepseek.com', 'deepseek-flash'),
])
def test_unknown_provider_or_model_never_inherits_1m(base, model):
    capacity = ai_capacity.configured_model_capacity(model=model, base_url=base, environ={})
    assert capacity.context_window == 131072
    assert capacity.source == 'unverified_provider_fallback'
    assert not ai_capacity.assess_messages(
        [{'role': 'user', 'content': 'A' * 100000}],
        max_output_tokens=32768, capacity=capacity).fits


def test_explicit_public_model_metadata_and_env_overrides_are_supported():
    capacity = ai_capacity.configured_model_capacity(
        model='custom-model', base_url='https://provider.example', environ={},
        metadata={'id': 'custom-model', 'context_window': 2000000, 'max_output_tokens': 50000})
    assert (capacity.context_window, capacity.max_output_tokens, capacity.source) == (
        2000000, 50000, 'explicit_model_metadata')
    env_capacity = ai_capacity.configured_model_capacity(environ={
        'DEEPSEEK_CONTEXT_WINDOW_TOKENS': '262144', 'DEEPSEEK_MAX_OUTPUT_TOKENS': '16384'})
    assert (env_capacity.context_window, env_capacity.max_output_tokens, env_capacity.source) == (
        262144, 16384, 'explicit_environment_configuration')
    metadata_wins = ai_capacity.configured_model_capacity(
        environ={'DEEPSEEK_CONTEXT_WINDOW_TOKENS': '1024'},
        metadata={'context_window': 1048576, 'max_output_tokens': 393216})
    assert metadata_wins.context_window == 1048576


@pytest.mark.parametrize('metadata', [
    {'id': 'different-model', 'context_window': 1048576},
    {'context_window': True}, {'context_window': 100.5}, {'context_window': '1e6'},
    {'context_window': 'NaN'}, {'context_window': 0},
    {'context_window': 1024, 'max_output_tokens': 1025},
])
def test_invalid_capacity_metadata_is_rejected(metadata):
    with pytest.raises(ValueError):
        ai_capacity.configured_model_capacity(metadata=metadata, environ={})


def test_actual_message_utf8_bytes_and_output_reserve_are_assessed_separately():
    messages = [{'role': 'system', 'content': 'JSON'}, {'role': 'user', 'content': '日本😀'}]
    capacity = ai_capacity.ModelCapacity('test', 1024, 256, 'test_metadata')
    result = ai_capacity.assess_messages(messages, max_output_tokens=128, capacity=capacity)
    assert result.utf8_bytes == len('systemJSONuser日本😀'.encode('utf-8'))
    assert result.input_tokens_upper_bound == result.utf8_bytes + 256 + 8 * 2
    assert result.input_capacity_tokens == 896
    assert result.output_reserved_tokens == 128
    assert result.fits
    diagnostics = result.as_dict()
    assert diagnostics['exact_token_count'] is False
    assert diagnostics['estimation_method'] == ai_capacity.ESTIMATION_METHOD
    assert diagnostics['estimator_assumption'] == 'ordinary_byte_based_text_tokenizer'
    assert '日本' not in str(diagnostics)


def test_exact_capacity_boundary_includes_output_and_protocol_reserve():
    capacity = ai_capacity.ModelCapacity('test', 1024, 256, 'test_metadata')
    content_length = 1024 - 128 - 256 - 8 - len('user')
    fits = ai_capacity.assess_messages([{'role': 'user', 'content': 'A' * content_length}],
                                     max_output_tokens=128, capacity=capacity)
    exceeds = ai_capacity.assess_messages([{'role': 'user', 'content': 'A' * (content_length + 1)}],
                                        max_output_tokens=128, capacity=capacity)
    assert fits.fits and not exceeds.fits
    assert fits.input_tokens_upper_bound == fits.input_capacity_tokens


@pytest.mark.parametrize('messages', [
    [{'role': 'user', 'content': [{'type': 'image_url', 'image_url': 'private-marker'}]}],
    [{'role': 'assistant', 'content': '', 'tool_calls': [{'function': 'unestimated'}]}],
    [{'role': 'user', 'content': 'text', 'name': 1}],
])
def test_non_text_or_unestimated_messages_are_rejected(messages):
    with pytest.raises(ValueError):
        ai_capacity.assess_messages(messages, max_output_tokens=1800,
                                    capacity=ai_capacity.configured_model_capacity(environ={}))


@pytest.mark.parametrize('output', [0, -1, True, 2.5, '32', 393217])
def test_invalid_or_oversized_output_budgets_are_rejected(output):
    with pytest.raises(ValueError):
        ai_capacity.assess_messages([{'role': 'user', 'content': 'public'}],
                                    max_output_tokens=output,
                                    capacity=ai_capacity.configured_model_capacity(environ={}))


def test_model_capacity_failure_is_safe_distinct_and_never_posts(mock_ai):
    marker = 'private-source-marker'
    with pytest.raises(PipelineError) as caught:
        asyncio.run(query_plan.configured_ai_json(
            'JSON', {'source': '平' * 350000 + marker}, max_tokens=32768))
    assert caught.value.code == 'model_context_capacity'
    assert caught.value.ai_diagnostic['context_window'] == 1048576
    assert caught.value.ai_diagnostic['output_reserved_tokens'] == 32768
    assert caught.value.ai_diagnostic['exact_token_count'] is False
    assert marker not in str(caught.value.ai_diagnostic) + str(caught.value)
    assert 'synthetic-test-only' not in str(caught.value.ai_diagnostic) + str(caught.value)
    assert not mock_ai


def test_small_task_character_limit_is_distinct_and_never_posts(mock_ai):
    with pytest.raises(PipelineError) as caught:
        asyncio.run(query_plan.configured_ai_json('JSON', {'source': 'A' * 1000}, max_input_chars=500))
    assert caught.value.code == 'per_call_input_chars'
    assert caught.value.ai_diagnostic['max_input_chars'] == 500
    assert '字符预算' in str(caught.value)
    assert not mock_ai


@pytest.mark.parametrize('env_timeout,limit,expected', [
    (None, 600, 600), ('480', 600, 480), ('3', 600, 3),
    ('900', 75, 75), ('NaN', 600, 600), ('inf', 600, 600),
    ('0', 600, 600), ('invalid', 75, 75),
])
def test_timeout_preserves_explicit_configuration_and_per_call_limit(
        mock_ai, monkeypatch, env_timeout, limit, expected):
    if env_timeout is not None:
        monkeypatch.setenv('DEEPSEEK_TIMEOUT_SECONDS', env_timeout)
    factory = query_plan.httpx.AsyncClient
    timeouts = []

    def recording_factory(**options):
        timeouts.append(options['timeout'].read)
        return factory(**options)

    monkeypatch.setattr(query_plan.httpx, 'AsyncClient', recording_factory)
    assert asyncio.run(query_plan.configured_ai_json('JSON', {'source': 'public'},
                                                    timeout_limit=limit)) == {'ok': True}
    assert timeouts == [expected]


def test_compact_json_preserves_every_value_and_only_removes_formatting_spaces():
    data = {'source': '内部 空格\n二行 "引用" \\ 😀', 'rows': [{'value': 1}, None, True]}
    compact = ai_capacity.compact_json(data)
    assert json.loads(compact) == data
    assert len(compact) < len(json.dumps(data, ensure_ascii=False))
