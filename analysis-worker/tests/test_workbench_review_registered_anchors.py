"""Exact target registry anchors do not authorize other accounts on the same page."""
import asyncio
import copy

import pytest

from app.workbench import automated_review as auto
from app.workbench.pipeline import PipelineError


def account(identity, handle, *, basis=None):
    sources = [] if not basis else [{'url':'https://publisher.example.org/about','label':'Registered source',
                                     'account_type':'organization','trust_basis':basis}]
    return {'kind':'accounts','id':identity,'record':{'platform':'twitter','handle':handle,'name':handle,
            'url':'https://x.com/'+handle, 'metadata':{'verification_sources':sources}}}


def inspect(rows, monkeypatch, *, body=None, final=None, pages=None):
    async def fetch(url, **kwargs):
        return (body or '<main>Public A is a registered organization.<a href="https://x.com/public_a">Official A</a>'
                '<a href="https://x.com/public_b">Quoted guest B</a></main>').encode(), final or url, 200
    monkeypatch.setattr(auto.pipeline,'_fetch_public_feed',fetch)
    gateway = auto.ReviewGateway(rows, _pages=pages)
    proof = asyncio.run(gateway.call('check_citation', {'citation_id':'https://publisher.example.org/about',
                                                     'exact_quote':'Public A is a registered organization.'}))
    return gateway, proof


def answer(identity):
    return {'decisions':[{'kind':'accounts','id':identity,'outcome':'organization','note':'Target-specific registered publisher and exact homepage',
             'evidence':[{'citation_id':'https://publisher.example.org/about','quote':'Public A is a registered organization.'}]}]}


def test_target_manual_registry_body_backlink_is_accepted_without_global_ownership(monkeypatch):
    gateway, proof = inspect([account('a','public_a',basis='manual_registry')],monkeypatch)
    assert proof['owner_account_keys'] == []
    assert proof['registered_account_keys'] == ['twitter:public_a']
    result = auto.validate_decisions(answer('a'),gateway,[{'name':'check_citation','ok':True,'result':proof}])
    assert result['decisions'][0]['outcome'] == 'organization'


@pytest.mark.parametrize('basis', [None,'evidence_review_registry'])
def test_unregistered_or_automatic_source_body_link_is_not_an_owner_assertion(monkeypatch,basis):
    gateway, proof = inspect([account('a','public_a',basis=basis)],monkeypatch)
    with pytest.raises(PipelineError,match='站点自有'):
        auto.validate_decisions(answer('a'),gateway,[{'name':'check_citation','ok':True,'result':proof}])


def test_shared_page_cache_does_not_spread_manual_authority_to_quoted_guest(monkeypatch):
    pages = {}
    gateway, proof = inspect([account('a','public_a',basis='manual_registry')],monkeypatch,pages=pages)
    original = copy.deepcopy(pages)
    guest, guest_proof = inspect([account('b','public_b')],monkeypatch,pages=pages)
    assert guest_proof.get('registered_account_keys',[]) == []
    assert pages == original
    with pytest.raises(PipelineError,match='站点自有'):
        auto.validate_decisions(answer('b'),guest,[{'name':'check_citation','ok':True,'result':guest_proof}])


def test_registered_anchor_cannot_transfer_across_independent_sites(monkeypatch):
    gateway, proof = inspect([account('a','public_a',basis='manual_registry')],monkeypatch,
                            final='https://other-publisher.example.org/changed')
    assert proof.get('registered_account_keys',[]) == []
    with pytest.raises(PipelineError,match='站点自有'):
        auto.validate_decisions(answer('a'),gateway,[{'name':'check_citation','ok':True,'result':proof}])


@pytest.mark.parametrize('link', ['https://x.com/public_a/status/123','https://x.com/public_aa',
                                'https://x.com.attacker.example.org/public_a'])
def test_exact_registered_subject_still_requires_exact_homepage(monkeypatch,link):
    gateway, proof = inspect([account('a','public_a',basis='manual_registry')],monkeypatch,
                            body=f'<main>Public A is a registered organization.<a href="{link}">A</a></main>')
    assert proof.get('registered_account_keys',[]) == []
    with pytest.raises(PipelineError,match='站点自有'):
        auto.validate_decisions(answer('a'),gateway,[{'name':'check_citation','ok':True,'result':proof}])
