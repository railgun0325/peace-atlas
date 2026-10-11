"""Candidate identity boundaries; the quoted fixtures mirror saved public text."""
import asyncio
import copy
import json
import sqlite3

import pytest

from app.workbench import relationship_analysis as relationships
from app.workbench.relationship_identity import merge_candidate_entries, prepare_candidate


def account(handle, bio):
    return {'account_key': f'twitter:{handle}', 'name': handle, 'profile': {
        'verified_in_dom': True, 'url': f'https://x.com/{handle}/',
        'bio': bio, 'observed_at': '2026-10-09T00:00:00Z'}}


def ai_rows(result):
    return [row for row in result['affiliations'] if row['method'] == 'configured_ai']


def review(monkeypatch, accounts, variants, items=None, previous=None):
    calls = []
    async def provider(system, data, **options):
        calls.append(copy.deepcopy(data))
        candidates = []
        for label, claim, handle in variants:
            own = next(row for row in data['evidence'] if row['account_key'] == f'twitter:{handle}' and row['type'] == 'profile')
            ids = [own['id']]
            if '全学連' in label:
                ids += [row['id'] for row in data['evidence'] if row['type'] == 'post' and row['account_key'] == f'twitter:{handle}'][:5]
            candidates.append({'account_key': f'twitter:{handle}', 'organization': label,
                               'claim': claim, 'evidence_ids': ids})
        return {'affiliation_candidates': candidates}
    monkeypatch.setattr(relationships, 'configured_ai_json', provider)
    result = asyncio.run(relationships.analyze_relationships_with_ai(accounts, items or [], [],
        use_ai=True, previous_analysis=previous))
    return result, calls


def test_saved_outer_quote_pair_merges_and_retains_both_original_reasons(monkeypatch):
    accounts = [account('horaguchitomoko', '洞口朋子 / 杉並区議会議員 / 「改憲・戦争阻止！大行進」呼びかけ人 / お問い合わせ')]
    variants = [('「改憲・戦争阻止！大行進」', '简介自述：『「改憲・戦争阻止！大行進」呼びかけ人』。候选隶属/角色：该行动呼びかけ人。', 'horaguchitomoko'),
                ('改憲・戦争阻止！大行進', '候选理由：简介自述“「改憲・戦争阻止！大行進」呼びかけ人”，候选为该运动/集会呼びかけ人，待核对。', 'horaguchitomoko')]
    result, calls = review(monkeypatch, accounts, variants)
    rows = ai_rows(result)
    assert len(rows) == 1
    assert rows[0]['organization'] == variants[0][0]
    assert {row['model_claim'] for row in rows[0]['identity_variants']} == {v[1] for v in variants}
    assert rows[0]['identity_merge_audit'][0]['method'] == 'decorative_outer_quotes'
    resumed, calls = review(monkeypatch, accounts, [], previous=result)
    assert not calls and resumed['ai']['calls_made'] == 0
    assert ai_rows(resumed) == rows


def test_saved_full_name_alias_pair_requires_own_explicit_source_and_preserves_leader(monkeypatch):
    accounts = [account('zengakuren', '全日本学生自治会総連合（矢嶋委員長）▶︎全国で学生運動')]
    items = [{'account_key': 'twitter:zengakuren', 'url': 'https://x.com/zengakuren/status/2094040185969238521',
              'content': '全日本学生自治会総連合（全学連）は2026年11月14日（土）-15日（日）、第87回定期全国大会を開催します。'}]
    variants = [('全日本学生自治会総連合（全学連）', '简介自述：『全日本学生自治会総連合（矢嶋委員長）』。候选隶属：全日本学生自治会総連合（全学連），保留矢嶋委員長限定。', 'zengakuren'),
                ('全日本学生自治会総連合', '候选理由：简介自述“全日本学生自治会総連合（矢嶋委員長）”，候选为全日本学生自治会総連合，委员长记为矢嶋；待核对。', 'zengakuren')]
    result, _ = review(monkeypatch, accounts, variants, items)
    rows = ai_rows(result)
    assert len(rows) == 1
    assert len(rows[0]['identity_variants']) == 2 and len(rows[0]['evidence_ids']) == 2
    assert rows[0]['identity_merge_audit'][0]['method'] == 'explicit_source_alias'
    assert any('矢嶋' in qualifier for qualifier in rows[0]['identity_qualifiers'])
    resumed, calls = review(monkeypatch, accounts, [], items, previous=result)
    assert not calls and ai_rows(resumed) == rows


