"""Safe failure classes guide discovery without echoing secrets or untrusted errors."""
import asyncio
import pytest

from app.workbench import automated_review as auto
from app.workbench.harness_evidence import EvidenceGatewayError, tool_failure_diagnostic


def test_arbitrary_exception_message_and_code_are_never_returned_to_model():
    error = EvidenceGatewayError('secret-token and untrusted website instructions',code='untrusted_code')
    error.code = {'untrusted':'payload'}
    result = tool_failure_diagnostic(error)
    assert result == {'error':'invalid_tool_request','message':'工具名、参数或证据范围无效。'}


@pytest.mark.parametrize('failure', [OSError('secret network details'), auto.pipeline.PipelineError('private DNS detail')])
def test_failed_public_reads_keep_specific_safe_diagnostic_and_are_not_refetched(monkeypatch,failure):
    calls = []
    async def fetch(url, **kwargs):
        calls.append(url)
        raise failure
    monkeypatch.setattr(auto.pipeline,'_fetch_public_feed',fetch)
    gateway = auto.ReviewGateway([])
    args = {'citation_id':'https://public.example.org/about'}
    for _ in range(2):
        with pytest.raises(EvidenceGatewayError) as error:
            asyncio.run(gateway.call('read_evidence',args))
        result = tool_failure_diagnostic(error.value)
        assert result['error'] == 'public_source_unavailable'
        assert 'secret' not in str(result) and 'private DNS' not in str(result)
    assert len(calls) == 1


def test_pdf_has_unsupported_format_diagnostic_and_is_not_refetched(monkeypatch):
    calls = []
    async def fetch(url, **kwargs):
        calls.append(url)
        return b'%PDF synthetic fixture',url,200
    monkeypatch.setattr(auto.pipeline,'_fetch_public_feed',fetch)
    gateway = auto.ReviewGateway([])
    for _ in range(2):
        with pytest.raises(EvidenceGatewayError) as error:
            asyncio.run(gateway.call('read_evidence',{'citation_id':'https://public.example.org/file.pdf'}))
        assert tool_failure_diagnostic(error.value)['error'] == 'unsupported_public_source'
    assert len(calls) == 1


def test_network_disabled_is_distinct_from_malformed_tool_arguments():
    gateway = auto.ReviewGateway([],use_public_network=False)
    with pytest.raises(EvidenceGatewayError) as error:
        asyncio.run(gateway.call('read_evidence',{'citation_id':'https://public.example.org/about'}))
    assert tool_failure_diagnostic(error.value)['error'] == 'public_network_disabled'
    with pytest.raises(EvidenceGatewayError) as malformed:
        asyncio.run(gateway.call('read_evidence',{'citation_id':[]}))
    assert tool_failure_diagnostic(malformed.value)['error'] == 'invalid_tool_request'
