"""Persistent public reads use owned SQLite fixtures, never profiles/model/network."""
import asyncio
import copy
from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from app.workbench import automated_review as auto
from app.workbench.account_identity import register_sources
from app.workbench.harness_evidence import EvidenceGatewayError
from app.workbench.review import AutoReviewArgs, digest, snapshot, start_review
from app.workbench.store import Store

URL = 'https://publisher.example.org/about'
QUOTE = 'Public A is a registered organization.'
BODY = ('<main>' + QUOTE + '<a href="https://x.com/public_a">Public A</a>'
        '<a href="https://x.com/public_b">Guest B</a></main>').encode()
CHANGED = b'<main>A public archive, no type assertion.<a href="https://x.com/public_a">Public A</a></main>'


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / 'public-cache.sqlite3')
    yield value
    value.close()


def actor(store, handle='public_a', registered=True):
    account = store.save_account({'name': handle, 'platform': 'twitter', 'handle': handle, 'url': 'https://x.com/' + handle})
    if registered:
        register_sources(store, account['id'], [{'url': URL, 'label': 'Owned fixture registry', 'account_type': 'organization'}], _schedule=False)
    return account


def row(store, account):
    return next(value for value in snapshot(store, ['accounts'], pending_only=False) if value['id'] == account['id'])


def fetch_fixture(monkeypatch, bodies=None):
    calls = []
    bodies = bodies or [BODY]
    async def fetch(url, **kwargs):
        calls.append(url)
        body = bodies[min(len(calls) - 1, len(bodies) - 1)]
        if isinstance(body, BaseException):
            raise body
        return body, url, 200
    monkeypatch.setattr(auto.pipeline, '_fetch_public_feed', fetch)
    return calls


def fixture_harness(monkeypatch, *, failure_after_read=False):
    observed = []
    async def harness(items, context, reviews, **options):
        gateway = options['_gateway']
        proof = await gateway.call('check_citation', {'citation_id': URL, 'exact_quote': QUOTE})
        observed.append(proof)
        if failure_after_read:
            await options['checkpoint']({'model_calls': 0, 'tool_calls': 1, 'trace': []})
            raise auto.pipeline.PipelineError('Owned fixture harness failure after complete read')
        # Unknown still requires the actual investigation contract. The search
        # boundary is fixture-only; it must never become a persistent cache.
        key = 'twitter:' + gateway.rows[0]['record']['handle']
        investigation = await gateway.call('inspect_account', {'account_key': key})
        trace = [{'name': 'check_citation', 'ok': True, 'result': proof},
                 {'name': 'inspect_account', 'ok': True, 'result': investigation}]
        values = {'decisions': [{'kind': 'accounts', 'id': value['id'], 'outcome': 'unknown',
                 'note': 'Owned fixture: insufficient independent type evidence', 'evidence': []} for value in gateway.rows]}
        return {'findings': options['_validator'](values, gateway, trace), 'trace': trace, 'model_calls': 0, 'tool_calls': 2}
    async def search(account):
        return {'saved_sources': [], 'search_results': [], 'search_is_not_verification': True, 'diagnostics': []}
    async def checks(accounts, **kwargs):
        return []
    from app.workbench import review_search, account_verification
    monkeypatch.setattr(auto, 'run_evidence_harness', harness)
    monkeypatch.setattr(review_search, 'search_account_public', search)
    monkeypatch.setattr(account_verification, 'run_checks', checks)
    return observed


def review_job(store, **options):
    arguments = {'kinds': ['accounts'], 'recheck': True, 'batch_size': 1, **options}
    values = snapshot(store, arguments['kinds'], pending_only=False)
    return start_review(store, arguments, expected_digest=digest(values))


def execute(store, job, *, cancelled=lambda: False):
    async def checkpoint(value):
        store.update_job(job['id'], result=value)
    result = asyncio.run(auto.run_auto_review(store, job, checkpoint=checkpoint, cancelled=cancelled))
    store.update_job(job['id'], status='succeeded', result=result)
    return result