def helper_row(label, claim='本人公开文本所述组织，仍待独立核对。', *, subject='twitter:alpha', ids=None):
    return {'id':relationships.stable_id('ai-affiliation',subject,label.casefold()), 'account_key':subject,
        'organization':label,'claim':claim,'evidence_ids':ids or ['own'],
        'evidence_urls':[f'https://x.com/{subject.split(":")[1]}/'],
        'status':'candidate','method':'configured_ai','requires_review':True}


def sources(text='所属：团体A。'):
    return {'own':{'id':'own','type':'profile','account_key':'twitter:alpha',
                   'url':'https://x.com/alpha/','excerpt':text}}


@pytest.mark.parametrize('left,right',[
    ('团体A（関西）','团体A'), ('团体A（関西）','团体A（関東）'),
    ('团体A（田中派）','团体A（矢嶋派）'), ('团体A（旧執行部）','团体A'),
    ('团体A（矢嶋委員長）','团体A（田中委員長）'),
    ('团体A（支部）','团体A'), ('現代革命労働者党','機関紙「碇星」'),
    ('中核派','中核派机关报《前進》'), ('团体A（未知简称）','团体A'),
    ('A連合','A联合'), ('大行進','改憲・戦争阻止！大行進'), ('ABC','abc'),
    ('「团体A」及「团体B」','团体A」及「团体B'),
])
def test_no_fuzzy_or_qualifier_or_party_publication_collapse(left,right):
    evidence=sources(left+' '+right)
    assert merge_candidate_entries(helper_row(left),helper_row(right),evidence) is None


def test_known_alias_still_requires_explicit_own_full_name_pair():
    evidence=sources('全日本学生自治会総連合です。全学連について発信します。')
    assert merge_candidate_entries(helper_row('全日本学生自治会総連合'),helper_row('全学連'),evidence) is None
    evidence['other']={'id':'other','type':'profile','account_key':'twitter:other',
                       'excerpt':'全日本学生自治会総連合（全学連）'}
    assert merge_candidate_entries(helper_row('全日本学生自治会総連合',ids=['own','other']),
        helper_row('全学連',ids=['own','other']),evidence) is None


def test_own_explicit_known_alias_can_merge_bare_abbreviation():
    evidence=sources('全日本学生自治会総連合（全学連）です。')
    merged=merge_candidate_entries(helper_row('全日本学生自治会総連合'),helper_row('全学連'),evidence)
    assert merged and len(merged['identity_variants'])==2
    assert merged['identity_merge_audit'][0]['alias_proof_evidence_ids']==['own']


def test_quote_variants_need_common_own_citation_and_same_subject():
    evidence=sources()
    evidence['second']={**evidence['own'],'id':'second'}
    assert merge_candidate_entries(helper_row('「团体A」'),helper_row('团体A',ids=['second']),evidence) is None
    assert merge_candidate_entries(helper_row('团体A'),helper_row('团体A',ids=['second']),evidence) is None
    evidence['other']={**evidence['own'],'id':'other','account_key':'twitter:other'}
    assert merge_candidate_entries(helper_row('「团体A」'),helper_row('团体A',subject='twitter:other',ids=['other']),evidence) is None


@pytest.mark.parametrize('status',['confirmed','rejected'])
def test_identity_helper_does_not_merge_reviewed_human_records(status):
    existing=helper_row('团体A');existing['status']=status
    assert merge_candidate_entries(existing,helper_row('「团体A」'),sources()) is None


