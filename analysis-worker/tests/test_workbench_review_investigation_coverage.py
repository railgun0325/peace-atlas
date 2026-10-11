"""Automatic unknown decisions must reflect an actual permitted discovery attempt."""
import asyncio
import pytest

from app.workbench.automated_review import ReviewGateway, validate_decisions
from app.workbench.pipeline import PipelineError


def fixture(network=True):
    rows = [
        {'kind':'accounts','id':identity,'record':{'platform':'twitter','handle':handle,'name':handle}}
        for identity,handle in [('account-a','public_a'),('account-b','public_b')]
    ]
    gateway = ReviewGateway(rows, use_public_network=network)
    value = {'decisions':[{'kind':'accounts','id':row['id'],'outcome':'unknown',
                          'note':'公开出处读取受限，无法判定','evidence':[]} for row in rows]}
    return gateway, value


def test_network_enabled_unknown_cannot_skip_any_account_investigation():
    gateway, value = fixture()
    trace = [{'name':'inspect_account','ok':True,'result':{'account_key':'twitter:public_a','search_unavailable':True}}]
    with pytest.raises(PipelineError, match='twitter:public_b'):
        validate_decisions(value, gateway, trace)


def test_missing_investigations_are_reported_together_for_one_repair():
    gateway, value = fixture()
    with pytest.raises(PipelineError) as error:
        validate_decisions(value, gateway, [])
    assert 'twitter:public_a' in str(error.value) and 'twitter:public_b' in str(error.value)


def test_provider_unavailable_is_an_honest_completed_attempt_not_confirmation():
    gateway, value = fixture()
    trace = [{'name':'inspect_account','ok':True,'result':{'account_key':'twitter:'+handle,
              'search_unavailable':True,'search_results':[]}} for handle in ('public_a','public_b')]
    accepted = validate_decisions(value, gateway, trace)
    assert [row['outcome'] for row in accepted['decisions']] == ['unknown','unknown']


def test_network_disabled_does_not_require_forbidden_investigation():
    gateway, value = fixture(network=False)
    assert len(validate_decisions(value, gateway, [])['decisions']) == 2


def test_failed_or_other_account_inspection_cannot_satisfy_coverage():
    gateway, value = fixture()
    trace = [{'name':'inspect_account','ok':False,'result':{'account_key':'twitter:public_a'}},
             {'name':'inspect_account','ok':True,'result':{'account_key':'twitter:unrelated'}}]
    with pytest.raises(PipelineError, match='twitter:public_a'):
        validate_decisions(value, gateway, trace)


def test_discovery_is_account_scoped_and_reused_within_and_between_batches(monkeypatch):
    from app.workbench import review_search
    calls = []
    async def search(account):
        calls.append(account['handle'])
        return {'saved_sources': [], 'search_results': [], 'diagnostics': [{'status':'limited'}],
                'search_is_not_verification':True}
    monkeypatch.setattr(review_search, 'search_account_public', search)
    gateway, _ = fixture()
    args = {'account_key':'twitter:public_a'}
    first = asyncio.run(gateway.call('inspect_account', args))
    assert first['account_key'] == 'twitter:public_a' and first['search_is_not_verification']
    assert asyncio.run(gateway.call('inspect_account', args)) == first
    later = ReviewGateway(gateway.rows, _searches=gateway.searches)
    assert asyncio.run(later.call('inspect_account', args)) == first
    assert calls == ['public_a']
    from app.workbench.harness_evidence import EvidenceGatewayError
    with pytest.raises(EvidenceGatewayError):
        asyncio.run(gateway.call('inspect_account', {'account_key':'twitter:outside_batch'}))


def test_network_disabled_inspection_never_calls_discovery(monkeypatch):
    from app.workbench import review_search
    async def forbidden(account):
        raise AssertionError('Network-disabled review must not invoke public discovery')
    monkeypatch.setattr(review_search, 'search_account_public', forbidden)
    gateway, _ = fixture(network=False)
    result = asyncio.run(gateway.call('inspect_account', {'account_key':'twitter:public_a'}))
    assert result['search_results'] == [] and result['diagnostics'][0]['status'] == 'network_disabled'