def test_real_store_two_jobs_and_completed_retry_reuse_one_successful_capture(store, monkeypatch):
    actor(store)
    calls = fetch_fixture(monkeypatch)
    proofs = fixture_harness(monkeypatch)
    first_job = review_job(store)
    first = execute(store, first_job)
    second_job = review_job(store)
    second = execute(store, second_job)
    assert len(calls) == 1  # The pre-feature implementation fetches twice.
    assert first['source_cache']['fetch_calls'] == 1
    assert second['source_cache']['persistent_hits'] == 1
    assert proofs[0]['source_capture_sha256'] == proofs[1]['source_capture_sha256']
    retry = store.create_job('collect', {**second_job['params'], '_workbench_retry': {'parent_job_id': second_job['id']}})
    execute(store, retry)
    assert len(proofs) == 2 and len(calls) == 1


def test_cache_parameters_are_strict_and_frozen_into_real_job(store):
    actor(store)
    job = review_job(store, source_cache_mode='refresh', source_cache_ttl_seconds=120)
    assert job['params']['review_options']['source_cache_mode'] == 'refresh'
    assert job['params']['review_options']['source_cache_ttl_seconds'] == 120
    for invalid in (True, 0, -1, 86401, 1.5, '60'):
        with pytest.raises(ValueError):
            AutoReviewArgs(source_cache_ttl_seconds=invalid)


def gateway(store, rows=(), **policy):
    from app.workbench.public_source_cache import PublicSourceCache
    cache = PublicSourceCache(store, **policy)
    return auto.ReviewGateway(list(rows), _source_cache=cache), cache


def proof(gate, quote=QUOTE, url=URL):
    return asyncio.run(gate.call('check_citation', {'citation_id': url, 'exact_quote': quote}))


def test_real_multibatch_job_records_all_cache_hits_and_capture_origin(store, monkeypatch):
    actor(store)
    actor(store, 'public_b', registered=False)
    calls = fetch_fixture(monkeypatch)
    fixture_harness(monkeypatch)
    first = execute(store, review_job(store))
    second = execute(store, review_job(store))
    assert len(calls) == 1
    assert first['source_cache']['fetch_calls'] == first['source_cache']['successful_fetches'] == 1
    assert first['source_cache']['run_hits'] == 1
    assert second['source_cache']['persistent_hits'] == second['source_cache']['run_hits'] == 1
    assert second['source_cache']['fetch_calls'] == 0
    assert second['source_cache']['captures'][0]['first_read_via'] == 'persistent'
    assert first['source_cache']['captures'][0]['captured_at'] == second['source_cache']['captures'][0]['captured_at']


def test_reopen_persistent_capture_reprojects_registry_without_cross_target_authority(store, monkeypatch):
    a, b = actor(store), actor(store, 'public_b', registered=False)
    calls = fetch_fixture(monkeypatch)
    gate, cache = gateway(store, [row(store, a)])
    first = proof(gate)
    assert first['registered_account_keys'] == ['twitter:public_a']
    with store._transaction() as connection:
        stored = dict(store._execute(connection, 'SELECT * FROM wb_public_source_cache').fetchone())
    assert 'registered_account_keys' not in json.loads(stored['page'])
    reopened = Store(store.path)
    try:
        guest, guest_cache = gateway(reopened, [row(reopened, b)])
        second = proof(guest)
        assert second['registered_account_keys'] == []
        assert second['source_capture_sha256'] == first['source_capture_sha256']
        assert guest_cache.stats['persistent_hits'] == 1 and len(calls) == 1
        answer = {'decisions': [{'kind': 'accounts', 'id': b['id'], 'outcome': 'organization',
                  'note': 'fixture guest must not inherit registry', 'evidence': [{'citation_id': URL, 'quote': QUOTE}]}]}
        with pytest.raises(auto.pipeline.PipelineError, match='站点自有'):
            auto.validate_decisions(answer, guest, [{'name': 'check_citation', 'ok': True, 'result': second}])
        register_sources(reopened, a['id'], [], _schedule=False)
        removed, removed_cache = gateway(reopened, [row(reopened, a)])
        assert proof(removed)['registered_account_keys'] == []
        assert removed_cache.stats['persistent_hits'] == 1 and len(calls) == 1
    finally:
        reopened.close()


@pytest.mark.parametrize('mode,expected_fetches,expected_writes,expected_hits', [
    ('reuse', 1, 0, 1), ('refresh', 2, 1, 0), ('off', 2, 0, 0), ('shadow', 2, 1, 0)])