def test_same_bare_label_conflicting_claim_leaders_is_rejected_with_original_audit(monkeypatch):
    accounts=[account('alpha','团体A（矢嶋委員長）。团体A（田中委員長）。')]
    variants=[('团体A','简介自述团体A（矢嶋委員長）。','alpha'),
              ('团体A','简介自述团体A（田中委員長）。','alpha')]
    result,_=review(monkeypatch,accounts,variants)
    assert len(ai_rows(result))==1 and len(ai_rows(result)[0]['identity_variants'])==1
    rejected=result['ai']['rejected_candidates']
    assert result['ai']['rejected_candidate_count']==1 and rejected[0]['claim']==variants[1][1]
    assert rejected[0]['evidence_urls']==['https://x.com/alpha/'] and rejected[0]['review_batch_id']
    assert '限定不一致' in rejected[0]['reason']
    resumed,calls=review(monkeypatch,accounts,[],previous=result)
    assert not calls and resumed['ai']['rejected_candidates']==rejected


def test_quote_variant_claim_only_region_role_or_publication_qualifier_is_not_laundered():
    evidence=sources('所属：团体A。関西、関東の集会を紹介。')
    for first,second in [('候选为团体A（関西）','候选为团体A（関東）'),
                         ('团体A（矢嶋委員長）','团体A（田中委員長）'),
                         ('团体A所属候选','团体A機関紙紹介候选')]:
        assert merge_candidate_entries(helper_row('「团体A」',first),helper_row('团体A',second),evidence) is None


def test_cross_batch_union_beyond_six_and_all_variants_survive_completed_resume(monkeypatch):
    accounts=[account('alpha','所属：团体A。')]
    items=[{'account_key':'twitter:alpha','url':f'https://x.com/alpha/status/{i+1}',
            'content':f'团体A公开记录{i+1}。'} for i in range(150)]
    supplied=[]
    async def provider(system,data,**options):
        own=next(row for row in data['evidence'] if row['type']=='profile')
        ids=[own['id']]+[row['id'] for row in data['evidence'] if row['type']=='post'][:5]
        supplied.append(ids)
        return {'affiliation_candidates':[{'account_key':'twitter:alpha','organization':'团体A',
            'claim':'本人公开资料说明团体A，仍待核对。','evidence_ids':ids}]}
    monkeypatch.setattr(relationships,'configured_ai_json',provider)
    result=asyncio.run(relationships.analyze_relationships_with_ai(accounts,items,[],use_ai=True))
    row=ai_rows(result)[0]
    assert len(supplied)==2 and len(row['evidence_ids'])==11
    assert set(row['evidence_ids'])==set().union(*map(set,supplied))
    assert len(row['identity_variants'])==2 and all(len(v['evidence_ids'])==6 for v in row['identity_variants'])
    assert len(row['review_batch_ids'])==2
    async def forbidden(*args,**kwargs): raise AssertionError('completed source review must not call provider')
    monkeypatch.setattr(relationships,'configured_ai_json',forbidden)
    resumed=asyncio.run(relationships.analyze_relationships_with_ai(accounts,items,[],use_ai=True,previous_analysis=result))
    assert resumed['ai']['calls_made']==0 and ai_rows(resumed)==[row]


def test_repeated_identical_variant_only_accumulates_batch_provenance():
    evidence=sources()
    first=prepare_candidate(helper_row('团体A'),evidence,review_batch_ids=['batch-1'])
    second=prepare_candidate(helper_row('团体A'),evidence,review_batch_ids=['batch-2'])
    merged=merge_candidate_entries(first,second,evidence)
    assert len(merged['identity_variants'])==1 and not merged['identity_merge_audit']
    assert merged['identity_variants'][0]['review_batch_ids']==['batch-1','batch-2']


def test_new_variant_raw_claim_and_corrections_remain_separate_on_resume(monkeypatch):
    accounts=[account('alpha','正在YouTube上介绍团体A机关报的版面。')]
    variants=[('「团体A」','简介自述介绍团体A机关报版面，不能称官方。','alpha'),
              ('团体A','简介介绍团体A机关报版面；官方频道仍待核对。','alpha')]
    result,_=review(monkeypatch,accounts,variants)
    assert len(ai_rows(result))==1
    row=ai_rows(result)[0]
    assert row['support_kind']=='publication_content_reference'
    assert {v['model_claim'] for v in row['identity_variants']}=={v[1] for v in variants}
    assert all(v['support_kind']=='publication_content_reference' and v['claim_corrections'] for v in row['identity_variants'])
    resumed,calls=review(monkeypatch,accounts,[],previous=result)
    assert not calls and ai_rows(resumed)==[row]


