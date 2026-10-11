"""Bounded original-post context, with no network or formal data access."""
import asyncio
import copy
from contextlib import closing

import pytest

from app.workbench import automated_review as auto
from app.workbench.activity_events import extract_candidates, item_activities
from app.workbench.review import apply_batch, digest, preview_batch, snapshot
from app.workbench.store import Store


def source(content, *, published='2026-10-05T23:24:14Z', **changes):
    value = {'id': 'source', 'type': 'social', 'title': '公開活動', 'content': content,
             'url': 'https://x.com/i/status/2107250543144120802', 'published_at': published,
             'publication_date_unknown': False,
             'metadata': {'collection_method': 'x_browser_profile_timeline',
                          'author_verified_in_dom': True, 'post_id': '2107250543144120802',
                          'post_kind': 'post', 'content_kind': 'body',
                          'text_representation': 'public_post_dom'}}
    value.update(changes)
    return value


def inputs(item, quote=None, *, day='2026-10-06', prefecture='長崎県'):
    record = extract_candidates(item)[0]
    row = {'kind': 'activities', 'id': record['id'], 'record': record, 'source': item}
    text = item['title'] + '\n' + item['content']
    proof = {'valid': True, 'citation_id': 'activities:' + record['id'],
             'quote': quote or record['quote'], 'url': item['url'],
             'source_capture_sha256': digest(text), 'independent': False}
    decision = {'date': day, 'prefecture': prefecture, 'outcome': 'confirmed'}
    return row, [proof], decision


def build(item, quote=None, **decision):
    from app.workbench.activity_review_support import build_activity_review_basis
    return build_activity_review_basis(*inputs(item, quote, **decision))


@pytest.mark.parametrize('text,published,day,prefecture', [
    ('本日は千葉県庁デモに参加しています。', '2026-09-08T00:19:38Z', '2026-09-08', '千葉県'),
    ('本日、東京で反戦集会が開催されました。', '2026-10-02T03:12:52Z', '2026-10-02', '東京都'),
    ('昨日、京都で反戦デモを行いました。', '2025-12-31T16:00:00Z', '2025-12-31', '京都府'),
])
def test_actual_review_gateway_accepts_checked_relative_original_post(tmp_path, text, published, day, prefecture):
    with closing(Store(tmp_path / 'atlas.sqlite3')) as store:
        store.import_items([source(text, published=published)])
        rows = snapshot(store, ['activities'])
        gateway = auto.ReviewGateway(rows, use_public_network=False)
        row = rows[0]
        proof = asyncio.run(gateway.call('check_citation', {
            'citation_id': 'activities:' + row['id'], 'exact_quote': row['record']['quote']}))
        answer = {'decisions': [{'kind': 'activities', 'id': row['id'], 'outcome': 'confirmed',
                                'note': '原帖明确描述参加或举行；未独立核实。',
                                'date': day, 'prefecture': prefecture,
                                'evidence': [{'citation_id': proof['citation_id'], 'quote': proof['quote']}]}]}
        validated = auto.validate_auto_review(answer, gateway, [{'name': 'check_citation', 'ok': True, 'result': proof}])
        assert validated['decisions'][0]['outcome'] == 'confirmed'
        batch = auto._batch(rows, validated['decisions'])
        assert batch['activities'][0]['review_basis']['scope'] == 'source_claim_only'
        preview = preview_batch(store, batch)
        apply_batch(store, batch, expected_digest=preview['digest'], reviewer='deepseek_auto_review')
        event = store.activities()['items'][0]
        assert event['date'] == day and event['prefecture'] == prefecture
        assert event['quote'] == row['record']['quote'] and event['id'] == row['id']


@pytest.mark.parametrize('expression,published,day', [
    ('本日', '2026-10-05T23:24:14Z', '2026-10-06'),
    ('今日', '2026-10-05T15:00:00Z', '2026-10-06'),
    ('昨日', '2025-12-31T16:00:00Z', '2025-12-31'),
])
def test_japanese_calendar_relative_dates(expression, published, day):
    basis = build(source(expression + '、長崎で反戦デモを行いました。', published=published), day=day)
    assert basis['date']['value'] == day
    assert basis['publication']['time_zone'] == 'Asia/Tokyo'
    assert basis['occurrence']['status'] == 'reported_held'


def test_this_year_requires_checked_matching_month_day_and_keeps_post_day_separate():
    text = '10月7日、反戦デモを東京で行いました。\n今年の10.7は大切な行動となりました。'
    basis = build(source(text, published='2026-10-07T17:55:05Z'), text,
                  day='2026-10-07', prefecture='東京都')
    assert basis['date']['rule'] == 'publication_year'
    assert '今年' in basis['date']['expression']


