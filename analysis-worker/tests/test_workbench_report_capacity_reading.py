import asyncio
import copy

import pytest

from app.workbench import report_analysis as reports
from app.workbench.pipeline import PipelineError


def source(identity='S001',content='公开来源完整文字。',**extra):
    return {'citation_id':identity,'title':'公开材料','source_name':'保存来源',
            'url':f'https://public.example/{identity}','content':content,**extra}


def model_response(sources):
    return {'source_reviews':[{'citation_id':row['citation_id'],
        'quote':next((part['text'][:40] for part in row['content_fragments'] if part['text'].strip()),row['title']),
        'reading':'公开输入记载该信息，保留来源层级。','limitations':'仍须区分来源自称、观察日期和关系含义。'} for row in sources]}


def test_no_global_or_source_text_omission_and_middle_reversal_is_supplied():
    body='开头公开来源。'*2000+'中段明确更正，不能把计划视为已发生。'+'末尾公开说明。'*4000
    inputs=[source(f'S{i:03}',body) for i in range(1,31)]
    samples,coverage=reports.source_samples(inputs)
    assert sum(row['sent_characters'] for row in coverage)==sum(len(row['content']) for row in inputs)
    assert all(row['complete_text_sent'] and not row['omitted_ranges'] for row in coverage)
    assert all(''.join(part['text'] for part in row['content_fragments'])==body for row in samples)


def test_typed_observed_follow_information_and_goal_reach_every_reading(monkeypatch):
    row=source('S087','公开可见列表：twitter:alpha → twitter:beta',type='research_evidence',
        metadata={'kind':'visible_follow_list','source_key':'twitter:alpha','target_key':'twitter:beta',
                  'relation_type':'follows','observed_at':'2026-10-09T00:00:00Z',
                  'research_report_source_role':'observed_relation'})
    samples,_=reports.source_samples([row])
    captured=[]
    async def model(system,data,**options):
        captured.append((system,copy.deepcopy(data)))
        assert data['research_context']['research_goal']=='比较公开观察关系与自述角色'
        sent=data['sources'][0]
        assert sent['evidence_kind']=='visible_follow_list'
        assert sent['source_key']=='twitter:alpha' and sent['target_key']=='twitter:beta'
        assert sent['relation_type']=='follows' and sent['observed_at']=='2026-10-09T00:00:00Z'
        assert '不代表隶属' in system and '观察' in system
        return model_response(data['sources'])
    monkeypatch.setattr(reports,'configured_ai_json',model)
    state=asyncio.run(reports.read_sources(samples,research_context={'research_goal':'比较公开观察关系与自述角色'},return_state=True))
    assert state['complete'] and captured


def test_distinct_follow_targets_same_following_url_are_distinct_evidence():
    rows=[source('S001','公开可见列表：twitter:alpha → twitter:beta',url='https://x.com/alpha/following',
                 metadata={'kind':'visible_follow_list','source_key':'twitter:alpha','target_key':'twitter:beta','relation_type':'follows','observed_at':'2026-10-09'}),
          source('S002','公开可见列表：twitter:alpha → twitter:gamma',url='https://x.com/alpha/following',
                 metadata={'kind':'visible_follow_list','source_key':'twitter:alpha','target_key':'twitter:gamma','relation_type':'follows','observed_at':'2026-10-09'})]
    dependencies=reports.source_dependencies(rows)
    assert dependencies['S001']['support_group_id']!=dependencies['S002']['support_group_id']


def test_known_multi_citations_ranges_bare_scope_and_four_digit_ids_resolve():
    rows=[source(f'S{i:03}') for i in range(1,5)]+[source('S1000')]
    text='本报告覆盖资料S001至S004。\n\n来源说法存在差异。[S001–S003；S004] 另有保存材料。[S1000]'
    rendered=reports.render_citations(text,rows)
    assert all(row['url'] in rendered for row in rows)
    assert 'S001至S004' not in rendered


@pytest.mark.parametrize('text',['来源依据。[S001-S005]','范围S001至S003。','范围[S004-S001]。','来源依据S099。'])
def test_unknown_missing_middle_or_reversed_range_never_legalized(text):
    rows=[source('S001'),source('S003'),source('S004')]
    with pytest.raises(PipelineError):reports.render_citations(text,rows)