@pytest.mark.parametrize('status',['confirmed','rejected'])
@pytest.mark.parametrize('reviewed_label',['「团体A」','团体A'])
def test_manual_exact_label_record_stays_stable_and_automatic_variants_are_observations(tmp_path,monkeypatch,status,reviewed_label):
    from app.workbench.store import Store
    accounts=[account('alpha','所属：「团体A」。')]
    variants=[('「团体A」','本人简介自述团体A，待核对。','alpha'),
              ('团体A','公开简介支持团体A候选，仍需核对。','alpha')]
    result,_=review(monkeypatch,accounts,variants)
    merged=ai_rows(result)[0]
    legacy=copy.deepcopy(result)
    legacy['affiliations']=[{**v,'status':'candidate','method':'configured_ai','requires_review':True}
                            for v in merged['identity_variants']]
    def claims(rows):
        return [{**row,'subject_key':row['account_key'],'relationship':'affiliation',
                 'object_label':row['organization'],'basis':'inferred','statement':row['claim']} for row in rows]
    store=Store(tmp_path/'identity-manual.sqlite3')
    try:
        job=store.create_job('research',{})
        run=store.create_research_run(job['id'],{})
        store.checkpoint_research(run['id'],nodes=[{'account_key':'twitter:alpha'}],
            analysis=legacy,claims=claims(legacy['affiliations']))
        old=next(row for row in store.get_research_run(run['id'])['claims'] if row['object_label']==reviewed_label)
        store.review_research_claim(run['id'],old['id'],status,note='人工原始审阅内容，不能转移到别名',
                                    evidence_url='https://example.org/independent-proof')
        reviewed=next(row for row in store.get_research_run(run['id'])['claims'] if row['id']==old['id'])
        store.checkpoint_research(run['id'],analysis=result,claims=claims([merged]),phase='analysis')
        saved=store.get_research_run(run['id'])
        current=next(row for row in saved['claims'] if row['id']==old['id'])
        assert all(current[field]==reviewed[field] for field in ['id','object_label','statement','status','review_note','review_evidence_url'])
        assert saved['analysis']['affiliations']==result['affiliations']
        assert saved['analysis']['ai']['identity_variant_count']==2
        with sqlite3.connect(store.path) as db:
            captures=[json.loads(record) for (record,) in db.execute('SELECT record FROM wb_research_claim_observations')]
            assert any(row.get('identity_variants')==merged['identity_variants'] for row in captures)
            assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
    finally:
        store.close()