def test_close_refresh_and_shadow_preserve_one_run_snapshot(store, monkeypatch, mode, expected_fetches, expected_writes, expected_hits):
    calls = fetch_fixture(monkeypatch, [BODY, CHANGED])
    first, _ = gateway(store)
    original = proof(first)
    next_gate, cache = gateway(store, mode=mode)
    second = proof(next_gate)
    again = proof(next_gate)
    assert len(calls) == expected_fetches
    assert cache.stats['persistent_hits'] == expected_hits and cache.stats['writes'] == expected_writes
    assert cache.stats['run_hits'] == 1
    assert again['source_capture_sha256'] == second['source_capture_sha256']
    assert (second['source_capture_sha256'] == original['source_capture_sha256']) == (mode == 'reuse')
    assert cache.stats['successful_fetches'] == (mode != 'reuse')
    assert cache.stats['shadow_hits'] == (mode == 'shadow')


def test_exact_expiry_and_shorter_caller_policy_refresh_without_overwriting_business_evidence(store, monkeypatch):
    from app.workbench import public_source_cache as source
    clock = [datetime(2026, 10, 11, tzinfo=timezone.utc)]
    monkeypatch.setattr(source, 'utc_now', lambda: clock[0])
    calls = fetch_fixture(monkeypatch, [BODY, CHANGED])
    saved_item = store.import_items([{'title': 'Original business source', 'content': QUOTE, 'url': URL, 'type': 'news'}])['ids'][0]
    report = store.save_report({'title': 'Historical snapshot', 'start_date': '', 'end_date': '', 'mode': 'evidence',
                                'markdown': QUOTE, 'snapshot': [store.get_item(saved_item)], 'source_count': 1})
    original_item, original_report = store.get_item(saved_item), store.get_report(report['id'])
    first_gate, _ = gateway(store)
    first = proof(first_gate)
    clock[0] += timedelta(seconds=3599)
    hit_gate, hit_cache = gateway(store)
    assert proof(hit_gate)['source_capture_sha256'] == first['source_capture_sha256']
    assert hit_cache.stats['persistent_hits'] == 1
    clock[0] += timedelta(seconds=1)
    expired_gate, expired_cache = gateway(store)
    replaced = proof(expired_gate)
    assert not replaced['valid'] and replaced['source_capture_sha256'] != first['source_capture_sha256']
    assert expired_cache.stats['expired'] == 1 and len(calls) == 2
    assert store.get_item(saved_item) == original_item and store.get_report(report['id']) == original_report
    clock[0] += timedelta(seconds=2)
    short_gate, short_cache = gateway(store, ttl_seconds=1)
    proof(short_gate)
    assert short_cache.stats['expired'] == 1 and len(calls) == 3


def test_capture_expiring_while_database_lookup_waits_is_refreshed(store, monkeypatch):
    from app.workbench import public_source_cache as source
    clock = [datetime(2026, 10, 11, tzinfo=timezone.utc)]
    monkeypatch.setattr(source, 'utc_now', lambda: clock[0])
    calls = fetch_fixture(monkeypatch, [BODY, CHANGED])
    first, _ = gateway(store)
    proof(first)
    clock[0] += timedelta(seconds=3599)
    execute_sql = store._execute
    def expiry_during_lookup(connection, sql, params=()):
        value = execute_sql(connection, sql, params)
        if sql.startswith('SELECT * FROM wb_public_source_cache'):
            clock[0] += timedelta(seconds=2)
        return value
    monkeypatch.setattr(store, '_execute', expiry_during_lookup)
    expired, cache = gateway(store)
    assert not proof(expired)['valid']
    assert cache.stats['expired'] == 1 and cache.stats['persistent_hits'] == 0 and len(calls) == 2


