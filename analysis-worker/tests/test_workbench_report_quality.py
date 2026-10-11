import copy
import importlib

import pytest

from app.workbench import report_analysis
from app.workbench.pipeline import PipelineError


def quality():return importlib.import_module('app.workbench.report_quality')


def profile(identity='S085',key='twitter:west',text='関西活動と機関紙の公開紹介。 @Alpha',**extra):
    return {'citation_id':identity,'account_key':key,'source_name':key,'url':f'https://x.com/{str(key or "unbound").split(":")[-1]}',
            'content':text,'metadata':{'research_report_source_role':'public_profile'},**extra}


def context():
    return {'research_goal':'区分公开提及、观察关注与正式隶属',
            'accounts':[{'account_key':'twitter:west','name':'西方公开账号','status':'candidate'},
                        {'account_key':'twitter:alpha','name':'主体公开账号','status':'candidate'},
                        {'account_key':'mastodon:unknown','name':'其他平台'}],
            'status':'partial','summary':{'visited_count':8,'account_budget_skipped':49,'stop_reason':'account_budget'},
            'diagnostics':[{'stage':'following','account_key':'twitter:west','scanned':230,'accepted':15,
                'source_url':'https://x.com/west/following','coverage':'finite','raw_custom':{'public_count':230}},
                {'mode':'account','scanned':20,'source_url':'https://x.com/west'}]}


def test_inventory_preserves_exact_profile_literal_qualifiers_full_text_and_scope_without_promoting_identity():
    bio='明示的な地域・委員長・党機関紙の限定を保持。'*100+' @AlPhA と @unknown / contact@example.com / ＠FullWidth'
    rows=[profile(text=bio)]
    original=copy.deepcopy((rows,context()))
    result=quality().deterministic_report_inventory(rows,context())
    assert result['profile_sources'][0]['saved_profile_text']==bio
    assert result['profile_sources'][0]['content_characters']==len(bio)
    mentions=result['profile_handle_mentions']
    assert [row['quote'] for row in mentions]==['@AlPhA','@unknown']
    assert mentions[0]['target_key']=='twitter:alpha' and mentions[1]['target_key'] is None
    assert all(row['basis']=='saved_profile_text_mention' and row['citation_id']=='S085' for row in mentions)
    assert all(bio[row['start']:row['end']]==row['quote'] for row in mentions)
    assert not any(row.get('verified') or row.get('affiliation') for row in mentions)
    assert result['collection_scope']['visited_count']==8 and result['collection_scope']['account_budget_skipped']==49
    assert result['following_diagnostics'][0]['scanned']==230
    assert result['following_diagnostics'][0]['raw_custom']=={'public_count':230}
    assert (rows,context())==original


def test_only_saved_profiles_enter_profile_inventory_and_contiguous_fragments_preserve_boundary_mention():
    first=profile(text='')
    first.pop('content')
    first.update(content_fragments=[{'start':0,'end':7,'text':'公開 @Alp'},
        {'start':7,'end':9,'text':'ha'}],content_characters=9,complete_text_sent=True)
    follow=profile('S086',text='@Alpha in observed following')
    follow['metadata']['research_report_source_role']='visible_follow_list'
    news=profile('S087',text='public article @Alpha');news['metadata']={'content_kind':'public_article_body'}
    result=quality().deterministic_report_inventory([first,follow,news],context())
    assert len(result['profile_sources'])==1
    assert result['profile_sources'][0]['source_text_complete']
    assert result['profile_handle_mentions'][0]['quote']=='@Alpha'


def test_incomplete_segments_never_claim_full_profile_or_join_gap_to_invent_handle():
    row=profile(text='');row.pop('content')
    row.update(content_fragments=[{'start':0,'end':7,'text':'公開 @Alp'},
        {'start':20,'end':22,'text':'ha'}],content_characters=22,complete_text_sent=False)
    result=quality().deterministic_report_inventory([row],context())
    assert not result['profile_sources'][0]['source_text_complete']
    assert result['profile_sources'][0]['saved_profile_text'] is None
    assert not any(row['quote']=='@Alpha' for row in result['profile_handle_mentions'])