def test_identity_storage_budget_is_pending_not_semantic_refutation_and_survives_resume(monkeypatch):
    accounts=[account('alpha','所属：团体A。')]
    catalogue=relationships.evidence_catalog(accounts,[],[])
    evidence={row['id']:row for row in catalogue}
    additions,_=relationships._validated_candidates({'affiliation_candidates':[{'account_key':'twitter:alpha',
        'organization':'团体A','claim':'本人公开文本称团体A。','evidence_ids':[catalogue[0]['id']]}]},
        {'twitter:alpha'},evidence)
    first=prepare_candidate(additions[0],evidence,review_batch_ids=['f'*64])
    first_size=len(json.dumps(first,ensure_ascii=False,separators=(',',':')).encode('utf-8'))
    monkeypatch.setattr(relationships,'CANDIDATE_BYTE_LIMIT',first_size+100)
    variants=[('团体A','本人公开文本称团体A。','alpha'),
              ('团体A','另一原始理由明确提及团体A；仍须人工核对。','alpha')]
    result,_=review(monkeypatch,accounts,variants)
    assert len(ai_rows(result))==1 and len(ai_rows(result)[0]['identity_variants'])==1
    assert result['ai']['complete'] and result['ai']['identity_complete'] is False
    assert result['ai']['identity_omitted_variant_count']==1
    omitted=result['ai']['rejected_candidates'][0]
    assert omitted['outcome']=='identity_serialization_budget' and omitted['pending'] is True
    assert omitted['claim']==variants[1][1] and omitted['evidence_ids'] and omitted['review_batch_id']
    assert omitted['candidate_id'] and omitted['evidence_urls']==['https://x.com/alpha/']
    assert omitted['proposed_candidate_bytes']>omitted['candidate_byte_limit']
    resumed,calls=review(monkeypatch,accounts,[],previous=result)
    assert not calls and resumed['ai']['calls_made']==0
    assert ai_rows(resumed)==ai_rows(result)
    assert resumed['ai']['identity_complete'] is False and resumed['ai']['identity_omitted_variant_count']==1
    assert resumed['ai']['rejected_candidates']==result['ai']['rejected_candidates']
    # A checkpoint revalidation event has no newly generated provider batch.
    # Its aggregate pending audit must still survive when older batch metadata
    # does not carry the later identity-specific counters.
    legacy=copy.deepcopy(result)
    for batch in legacy['ai']['review_batches']:
        batch.pop('identity_omitted_variant_count',None)
        batch['rejected_candidate_count']=0
    again,calls=review(monkeypatch,accounts,[],previous=legacy)
    assert not calls and again['ai']['rejected_candidate_count']==1
    assert again['ai']['identity_omitted_variant_count']==1 and again['ai']['identity_complete'] is False


def test_actual_104_council_quote_extra_campaign_role_does_not_change_council_identity():
    evidence=sources('洞口朋子 / 杉並区議会議員 / 「改憲・戦争阻止！大行進」呼びかけ人 / お問い合わせ')
    first=helper_row('杉並区議会','候选理由：简介自述“洞口朋子 / 杉並区議会議員 / 「改憲・戦争阻止！大行進」呼びかけ人”，其中职务限定为杉並区議会議員。')
    second=helper_row('杉並区議会','简介自述“洞口朋子 / 杉並区議会議員”，表明本人自称为杉並区議会議員；保留职务限定，待核对。')
    merged=merge_candidate_entries(first,second,evidence)
    assert merged is not None and len(merged['identity_variants'])==2


def test_actual_104_earthquake_place_is_not_organization_regional_qualifier():
    evidence=sources('全日本学生自治会総連合（矢嶋委員長）▶︎全国で学生運動')
    evidence['earthquake']={'id':'earthquake','type':'post','account_key':'twitter:alpha',
        'url':'https://x.com/i/status/2094262585310978514',
        'excerpt':'関東大震災から103年　９・１新宿反戦スタンディングへ | 全日本学生自治会総連合（矢嶋委員長）'}
    first=helper_row('全日本学生自治会総連合','候选理由：简介自述“全日本学生自治会総連合（矢嶋委員長）”，保留委员长名与组织全称限定。')
    second=helper_row('全日本学生自治会総連合','简介自述“全日本学生自治会総連合（矢嶋委員長）”，账号名“全学連”为其简称；保留委员长（矢嶋）限定，待核对。',ids=['own','earthquake'])
    merged=merge_candidate_entries(first,second,evidence)
    assert merged is not None and 'marker:関東' not in merged.get('identity_qualifiers',[])


@pytest.mark.parametrize('left,right',[
    ('保留矢嶋委員長限定','保留田中委員長限定'),
    ('派系：矢嶋派','派系：田中派'), ('限定为関西支部','限定为関東支部'),
])
def test_late_explicit_claim_qualifiers_are_preserved_outside_subject_clause(left,right):
    evidence=sources('所属：团体A。')
    assert merge_candidate_entries(helper_row('「团体A」','团体A候选，'+left),
        helper_row('团体A','团体A候选，'+right),evidence) is None


def test_alias_full_name_is_not_a_fragment_of_another_organization():
    evidence=sources('別団体全日本学生自治会総連合（全学連）です。')
    assert merge_candidate_entries(helper_row('全日本学生自治会総連合'),helper_row('全学連'),evidence) is None
