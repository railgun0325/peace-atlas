"""Local corrections keep unrelated findings literal and recheck all units."""
import asyncio,copy
import pytest
from app.workbench import report_synthesis as synthesis
from app.workbench.report_note_patch import revision_patch_targets,apply_note_patch
from app.workbench.report_compression_review import validate_note
from app.workbench.pipeline import PipelineError
from test_workbench_report_compression_review import SMALL,review
from test_workbench_report_synthesis_capacity import packet,compressed_note,SYSTEM,OUTPUT


def base_note(supplied):
    result=compressed_note(supplied)
    original=result['findings'][0]
    split=max(1,len(original['support_refs'])//2)
    left,right=copy.deepcopy(original),copy.deepcopy(original)
    left['statement']='来源提出计划，尚未独立证实。'
    right['statement']='另一些来源的具体说法，引用不可重新措辞。'
    left['support_refs']=left['support_refs'][:split]
    right['support_refs']=right['support_refs'][split:]
    for row in (left,right):row['citation_ids']=list(dict.fromkeys(x['citation_id'] for x in row['support_refs']))
    result['findings']=[left,right]
    return result


def test_selected_finding_can_split_without_changing_other_values_or_original():
    candidate={'findings':[{'id':'a','quote':'18:30'}, {'id':'b','quote':'不变'}], 'limitations':['old']}
    before=copy.deepcopy(candidate)
    merged=apply_note_patch(candidate,[0],{'replacements':[{'finding_index':0,'findings':[{'id':'a1'},{'id':'a2'}]}],'limitations':['new']})
    assert merged['findings']==[{'id':'a1'},{'id':'a2'},candidate['findings'][1]]
    merged['findings'][-1]['quote']='changed owned result'
    assert candidate==before


@pytest.mark.parametrize('quote,unit_id,citation_id',[
    ('このあと18時30分から新宿駅東口駅前広場で行われる。','S047~P1','S047'),
    ('このあと18:30から新宿駅東口駅前広場で行われる。','S999~P1','S999'),
])
def test_merged_patch_still_rejects_changed_japanese_time_quote_or_unknown_source(quote,unit_id,citation_id):
    literal='このあと18:30から新宿駅東口駅前広場で行われる。'
    candidate={'findings':[{'statement':'账号预告行动。','citation_ids':['S047'],'kind':'source_claim',
        'support_refs':[{'unit_id':'S047~P1','citation_id':'S047','quote':literal}]}],'limitations':[]}
    changed=copy.deepcopy(candidate['findings'][0]);changed['citation_ids']=[citation_id]
    changed['support_refs']=[{'unit_id':unit_id,'citation_id':citation_id,'quote':quote}]
    merged=apply_note_patch(candidate,[0],{'replacements':[{'finding_index':0,'findings':[changed]}],'limitations':[]})
    with pytest.raises(PipelineError):validate_note(merged,{'S047'},{'S047~P1':{'text':literal,'citation_id':'S047'}})
    assert candidate['findings'][0]['support_refs'][0]['quote']==literal


@pytest.mark.parametrize('response',[
    {'replacements':[{'finding_index':1,'findings':[]}],'limitations':[]},
    {'replacements':[{'finding_index':True,'findings':[]}],'limitations':[]},
    {'replacements':[],'limitations':[]},
    {'replacements':[{'finding_index':0,'findings':[]},{'finding_index':0,'findings':[]}],'limitations':[]},
    {'replacements':[{'finding_index':0,'findings':[]}],'limitations':[], 'findings':[]},
    {'replacements':[{'finding_index':0,'findings':['unsafe']}],'limitations':[]},
    {'replacements':[{'finding_index':0,'findings':[], 'overwrite_all':True}],'limitations':[]},
    {'replacements':[{'finding_index':0,'findings':[]}],'limitations':[{}]},
])
def test_unrequested_or_malformed_patch_is_rejected_without_mutation(response):
    candidate={'findings':[{'quote':'original'}],'limitations':[]};before=copy.deepcopy(candidate)
    with pytest.raises(PipelineError,match='目标范围'):apply_note_patch(candidate,[0],response)
    assert candidate==before


@pytest.mark.parametrize('issues,expected',[
    ([{'finding_index':1},{'finding_indexes':[0,1]}],[0,1]),
    ([{'finding_indexes':[True]}],None),
    ([{'finding_indexes':[2]}],None),
    ([{'code':'note_schema'}],None),
    ([{'finding_indexes':[]}],None),
])
def test_only_fully_located_issues_allow_local_correction(issues,expected):
    assert revision_patch_targets({'findings':[{},{}]},issues)==expected


def test_bad_patch_saved_then_valid_patch_preserves_other_finding_and_full_review(monkeypatch):
    data=packet(40,reading_characters=5000);saved=[];raw_patches=[];untouched={};patch_calls=[0]
    monkeypatch.setattr(synthesis,'configured_model_capacity',lambda:SMALL)
    async def model(system,supplied,**options):
        if supplied.get('compression_review'):
            assert supplied['candidate_notes']['findings'][1]==untouched[supplied['evidence_units'][0]['unit_id']]
            assert len(supplied['evidence_units'])==len({x['unit_id'] for row in supplied['candidate_notes']['findings'] for x in row['support_refs']})
            return review(supplied)
        if supplied.get('compression_patch_revision'):
            patch_calls[0]+=1
            assert supplied['compression_patch_revision']['finding_indexes']==[0]
            candidate=copy.deepcopy(supplied['compression_revision']['previous_notes'])
            if patch_calls[0]==1:
                response={'replacements':[{'finding_index':1,'findings':[]}],'limitations':[]}
                raw_patches.append(copy.deepcopy(response));return response
            if patch_calls[0]==2:assert supplied['compression_revision']['patch_format_feedback']
            candidate['findings'][0]['support_refs'][0]['quote']=supplied['evidence_units'][0]['reading']['quote']
            return {'replacements':[{'finding_index':0,'findings':[candidate['findings'][0]]}], 'limitations':candidate['limitations']}
        note=base_note(supplied)
        untouched[supplied['evidence_units'][0]['unit_id']]=copy.deepcopy(note['findings'][1])
        note['findings'][0]['support_refs'][0]['quote']='WRONG_SOURCE_QUOTE'
        return note
    monkeypatch.setattr(synthesis,'configured_ai_json',model)
    _,_,state=asyncio.run(synthesis.fit_report_input(data,SYSTEM,max_output_tokens=OUTPUT,capacity=SMALL,
        checkpoint=lambda value:saved.append(copy.deepcopy(value))))
    first=next(iter(state['batches'].values()))
    assert state['complete'] and first['revision_count']==2
    assert first['patch_attempts'][0]['response']==raw_patches[0] and not first['patch_attempts'][0]['merged']
    assert first['patch_attempts'][1]['merged']
    assert first['draft_history'][0]['note']['findings'][0]['support_refs'][0]['quote']=='WRONG_SOURCE_QUOTE'
    assert any(b.get('patch_format_error') for snapshot in saved for b in snapshot['batches'].values())


def test_patch_checkpoint_cancel_resumes_validation_without_calling_same_patch_again(monkeypatch):
    data=packet(40,reading_characters=5000);saved=[];patched=[]
    monkeypatch.setattr(synthesis,'configured_model_capacity',lambda:SMALL)
    async def model(system,supplied,**options):
        if supplied.get('compression_review'):return review(supplied)
        if supplied.get('compression_patch_revision'):
            patched.append(supplied['evidence_units'][0]['unit_id'])
            note=base_note(supplied)
            return {'replacements':[{'finding_index':0,'findings':[note['findings'][0]]}], 'limitations':note['limitations']}
        note=base_note(supplied);note['findings'][0]['support_refs'][0]['quote']='WRONG_SOURCE_QUOTE';return note
    monkeypatch.setattr(synthesis,'configured_ai_json',model)
    def checkpoint(value):
        saved.append(copy.deepcopy(value))
        if any(b['phase']=='draft_validation_pending' and b.get('patch_attempts') for b in value['batches'].values()):raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(synthesis.fit_report_input(data,SYSTEM,max_output_tokens=OUTPUT,capacity=SMALL,checkpoint=checkpoint))
    first_id=patched[0]
    _,_,state=asyncio.run(synthesis.fit_report_input(data,SYSTEM,max_output_tokens=OUTPUT,capacity=SMALL,previous_state=saved[-1]))
    assert state['complete'] and patched.count(first_id)==1
    assert next(iter(state['batches'].values()))['revision_count']==1


def test_local_patch_cannot_delete_last_source_support(monkeypatch):
    data=packet(40,reading_characters=5000);saved=[]
    monkeypatch.setattr(synthesis,'configured_model_capacity',lambda:SMALL)
    monkeypatch.setattr(synthesis,'_NOTE_REVISION_LIMIT',1)
    async def model(system,supplied,**options):
        if supplied.get('compression_patch_revision'):
            return {'replacements':[{'finding_index':0,'findings':[]}],'limitations':['Missing support does not count as reading.']}
        note=base_note(supplied);note['findings'][0]['support_refs'][0]['quote']='WRONG_SOURCE_QUOTE';return note
    monkeypatch.setattr(synthesis,'configured_ai_json',model)
    with pytest.raises(PipelineError,match='修正次数达到上限'):
        asyncio.run(synthesis.fit_report_input(data,SYSTEM,max_output_tokens=OUTPUT,capacity=SMALL,checkpoint=lambda value:saved.append(copy.deepcopy(value))))
    first=next(iter(saved[-1]['batches'].values()))
    assert first['phase']=='revision_required' and first['revision_kind']=='structure'
    assert any(issue['code']=='unit_coverage' for issue in first['structural_issues'])
