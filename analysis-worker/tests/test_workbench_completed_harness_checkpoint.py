import copy
import json

from fastapi.testclient import TestClient
from app.workbench.server import create_app
from app.workbench.store import Store
from app.workbench import report_generation
from app.workbench.pipeline import PipelineError
from app.workbench.job_retries import report_resume_checkpoint
from test_workbench_harness_report_integration import (
    synthetic_model_settings, fake_sidecar, synthetic_transport, create_run,
    create_report, terminal, SYNTHETIC_KEY, REASONING,
)


def test_two_draft_failures_keep_completed_harness_before_third_retry(monkeypatch,tmp_path):
    _,processes=fake_sidecar(monkeypatch,tmp_path,'model')
    calls=synthetic_transport(monkeypatch,failed_drafts=2)
    store=Store(tmp_path/'two-failures.sqlite3')
    run=create_run(store)
    with TestClient(create_app(store)) as client:
        first=create_report(client,run)
        assert first['status']=='failed'
        original=copy.deepcopy(first['result']['report_checkpoint']['harness_state'])
        second_create=client.post(f"/api/v2/jobs/{first['id']}/retry")
        assert second_create.status_code==202
        second=terminal(client,second_create.json()['id'])
        assert second['status']=='failed'
        checkpoint=second['result']['report_checkpoint']
        assert checkpoint['harness_state']['complete'] is True
        assert checkpoint['harness_state']['reused_completed_checkpoint'] is True
        for field in ('trace','findings','input_fingerprint','model_calls','tool_calls'):
            assert checkpoint['harness_state'][field]==original[field]
        third_create=client.post(f"/api/v2/jobs/{second['id']}/retry")
        assert third_create.status_code==202
        third=terminal(client,third_create.json()['id'])
        assert third['status']=='succeeded'
        snapshot=store.get_report(third['result']['report_id'])['snapshot']
    assert len(processes)==1
    assert [kind for kind,_,_ in calls].count('reading')==1
    assert [kind for kind,_,_ in calls].count('harness')==1
    assert snapshot['analysis_review']['reading_state']['harness_state']==snapshot['harness_state']
    assert snapshot['analysis_review']['reading_state']['report_generation']==snapshot['report_generation']
    assert REASONING not in json.dumps(snapshot,ensure_ascii=False)
    assert SYNTHETIC_KEY not in json.dumps(snapshot,ensure_ascii=False)


def test_single_retry_report_snapshot_retains_the_same_completed_harness(monkeypatch,tmp_path):
    _,processes=fake_sidecar(monkeypatch,tmp_path,'model')
    calls=synthetic_transport(monkeypatch,failed_drafts=1)
    store=Store(tmp_path/'one-retry.sqlite3')
    run=create_run(store)
    with TestClient(create_app(store)) as client:
        first=create_report(client,run)
        created=client.post(f"/api/v2/jobs/{first['id']}/retry")
        assert created.status_code==202
        completed=terminal(client,created.json()['id'])
        assert completed['status']=='succeeded'
        snapshot=store.get_report(completed['result']['report_id'])['snapshot']
    assert len(processes)==1
    assert snapshot['harness_state']['reused_completed_checkpoint'] is True
    assert snapshot['analysis_review']['reading_state']['harness_state']==snapshot['harness_state']
    assert [kind for kind,_,_ in calls].count('harness')==1


def test_synthesis_failure_retains_prior_generation_and_reused_harness(monkeypatch,tmp_path):
    _,processes=fake_sidecar(monkeypatch,tmp_path,'model')
    calls=synthetic_transport(monkeypatch,failed_drafts=1)
    store=Store(tmp_path/'synthesis-failure.sqlite3')
    run=create_run(store)
    with TestClient(create_app(store)) as client:
        first=create_report(client,run)
        assert first['status']=='failed'
        prior=copy.deepcopy(first['result']['report_checkpoint']['report_generation'])
        async def fail_after_synthesis_checkpoint(*args,**kwargs):
            assert kwargs['previous_state']==prior
            await kwargs['synthesis_checkpoint']({'fixture_phase':'saved-before-failure'})
            raise PipelineError('Synthetic synthesis failure after a durable checkpoint')
        monkeypatch.setattr(report_generation,'generate_checked_report',fail_after_synthesis_checkpoint)
        created=client.post(f"/api/v2/jobs/{first['id']}/retry")
        assert created.status_code==202
        second=terminal(client,created.json()['id'])
        assert second['status']=='failed'
    checkpoint=second['result']['report_checkpoint']
    assert checkpoint['harness_state']['complete'] is True
    assert checkpoint['harness_state']['reused_completed_checkpoint'] is True
    assert checkpoint['report_generation']==prior
    assert checkpoint['synthesis_state']=={'fixture_phase':'saved-before-failure'}
    assert len(processes)==1
    assert [kind for kind,_,_ in calls].count('reading')==1
    assert [kind for kind,_,_ in calls].count('harness')==1


