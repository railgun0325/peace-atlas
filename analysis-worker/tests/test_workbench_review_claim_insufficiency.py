"""An unsupported saved qualifier cannot become a confirmed relationship."""
import asyncio
from contextlib import closing
import copy
import json

import pytest

from app.workbench import automated_review as auto
from app.workbench.pipeline import PipelineError
from app.workbench.review import apply_batch, preview_batch, snapshot, start_review, digest
from app.workbench.store import Store
from test_workbench_automated_review import seed


def fixture(store):
    _, run, _ = seed(store)
    identity = store.get_research_run(run['id'])['claims'][0]['id']
    with store._transaction(write=True) as connection:
        raw = store._execute(connection,'SELECT record FROM wb_research_claims WHERE id=?',(identity,)).fetchone()
        record = json.loads(raw['record'] or '{}')
        record.setdefault('metadata',{})['identity_qualifiers'] = ['marker:関西','marker:机关报']
        store._execute(connection,'UPDATE wb_research_claims SET object_label=?,record=? WHERE id=?',
                       ('公開団体',json.dumps(record),identity))
    rows = snapshot(store,['claims','activities'])
    gateway = auto.ReviewGateway(rows,use_public_network=False)
    proof = {'citation_id':'https://public.example.org/about','quote':'公開団体の関西通信アカウントです。',
        'url':'https://public.example.org/about','independent':True,'owner_account_keys':['twitter:peace_a'],
        'source_capture_sha256':'owned-checked-source','valid':True}
    trace = [{'name':'check_citation','ok':True,'result':proof}]
    answer = {'decisions':[]}
    for row in rows:
        d = {'kind':row['kind'],'id':row['id'],'outcome':'inconclusive','note':'不足以判断','evidence':[]}
        if row['kind'] == 'claims':
            d.update(outcome='confirmed',note='Wrongly preserves publication qualifier',
                     evidence=[{'citation_id':proof['citation_id'],'quote':proof['quote']}])
        answer['decisions'].append(d)
    return run, rows, gateway, answer, trace


def test_saved_publication_qualifier_gap_is_reviewed_without_false_confirmation(tmp_path):
    with closing(Store(tmp_path/'review.sqlite3')) as store:
        run, rows, gateway, answer, trace = fixture(store)
        claim = copy.deepcopy(rows[0]['record'])
        original = copy.deepcopy(answer)
        with pytest.raises(PipelineError,match='身份限定'):
            auto.validate_decisions(answer,gateway,trace)
        accepted = auto.validate_auto_review(answer,gateway,trace)
        assert answer == original
        assert all(d['outcome'] == 'inconclusive' for d in accepted['decisions'])
        assert '系统未采纳' in accepted['decisions'][0]['note']
        assert accepted['decisions'][0]['resolved_evidence'] == [trace[0]['result']]
        values = auto._batch(rows,accepted['decisions'])
        preview = preview_batch(store,values)
        result = apply_batch(store,values,expected_digest=preview['digest'],reviewer='deepseek_auto_review')
        assert result['applied'] == 3
        saved = store.get_research_run(run['id'])['claims'][0]
        assert saved['status'] == 'candidate' and saved['review_outcome'] == 'inconclusive'
        assert saved['metadata']['identity_qualifiers'] == claim['metadata']['identity_qualifiers']
        assert saved['statement'] == claim['statement'] and saved['object_label'] == claim['object_label']
        assert snapshot(store,['claims','activities']) == []


@pytest.mark.parametrize('fault',['unchecked','duplicate','missing','malformed','account','schema'])
def test_qualifier_gap_never_makes_an_invalid_batch_savable(tmp_path,fault):
    with closing(Store(tmp_path/'review.sqlite3')) as store:
        _, rows, gateway, answer, trace = fixture(store)
        if fault == 'unchecked':
            answer['decisions'][0]['evidence'][0]['quote']='Unchecked new source'
        elif fault == 'duplicate':
            answer['decisions'][1]=copy.deepcopy(answer['decisions'][0])
        elif fault == 'missing':
            answer['decisions'].pop()
        elif fault == 'malformed':
            answer['decisions'][1]['id']=[]
        elif fault == 'schema':
            rows[0]['record']['metadata']['identity_qualifiers']={}
            gateway = auto.ReviewGateway(rows,use_public_network=False)
        else:
            account = snapshot(store,['accounts'])[0]
            rows.append(account)
            gateway = auto.ReviewGateway(rows,use_public_network=False)
            answer['decisions'].append({'kind':'accounts','id':account['id'],'outcome':'organization',
                                       'note':'Wrong account proof','evidence':[]})
        with pytest.raises(PipelineError):
            auto.validate_auto_review(answer,gateway,trace)
        assert len(snapshot(store,['claims','activities'])) == 3


def test_background_review_commits_honest_qualifier_result_and_finishes(tmp_path,monkeypatch):
    with closing(Store(tmp_path/'review.sqlite3')) as store:
        _, rows, _, answer, trace = fixture(store)
        async def harness(*args,**options):
            return {'findings':options['_validator'](answer,options['_gateway'],trace),
                    'trace':trace,'model_calls':1,'tool_calls':1}
        monkeypatch.setattr(auto,'run_evidence_harness',harness)
        job = start_review(store,{'kinds':['claims','activities'],'use_public_network':False},expected_digest=digest(rows))
        async def checkpoint(value):
            store.update_job(job['id'],result=value)
        result = asyncio.run(auto.run_auto_review(store,job,checkpoint=checkpoint,cancelled=lambda:False))
        assert result['complete'] and result['_status']=='succeeded' and result['completed']==3
        assert result['outcomes']=={'inconclusive':3}