def test_negative_mention_claim_with_both_actors_and_exact_bio_is_only_reconciliation_advisory():
    rows=[profile()];ctx=context();inventory=quality().deterministic_report_inventory(rows,ctx)
    text='twitter:alpha 与 twitter:west 都有简介，但没有二者之间的关注、提及或共同链接记录。[S085]'
    issues=quality().report_quality_issues(text,rows,ctx,inventory)
    assert len(issues)==1 and issues[0]['needs_reconciliation']
    assert issues[0]['citation_id']=='S085' and issues[0]['quote']=='@Alpha'
    assert issues[0]['source_key']=='twitter:west' and issues[0]['target_key']=='twitter:alpha'
    assert issues[0]['semantic_truth_established'] is False and issues[0]['blocking'] is False


@pytest.mark.parametrize('text',[
    'twitter:west 简介提及 @Alpha，不能据此确认组织隶属。[S085]',
    'twitter:someone 与 twitter:unrelated 没有提及记录。[S085]',
    'twitter:alpha 与 twitter:west 没有正式隶属核验。[S085]'])
def test_positive_mention_unrelated_actor_or_affiliation_insufficiency_is_not_negative_mention_issue(text):
    rows=[profile()];ctx=context()
    assert not quality().report_quality_issues(text,rows,ctx,quality().deterministic_report_inventory(rows,ctx))


def test_shared_display_name_alone_does_not_identify_two_actors():
    ctx=context();ctx['accounts'][0]['name']=ctx['accounts'][1]['name']='同名公开组织'
    rows=[profile()]
    assert not quality().report_quality_issues('同名公开组织之间没有提及。',rows,ctx,
        quality().deterministic_report_inventory(rows,ctx))


def test_advisories_are_bounded_and_overflow_is_explicit_not_silent():
    rows=[profile(f'S{i:03}',f'twitter:source{i}',text='公开简介 @Alpha') for i in range(1,81)]
    ctx=context();ctx['accounts'] += [{'account_key':row['account_key']} for row in rows]
    text='、'.join(row['account_key'] for row in rows)+' 与 twitter:alpha 没有提及记录。'
    issues=quality().report_quality_issues(text,rows,ctx,quality().deterministic_report_inventory(rows,ctx))
    assert len(issues)<=65
    assert issues[-1]['code']=='quality_advisory_limit' and issues[-1]['omitted_count']>0
    assert all(len(row.get('excerpt',''))<=600 for row in issues)


def test_actual_english_source_role_catalog_ranges_normalize_to_known_saved_ids():
    rows=[profile(f'S{i:03}',text=f'不同保存文本{i}') for i in range(1,104)]
    text=('就来源角色看，S001 至 S080 主要是 saved_public_material，'
          'S081 至 S086、S102、S103 是 public_profile，S087 至 S101 是 visible_follow_list。')
    rendered=report_analysis.render_citations(text,rows)
    assert all(row['url'] in rendered for row in rows)
    assert not [row for row in report_analysis.report_audit(text,rows)['issues'] if row['blocking']]


@pytest.mark.parametrize('text',[
    'S001至S004 是 public_profile。','S003至S001 是 public_profile。',
    'S001至S003 是 public_profile。\n\n会议举行1000人参与。','S001至S002 是 definitely_verified_organization。'])
def test_english_role_catalog_does_not_legalize_missing_reversed_unknown_role_or_uncited_fact(text):
    with pytest.raises(PipelineError):report_analysis.render_citations(text,[profile('S001'),profile('S003')])


@pytest.mark.parametrize('text',[
    'S001至S003 是 public_profile。\n\n会议举行1000人参与。',
    'S001至S003 是 definitely_verified_organization。'])
def test_known_complete_range_does_not_exempt_uncited_fact_or_unprovided_role(text):
    with pytest.raises(PipelineError):report_analysis.render_citations(text,[profile(f'S{i:03}') for i in range(1,4)])


def test_other_platform_and_unknown_source_do_not_bind_literal_to_twitter_identity():
    rows=[profile(key='mastodon:west'),profile('S086',key=None)]
    inventory=quality().deterministic_report_inventory(rows,context())
    assert len(inventory['profile_handle_mentions'])==2
    assert all(row['target_key'] is None for row in inventory['profile_handle_mentions'])


@pytest.mark.parametrize('field,value',[
    ('quote','@Invented'),('source_key','twitter:someone'),('target_key','twitter:someone'),
    ('target_handle','@Invented'),('basis','verified_affiliation'),('citation_id','S999')])
def test_injected_inventory_cannot_create_reconciliation_evidence(field,value):
    rows=[profile()];ctx=context();inventory=quality().deterministic_report_inventory(rows,ctx)
    inventory['profile_handle_mentions'][0][field]=value
    assert not quality().report_quality_issues('twitter:west 与 twitter:alpha 没有提及记录。',rows,ctx,inventory)