@pytest.mark.parametrize('corruption', ['bad-json', 'blob-page', 'deep-json', 'raw-body', 'raw-hash', 'target-authority', 'owner-authority', 'future-clock', 'extractor'])
def test_corrupt_or_old_extractor_copy_falls_back_to_live_read(store, monkeypatch, corruption):
    calls = fetch_fixture(monkeypatch)
    first, _ = gateway(store)
    proof(first)
    with store._transaction(write=True) as connection:
        value = dict(store._execute(connection, 'SELECT * FROM wb_public_source_cache').fetchone())
        if corruption == 'bad-json':
            field, changed = 'page', '{broken'
        elif corruption == 'blob-page':
            field, changed = 'page', b'{}'
        elif corruption == 'deep-json':
            field, changed = 'page', '[' * 1100 + '0' + ']' * 1100
        elif corruption == 'raw-body':
            field, changed = 'raw_body', 'not-valid-base64'
        elif corruption == 'raw-hash':
            field, changed = 'raw_body_sha256', '0' * 64
        elif corruption in {'target-authority', 'owner-authority'}:
            page = json.loads(value['page'])
            page['registered_account_keys' if corruption == 'target-authority' else 'owner_account_keys'] = ['twitter:public_b']
            field, changed = 'page', json.dumps(page)
        elif corruption == 'future-clock':
            field, changed = 'captured_at', '2099-01-01T00:00:00Z'
        else:
            field, changed = 'extractor_version', 'obsolete-fixture-parser'
        store._execute(connection, f'UPDATE wb_public_source_cache SET {field}=? WHERE source_url=?', (changed, URL))
    next_gate, cache = gateway(store)
    result = proof(next_gate)
    assert result['owner_account_keys'] == result['registered_account_keys'] == []
    assert cache.stats['invalid'] == 1 and len(calls) == 2
    assert store.get_public_source_cache(URL)['status'] == 'hit'


def test_network_disabled_does_not_bypass_public_read_contract_with_cache(store, monkeypatch):
    calls = fetch_fixture(monkeypatch)
    first, _ = gateway(store)
    proof(first)
    from app.workbench.public_source_cache import PublicSourceCache
    cache = PublicSourceCache(store)
    disabled = auto.ReviewGateway([], use_public_network=False, _source_cache=cache)
    with pytest.raises(EvidenceGatewayError) as failed:
        proof(disabled)
    assert failed.value.code == 'public_network_disabled'
    assert len(calls) == 1 and cache.stats['persistent_hits'] == cache.stats['fetch_calls'] == 0


@pytest.mark.parametrize('failure', [OSError('owned transient failure'), b'%PDF fixture', b'<html><script>inert</script></html>'])
def test_failed_or_empty_reads_do_not_become_persistent_failures(store, monkeypatch, failure):
    calls = fetch_fixture(monkeypatch, [failure, BODY])
    first, cache = gateway(store)
    if isinstance(failure, BaseException) or failure.startswith(b'%PDF'):
        with pytest.raises(EvidenceGatewayError):
            proof(first)
        with pytest.raises(EvidenceGatewayError):
            proof(first)
    else:
        assert not proof(first)['valid']
    assert store.public_source_cache_stats()['entries'] == 0
    second, _ = gateway(store)
    assert proof(second)['valid'] and len(calls) == 2


def test_refresh_failure_preserves_prior_copy_but_does_not_use_it_as_new_success(store, monkeypatch):
    calls = fetch_fixture(monkeypatch, [BODY, OSError('owned refresh failure')])
    first, _ = gateway(store)
    original = proof(first)
    refresh, cache = gateway(store, mode='refresh')
    with pytest.raises(EvidenceGatewayError):
        proof(refresh)
    old = store.get_public_source_cache(URL)
    assert old['status'] == 'hit' and old['page']['source_capture_sha256'] == original['source_capture_sha256']
    assert cache.stats['fetch_calls'] == 1 and cache.stats['successful_fetches'] == 0 and len(calls) == 2


def test_cache_db_write_failure_is_nonfatal_to_valid_source_read(store, monkeypatch):
    fetch_fixture(monkeypatch)
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('owned storage failure')
    monkeypatch.setattr(store, 'put_public_source_cache', fail)
    gate, cache = gateway(store)
    assert proof(gate)['valid']
    assert cache.stats['write_errors'] == 1 and cache.stats['successful_fetches'] == 1
    assert store.public_source_cache_stats()['entries'] == 0


