"""One unsupported activity cannot confirm a false date or discard a valid sibling."""
import asyncio
import copy
from contextlib import closing

import pytest

from app.workbench import automated_review as auto
from app.workbench.pipeline import PipelineError
from app.workbench.review import apply_batch, preview_batch, snapshot, start_review, digest
from app.workbench.store import Store


def fixture(store):
    item = store.import_items([{'type':'news','url':'https://public.example.org/event','title':'公開集会',
        'content':'2026年10月5日、東京都で平和集会が開催された。\n2026年10月6日、京都府で反戦デモが行われた。'}])['ids'][0]
    rows = snapshot(store, ['activities'])
    gateway = auto.ReviewGateway(rows, use_public_network=False)
    decisions, trace = [], []
    for row in rows:
        quote = row['record']['quote']
        proof = asyncio.run(gateway.call('check_citation', {'citation_id':f"activities:{row['id']}", 'exact_quote':quote}))
        trace.append({'name':'check_citation','ok':True,'result':proof})
        decisions.append({'kind':'activities','id':row['id'],'outcome':'confirmed','note':'已核对举办描述',
            'date':'2026-10-05' if '5日' in quote else '2026-10-06',
            'prefecture':'東京都' if '東京都' in quote else '京都府', 'title':row['record']['title'],
            'evidence':[{'citation_id':proof['citation_id'],'quote':quote}]})
    return item, rows, gateway, {'decisions':decisions}, trace


@pytest.mark.parametrize('field,bad', [('date','2025-10-05'),('date',''),('date','2026-02-30'),('prefecture','大阪府')])
def test_checked_activity_gap_downgrades_only_bad_sibling_and_commits_atomically(tmp_path, field, bad):
    with closing(Store(tmp_path/'review.sqlite3')) as store:
        item, rows, gateway, answer, trace = fixture(store)
        original = store.get_item(item)
        answer['decisions'][0][field] = bad
        incoming = copy.deepcopy(answer)
        with pytest.raises(PipelineError):
            auto.validate_decisions(answer, gateway, trace)
        result = auto.validate_auto_review(answer, gateway, trace)
        assert answer == incoming
        assert [d['outcome'] for d in result['decisions']] == ['inconclusive','confirmed']
        assert result['decisions'][0]['date'] == result['decisions'][0]['prefecture'] == ''
        assert '系统未采纳' in result['decisions'][0]['note']
        assert result['decisions'][0]['resolved_evidence'][0]['quote'] == trace[0]['result']['quote']
        assert auto.validate_decisions(result, gateway, trace) == result
        values = auto._batch(rows,result['decisions'])
        preview = preview_batch(store, values)
        assert apply_batch(store, values, expected_digest=preview['digest'])['applied'] == 2
        assert store.get_item(item)['content'] == original['content']
        assert snapshot(store,['activities']) == []
        events = store.activities()
        assert events['inconclusive'] == 1 and events['denominator'] == 1


@pytest.mark.parametrize('fault', ['unchecked','no_proof','duplicate','missing','foreign','malformed','extra_field','account'])
def test_evidence_gap_fallback_does_not_relax_other_validation(tmp_path, fault):
    with closing(Store(tmp_path/'review.sqlite3')) as store:
        _, rows, gateway, answer, trace = fixture(store)
        answer['decisions'][0]['date'] = '2025-10-05'
        bad = answer['decisions'][1]
        if fault == 'unchecked':
            bad['evidence'][0]['quote'] = 'unread invented quotation'
        elif fault == 'no_proof':
            bad['evidence'] = []
        elif fault == 'duplicate':
            bad['id'] = answer['decisions'][0]['id']
        elif fault == 'missing':
            answer['decisions'].pop()
        elif fault == 'foreign':
            bad['id'] = 'foreign-id'
        elif fault == 'malformed':
            bad['id'] = []
        elif fault == 'extra_field':
            bad['bypass'] = True
        else:
            bad.update(kind='accounts',outcome='organization')
        with pytest.raises(PipelineError):
            auto.validate_auto_review(answer,gateway,trace)
        assert len(snapshot(store,['activities'])) == 2


def test_actual_background_runner_records_gap_and_reaches_complete(tmp_path, monkeypatch):
    with closing(Store(tmp_path/'review.sqlite3')) as store:
        item, rows, _, answer, trace = fixture(store)
        answer['decisions'][0]['date'] = '2025-10-05'
        async def harness(*args, **options):
            assert options['_validator'] is auto.validate_auto_review
            return {'findings':options['_validator'](answer,options['_gateway'],trace),
                    'trace':trace, 'model_calls':1,'tool_calls':2}
        monkeypatch.setattr(auto,'run_evidence_harness',harness)
        job = start_review(store,{'kinds':['activities'],'use_public_network':False}, expected_digest=digest(rows))
        async def checkpoint(value):
            store.update_job(job['id'],result=value)
        result = asyncio.run(auto.run_auto_review(store,job,checkpoint=checkpoint,cancelled=lambda:False))
        assert result['complete'] and result['completed'] == 2 and result['_status'] == 'succeeded'
        assert result['outcomes'] == {'inconclusive':1,'confirmed':1}
        assert snapshot(store,['activities']) == []
        assert '2026年10月5日' in store.get_item(item)['content']


def test_uncertain_model_date_is_never_written_as_actual_event_date(tmp_path):
    with closing(Store(tmp_path/'review.sqlite3')) as store:
        _, rows, gateway, answer, trace = fixture(store)
        answer['decisions'][0].update(outcome='inconclusive',date='not-a-date',prefecture='大阪府')
        result = auto.validate_auto_review(answer,gateway,trace)
        values = auto._batch(rows,result['decisions'])
        assert values['activities'][0]['date'] == values['activities'][0]['prefecture'] == ''
        assert preview_batch(store,values)['count'] == 2