def material(identity, key=None, source_key=None, source_name=None, **extra):
    return {'citation_id':identity,'account_key':key,'source_key':source_key,'source_name':source_name,
        'url':f'https://public.example/{identity}','content':'保存原帖正文。',
        'metadata':{'research_report_source_role':'saved_public_material'},**extra}


def test_budget_skip_raw_count_is_preserved_but_never_a_unique_account_count():
    ctx=context();before=copy.deepcopy(ctx)
    scope=quality().deterministic_report_inventory([],ctx)['collection_scope']
    assert scope['account_budget_skipped']==49 and scope['summary']==ctx['summary']
    assert scope['budget_skip_count_not_unique_accounts'] is True
    assert scope['account_budget_skipped_uniqueness']=='uniqueness_not_established'
    assert scope['account_budget_skipped_count_unit']=='admission_attempts'
    assert 'skipped_account_urls' not in scope and ctx==before


@pytest.mark.parametrize('counts',[(20,20,20,15,5),(4,2,1,1,1)])
def test_only_saved_material_roles_contribute_to_dynamic_publisher_counts_and_top4(counts):
    rows=[]
    for index,count in enumerate(counts):
        rows += [material(f'S{len(rows)+serial:03}',key=f'twitter:publisher{index}') for serial in range(1,count+1)]
    original=copy.deepcopy(rows)
    follow=material('S900',key='twitter:publisher4');follow['metadata']['research_report_source_role']='visible_follow_list'
    rows += [profile('S901'),follow,{'citation_id':'S902','source_name':'untyped','content':'未分配来源角色。'}]
    result=quality().deterministic_report_inventory(rows,context())['saved_public_material_distribution']
    assert result['total_material_count']==sum(counts)
    assert result['top4_count']==sum(sorted(counts,reverse=True)[:4])
    assert result['top4_share']==pytest.approx(result['top4_count']/sum(counts))
    assert sum(group['count'] for group in result['groups'])==sum(counts)
    assert set(identity for group in result['groups'] for identity in group['citation_ids'])=={row['citation_id'] for row in original}
    assert not result['independence_established'] and not result['semantic_truth_established']
    assert rows[:sum(counts)]==original


def test_publisher_grouping_uses_account_then_source_key_then_literal_source_name():
    rows=[material('S001','twitter:Alpha','twitter:different','共享显示名'),
          material('S002','twitter:alpha',source_name='另一个显示名'),
          material('S003',source_key='twitter:beta',source_name='共享显示名'),
          material('S004',source_name='共享显示名'),
          material('S005',source_name='共享显示名'),
          material('S006',key='mastodon:Alpha'),material('S007',key='mastodon:alpha')]
    groups=quality().deterministic_report_inventory(rows,{})['saved_public_material_distribution']['groups']
    by_key={group['publisher_key']:group for group in groups}
    assert by_key['twitter:alpha']['count']==2 and by_key['twitter:alpha']['citation_ids']==['S001','S002']
    assert by_key['twitter:alpha']['grouping_basis']=='account_key'
    assert by_key['twitter:alpha']['source_names']==['共享显示名','另一个显示名']
    assert by_key['twitter:beta']['grouping_basis']=='source_key'
    assert by_key['共享显示名']['count']==2 and by_key['共享显示名']['grouping_basis']=='source_name'
    assert by_key['mastodon:Alpha']['count']==by_key['mastodon:alpha']['count']==1
    assert all(not group['identity_verified'] for group in groups)


def test_missing_publishers_remain_separate_unknown_records_and_empty_share_is_undefined():
    empty=quality().deterministic_report_inventory([profile()],{})['saved_public_material_distribution']
    assert empty['total_material_count']==empty['top4_count']==0 and empty['top4_share'] is None
    rows=[material('S001'),material('S002')]
    result=quality().deterministic_report_inventory(rows,{})['saved_public_material_distribution']
    assert len(result['groups'])==2 and result['unknown_publisher_material_count']==2
    assert all(group['grouping_basis']=='unknown_per_source' for group in result['groups'])


def test_explicit_profile_role_takes_priority_over_misleading_material_type():
    row=profile(type='saved_public_material')
    inventory=quality().deterministic_report_inventory([row],context())
    assert len(inventory['profile_sources'])==1
    assert inventory['saved_public_material_distribution']['total_material_count']==0
