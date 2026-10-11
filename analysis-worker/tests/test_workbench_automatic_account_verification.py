"""Automatic public-source verification through real Store, worker and HTTP paths."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.workbench import account_verification as verification
from app.workbench.jobs import JobRunner
from app.workbench.server import create_app
from app.workbench.store import Store


SITE = 'https://registered.example.org/'


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / 'verification.sqlite3')
    yield value
    value.close()


def public_account(handle='alpha', website=SITE, metadata=None):
    return {'name': 'Public account ' + handle, 'platform': 'twitter', 'handle': handle,
            'url': 'https://x.com/' + handle, 'metadata': metadata or {},
            'profile': {'verified_in_dom': True, 'url': 'https://x.com/' + handle,
                        'bio': '公开简介', 'links': [{'url': website, 'label': 'Website'}],
                        'observed_at': '2026-10-09T00:00:00Z'}}


def seed(store, **kwargs):
    result = store.import_social([], [public_account(**kwargs)], [])
    return store.get_account(result['account_ids']['twitter:' + kwargs.get('handle', 'alpha')])


def active_checks(store):
    return [job for job in store.list_jobs(page_size=200)['items']
            if job['params'].get('_account_verification') and job['status'] in ('queued', 'running')]


def response_fixture(monkeypatch, body):
    requested = []
    async def fetch(url, **kwargs):
        requested.append(url)
        return body.encode('utf-8'), url, 200
    monkeypatch.setattr(verification.pipeline, '_fetch_public_feed', fetch)
    return requested


def test_profile_links_are_saved_and_trigger_real_automatic_worker(store, monkeypatch):
    requested = response_fixture(monkeypatch, '<footer><a rel="me" href="https://x.com/alpha">X</a></footer>')
    account = seed(store)
    assert account['metadata']['last_public_profile']['links'][0]['url'] == SITE
    jobs = active_checks(store)
    assert len(jobs) == 1
    job = store.claim_job(verification=True)
    asyncio.run(JobRunner(store)._execute(job))
    saved = store.get_account(account['id'])
    assert requested == [SITE]
    assert saved['status'] == 'candidate'
    assert saved['metadata']['public_verification']['outcome'] == 'untrusted_backlink'
    assert saved['metadata']['public_verification']['candidate_evidence_url'] == SITE
    assert store.get_job(job['id'])['status'] == 'succeeded'


def test_trusted_registered_site_discovers_exact_backlink_on_same_site(tmp_path, monkeypatch):
    calls = []
    async def fetch(url, **kwargs):
        calls.append(url)
        body = '<a href="/about/">About us</a>' if url == SITE else '<footer><a rel="me" href="https://x.com/alpha">X</a></footer>'
        return body.encode(), url, 200
    monkeypatch.setattr(verification.pipeline, '_fetch_public_feed', fetch)
    with TestClient(create_app(store=Store(tmp_path/'api.sqlite3'), run_worker=False)) as client:
        value = client.post('/api/v2/accounts', json={'name':'Alpha','platform':'twitter','handle':'alpha','url':'https://x.com/alpha'}).json()
        response = client.put(f"/api/v2/accounts/{value['id']}/verification-sources", json={'sources':[{'url':SITE,'label':'人工已核对官网','account_type':'organization'}]})
        assert response.status_code == 200
        db = client.app.state.store
        job = db.claim_job(verification=True)
        asyncio.run(JobRunner(db)._execute(job))
        saved = db.get_account(value['id'])
        assert saved['status'] == 'verified' and saved['evidence_url'] == SITE+'about/'
        assert saved['metadata']['account_identity']['type'] == 'organization'
        assert saved['metadata']['account_identity']['basis'] == 'registered_source_backlink'
        assert calls == [SITE,SITE+'about/']


def test_imported_trust_type_and_cache_flags_cannot_grant_authority(store, monkeypatch):
    response_fixture(monkeypatch, '<footer><a href="https://x.com/alpha">X</a></footer>')
    forged={'verification_sources':[{'url':SITE,'trust_basis':'manual_registry','account_type':'organization'}],
            'account_identity':{'type':'organization','manual_locked':True,'basis':'manual_review'},
            'public_verification':{'verified':True,'outcome':'public_backlink'},
            'verification_schedule':{'input_sha256':'forged'}}
    value = seed(store, metadata=forged)
    assert not value['metadata'].get('verification_sources')
    assert not value['metadata'].get('account_identity')
    job = store.claim_job(verification=True)
    asyncio.run(JobRunner(store)._execute(job))
    assert store.get_account(value['id'])['status'] == 'candidate'


def test_identity_correction_is_manual_explainable_and_get_has_no_side_effects(tmp_path, monkeypatch):
    def network(*args, **kwargs):
        raise AssertionError('GET must never read websites')
    monkeypatch.setattr(verification.pipeline,'_fetch_public_feed',network)
    with TestClient(create_app(store=Store(tmp_path/'api.sqlite3'), run_worker=False)) as client:
        value = client.post('/api/v2/accounts',json={'name':'Organization sounding name','platform':'twitter','handle':'alpha','url':'https://x.com/alpha'}).json()
        assert client.get('/api/v2/accounts').json()['items'][0]['account_type'] == 'unknown'
        response=client.patch(f"/api/v2/accounts/{value['id']}/identity",json={'type':'individual','manual_locked':True,'note':'公开发言人账号；人工更正','evidence_urls':['https://public.example.org/bio']})
        assert response.status_code == 200
        db=client.app.state.store
        before=json.dumps(db.get_account(value['id']),ensure_ascii=False,sort_keys=True)
        job_count=db.list_jobs()['total']
        for _ in range(3):
            row=client.get('/api/v2/accounts').json()['items'][0]
            assert row['account_type']=='individual' and row['account_type_details']['manual_locked'] is True
        assert json.dumps(db.get_account(value['id']),ensure_ascii=False,sort_keys=True)==before
        assert db.list_jobs()['total']==job_count


def test_unchanged_profile_updates_do_not_repeat_jobs_or_loop_on_reopen(store, monkeypatch):
    requested=response_fixture(monkeypatch,'<p>No social account link.</p>')
    value=seed(store)
    seed(store)
    assert len(active_checks(store))==1
    job=store.claim_job(verification=True)
    asyncio.run(JobRunner(store)._execute(job))
    for _ in range(4):
        seed(store)
    assert active_checks(store)==[] and len(requested)<=3
    path=store.path
    reopened=Store(path)
    try:
        assert active_checks(reopened)==[]
        assert reopened.get_account(value['id'])['metadata']['public_verification']['verified'] is False
    finally:
        reopened.close()


def test_verification_lane_does_not_claim_ordinary_collect_or_block_cleanup(store, monkeypatch):
    response_fixture(monkeypatch,'<p>No link</p>')
    seed(store)
    normal=store.create_job('collect',{'source':'rss','feed_url':'https://news.example.org/feed'})
    check=store.claim_job(verification=True)
    assert check['params']['_account_verification']
    claimed=store.claim_job(verification=False)
    assert claimed['id']==normal['id']
    store.update_job(normal['id'],status='succeeded')
    item=store.import_items([{'type':'news','title':'Article','content':'公开测试正文','url':'https://news.example.org/a'}])['ids'][0]
    preview=store.cleanup_preview([item],'trash')
    assert preview['count']==1
    store.cleanup_apply([item],'trash',preview['preview_digest'],'核对后移除无关材料')


def test_same_site_discovery_is_bounded_deduplicated_and_comments_cannot_verify(store, monkeypatch):
    calls=[]
    async def fetch(url,**kwargs):
        calls.append(url)
        return ('<a href="/contact">Contact</a><a href="/contact#same">Contact</a><a href="https://evil.example.org/about">External</a>'
                '<div id="comments"><a href="https://x.com/alpha">visitor</a></div>').encode(),url,200
    monkeypatch.setattr(verification.pipeline,'_fetch_public_feed',fetch)
    value=seed(store)
    job=store.claim_job(verification=True)
    asyncio.run(JobRunner(store)._execute(job))
    saved=store.get_account(value['id'])
    assert saved['status']=='candidate'
    assert calls==[SITE,SITE+'contact']
    assert saved['metadata']['public_verification']['pages_checked']==2


def test_running_check_cannot_overwrite_concurrent_manual_identity(tmp_path,monkeypatch):
    with TestClient(create_app(store=Store(tmp_path/'api.sqlite3'),run_worker=False)) as client:
        value=client.post('/api/v2/accounts',json={'name':'Alpha','platform':'twitter','handle':'alpha','url':'https://x.com/alpha'}).json()
        assert client.put(f"/api/v2/accounts/{value['id']}/verification-sources",json={'sources':[{'url':SITE,'account_type':'organization'}]}).status_code==200
        db=client.app.state.store
        async def fetch(url,**kwargs):
            response=client.patch(f"/api/v2/accounts/{value['id']}/identity",json={'type':'individual','manual_locked':True,'note':'并发人工决定'})
            assert response.status_code==200
            return b'<a href="https://x.com/alpha">X</a>',url,200
        monkeypatch.setattr(verification.pipeline,'_fetch_public_feed',fetch)
        job=db.claim_job(verification=True)
        asyncio.run(JobRunner(db)._execute(job))
        saved=db.get_account(value['id'])
        assert saved['status']=='candidate'
        assert saved['metadata']['account_identity']['type']=='individual'
        assert db.get_job(job['id'])['result']['diagnostics'][0]['outcome']=='changed_during_check'


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


def test_real_workers_continue_normal_collection_and_close_verification_then_restart(tmp_path, monkeypatch):
    path = tmp_path/'workers.sqlite3'
    db = Store(path)
    account = seed(db)
    verification_job = active_checks(db)[0]
    normal = db.create_job('collect', {'source':'rss'})
    closed = []

    async def exercise():
        entered = asyncio.Event()
        async def fetch(url, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.append(url)
        async def collect(params):
            return {'items':[{'type':'news','title':'正常采集','content':'公开正文','url':'https://news.example.org/worker'}],
                    'diagnostics':[{'ok':True,'source':'rss'}]}
        monkeypatch.setattr(verification.pipeline, '_fetch_public_feed', fetch)
        monkeypatch.setattr(verification.pipeline, 'collect', collect)
        runner = JobRunner(db)
        await runner.start()
        await asyncio.wait_for(entered.wait(), 3)
        await until(lambda: db.get_job(normal['id'])['status']=='succeeded')
        assert db.get_job(verification_job['id'])['status']=='running'
        item = db.list_items()['items'][0]['id']
        preview = db.cleanup_preview([item], 'trash')
        db.cleanup_apply([item], 'trash', preview['preview_digest'], '独立核验等待时清理资料')
        await runner.stop()
        assert all(task.done() for task in (runner.task, runner.login_task, runner.verification_task))
        assert db.get_job(verification_job['id'])['status']=='interrupted'
        assert db.get_account(account['id'])['metadata']['verification_schedule']['state']=='interrupted'
    asyncio.run(exercise())
    assert closed == [SITE]
    db.close()
    reopened = Store(path)
    calls = response_fixture(monkeypatch, '<p>No backlink.</p>')
    retry = reopened.retry_job(verification_job['id'])
    async def resumed():
        runner = JobRunner(reopened)
        await runner.start()
        await until(lambda: reopened.get_job(retry['id'])['status']=='succeeded')
        await runner.stop()
    asyncio.run(resumed())
    assert calls == [SITE] and not active_checks(reopened)
    assert reopened.get_account(account['id'])['status']=='candidate'
    reopened.close()


def test_shutdown_checkpoints_completed_accounts_and_retry_reuses_only_accepted_reads(tmp_path, monkeypatch):
    path = tmp_path/'checkpoint.sqlite3'
    db = Store(path)
    result = db.import_social([], [public_account('alpha', 'https://alpha.example.org/'),
                                  public_account('beta', 'https://beta.example.org/')], [])
    alpha = result['account_ids']['twitter:alpha']
    job = active_checks(db)[0]
    calls, cancelled = [], []

    async def exercise():
        async def fetch(url, **kwargs):
            calls.append(url)
            if 'beta.' in url:
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.append(url)
            return b'<p>No backlink.</p>', url, 200
        monkeypatch.setattr(verification.pipeline, '_fetch_public_feed', fetch)
        runner = JobRunner(db)
        await runner.start()
        await until(lambda: bool(db.get_account(alpha)['metadata'].get('public_verification')))
        await until(lambda: (db.get_job(job['id'])['result'] or {}).get('checked')==1)
        await runner.stop()
    asyncio.run(exercise())
    assert db.get_job(job['id'])['status']=='interrupted'
    assert db.get_job(job['id'])['result']['diagnostics'][0]['account_id']==alpha
    assert cancelled == ['https://beta.example.org/']
    db.close()
    reopened = Store(path)
    requests = response_fixture(monkeypatch, '<p>No backlink.</p>')
    retry = reopened.retry_job(job['id'])
    asyncio.run(JobRunner(reopened)._execute(reopened.claim_job(verification=True)))
    assert reopened.get_job(retry['id'])['status']=='succeeded'
    diagnostics = reopened.get_job(retry['id'])['result']['diagnostics']
    assert next(row for row in diagnostics if row['account_id']==alpha)['cached'] is True
    assert requests == ['https://beta.example.org/']
    assert calls == ['https://alpha.example.org/', 'https://beta.example.org/']
    reopened.close()


def test_explicit_retry_after_transport_failure_reads_again_with_safe_diagnostics(store, monkeypatch):
    import httpx
    value = seed(store)
    calls = []
    async def failure(url, **kwargs):
        calls.append(url)
        raise httpx.ReadTimeout('private key / header / session details')
    monkeypatch.setattr(verification.pipeline, '_fetch_public_feed', failure)
    job = store.claim_job(verification=True)
    asyncio.run(JobRunner(store)._execute(job))
    assert store.get_job(job['id'])['status']=='partial'
    saved = store.get_account(value['id'])
    assert saved['metadata']['public_verification']['failure_reason']=='timeout'
    assert 'private key' not in json.dumps(saved['metadata'])
    retry = store.retry_job(job['id'])
    async def recovered(url, **kwargs):
        calls.append(url)
        return b'<p>Recovered, no backlink.</p>', url, 200
    monkeypatch.setattr(verification.pipeline, '_fetch_public_feed', recovered)
    asyncio.run(JobRunner(store)._execute(store.claim_job(verification=True)))
    assert calls == [SITE, SITE]
    assert store.get_job(retry['id'])['status']=='succeeded'
    assert store.get_account(value['id'])['status']=='candidate'


def test_manual_check_uses_durable_negative_cache_and_force_performs_new_request(tmp_path, monkeypatch):
    db = Store(tmp_path/'force.sqlite3')
    value = seed(db)
    calls = response_fixture(monkeypatch, '<p>No link</p>')
    asyncio.run(JobRunner(db)._execute(db.claim_job(verification=True)))
    with TestClient(create_app(store=db, run_worker=False)) as client:
        first = client.post('/api/v2/accounts/check-public-sources', json={'account_ids':[value['id']]}).json()
        asyncio.run(JobRunner(db)._execute(db.claim_job(verification=True)))
        assert db.get_job(first['id'])['result']['diagnostics'][0]['cached'] is True
        assert calls == [SITE]
        second = client.post('/api/v2/accounts/check-public-sources', json={'account_ids':[value['id']], 'force':True}).json()
        asyncio.run(JobRunner(db)._execute(db.claim_job(verification=True)))
        assert db.get_job(second['id'])['result']['diagnostics'][0]['cached'] is False
        assert calls == [SITE, SITE]


def test_concurrent_import_writers_queue_one_check_for_one_identity(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path = tmp_path/'concurrent.sqlite3'
    stores = [Store(path), Store(path)]
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            values = list(pool.map(lambda index: seed(stores[index % 2]), range(16)))
        assert len({row['id'] for row in values}) == 1
        assert len(active_checks(stores[0])) == 1
        with ThreadPoolExecutor(max_workers=4) as pool:
            claimed = list(pool.map(lambda index: stores[index % 2].claim_job(verification=True), range(8)))
        assert len([row for row in claimed if row]) == 1
    finally:
        for db in stores:
            db.close()


@pytest.mark.parametrize('kind,flag', [('research',True), ('collect','true'), ('collect',1), ('collect',False)])
def test_cleanup_exemption_does_not_cover_ordinary_collect_or_research(store, kind, flag):
    job = store.create_job(kind, {'_account_verification':flag})
    assert store.claim_job(verification=True) is None
    assert store.claim_job(verification=False)['id'] == job['id']
    item = store.import_items([{'title':'Article','content':'公开正文'}])['ids'][0]
    preview = store.cleanup_preview([item], 'trash')
    with pytest.raises(ValueError, match='采集或研究'):
        store.cleanup_apply([item], 'trash', preview['preview_digest'], '不能绕过普通任务')


def test_registered_site_can_verify_new_profile_owner_but_article_mention_cannot(tmp_path, monkeypatch):
    from app.workbench.account_identity import register_sources, describe_identity
    db = Store(tmp_path/'site-owner.sqlite3')
    anchor = db.save_account({'name':'Registrar','platform':'twitter','handle':'registrar','url':'https://x.com/registrar'})
    register_sources(db, anchor['id'], [{'url':SITE,'account_type':'organization'}])
    db.request_cancel(active_checks(db)[0]['id'])
    body = '<article><a href="https://x.com/alpha">interview</a></article><footer><a rel="me" href="https://x.com/beta">Site account</a></footer>'
    calls = response_fixture(monkeypatch, body)
    alpha = seed(db)
    beta = seed(db, handle='beta')
    while (job := db.claim_job(verification=True)):
        asyncio.run(JobRunner(db)._execute(job))
    assert db.get_account(alpha['id'])['status']=='candidate'
    assert db.get_account(beta['id'])['status']=='verified'
    assert describe_identity(db.get_account(beta['id']))['account_type']=='unknown'
    assert len(calls)==2
    db.close()


def test_cancelled_queued_verification_is_durable_idempotent_and_never_fetched(tmp_path, monkeypatch):
    calls = response_fixture(monkeypatch, '<p>No network expected.</p>')
    db = Store(tmp_path/'queued-cancel.sqlite3')
    account = seed(db)
    job = active_checks(db)[0]
    with TestClient(create_app(store=db, run_worker=False)) as client:
        first = client.post(f"/api/v2/jobs/{job['id']}/cancel")
        assert first.status_code == 200 and first.json()['status'] == 'cancelled'
        saved = db.get_account(account['id'])
        assert saved['metadata']['verification_schedule']['state'] == 'cancelled'
        assert saved['metadata']['verification_schedule']['job_id'] == job['id']
        again = client.post(f"/api/v2/jobs/{job['id']}/cancel")
        assert again.status_code == 200 and again.json()['status'] == 'cancelled'
        assert db.get_account(account['id']) == saved
        assert db.claim_job(verification=True) is None and calls == []


@pytest.mark.parametrize('kind,flag', [('collect',False),('collect','true'),('collect',1),('research',True)])
def test_ordinary_cancel_does_not_touch_verification_schedule(tmp_path, kind, flag):
    db = Store(tmp_path/'ordinary-cancel.sqlite3')
    account = seed(db)
    special = active_checks(db)[0]
    db.request_cancel(special['id'])
    ordinary = db.create_job(kind, {'_account_verification':flag, 'account_ids':[account['id']]})
    before = db.get_account(account['id'])
    with TestClient(create_app(store=db, run_worker=False)) as client:
        response = client.post(f"/api/v2/jobs/{ordinary['id']}/cancel")
        assert response.status_code == 200 and response.json()['status'] == 'cancelled'
        assert db.get_account(account['id']) == before


def test_repeated_old_cancel_cannot_overwrite_a_new_verification_attempt(tmp_path):
    db = Store(tmp_path/'newer-schedule.sqlite3')
    account = seed(db)
    old = active_checks(db)[0]
    with TestClient(create_app(store=db, run_worker=False)) as client:
        assert client.post(f"/api/v2/jobs/{old['id']}/cancel").status_code == 200
        retry = db.retry_job(old['id'])
        claimed = db.claim_job(verification=True)
        assert claimed['id'] == retry['id']
        verification.begin_schedule(db, claimed)
        before = db.get_account(account['id'])
        assert before['metadata']['verification_schedule']['state'] == 'running'
        assert client.post(f"/api/v2/jobs/{old['id']}/cancel").status_code == 200
        assert db.get_account(account['id']) == before


def test_atomic_claim_cancel_race_keeps_verification_terminal_and_joins_work(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    calls, closed = [], []
    async def fetch(url, **kwargs):
        calls.append(url)
        try:
            await asyncio.Event().wait()
        finally:
            closed.append(url)
    monkeypatch.setattr(verification.pipeline, '_fetch_public_feed', fetch)
    for index in range(6):
        db = Store(tmp_path/f'cancel-race-{index}.sqlite3')
        account = seed(db)
        job = active_checks(db)[0]
        barrier = Barrier(2)
        with TestClient(create_app(store=db, run_worker=False)) as client:
            def claim():
                barrier.wait()
                return db.claim_job(verification=True)
            def cancel():
                barrier.wait()
                return client.post(f"/api/v2/jobs/{job['id']}/cancel")
            with ThreadPoolExecutor(max_workers=2) as executor:
                claimed_future, cancelled_future = executor.submit(claim), executor.submit(cancel)
                claimed, cancelled = claimed_future.result(), cancelled_future.result()
            assert cancelled.status_code == 200
            if claimed:
                assert claimed['id'] == job['id']
                assert cancelled.json()['status'] == 'running' and cancelled.json()['cancel_requested']
                asyncio.run(JobRunner(db)._execute(claimed))
            else:
                assert cancelled.json()['status'] == 'cancelled'
            assert db.get_job(job['id'])['status'] == 'cancelled'
            assert db.get_account(account['id'])['metadata']['verification_schedule']['state'] == 'cancelled'
            assert db.claim_job(verification=True) is None
    assert calls == closed


def test_shared_site_with_different_registrants_verifies_without_cross_account_type(tmp_path, monkeypatch):
    from app.workbench.account_identity import register_sources, describe_identity
    db = Store(tmp_path/'shared-type-scope.sqlite3')
    for handle, kind in [('organization','organization'),('person','individual')]:
        account = db.save_account({'name':handle,'platform':'twitter','handle':handle,'url':'https://x.com/'+handle})
        register_sources(db, account['id'], [{'url':SITE,'account_type':kind}])
        db.request_cancel(active_checks(db)[0]['id'])
    author = seed(db, handle='author')
    calls = response_fixture(monkeypatch, '<article><header><a rel="me" href="https://x.com/author">Author</a></header></article>')
    job = db.claim_job(verification=True)
    asyncio.run(JobRunner(db)._execute(job))
    saved = db.get_account(author['id'])
    assert saved['status'] == 'verified' and saved['evidence_url'] == SITE
    assert describe_identity(saved)['account_type'] == 'unknown'
    assert 'account_identity' not in saved['metadata']
    assert saved['metadata']['public_verification']['source_basis'] == 'registered_site'
    assert calls == [SITE]
    db.close()


def test_conflicting_trusted_backlink_types_do_not_verify_or_infer_identity(tmp_path, monkeypatch):
    from app.workbench.account_identity import register_sources, describe_identity
    db = Store(tmp_path/'conflict.sqlite3')
    value = db.save_account({'name':'Alpha','platform':'twitter','handle':'alpha','url':'https://x.com/alpha'})
    register_sources(db, value['id'], [{'url':SITE,'account_type':'organization'},
                                     {'url':'https://other.example.org/','account_type':'individual'}])
    response_fixture(monkeypatch, '<a href="https://x.com/alpha">Exact</a>')
    job = db.claim_job(verification=True)
    asyncio.run(JobRunner(db)._execute(job))
    saved = db.get_account(value['id'])
    assert saved['status']=='candidate' and describe_identity(saved)['account_type']=='unknown'
    assert saved['metadata']['public_verification']['outcome']=='conflicting_sources'
    db.close()


@pytest.mark.parametrize('href', ['https://x.com/alpha/status/123', 'https://x.com/alpha_other',
                                  'http://x.com/alpha', 'https://x.com:8443/alpha'])
def test_owner_scope_requires_exact_https_homepage(store, monkeypatch, href):
    response_fixture(monkeypatch, '<footer><a rel="me" href="'+href+'">Related</a></footer>')
    value = seed(store)
    asyncio.run(JobRunner(store)._execute(store.claim_job(verification=True)))
    assert store.get_account(value['id'])['status']=='candidate'
    assert store.get_account(value['id'])['metadata']['public_verification']['outcome']=='no_backlink'


def test_ugc_and_comment_links_are_not_verification_evidence(tmp_path, monkeypatch):
    from app.workbench.account_identity import register_sources
    db = Store(tmp_path/'ugc.sqlite3')
    value = db.save_account({'name':'Alpha','platform':'twitter','handle':'alpha','url':'https://x.com/alpha'})
    register_sources(db, value['id'], [{'url':SITE}])
    response_fixture(monkeypatch, '<footer><a rel="ugc me" href="https://x.com/alpha">visitor</a></footer>'
                     '<!-- <a href="https://x.com/alpha">comment</a> -->'
                     '<script><a href="https://x.com/alpha">script</a></script>')
    asyncio.run(JobRunner(db)._execute(db.claim_job(verification=True)))
    assert db.get_account(value['id'])['metadata']['public_verification']['outcome']=='no_backlink'
    db.close()


def test_off_site_redirect_cannot_inherit_trusted_anchor_with_real_public_transport(tmp_path, monkeypatch):
    import httpx
    from app.workbench.account_identity import register_sources
    db = Store(tmp_path/'redirect.sqlite3')
    value = db.save_account({'name':'Alpha','platform':'twitter','handle':'alpha','url':'https://x.com/alpha'})
    register_sources(db, value['id'], [{'url':SITE}])
    requests = []
    async def resolve(url):
        return '93.184.216.34'
    def request(req):
        requests.append(req.headers['Host'])
        if req.headers['Host']=='registered.example.org':
            return httpx.Response(302, headers={'location':'https://evil.example.org/about'})
        return httpx.Response(200, content=b'<footer><a rel="me" href="https://x.com/alpha">X</a></footer>')
    original = httpx.AsyncClient
    monkeypatch.setattr(verification.pipeline, '_public_address', resolve)
    monkeypatch.setattr(verification.pipeline.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(request), **kwargs))
    asyncio.run(JobRunner(db)._execute(db.claim_job(verification=True)))
    saved = db.get_account(value['id'])
    assert saved['status']=='candidate' and saved['metadata']['public_verification']['outcome']=='source_redirected'
    assert requests==['registered.example.org','evil.example.org']
    db.close()


def test_reserved_metadata_survives_valid_backup_restore_without_get_queueing(tmp_path, monkeypatch):
    from app.workbench.account_identity import register_sources, correct_identity, describe_identity
    from app.workbench.backup import BackupService
    from app.workbench.restore import prepare_restore
    db = Store(tmp_path/'backup.sqlite3')
    value = db.save_account({'name':'Alpha','platform':'twitter','handle':'alpha','url':'https://x.com/alpha'})
    correct_identity(db,value['id'],type='individual',manual_locked=True,note='人工登记')
    register_sources(db,value['id'],[{'url':SITE,'account_type':'organization'}])
    response_fixture(monkeypatch, '<a href="https://x.com/alpha">Exact</a>')
    asyncio.run(JobRunner(db)._execute(db.claim_job(verification=True)))
    assert describe_identity(db.get_account(value['id']))['account_type']=='individual'
    before = db.get_account(value['id'])
    response = BackupService().response(db)
    source = tmp_path/'saved-backup.sqlite3'
    try:
        from pathlib import Path
        source.write_bytes(Path(response.path).read_bytes())
    finally:
        response.directory.cleanup()
    import hashlib
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    prepare_restore(source, tmp_path/'restored')
    assert hashlib.sha256(source.read_bytes()).hexdigest()==source_sha
    db.close()
    reopened=Store(tmp_path/'restored'/'workbench.sqlite3')
    assert reopened.get_account(value['id'])==before
    assert describe_identity(reopened.get_account(value['id']))['account_type_details']['manual_locked'] is True
    assert not active_checks(reopened)
    reopened.close()


def test_recover_running_verification_has_no_automatic_requeue_or_http(store, monkeypatch):
    value=seed(store)
    job=store.claim_job(verification=True)
    verification.begin_schedule(store, job)
    def no_network(*args, **kwargs):
        raise AssertionError('Recovering historical state must not fetch')
    monkeypatch.setattr(verification.pipeline,'_fetch_public_feed',no_network)
    async def recovery():
        runner=JobRunner(store)
        await runner.start()
        await runner.stop()
    asyncio.run(recovery())
    assert store.get_job(job['id'])['status']=='interrupted'
    assert store.get_account(value['id'])['metadata']['verification_schedule']['state']=='interrupted'
    assert not active_checks(store)


def test_changed_profile_during_check_queues_one_successor_and_never_loops(store,monkeypatch):
    value=seed(store)
    calls=[]
    async def fetch(url,**kwargs):
        calls.append(url)
        if url==SITE:
            seed(store,website='https://replacement.example.org/')
        return b'<p>No backlink.</p>',url,200
    monkeypatch.setattr(verification.pipeline,'_fetch_public_feed',fetch)
    first=store.claim_job(verification=True)
    asyncio.run(JobRunner(store)._execute(first))
    assert store.get_job(first['id'])['result']['diagnostics'][0]['outcome']=='changed_during_check'
    assert len(active_checks(store))==1
    second=store.claim_job(verification=True)
    asyncio.run(JobRunner(store)._execute(second))
    assert calls==[SITE,'https://replacement.example.org/']
    assert not active_checks(store)
    assert store.get_account(value['id'])['status']=='candidate'


def test_request_coalescing_within_one_automatic_batch_saves_both_account_results(store,monkeypatch):
    calls=response_fixture(monkeypatch,'<p>No backlink.</p>')
    result=store.import_social([], [public_account('alpha'),public_account('beta')], [])
    assert len(active_checks(store))==1
    job=store.claim_job(verification=True)
    asyncio.run(JobRunner(store)._execute(job))
    saved=store.get_job(job['id'])['result']
    assert saved['checked']==2 and calls==[SITE]
    assert sum(row['cache_hits'] for row in saved['diagnostics'])==1
    assert all(store.get_account(identity)['metadata'].get('public_verification') for identity in result['account_ids'].values())


def test_failed_checkpoint_cancels_and_joins_other_inflight_verification_requests(store,monkeypatch):
    result=store.import_social([], [public_account('alpha','https://alpha.example.org/'),
                                   public_account('beta','https://beta.example.org/')], [])
    alpha=result['account_ids']['twitter:alpha']
    job=store.claim_job(verification=True)
    cancelled=[]
    original=store.update_job
    def fail_progress(identity,**fields):
        if fields.get('progress',0) not in (0,100) and fields.get('result'):
            raise OSError('isolated checkpoint write failed')
        return original(identity,**fields)
    monkeypatch.setattr(store,'update_job',fail_progress)
    async def fetch(url,**kwargs):
        if 'beta.' in url:
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(url)
        return b'<p>No backlink.</p>',url,200
    monkeypatch.setattr(verification.pipeline,'_fetch_public_feed',fetch)
    asyncio.run(JobRunner(store)._execute(job))
    assert store.get_job(job['id'])['status']=='failed'
    assert cancelled==['https://beta.example.org/']
    # The accepted account decision remains durable even if its progress
    # receipt write fails; retry uses that saved source check.
    assert store.get_account(alpha)['metadata']['public_verification']['outcome']=='no_backlink'
    monkeypatch.setattr(store,'update_job',original)
    calls=response_fixture(monkeypatch,'<p>No backlink.</p>')
    retry=store.retry_job(job['id'])
    asyncio.run(JobRunner(store)._execute(store.claim_job(verification=True)))
    assert store.get_job(retry['id'])['status']=='succeeded'
    assert calls==['https://beta.example.org/']


def test_profile_link_whitelist_does_not_store_credentials_or_arbitrary_nested_data(store):
    account=public_account()
    account['profile'].update(links=[{'url':'https://user:private-password@bad.example.org/','headers':{'Authorization':'private-header'}},
        {'url':SITE,'label':'Visible website','cookies':'private-cookie'}],followers_count={'token':'private-token'},
        bio={'session':'private-session'},observed_at={'cookie':'private-cookie'})
    imported=store.import_social([], [account], [])
    value=store.get_account(imported['account_ids']['twitter:alpha'])
    profile=value['metadata']['last_public_profile']
    assert profile['links']==[{'url':SITE,'label':'Visible website'}]
    assert 'followers_count' not in profile and 'bio' not in profile and 'observed_at' not in profile
    assert 'private-' not in json.dumps(value['metadata'])


def test_private_source_ip_is_rejected_before_any_http_request(store,monkeypatch):
    calls=[]
    def client(**kwargs):
        calls.append(kwargs)
        raise AssertionError('Private IP must never start HTTP')
    monkeypatch.setattr(verification.pipeline.httpx,'AsyncClient',client)
    value=seed(store,website='https://127.0.0.1/private')
    job=store.claim_job(verification=True)
    asyncio.run(JobRunner(store)._execute(job))
    saved=store.get_account(value['id'])['metadata']['public_verification']
    assert not calls and saved['verified'] is False
    assert saved['outcome']=='source_unavailable' and saved['failure_reason']=='address_rejected'


def historical_curated(store,handle='zengakuren'):
    source=verification.OFFICIAL_SOURCES['twitter:'+handle]
    account=store.save_account({'name':'公开组织','platform':'twitter','handle':handle,'url':'https://x.com/'+handle,
                                'status':'verified','evidence_url':source})
    check={'account_id':account['id'],'account_key':'twitter:'+handle,'account_url':account['url'],
           'source_url':source,'source_basis':'official_registry','verified':True,'outcome':'public_backlink',
           'evidence_url':account['evidence_url'],'matched_account_url':account['url'],'checked_at':'2026-10-09T00:00:00Z'}
    with store._transaction(write=True) as connection:
        store._execute(connection,'UPDATE wb_accounts SET metadata=? WHERE id=?',
                       (json.dumps({'public_verification':check}),account['id']))
    return store.get_account(account['id'])


@pytest.mark.parametrize('handle',['zengakuren','zengakuren_west'])
def test_accepted_historical_curated_record_projects_type_without_network_or_writes(store,monkeypatch,handle):
    from app.workbench.account_identity import describe_identity,correct_identity
    account=historical_curated(store,handle)
    before=store.get_account(account['id'])
    def no_network(*args,**kwargs):
        raise AssertionError('Historic type projection must not fetch')
    monkeypatch.setattr(verification.pipeline,'_fetch_public_feed',no_network)
    projected=describe_identity(account)
    assert projected['account_type']=='organization'
    assert projected['account_type_details']['basis']=='curated_registry_backlink'
    assert store.get_account(account['id'])==before and not active_checks(store)
    for type in ('individual','unknown'):
        corrected=correct_identity(store,account['id'],type=type,manual_locked=True,note='人工修正原组织映射')
        assert describe_identity(corrected)['account_type']==type
        assert describe_identity(corrected)['account_type_details']['manual_locked'] is True


@pytest.mark.parametrize('field,value',[
    ('source_url','https://unregistered.example.org/'),('source_basis','public_profile_link'),
    ('matched_account_url','https://x.com/other'),('account_url','https://x.com/other'),
    ('verified',False),('outcome','candidate_backlink'),('evidence_url','https://elsewhere.example.org/'),
    ('account_id','different-account'),('account_key','twitter:other')])
def test_changed_or_untrusted_historic_record_cannot_project_organization(store,field,value):
    from app.workbench.account_identity import describe_identity
    account=historical_curated(store)
    account['metadata']['public_verification'][field]=value
    assert describe_identity(account)['account_type']=='unknown'


def test_verified_status_organization_name_and_curated_following_do_not_set_type(store):
    from app.workbench.account_identity import describe_identity
    ordinary=store.save_account({'name':'Organization sounding','organization':'Known organization','platform':'twitter',
        'handle':'ordinary','url':'https://x.com/ordinary','status':'verified','evidence_url':'https://zengakuren.jp/'})
    assert describe_identity(ordinary)['account_type']=='unknown'
    curated=historical_curated(store)
    account=store.save_account({'name':'Existing curated account without automatic receipt','platform':'twitter','handle':'zengakuren_west',
        'url':'https://x.com/zengakuren_west','status':'verified','evidence_url':verification.OFFICIAL_SOURCES['twitter:zengakuren_west']})
    assert describe_identity(account)['account_type']=='unknown'
    assert describe_identity(curated)['account_type']=='organization'


def test_manual_relevance_correction_retains_identity_status_evidence_and_jobs(tmp_path):
    db=Store(tmp_path/'relevance.sqlite3')
    with TestClient(create_app(store=db,run_worker=False)) as client:
        value=client.post('/api/v2/accounts',json={'name':'Public','platform':'twitter','handle':'alpha','url':'https://x.com/alpha',
              'status':'verified','evidence_url':'https://public.example.org/official'}).json()
        assert client.patch(f"/api/v2/accounts/{value['id']}/identity",json={'type':'individual','manual_locked':True}).status_code==200
        material=db.import_social([{'title':'原文','content':'保存的公开原文','url':'https://x.com/alpha/status/1','account_key':'twitter:alpha'}],[],[])
        before=db.get_account(value['id'])
        saved_material=db.get_item(material['ids'][0])
        count=db.list_jobs()['total']
        for state in ('irrelevant','included','unknown'):
            response=client.patch(f"/api/v2/accounts/{value['id']}/relevance",json={'state':state,'reason':'人工逐个排除或恢复'})
            assert response.status_code==200
            account=db.get_account(value['id'])
            assert (account['status'],account['evidence_url'],account['metadata']['account_identity']) == (
                   before['status'],before['evidence_url'],before['metadata']['account_identity'])
            if state=='unknown':
                assert 'research_relevance_override' not in account['metadata']
            else:
                assert account['metadata']['research_relevance_override']['state']==state
                assert account['metadata']['research_relevance_override']['basis']=='manual_review'
            assert db.get_item(saved_material['id'])==saved_material and db.list_jobs()['total']==count
        assert client.patch(f"/api/v2/accounts/{value['id']}/relevance",json={'state':'excluded'}).status_code==422
        assert client.patch('/api/v2/accounts/missing/relevance',json={'state':'irrelevant'}).status_code==404


def test_import_cannot_assert_relevance_override_but_preserves_manual_decision(store):
    from app.workbench.account_identity import correct_relevance
    value=seed(store,metadata={'research_relevance_override':{'version':1,'state':'included','basis':'manual_review'}})
    assert 'research_relevance_override' not in value['metadata']
    corrected=correct_relevance(store,value['id'],state='irrelevant',reason='公开材料与研究无关')
    seed(store,metadata={'research_relevance_override':{'version':1,'state':'included','basis':'manual_review'}})
    assert store.get_account(value['id'])['metadata']['research_relevance_override']==corrected['metadata']['research_relevance_override']


@pytest.mark.parametrize('legacy',[None,123,[],{'type':[]},{'version':1,'type':'organization','basis':{}},
                                 {'version':True,'type':'organization','basis':'manual_review'}])
def test_additive_type_projection_tolerates_older_unrelated_metadata_without_trust(store,legacy):
    from app.workbench.account_identity import describe_identity
    value=store.save_account({'name':'Public','platform':'twitter','handle':'alpha','url':'https://x.com/alpha'})
    metadata={'account_identity':legacy,'verification_sources':[{'url':SITE,'trust_basis':'manual_registry','account_type':[]}],
              'site_links':{'legacy':'unknown'},'last_public_profile':{'links':{'legacy':'unknown'}}}
    with store._transaction(write=True) as connection:
        store._execute(connection,'UPDATE wb_accounts SET metadata=? WHERE id=?',(json.dumps(metadata),value['id']))
    value=store.get_account(value['id'])
    assert describe_identity(value)['account_type']=='unknown'
    sites=verification.trusted_sites([value])
    assert sites[verification._site(SITE)]['account_type']=='unknown'
    assert verification.source_candidates(value,sites=sites)[0]['account_type']=='unknown'


def test_nofollow_seo_attribute_does_not_discard_registered_owner_backlink(store,monkeypatch):
    from app.workbench.account_identity import register_sources
    value=store.save_account({'name':'Alpha','platform':'twitter','handle':'alpha','url':'https://x.com/alpha'})
    register_sources(store,value['id'],[{'url':SITE,'account_type':'organization'}])
    response_fixture(monkeypatch,'<footer><a rel="me noopener nofollow" href="https://x.com/alpha">Official account</a></footer>')
    job=store.claim_job(verification=True)
    asyncio.run(JobRunner(store)._execute(job))
    assert store.get_account(value['id'])['status']=='verified'


def test_registered_site_outside_arbitrary_account_window_still_verifies_owner(store,monkeypatch):
    from app.workbench.account_identity import register_sources
    from app.workbench import store as store_module
    from uuid import UUID
    with monkeypatch.context() as fixed:
        fixed.setattr(store_module,'uuid4',lambda:UUID('ffffffff-ffff-ffff-ffff-ffffffffffff'))
        anchor=store.save_account({'name':'Registrar','platform':'twitter','handle':'registrar','url':'https://x.com/registrar'})
    # Populate unrelated accepted account rows in owned QA, then put the normal
    # source registration after them in the old ID-sorted arbitrary window.
    with store._transaction(write=True) as connection:
        for index in range(5001):
            store._execute(connection,"INSERT INTO wb_accounts (id,name,handle,platform,url,organization,status,evidence_url,tags,metadata,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (f'aa-{index:05d}',f'Public {index}',f'a{index}','twitter',f'https://x.com/a{index}','','candidate','','[]','{}','2026-10-09T00:00:00Z','2026-10-09T00:00:00Z'))
    register_sources(store,anchor['id'],[{'url':SITE,'account_type':'organization'}])
    store.request_cancel(active_checks(store)[0]['id'])
    value=seed(store)
    response_fixture(monkeypatch,'<footer><a rel="me" href="https://x.com/alpha">Website owner</a></footer>')
    job=store.claim_job(verification=True)
    asyncio.run(JobRunner(store)._execute(job))
    assert store.get_account(value['id'])['status']=='verified'
    assert store.get_job(job['id'])['result']['diagnostics'][0]['outcome']=='public_backlink'
