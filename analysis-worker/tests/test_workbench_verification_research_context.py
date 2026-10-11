"""Real worker→engine context, preserving registry decisions without model/HTTP."""
import asyncio
import json

import pytest

from app.workbench.account_identity import correct_identity, correct_relevance, describe_identity, research_registry_context
from app.workbench.jobs import JobRunner
from app.workbench.store import Store
from test_research_engine import adapter, following, params, post, prof
from test_workbench_automatic_account_verification import historical_curated


@pytest.mark.parametrize('state,expanded',[('irrelevant',False),('included',True)])
def test_real_worker_passes_manual_relevance_to_engine_without_persisting_context(tmp_path,adapter,monkeypatch,state,expanded):
    db=Store(tmp_path/'worker-context.sqlite3')
    try:
        account=db.save_account({'name':'General platform','platform':'twitter','handle':'youtube','url':'https://x.com/youtube'})
        correct_identity(db,account['id'],type='organization',manual_locked=True,note='人工公开资料登记')
        correct_relevance(db,account['id'],state=state,reason='本次手工决定')
        before=db.get_account(account['id'])
        from app.workbench import research_engine
        run_research=research_engine.run_research
        async def observe_runtime(runtime,**kwargs):
            # This is the actual engine call, immediately after the worker hook.
            # Its public context neither changes registry rows nor job params.
            assert db.get_account(account['id'])==before
            assert runtime['_registry_account_context']['twitter:youtube']['metadata']['research_relevance_override']['state']==state
            return await run_research(runtime,**kwargs)
        monkeypatch.setattr(research_engine,'run_research',observe_runtime)
        adapter.profiles['seed']=prof('seed','')
        adapter.profiles['youtube']=prof('youtube','')
        adapter.neighbor_results[('seed','following')]=following('seed','youtube')
        adapter.post_results['seed']=[post('seed')]
        adapter.post_results['youtube']=[post('youtube',2)]
        options=params(mode='account',handle='seed',keyword='',expand_relations=['following'])
        job=db.create_job('research',options)
        asyncio.run(JobRunner(db)._execute(db.claim_job(kind='research',verification=False)))
        saved=db.get_job(job['id'])
        assert saved['status']=='succeeded',saved
        assert saved['params']==job['params'] and '_registry_account_context' not in saved['params']
        run=db.get_research_run(job['id'])
        keys={node['account_key'] for node in run['nodes']}
        calls=[call for instance in adapter.instances for call in instance.calls]
        assert ('twitter:youtube' in keys) is expanded
        assert any(call[:3]==('posts','twitter','youtube') for call in calls) is expanded
        after=db.get_account(account['id'])
        assert after['status']==before['status']
        assert after['metadata']['account_identity']==before['metadata']['account_identity']
        assert after['metadata']['research_relevance_override']==before['metadata']['research_relevance_override']
        assert all(instance.closed for instance in adapter.instances)
    finally:
        db.close()


def test_runtime_curated_type_is_public_bounded_and_registry_unchanged(tmp_path):
    db=Store(tmp_path/'legacy-runtime.sqlite3')
    try:
        account=historical_curated(db)
        before=db.get_account(account['id'])
        context=research_registry_context(db.list_accounts())
        value=context['twitter:zengakuren']
        assert set(value['metadata'])=={'account_identity'}
        assert describe_identity(value)['account_type']=='organization'
        assert value['metadata']['account_identity']['basis']=='registered_source_backlink'
        assert db.get_account(account['id'])==before
        # The same runtime map includes explicit manual unknown and excludes
        # arbitrary importer metadata from the model/engine curation context.
        correct_identity(db,account['id'],type='unknown',manual_locked=True,note='仍需人工核对')
        context=research_registry_context(db.list_accounts())
        assert describe_identity(context['twitter:zengakuren'])['account_type']=='unknown'
        assert 'public_verification' not in json.dumps(context)
    finally:
        db.close()