@pytest.mark.parametrize('changes', [
    {'published_at': None}, {'published_at': '2026-10-06'},
    {'published_at': '2026-10-06T08:24:14'}, {'published_at': 'invalid'},
    {'publication_date_unknown': True}, {'metadata': {}},
    {'metadata': {'collection_method': 'manual_import', 'author_verified_in_dom': True}},
    {'metadata': {**source('')['metadata'], 'post_id': '999'}},
    {'metadata': {**source('')['metadata'], 'author_verified_in_dom': False}},
    {'metadata': {**source('')['metadata'], 'post_kind': 'repost'}},
    {'metadata': {**source('')['metadata'], 'content_kind': 'image_alt'}},
    {'metadata': {**source('')['metadata'], 'text_representation': 'translated_body'}},
    {'url': 'https://x.com.evil.example/i/status/2107250543144120802'},
])
def test_nonempty_time_is_not_reliable_original_publication(changes):
    from app.workbench.activity_review_support import ActivitySupportError
    with pytest.raises(ActivitySupportError):
        build(source('本日、長崎で反戦デモを行いました。', **changes))


@pytest.mark.parametrize('text,day', [
    ('10月6日、長崎で反戦デモを行いました。', '2026-10-06'),
    ('今年、長崎で反戦デモを行いました。', '2026-10-06'),
    ('本日10月5日、長崎で反戦デモを行いました。', '2026-10-06'),
    ('今年2月30日、長崎で反戦デモを行いました。', '2026-02-28'),
    ('2025年10月6日、長崎で反戦デモを行いました。', '2026-10-06'),
    ('今年2025年10月6日、長崎で反戦デモを行いました。', '2025-10-06'),
])
def test_bare_month_day_and_contradictory_dates_remain_unknown(text, day):
    from app.workbench.activity_review_support import ActivitySupportError
    with pytest.raises(ActivitySupportError):
        build(source(text), day=day)


@pytest.mark.parametrize('text', [
    '本日、長崎で反戦デモを開催します。',
    '本日、長崎で反戦デモに集まろう！',
    '2026年10月6日、長崎で反戦デモを行う予定です。',
    '今日も長崎で長年反戦デモを続けている人々の闘い。',
    '本日、長崎で反戦デモは開催されなかった。',
    '今日10月6日16-18時、長崎で反戦デモをやっています。',
])
def test_announcements_generic_and_future_clock_cannot_be_confirmed(text):
    from app.workbench.activity_review_support import ActivitySupportError, OCCURRENCE_GAP
    with pytest.raises(ActivitySupportError, match=OCCURRENCE_GAP):
        build(source(text), text)


def test_unrelated_today_does_not_cross_event_sentence():
    from app.workbench.activity_review_support import ActivitySupportError
    text = '本日は近畿大学で情宣を行いました。京都で反戦デモを行います。'
    with pytest.raises(ActivitySupportError):
        build(source(text), text, prefecture='京都府')


def test_same_post_future_events_cannot_borrow_current_event_date():
    text = '本日は、10・4東京で反戦デモに参加しています。11・1京都集会、11・22大阪デモへ！'
    item = source(text, published='2026-10-04T04:10:15Z')
    basis = build(item, text, day='2026-10-04', prefecture='東京都')
    assert basis['date']['value'] == '2026-10-04'
    assert '11・' not in basis['date']['quote']


def test_historical_date_and_background_place_do_not_replace_current_venue():
    text = '本日は沖縄返還と71年11・14渋谷闘争の公判に先立ち東京高裁包囲デモに参加しています。'
    basis = build(source(text, published='2026-10-02T03:12:52Z'), text,
                  day='2026-10-02', prefecture='東京都')
    assert basis['location']['alias'] == '東京'


@pytest.mark.parametrize('text,prefecture', [
    ('本日、動労千葉が反戦デモに参加しました。', '千葉県'),
    ('本日、東京の政治を論じる反戦集会を行いました。', '東京都'),
    ('本日、https://tokyo.example.org で反戦集会を行いました。', '東京都'),
    ('本日、新宿で反戦デモを行いました。', '東京都'),
    ('本日、東京反戦集会という団体がデモに参加しました。', '東京都'),
])
def test_organization_background_url_and_unknown_city_are_not_venues(text, prefecture):
    from app.workbench.activity_review_support import ActivitySupportError
    with pytest.raises(ActivitySupportError):
        build(source(text), prefecture=prefecture)