def test_cancel_after_fetch_never_stores_capture_or_half_page(store, monkeypatch):
    stopped = [False]
    async def fetch(url, **kwargs):
        stopped[0] = True
        return BODY, url, 200
    monkeypatch.setattr(auto.pipeline, '_fetch_public_feed', fetch)
    gate, cache = gateway(store, cancelled=lambda: stopped[0])
    with pytest.raises(asyncio.CancelledError):
        proof(gate)
    assert gate.pages == {} and store.public_source_cache_stats()['entries'] == 0
    assert cache.stats['fetch_calls'] == 1 and cache.stats['successful_fetches'] == 0


def test_cancel_while_cache_thread_waits_rolls_back_and_is_joined(store, monkeypatch):
    from threading import Event
    started, release = Event(), Event()
    original_put = store.put_public_source_cache
    def delayed(*args, **kwargs):
        started.set()
        release.wait(3)
        return original_put(*args, **kwargs)
    monkeypatch.setattr(store, 'put_public_source_cache', delayed)
    fetch_fixture(monkeypatch)
    async def scenario():
        gate, cache = gateway(store)
        task = asyncio.create_task(gate.call('read_evidence', {'citation_id': URL}))
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not gate.pages
    asyncio.run(scenario())
    assert store.public_source_cache_stats()['entries'] == 0


def test_cancel_survives_simultaneous_cache_thread_storage_error(store, monkeypatch):
    from threading import Event
    started, release = Event(), Event()
    def delayed_failure(*args, **kwargs):
        started.set()
        release.wait(3)
        raise OSError('Owned concurrent cache write failure')
    monkeypatch.setattr(store, 'put_public_source_cache', delayed_failure)
    fetch_fixture(monkeypatch)
    async def scenario():
        gate, cache = gateway(store)
        task = asyncio.create_task(gate.call('read_evidence', {'citation_id': URL}))
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cache.stats['write_errors'] == 1 and not gate.pages
    asyncio.run(scenario())
    assert store.public_source_cache_stats()['entries'] == 0


def test_cache_capacity_and_clear_only_remove_regenerable_copies(store, monkeypatch):
    from app.workbench import public_source_cache as source
    monkeypatch.setattr(source, 'MAX_CACHE_ROWS', 2)
    calls = fetch_fixture(monkeypatch)
    item = store.import_items([{'title': 'Keep original', 'content': QUOTE, 'url': URL, 'type': 'news'}])['ids'][0]
    original = store.get_item(item)
    for index in range(5):
        gate, _ = gateway(store)
        proof(gate, url=f'https://publisher.example.org/fixture/{index}')
    stats = store.public_source_cache_stats()
    assert stats['entries'] == 2 and stats['size_bytes'] <= stats['max_size_bytes'] and len(calls) == 5
    assert store.clear_public_source_cache() == 2
    assert store.public_source_cache_stats()['entries'] == 0 and store.get_item(item) == original


@pytest.mark.parametrize('bad_size', ['broken', 1])
def test_unrelated_corrupt_capacity_metadata_cannot_break_new_read_or_bypass_limit(store, monkeypatch, bad_size):
    from app.workbench import public_source_cache as source
    fetch_fixture(monkeypatch)
    first, _ = gateway(store)
    proof(first)
    with store._transaction(write=True) as connection:
        store._execute(connection, 'UPDATE wb_public_source_cache SET size_bytes=? WHERE source_url=?', (bad_size, URL))
    monkeypatch.setattr(source, 'MAX_CACHE_BYTES', 1000)
    second, cache = gateway(store)
    assert proof(second, url='https://publisher.example.org/new')['valid']
    assert cache.stats['evicted'] == 1
    stats = store.public_source_cache_stats()
    assert stats['entries'] == 1 and stats['size_bytes'] <= stats['max_size_bytes']


@pytest.mark.parametrize('bad_captures', [[{}], 'broken'])
def test_damaged_retry_cache_statistics_do_not_discard_existing_business_results(store, monkeypatch, bad_captures):
    actor(store)
    fetch_fixture(monkeypatch)
    fixture_harness(monkeypatch)
    parent = review_job(store)
    original = execute(store, parent)
    damaged = copy.deepcopy(original)
    damaged['source_cache']['captures'] = bad_captures
    damaged['source_cache']['run_hits'] = 'broken'
    # Deliberate on-disk corruption; terminal business jobs remain immutable via
    # the ordinary Store API.
    with store._transaction(write=True) as connection:
        store._execute(connection, 'UPDATE wb_jobs SET result=? WHERE id=?', (json.dumps(damaged), parent['id']))
    retry = store.create_job('collect', {**parent['params'], '_workbench_retry': {'parent_job_id': parent['id']}})
    result = execute(store, retry)
    assert result['results'] == original['results'] and result['completed'] == 1
    assert result['source_cache']['captures'] == [] and result['source_cache']['run_hits'] == 0