def test_fragment_failure_keeps_successful_ranges_and_retry_only_reads_pending(monkeypatch):
    body='第一个片段正文资料。'*3000+'末段明确反证资料。'*3000
    samples,_=reports.source_samples([source(content=body)])
    calls=[]
    async def fail_second(system,data,**options):
        calls.append(copy.deepcopy(data['sources']))
        if len(calls)==2:raise PipelineError('隔离片段失败')
        return model_response(data['sources'])
    monkeypatch.setattr(reports,'READ_BATCH_SOURCES',1)
    monkeypatch.setattr(reports,'configured_ai_json',fail_second)
    state=asyncio.run(reports.read_sources(samples,return_state=True))
    assert not state['complete'] and state['pending_citation_ids']==['S001']
    assert state['fragment_reviews'] and not state['source_reviews']
    completed={row['unit_id'] for row in state['fragment_reviews']}
    calls.clear()
    async def model(system,data,**options):
        calls.append(copy.deepcopy(data['sources']))
        assert not completed.intersection(row['citation_id'] for row in data['sources'])
        return model_response(data['sources'])
    monkeypatch.setattr(reports,'configured_ai_json',model)
    resumed=asyncio.run(reports.read_sources(samples,previous_review=state,return_state=True))
    assert resumed['complete'] and len(resumed['source_reviews'])==1
    aggregate=resumed['source_reviews'][0]
    assert aggregate['complete_saved_text_reviewed'] and len(aggregate['fragment_reviews'])==len(samples[0]['content_fragments'])
    assert sum(row['end']-row['start'] for row in aggregate['reviewed_ranges'])==len(body)
    assert all(row['quote'] in body[row['start']:row['end']] for row in aggregate['fragment_reviews'])


def test_goal_change_invalidates_reading_checkpoint(monkeypatch):
    samples,_=reports.source_samples([source()])
    calls=[]
    async def model(system,data,**options):
        calls.append(data)
        return model_response(data['sources'])
    monkeypatch.setattr(reports,'configured_ai_json',model)
    state=asyncio.run(reports.read_sources(samples,research_context={'research_goal':'目标甲'},return_state=True))
    calls.clear()
    assert asyncio.run(reports.read_sources(samples,previous_review=state,research_context={'research_goal':'目标甲'},return_state=True))['complete']
    assert not calls
    assert asyncio.run(reports.read_sources(samples,previous_review=state,research_context={'research_goal':'目标乙'},return_state=True))['complete']
    assert calls


def test_thousand_same_text_sources_preserve_one_full_dependency_table_and_every_input():
    inputs=[source(f'S{i:03}',content='明确原文同稿，不能当作独立核验。') for i in range(1,1001)]
    dependencies=reports.source_dependencies(inputs)
    first=dependencies['S001']['overlapping_citation_ids']
    assert len(first)==1000 and all(row['overlapping_citation_ids'] is first for row in dependencies.values())
    samples,coverage=reports.source_samples(inputs)
    assert len(samples)==1000 and all(row['complete_text_sent'] for row in coverage)
    assert sum(len(row['source_dependency'].get('overlapping_citation_ids',[])) for row in samples)==1000
    context=reports.report_context(samples,coverage)
    assert len(context['overlapping_support_groups'])==1
    assert context['overlapping_support_groups'][0]['overlapping_citation_ids']==first
    assert all(row['source_dependency']['overlap_count']==1000 for row in samples)


@pytest.mark.parametrize('field,value',[('target_key','twitter:gamma'),('observed_at','2026-10-10T00:00:00Z'),('relation_type','mentions')])
def test_only_identical_directed_observation_at_same_time_is_duplicate(field,value):
    first=source(metadata={'research_report_source_role':'visible_follow_list','source_key':'twitter:alpha',
        'target_key':'twitter:beta','relation_type':'follows','observed_at':'2026-10-09T00:00:00Z'})
    second=copy.deepcopy(first);second['citation_id']='S002';second['metadata'][field]=value
    duplicate=copy.deepcopy(first);duplicate['citation_id']='S003'
    dependencies=reports.source_dependencies([first,second,duplicate])
    assert dependencies['S001']['support_group_id']==dependencies['S003']['support_group_id']
    assert dependencies['S001']['support_group_id']!=dependencies['S002']['support_group_id']
    assert dependencies['S001']['overlap_basis']==['identical_observed_relationship']


def test_legacy_review_without_goal_or_full_fragment_proof_is_not_reused(monkeypatch):
    samples,_=reports.source_samples([source(content='完整正文中有更正，旧阅读不能证明目标相关。')])
    raw=model_response(samples)
    accepted=reports.validate_source_reviews(raw,samples)
    previous={'version':2,'source_reviews':accepted,'reviewed_source_fingerprints':{'S001':reports._digest(samples[0])}}
    calls=[]
    async def model(system,data,**options):
        calls.append(data)
        return model_response(data['sources'])
    monkeypatch.setattr(reports,'configured_ai_json',model)
    state=asyncio.run(reports.read_sources(samples,previous_review=previous,research_context={'research_goal':'检视更正'},return_state=True))
    assert state['complete'] and state['resumed_source_count']==0 and calls


def test_incomplete_input_ranges_never_become_full_reading(monkeypatch):
    samples,_=reports.source_samples([source(content='开头正文。'+'未提供的中段。'*3000+'末尾否认。')])
    row=samples[0];last=row['content_fragments'][-1]
    row['content_fragments']=[row['content_fragments'][0],last]
    row['complete_text_sent']=False
    async def model(system,data,**options):return model_response(data['sources'])
    monkeypatch.setattr(reports,'configured_ai_json',model)
    state=asyncio.run(reports.read_sources(samples,return_state=True))
    assert not state['complete'] and not state['source_reviews']
    assert state['stop_reason']=='source_text_incomplete' and state['pending_citation_ids']==['S001']