def test_unlocated_activity_can_keep_supported_date_without_invented_prefecture():
    basis = build(source('本日、新宿で反戦デモを行いました。'), prefecture='')
    assert basis['location']['prefecture'] == ''


def test_explicit_complete_date_needs_no_publication_provenance():
    basis = build(source('２０２６年１０月６日、東京都で反戦集会が開催された。',
                         published=None, metadata={}), day='2026-10-06', prefecture='東京都')
    assert basis['date']['rule'] == 'explicit_full_date' and basis['publication'] is None


@pytest.mark.parametrize('mode', ['x_browser_live_search', 'x_browser_profile_timeline'])
def test_existing_dom_capture_modes_and_original_language_toggle_are_supported(mode):
    metadata = {**source('')['metadata'], 'collection_method': mode,
                'text_representation': 'original_after_platform_toggle', 'post_kind': 'quote'}
    basis = build(source('本日、長崎で反戦デモを行いました。', metadata=metadata))
    assert basis['publication']['collection_method'] == mode


@pytest.mark.parametrize('expression', ['本日', '今年10月6日'])
def test_quoted_old_wording_does_not_inherit_new_post_timestamp(expression):
    from app.workbench.activity_review_support import ActivitySupportError
    text = f'旧資料「{expression}、長崎で反戦デモを行いました」を紹介します。'
    with pytest.raises(ActivitySupportError): build(source(text), text)


def test_provenance_projection_excludes_sibling_reviews_but_binds_capture_fields():
    from app.workbench.activity_review_support import activity_source_projection
    item = source('本日、長崎で反戦デモを行いました。')
    before = activity_source_projection(item)
    item['metadata']['atlas_activity_reviews'] = {'sibling': {'note': 'reviewed'}}
    assert activity_source_projection(item) == before
    item['metadata']['post_kind'] = 'repost'
    assert activity_source_projection(item) != before


@pytest.mark.parametrize('prefecture', ['', '京都府', '東京都'])
def test_multiple_named_venues_cannot_choose_one_prefecture(prefecture):
    from app.workbench.activity_review_support import ActivitySupportError
    with pytest.raises(ActivitySupportError):
        build(source('本日、東京と京都で反戦集会が開催されました。'), prefecture=prefecture)


def test_this_year_of_another_paragraph_cannot_fill_bare_event_year():
    from app.workbench.activity_review_support import ActivitySupportError
    text = '10月6日、長崎で反戦デモを行いました。別の歴史の話です。今年の10.6は重要な行動となりました。'
    with pytest.raises(ActivitySupportError): build(source(text), text)


@pytest.mark.parametrize('field,value', [('citation_id', 'https://other.example.org/post'),
                                       ('source_capture_sha256', '0' * 64),
                                       ('valid', False), ('quote', '本日、長崎で反戦デモを行いました。invented')])
def test_citation_proof_cannot_borrow_saved_time_or_invent_support(field, value):
    from app.workbench.activity_review_support import ActivitySupportError, build_activity_review_basis
    row, proofs, decision = inputs(source('本日、長崎で反戦デモを行いました。'))
    proofs[0][field] = value
    with pytest.raises((ActivitySupportError, ValueError)):
        build_activity_review_basis(row, proofs, decision)


def saved_batch(store, *, text='本日、長崎で反戦デモを行いました。', post_id='2107250543144120802'):
    from app.workbench.activity_review_support import build_activity_review_basis
    captured = source(text, url='https://x.com/i/status/' + post_id,
                      metadata={**source('')['metadata'], 'post_id': post_id})
    item_id = store.import_items([captured])['ids'][0]
    item = store.get_item(item_id)
    row, proofs, decision = inputs(item)
    basis = build_activity_review_basis(row, proofs, decision)
    record = row['record']
    return item_id, {'activities': [{'id': record['id'], 'item_id': item_id,
        'expected_sha256': next(row['expected_sha256'] for row in snapshot(store, ['activities']) if row['id'] == record['id']),
        'status': 'confirmed', 'title': record['title'], 'quote': record['quote'],
        'date': decision['date'], 'prefecture': decision['prefecture'],
        'note': '已核对原帖说法，未独立核实。', 'review_basis': basis}]}


