"""Public profile classification is separate from official account verification."""
import asyncio
from contextlib import closing
import copy

import pytest

from app.workbench import automated_review as auto
from app.workbench.pipeline import PipelineError
from app.workbench.review import apply_batch, preview_batch, snapshot, ReviewConflict
from app.workbench.store import Store
from app.workbench.account_identity import project_account


def classified_input(store, outcome, bio):
    imported = store.import_social([], [{'name': '公開主体', 'platform': 'twitter', 'handle': 'public_actor',
        'url': 'https://x.com/public_actor', 'profile': {'verified_in_dom': True, 'url': 'https://x.com/public_actor', 'bio': bio,
        'observed_at': '2026-10-11T00:00:00Z'}}], [])
    account = store.get_account(imported['account_ids']['twitter:public_actor'])
    row = snapshot(store, ['accounts'])[0]
    gateway = auto.ReviewGateway([row], use_public_network=False)
    citation = 'accounts:' + row['id']
    read = asyncio.run(gateway.call('read_evidence', {'citation_id': citation}))
    proof = asyncio.run(gateway.call('check_citation', {'citation_id': citation, 'exact_quote': bio or account['name']}))
    trace = [{'name': 'read_evidence', 'ok': True, 'args': {'citation_id': citation}, 'result': read},
             {'name': 'check_citation', 'ok': True, 'args': {'citation_id': citation, 'exact_quote': proof['quote']}, 'result': proof}]
    answer = {'decisions': [{'kind': 'accounts', 'id': row['id'], 'outcome': outcome,
        'note': '公开主页明确描述发布主体形式；不确认官方身份、隶属或政治身份。',
        'evidence': [{'citation_id': citation, 'quote': proof['quote']}]}]}
    return account, row, gateway, trace, answer


@pytest.mark.parametrize('outcome,bio', [
    ('organization', '広島大学の学生自治会です。お問い合わせはダイレクトメッセージまで。'),
    ('individual', '北の大地で田舎暮し。画像は私が育てたヒマワリです。'),
])
def test_checked_own_profile_classifies_without_granting_identity_or_registering_trust(tmp_path, outcome, bio):
    with closing(Store(tmp_path/'classification.sqlite3')) as store:
        account, row, gateway, trace, answer = classified_input(store, outcome, bio)
        original = copy.deepcopy(answer)
        accepted = auto.validate_decisions(answer, gateway, trace)
        assert answer == original
        assert accepted['decisions'][0]['type_evidence_basis'] == 'public_profile_description'
        assert auto.validate_decisions(accepted, gateway, trace) == accepted
        values = auto._batch([row], accepted['decisions'])
        assert 'sources' not in values['accounts'][0] or values['accounts'][0]['sources'] is None
        preview = preview_batch(store, values)
        assert apply_batch(store, values, expected_digest=preview['digest'], reviewer='deepseek_auto_review')['applied'] == 1
        saved = project_account(store.get_account(account['id']))
        assert saved['account_type'] == outcome and saved['status'] == 'candidate'
        assert saved['evidence_url'] == account['evidence_url']
        assert not saved['metadata'].get('verification_sources')
        assert saved['account_type_details']['classification_basis'] == 'public_profile_description'
        assert saved['account_type_details']['evidence_urls'] == [account['url']]
        assert '公开主页自述' in saved['account_type_details']['confidence_scope']
        assert saved['metadata']['last_public_profile']['bio'] == bio
        from app.workbench.account_identity import research_registry_context
        context = research_registry_context([saved])['twitter:public_actor']['metadata']['account_identity']
        assert context['basis'] == 'public_profile_statement'
        assert context['classification_basis'] == 'public_profile_description'


@pytest.mark.parametrize('fault', ['other_record', 'other_profile_url', 'other_profile_capture', 'read_args', 'check_args', 'unchecked', 'name_only'])
def test_profile_classification_requires_exact_own_bio_proof(tmp_path, fault):
    with closing(Store(tmp_path/'classification.sqlite3')) as store:
        account, row, gateway, trace, answer = classified_input(store, 'organization',
            '' if fault == 'name_only' else '公開の地域労働組合です。')
        if fault == 'other_record':
            proof = trace[-1]['result']
            proof['citation_id'] = 'accounts:other-account'
            answer['decisions'][0]['evidence'][0]['citation_id'] = proof['citation_id']
        elif fault == 'other_profile_url':
            trace[-1]['result']['url'] = 'https://x.com/other_actor'
        elif fault == 'other_profile_capture':
            gateway.rows[0]['record']['metadata']['last_public_profile']['url'] = 'https://x.com/other_actor'
        elif fault == 'read_args':
            trace[0]['args']['limit'] = 1
        elif fault == 'check_args':
            trace[-1]['args']['exact_quote'] = 'different argument'
        elif fault == 'unchecked':
            trace = trace[:1]
        with pytest.raises(PipelineError):
            auto.validate_decisions(answer, gateway, trace)
        assert project_account(store.get_account(account['id']))['account_type'] == 'unknown'
        assert store.get_account(account['id'])['status'] == 'candidate'


def test_profile_classification_conflict_does_not_overwrite_new_manual_decision(tmp_path):
    with closing(Store(tmp_path/'classification.sqlite3')) as store:
        account, row, gateway, trace, answer = classified_input(store, 'organization', '公開の地域労働組合です。')
        values = auto._batch([row], auto.validate_decisions(answer, gateway, trace)['decisions'])
        preview = preview_batch(store, values)
        from app.workbench.account_identity import correct_identity
        correct_identity(store, account['id'], type='individual', manual_locked=True,
                         note='人工决定', _schedule=False)
        with pytest.raises(ReviewConflict):
            apply_batch(store, values, expected_digest=preview['digest'], reviewer='deepseek_auto_review')
        saved = project_account(store.get_account(account['id']))
        assert saved['account_type'] == 'individual' and saved['account_type_details']['manual_locked']
        assert saved['status'] == 'candidate'


def test_profile_type_review_preserves_existing_independent_registry_sources(tmp_path):
    with closing(Store(tmp_path/'classification.sqlite3')) as store:
        account, _, _, _, _ = classified_input(store, 'organization', '公開の地域労働組合です。')
        from app.workbench.account_identity import register_sources
        register_sources(store, account['id'], [{'url': 'https://union.example.org/about',
            'label': '人工登记', 'account_type': 'organization'}], _schedule=False)
        original = copy.deepcopy(store.get_account(account['id'])['metadata']['verification_sources'])
        row = snapshot(store, ['accounts'])[0]
        gateway = auto.ReviewGateway([row], use_public_network=False)
        citation = 'accounts:' + row['id']
        quote = row['record']['metadata']['last_public_profile']['bio']
        trace = []
        for name, args in [('read_evidence', {'citation_id': citation}),
                           ('check_citation', {'citation_id': citation, 'exact_quote': quote})]:
            trace.append({'name': name, 'args': args, 'ok': True, 'result': asyncio.run(gateway.call(name, args))})
        answer = {'decisions': [{'kind':'accounts', 'id':row['id'], 'outcome':'organization',
            'note':'公开主页自述类型，核验保持独立', 'evidence':[{'citation_id':citation,'quote':quote}]}]}
        values = auto._batch([row], auto.validate_decisions(answer, gateway, trace)['decisions'])
        preview = preview_batch(store, values)
        apply_batch(store, values, expected_digest=preview['digest'], reviewer='deepseek_auto_review')
        saved = store.get_account(account['id'])
        assert saved['metadata']['verification_sources'] == original and saved['status'] == 'candidate'