def test_failed_actual_job_retry_reuses_complete_capture_without_saving_failed_decision(store, monkeypatch):
    account = actor(store)
    original = store.get_account(account['id'])
    calls = fetch_fixture(monkeypatch)
    fixture_harness(monkeypatch, failure_after_read=True)
    failed = review_job(store)
    with pytest.raises(auto.pipeline.PipelineError, match='Owned fixture'):
        execute(store, failed)
    store.update_job(failed['id'], status='failed', error='owned failure')
    assert store.get_account(account['id']) == original
    assert store.get_job(failed['id'])['result']['completed'] == 0
    fixture_harness(monkeypatch)
    retry = store.create_job('collect', {**failed['params'], '_workbench_retry': {'parent_job_id': failed['id']}})
    result = execute(store, retry)
    assert result['completed'] == 1 and len(result['results']) == 1 and len(calls) == 1
    assert result['source_cache']['persistent_hits'] == result['source_cache']['fetch_calls'] == 1


def test_refresh_tracks_raw_byte_change_even_when_extracted_text_is_unchanged(store, monkeypatch):
    calls = fetch_fixture(monkeypatch, [BODY, BODY + b'<!-- changed fixture markup -->'])
    first, _ = gateway(store)
    original = proof(first)
    refreshed, _ = gateway(store, mode='refresh')
    changed = proof(refreshed)
    assert changed['source_capture_sha256'] == original['source_capture_sha256']
    assert changed['source_read_receipt']['raw_body_sha256'] != original['source_read_receipt']['raw_body_sha256']
    assert len(calls) == 2


def test_capacity_keeps_recently_used_capture_and_enforces_actual_byte_limit(store, monkeypatch):
    from app.workbench import public_source_cache as source
    clock = [datetime(2026, 10, 11, tzinfo=timezone.utc)]
    monkeypatch.setattr(source, 'utc_now', lambda: clock[0])
    monkeypatch.setattr(source, 'MAX_CACHE_ROWS', 2)
    fetch_fixture(monkeypatch)
    urls = [f'https://publisher.example.org/lru/{index}' for index in range(3)]
    for url in urls[:2]:
        gate, _ = gateway(store)
        proof(gate, url=url)
        clock[0] += timedelta(seconds=1)
    assert store.get_public_source_cache(urls[0])['status'] == 'hit'
    clock[0] += timedelta(seconds=1)
    gate, cache = gateway(store)
    proof(gate, url=urls[2])
    assert cache.stats['evicted'] == 1
    assert store.get_public_source_cache(urls[0])['status'] == 'hit'
    assert store.get_public_source_cache(urls[1])['status'] == 'miss'
    assert store.get_public_source_cache(urls[2])['status'] == 'hit'
    monkeypatch.setattr(source, 'MAX_CACHE_BYTES', 1000)
    clock[0] += timedelta(seconds=1)
    next_gate, _ = gateway(store)
    proof(next_gate, url='https://publisher.example.org/lru/new')
    stats = store.public_source_cache_stats()
    assert stats['entries'] == 1 and stats['size_bytes'] <= 1000


def test_cancel_inside_actual_cache_transaction_rolls_back_insert(store, monkeypatch):
    stopped = [False]
    execute_sql = store._execute
    def cancel_after_insert(connection, sql, params=()):
        value = execute_sql(connection, sql, params)
        if 'INSERT INTO wb_public_source_cache' in sql:
            stopped[0] = True
        return value
    monkeypatch.setattr(store, '_execute', cancel_after_insert)
    fetch_fixture(monkeypatch)
    gate, _ = gateway(store, cancelled=lambda: stopped[0])
    with pytest.raises(asyncio.CancelledError):
        proof(gate)
    assert not gate.pages and store.public_source_cache_stats()['entries'] == 0