def test_legacy_second_failure_recovers_missing_harness_from_same_branch(monkeypatch,tmp_path):
    _,processes=fake_sidecar(monkeypatch,tmp_path,'model')
    calls=synthetic_transport(monkeypatch,failed_drafts=2)
    store=Store(tmp_path/'legacy-retry.sqlite3')
    run=create_run(store)
    with TestClient(create_app(store)) as client:
        first=create_report(client,run)
        created=client.post(f"/api/v2/jobs/{first['id']}/retry")
        assert created.status_code==202
        second=terminal(client,created.json()['id'])
        assert second['status']=='failed'
        legacy=copy.deepcopy(second['result'])
        legacy['report_checkpoint'].pop('harness_state')
        # Arrange the shape already saved by the old release in owned SQLite.
        # Normal product writes must continue to refuse terminal-job mutation.
        with store._transaction(write=True) as connection:
            store._execute(connection,'UPDATE wb_jobs SET result=? WHERE id=?',
                (json.dumps(legacy,ensure_ascii=False),second['id']))
        before=copy.deepcopy(store.get_job(second['id']))
        created=client.post(f"/api/v2/jobs/{second['id']}/retry")
        assert created.status_code==202
        completed=terminal(client,created.json()['id'])
        assert completed['status']=='succeeded'
        saved=store.get_report(completed['result']['report_id'])['snapshot']
    assert store.get_job(second['id'])==before
    assert len(processes)==1
    assert [kind for kind,_,_ in calls].count('reading')==1
    assert [kind for kind,_,_ in calls].count('harness')==1
    assert saved['harness_state']['reused_completed_checkpoint'] is True
    assert saved['analysis_review']['reading_state']['harness_state']==saved['harness_state']


def branch_fixture():
    scope={'mode':'ai','analysis_harness':'dsh','research_run_id':'run-a','read_original':False,
        'start_date':'2026-10-01','end_date':'2026-10-09'}
    root={'id':'root','kind':'report','params':copy.deepcopy(scope),
        'result':{'report_checkpoint':{'harness_state':{'complete':True,'model_calls':59,'tool_calls':255}}}}
    parent={'id':'parent','kind':'report','params':{**scope,'harness_max_model_calls':64,
        '_workbench_retry':{'parent_job_id':'root','root_job_id':'root'}},
        'result':{'report_checkpoint':{'fragment_reviews':[{'unit_id':'S001'}],
            'report_generation':{'phase':'review_pending'}}}}
    params={**scope,'harness_max_model_calls':64,'_workbench_retry':{'parent_job_id':'parent','root_job_id':'root'}}
    return params,parent,root


def test_legacy_recovery_preserves_latest_reading_generation_and_original_jobs():
    params,parent,root=branch_fixture()
    original=copy.deepcopy((parent,root))
    recovered=report_resume_checkpoint(params,parent,lambda identity:root if identity=='root' else None)
    assert recovered['fragment_reviews']==parent['result']['report_checkpoint']['fragment_reviews']
    assert recovered['report_generation']=={'phase':'review_pending'}
    assert recovered['harness_state']==root['result']['report_checkpoint']['harness_state']
    recovered['harness_state']['tool_calls']=0
    assert (parent,root)==original


def test_legacy_recovery_refuses_different_scope_root_and_explicit_invalid_stage():
    for mismatch in ('scope','root','kind','cycle','explicit_stage'):
        params,parent,root=branch_fixture()
        if mismatch=='scope':root['params']['research_run_id']='different-run'
        if mismatch=='root':parent['params']['_workbench_retry']['root_job_id']='different-root'
        if mismatch=='kind':root['kind']='research'
        if mismatch=='cycle':parent['params']['_workbench_retry']['parent_job_id']='parent'
        if mismatch=='explicit_stage':parent['result']['report_checkpoint']['harness_state']={'complete':False,'model_calls':64}
        recovered=report_resume_checkpoint(params,parent,lambda identity:root)
        if mismatch=='explicit_stage':
            assert recovered['harness_state']=={'complete':False,'model_calls':64}
        else:assert 'harness_state' not in recovered


def test_legacy_recovery_ignores_malformed_retry_metadata():
    params,parent,root=branch_fixture()
    params['_workbench_retry']=['invalid']
    assert report_resume_checkpoint(params,parent,lambda identity:root)==parent['result']['report_checkpoint']