def test_basis_persists_original_quote_and_time_only_correction_is_stale(tmp_path):
    with closing(Store(tmp_path / 'atlas.sqlite3')) as store:
        item_id, batch = saved_batch(store)
        preview = preview_batch(store, batch)
        apply_batch(store, batch, expected_digest=preview['digest'], reviewer='deepseek_auto_review')
        event = store.activities()['items'][0]
        assert event['review_basis'] == batch['activities'][0]['review_basis']
        assert event['quote'] == batch['activities'][0]['quote']
        with store._transaction(write=True) as connection:
            store._execute(connection, 'UPDATE wb_items SET published_at=? WHERE id=?', ('2026-10-06T23:24:14Z', item_id))
        corrected = store.get_item(item_id)
        assert any(row.get('stale') for row in item_activities(corrected))
        assert store.activities()['denominator'] == 0


@pytest.mark.parametrize('mutation', ['scope', 'date', 'quote', 'extra'])
def test_forged_review_basis_rejects_atomic_batch_before_any_write(tmp_path, mutation):
    with closing(Store(tmp_path / 'atlas.sqlite3')) as store:
        _, batch = saved_batch(store)
        bad = copy.deepcopy(batch)
        basis = bad['activities'][0]['review_basis']
        if mutation == 'scope': basis['scope'] = 'independently_verified'
        elif mutation == 'date': basis['date']['value'] = '2025-10-06'
        elif mutation == 'quote': basis['supports'][0]['quote'] = 'invented'
        else: basis['bypass'] = True
        with pytest.raises(ValueError): preview_batch(store, bad)
        assert store.activities()['denominator'] == 0


def test_public_import_cannot_grant_derived_review_and_old_unknown_is_not_upgraded(tmp_path):
    with closing(Store(tmp_path / 'atlas.sqlite3')) as store:
        item_id, batch = saved_batch(store)
        decision = batch['activities'][0]
        store.import_items([{**source('本日、長崎で反戦デモを行いました。'),
                             'metadata': {**source('')['metadata'], 'atlas_activity_reviews': {decision['id']: decision}}}])
        assert store.activities()['denominator'] == 0
        assert store.get_item(item_id)['metadata'].get('atlas_activity_reviews') is None


def test_changed_publication_after_preview_rejects_without_modifying_source_or_review(tmp_path):
    from app.workbench.review import ReviewConflict
    with closing(Store(tmp_path / 'atlas.sqlite3')) as store:
        item_id, batch = saved_batch(store)
        preview = preview_batch(store, batch)
        with store._transaction(write=True) as connection:
            store._execute(connection, 'UPDATE wb_items SET published_at=? WHERE id=?', ('2026-10-06T23:24:14Z', item_id))
        source_before = store.get_item(item_id)
        with pytest.raises(ReviewConflict):
            apply_batch(store, batch, expected_digest=preview['digest'], reviewer='deepseek_auto_review')
        assert store.get_item(item_id) == source_before
        assert store.activities()['denominator'] == 0


def test_basis_metadata_roundtrip_does_not_change_schema_or_source_text(tmp_path):
    with closing(Store(tmp_path / 'atlas.sqlite3')) as store:
        item_id, batch = saved_batch(store)
        original = store.get_item(item_id)
        with store._transaction() as connection:
            schema_before = [tuple(row) for row in store._execute(connection,
                "SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
        preview = preview_batch(store, batch)
        apply_batch(store, batch, expected_digest=preview['digest'], reviewer='deepseek_auto_review')
        with store._transaction() as connection:
            schema_after = [tuple(row) for row in store._execute(connection,
                "SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
        assert schema_after == schema_before
        assert store.get_item(item_id)['content'] == original['content']
        assert store.get_item(item_id)['published_at'] == original['published_at']


def test_bad_second_basis_cannot_partially_commit_valid_first_sibling(tmp_path):
    with closing(Store(tmp_path / 'atlas.sqlite3')) as store:
        _, first = saved_batch(store)
        _, second = saved_batch(store, text='今日、長崎で反戦集会が開催されました。', post_id='2107250543144120803')
        batch = {'activities': first['activities'] + second['activities']}
        before = copy.deepcopy(store.activities(status='candidate'))
        preview = preview_batch(store, batch)
        batch['activities'][1]['review_basis']['scope'] = 'independently_verified'
        with pytest.raises(ValueError):
            apply_batch(store, batch, expected_digest=preview['digest'], reviewer='deepseek_auto_review')
        assert store.activities(status='candidate') == before


def test_verified_time_provenance_change_alone_invalidates_saved_basis(tmp_path):
    with closing(Store(tmp_path / 'atlas.sqlite3')) as store:
        item_id, batch = saved_batch(store)
        preview = preview_batch(store, batch)
        apply_batch(store, batch, expected_digest=preview['digest'], reviewer='deepseek_auto_review')
        item = store.get_item(item_id)
        item['metadata']['author_verified_in_dom'] = False
        assert any(event.get('stale') for event in item_activities(item))