def test_fragment_quote_cannot_borrow_another_fragment_or_source(monkeypatch):
    body='开头来源文字。'*2000+'只在最后片段出现的独特更正。'
    samples,_=reports.source_samples([source(content=body)])
    async def model(system,data,**options):
        result=model_response(data['sources'])
        result['source_reviews'][0]['quote']='只在最后片段出现的独特更正。'
        return result
    monkeypatch.setattr(reports,'configured_ai_json',model)
    state=asyncio.run(reports.read_sources(samples,return_state=True))
    assert not state['complete'] and not state['fragment_reviews'] and state['stop_reason']=='source_read_error'


@pytest.mark.parametrize('text',['事实没有引用，会议已经举行五百人参与。',
    '报道依据S001。','出处[S001-S003-S004]。'])
def test_range_normalization_does_not_legalize_uncited_fact_or_ambiguous_expression(text):
    with pytest.raises(PipelineError):reports.render_citations(text,[source(f'S{i:03}') for i in range(1,5)])


def test_actual_saved_report_source_catalog_and_dependency_index_ranges_normalize():
    rows=[source(f'S{i:03}',content=f'不同公开保存文字{i}') for i in range(1,104)]
    text=('材料中大量帖子日期明确，但多为单一平台自述。S001 至 S080 主要是已保存公开帖文，'
          'S081 至 S086、S102、S103 是公开简介，S087 至 S101 是可见关系列表 [S001][S081]。\n\n'
          '同时，同支持组重叠的 S087 至 S091、S094 至 S095、S096 至 S097、'
          'S098 至 S099、S100 至 S101 不能当作彼此独立的交叉证据。[S087]')
    audit=reports.report_audit(text,rows)
    assert not [row for row in audit['issues'] if row['blocking']]
    assert set(audit['referenced_citation_ids'])=={row['citation_id'] for row in rows}
    assert 'https://public.example/S103' in reports.render_citations(text,rows)


def test_actual_metadata_role_fallback_preserves_observed_follow_and_date_scope(monkeypatch):
    row=source('S087','公开可见列表：twitter:alpha → twitter:beta',
        metadata={'research_report_source_role':'visible_follow_list','source_key':'twitter:alpha',
                  'target_key':'twitter:beta','relation_type':'follows','observed_at':'2026-10-08T23:01:00Z'})
    samples,_=reports.source_samples([row])
    async def model(system,data,**options):
        assert data['sources'][0]['evidence_kind']=='visible_follow_list'
        assert data['sources'][0]['target_key']=='twitter:beta'
        assert data['research_context']['date_scope']=={'start_date':'2026-10-01','end_date':'2026-10-09'}
        assert 'Asia/Tokyo(JST)' in data['research_context']['time_rules']
        assert '旧句柄无法登录不代表当前另一句柄不可用' in system
        return model_response(data['sources'])
    monkeypatch.setattr(reports,'configured_ai_json',model)
    state=asyncio.run(reports.read_sources(samples,research_context={'research_goal':'实际公开关注方向',
        'research_params':{'start_date':'2026-10-01','end_date':'2026-10-09'}},return_state=True))
    assert state['complete'] and state['source_reviews'][0]['quote_verification_scope']=='exact_quote_match_only'


def test_same_sentence_explicit_citation_allows_source_id_mention_but_not_cross_sentence():
    rows=[source('S073'),source('S077')]
    text=('S073 仅是外部新闻标题式转述，未提供报道正文，不能支持标题之外的事实 [S073]。'
          'S077 也只是标题式陈述，缺少统计机构、定义和时间范围 [S077]。')
    assert not [row for row in reports.report_audit(text,rows)['issues'] if row['blocking']]
    assert 'https://public.example/S073' in reports.render_citations(text,rows)
    with pytest.raises(PipelineError):reports.render_citations('报道依据S073。另一个判断引用。[S073]',rows)
    with pytest.raises(PipelineError):reports.render_citations('S099 的标题资料。[S073]',rows)


def test_chinese_numbered_pure_heading_and_date_definitions_are_not_uncited_facts():
    rows=[source()]
    text=('三、关键说法与时间线，区分报道时间、活动时间和计划时间\n\n'
          '以下“发布日期”指帖子发布时间；“活动时间”指帖子所述已发生或正在发生的行动时间；'
          '“计划时间”指帖子号召的未来安排，均不预测是否实际举行。\n\n来源材料。[S001]')
    assert not [row for row in reports.report_audit(text,rows)['issues'] if row['blocking']]
    assert reports.render_citations(text,rows)


@pytest.mark.parametrize('text',[
    '以下“发布日期”指帖子发布时间；已有1000人参与会议。',
    '三、会议已经举行，1000人参与。',
    '其五，覆盖停止原因是 account_budget，整体 partial。没有相反立场材料，不等于事件不存在；缺少报道也不等于事实成立。'])
def test_pure_format_exceptions_never_exempt_other_uncited_assertions(text):
    with pytest.raises(PipelineError):reports.render_citations(text,[source()])