def test_cache_read_database_error_falls_back_but_unexpected_application_error_propagates(store, monkeypatch):
    calls = fetch_fixture(monkeypatch)
    def failure(*args, **kwargs):
        raise sqlite3.OperationalError('Owned cache read failure')
    monkeypatch.setattr(store, 'get_public_source_cache', failure)
    gate, cache = gateway(store)
    assert proof(gate)['valid'] and len(calls) == 1 and cache.stats['invalid'] == 1
    def unexpected(*args, **kwargs):
        raise RuntimeError('Owned application bug')
    monkeypatch.setattr(store, 'get_public_source_cache', unexpected)
    another, _ = gateway(store)
    with pytest.raises(RuntimeError, match='Owned application bug'):
        proof(another)
    assert len(calls) == 1


def test_trash_restore_and_purge_clear_cache_copies_preserve_report(store, monkeypatch):
    fetch_fixture(monkeypatch)
    item = store.import_items([{'title': 'Public source', 'content': QUOTE, 'url': URL, 'type': 'news'}])['ids'][0]
    report = store.save_report({'title': 'Independent history', 'start_date': '', 'end_date': '', 'mode': 'evidence',
        'markdown': QUOTE, 'snapshot': [store.get_item(item)], 'source_count': 1})
    original_report = store.get_report(report['id'])
    first, _ = gateway(store)
    proof(first)
    preview = store.cleanup_preview([item], 'trash')
    store.cleanup_apply([item], 'trash', preview['preview_digest'], reason='Owned fixture cleanup')
    assert store.public_source_cache_stats()['entries'] == 0
    assert store.get_public_source_cache(URL)['status'] == 'blocked'
    preview = store.cleanup_preview([item], 'restore')
    store.cleanup_apply([item], 'restore', preview['preview_digest'], reason='Owned fixture restore')
    next_gate, _ = gateway(store)
    proof(next_gate)
    assert store.public_source_cache_stats()['entries'] == 1
    preview = store.cleanup_preview([item], 'trash')
    store.cleanup_apply([item], 'trash', preview['preview_digest'], reason='Owned fixture trash')
    preview = store.cleanup_preview([item], 'purge')
    store.cleanup_apply([item], 'purge', preview['preview_digest'], reason='Owned fixture purge', confirm_purge=True)
    assert store.public_source_cache_stats()['entries'] == 0 and store.get_item(item) is None
    assert store.get_report(report['id']) == original_report


@pytest.mark.parametrize('legacy_missing', [(), ('wb_public_source_cache',), ('wb_research_deferred',), ('wb_public_source_cache', 'wb_research_deferred')])
def test_cache_backup_and_known_old_complete_restore_contract(store, tmp_path, monkeypatch, legacy_missing):
    from contextlib import closing
    from pathlib import Path
    from app.workbench.backup import BackupService
    from app.workbench.restore import prepare_restore
    fetch_fixture(monkeypatch)
    first, _ = gateway(store)
    proof(first)
    item = store.import_items([{'title': 'Keep restored source', 'content': QUOTE, 'url': URL, 'type': 'news'}])['ids'][0]
    response = BackupService().response(store)
    source_path = tmp_path / 'owned-backup.sqlite3'
    try:
        source_path.write_bytes(Path(response.path).read_bytes())
    finally:
        response.directory.cleanup()
    if legacy_missing:
        with closing(sqlite3.connect(source_path)) as connection:
            for table in legacy_missing:
                connection.execute('DROP TABLE ' + table)
            connection.commit()
    original_bytes = source_path.read_bytes()
    stage = tmp_path / 'owned-restored'
    prepare_restore(source_path, stage)
    assert source_path.read_bytes() == original_bytes
    restored = Store(stage / 'workbench.sqlite3')
    try:
        assert restored.get_item(item)['content'] == QUOTE
        assert restored.public_source_cache_stats()['entries'] == (0 if 'wb_public_source_cache' in legacy_missing else 1)
        with restored._transaction() as connection:
            assert restored._execute(connection, 'PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert restored._execute(connection, 'PRAGMA foreign_key_check').fetchall() == []
    finally:
        restored.close()
