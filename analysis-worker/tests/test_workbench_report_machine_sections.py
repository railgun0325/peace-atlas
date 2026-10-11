import copy
import hashlib

import pytest

from app.workbench import report_machine_sections as machine
from app.workbench.report_analysis import render_citations
from app.workbench.pipeline import PipelineError


MARKER = '## 运行元数据〔程序生成〕'
SOURCES = [{'citation_id':'S001','title':'公开保存资料','source_name':'保存来源',
            'url':'https://public.example/S001','content':'公开原文。'}]


def test_exact_reserved_section_moves_wrong_numbers_and_mixed_facts_only_to_audit():
    before='正文事实与原文。[S001]\n\n'
    removed=MARKER+'\n错误计数999，不明主体已举行会议，1000人参与。[R001]\n### 槽内细节\n混入事实。\n\n'
    after='## 来源分析\n保存来源说法。[S001]\n'
    raw=before+removed+after
    result=machine.extract_machine_sections(raw)
    assert result['clean_markdown']==before+after and result['changed']
    assert result['footer_render_required'] and not result['raw_truth_established']
    entry=result['removed_sections'][0]
    assert entry['raw']==removed and entry['start']==len(before) and entry['end']==len(before+removed)
    assert raw[entry['start']:entry['end']]==entry['raw']
    assert entry['sha256']==hashlib.sha256(removed.encode('utf-8')).hexdigest()
    assert entry['document_sha256']==hashlib.sha256(raw.encode('utf-8')).hexdigest()
    assert entry['offset_unit']=='unicode_codepoints' and not entry['raw_truth_established']
    assert result['raw_sha256']==hashlib.sha256(raw.encode('utf-8')).hexdigest()
    assert result['clean_sha256']==hashlib.sha256((before+after).encode('utf-8')).hexdigest()
    assert render_citations(result['clean_markdown'],SOURCES)


@pytest.mark.parametrize('title',[
    '## 运行元数据', '## 运行元数据[程序生成]', '### 运行元数据〔程序生成〕',
    ' # 运行元数据〔程序生成〕', ' '+MARKER, MARKER+' ', MARKER+' ##',
    '> '+MARKER, '    '+MARKER, '`'+MARKER+'`', '说明：'+MARKER,
])
def test_other_titles_quotes_indentation_or_inline_prefixes_are_never_removed(title):
    raw=title+'\n发布统计75条，某组织举办集会。\n## 普通正文\n正文。[S001]'
    result=machine.extract_machine_sections(raw)
    assert result['clean_markdown']==raw and not result['removed_sections']
    assert not result['changed'] and not result['footer_render_required']


@pytest.mark.parametrize('fence,close',[
    ('```markdown','```'), ('~~~~','~~~~'), ('````','`````'),
    ('```',''), ('~~~text',''),
])
def test_reserved_heading_inside_closed_or_unclosed_fence_is_not_a_section(fence,close):
    raw=fence+'\n'+MARKER+'\n某主体已经发布声明。[R001]\n'+close
    result=machine.extract_machine_sections(raw)
    assert result['clean_markdown']==raw and not result['removed_sections']


def test_code_fence_headings_inside_reserved_slot_do_not_end_the_removal():
    removed=MARKER+'\n```md\n# 假终止\n## 假正文\n```\n统计999。\n'
    after='# 真正文\n保存资料。[S001]'
    result=machine.extract_machine_sections(removed+after)
    assert result['clean_markdown']==after and result['removed_sections'][0]['raw']==removed


@pytest.mark.parametrize('next_heading',['# 正文','## 正文','   ## 正文','##'])
def test_same_or_higher_atx_heading_is_retained_as_the_precise_boundary(next_heading):
    removed=MARKER+'\n槽内错误统计。\n'
    after=next_heading+'\n实际正文。[S001]'
    result=machine.extract_machine_sections(removed+after)
    assert result['clean_markdown']==after and result['removed_sections'][0]['raw']==removed


@pytest.mark.parametrize('underline',['===','---'])
def test_following_setext_heading_and_its_text_are_not_swallowed(underline):
    removed=MARKER+'\n统计。\n\n'
    after='实际正文标题\n'+underline+'\n事实正文。[S001]'
    result=machine.extract_machine_sections(removed+after)
    assert result['clean_markdown']==after and result['removed_sections'][0]['raw']==removed


def test_consecutive_multiple_sections_keep_separate_nonoverlapping_audits_and_later_facts():
    first=MARKER+'\n错误数据A。\n'
    second=MARKER+'\n错误数据B。\n'
    middle='## 中间正文\n事实A。[S001]\n'
    third=MARKER+'\n错误数据C。\n'
    final='# 后续正文\n事实B。[S001]'
    raw=first+second+middle+third+final
    result=machine.extract_machine_sections(raw)
    assert result['clean_markdown']==middle+final
    assert [row['raw'] for row in result['removed_sections']]==[first,second,third]
    assert all(left['end']<=right['start'] for left,right in zip(result['removed_sections'],result['removed_sections'][1:]))
    assert all(raw[row['start']:row['end']]==row['raw'] for row in result['removed_sections'])


def test_unicode_and_crlf_offsets_and_hashes_bind_the_exact_original_string():
    before='原始𠮷文字。[S001]\r\n'
    removed=MARKER+'\r\n运行数字999。\r\n'
    after='## 后文\r\n来源事实。[S001]\r\n'
    result=machine.extract_machine_sections(before+removed+after)
    assert result['clean_markdown']==before+after
    entry=result['removed_sections'][0]
    assert entry['start']==len(before) and entry['end']==len(before+removed)
    assert entry['raw']==removed and entry['sha256']==hashlib.sha256(removed.encode('utf-8')).hexdigest()


@pytest.mark.parametrize('raw',[
    '<!-- RUN_METADATA_START -->\n数字999。',
    '<!-- RUN_METADATA_END -->\n事实段。[S001]',
    '[RUN_METADATA_START]\n数字999。\n[RUN_METADATA_END]',
    '此段为运行元数据。发布80条，某主体已经举办集会。[R001]\n\n其他来源。[S001]',
    '文本\u2028'+MARKER+'\u2028运行999。',
])
def test_unknown_unpaired_markers_and_unmarked_metadata_prefixes_remain_untouched(raw):
    result=machine.extract_machine_sections(raw)
    assert result['clean_markdown']==raw and not result['removed_sections']


def test_unmarked_invented_fact_and_fake_runtime_citation_keep_existing_gate():
    raw='会议已经举行，1000人参与。[R001]\n\n保存资料。[S001]'
    result=machine.extract_machine_sections(raw)
    assert result['clean_markdown']==raw
    with pytest.raises(PipelineError):
        render_citations(result['clean_markdown'],SOURCES)


def test_only_reserved_content_cannot_become_an_accepted_empty_report():
    result=machine.extract_machine_sections(MARKER+'\n统计与事实混合，999人参与。[R001]')
    assert result['clean_markdown']=='' and not result['body_present']
    assert result['removed_sections'] and result['footer_render_required']
    with pytest.raises(PipelineError):
        render_citations(result['clean_markdown'],SOURCES)


def test_unmarked_fact_after_a_reserved_section_is_preserved_and_still_blocks():
    raw=MARKER+'\n统计。\n## 实际正文\n某主体已经举办会议，1000人参与。\n\n保存资料。[S001]'
    result=machine.extract_machine_sections(raw)
    assert '某主体已经举办会议' in result['clean_markdown']
    with pytest.raises(PipelineError):
        render_citations(result['clean_markdown'],SOURCES)


def test_resume_is_idempotent_without_mutating_saved_audit():
    first=machine.extract_machine_sections('正文。[S001]\n'+MARKER+'\n错误统计。')
    frozen=copy.deepcopy(first)
    second=machine.extract_machine_sections(first['clean_markdown'])
    assert second['clean_markdown']==first['clean_markdown'] and not second['removed_sections']
    assert first==frozen
    # The caller retains first's audit when reusing clean text; the raw hash
    # binds that audit to the saved original draft rather than a new proof.
    assert first['removed_sections'][0]['document_sha256']==first['raw_sha256']


@pytest.mark.parametrize('value',[None,{},1])
def test_nontext_input_is_not_implicitly_converted_into_report_content(value):
    with pytest.raises(TypeError):
        machine.extract_machine_sections(value)


@pytest.mark.parametrize('tag',['pre','script','style','textarea'])
def test_literal_heading_inside_html_code_container_is_not_removed(tag):
    raw='<'+tag+'>\n'+MARKER+'\n统计999。\n</'+tag+'>'
    result=machine.extract_machine_sections(raw)
    assert result['clean_markdown']==raw and not result['removed_sections']


def test_many_exact_sections_preserve_every_audit_and_only_remove_reserved_ranges():
    chunks=[MARKER+f'\n错误统计{index}，虚构发布事实。[S999]\n' for index in range(1000)]
    body='## 正文\n保存原文说法。[S001]'
    raw=''.join(chunks)+body
    result=machine.extract_machine_sections(raw)
    assert result['clean_markdown']==body and len(result['removed_sections'])==1000
    assert [entry['raw'] for entry in result['removed_sections']]==chunks
    assert all(raw[entry['start']:entry['end']]==entry['raw'] for entry in result['removed_sections'])
    assert render_citations(result['clean_markdown'],SOURCES)


@pytest.mark.parametrize('closing',['-->',''])
def test_heading_inside_closed_or_unclosed_html_comment_is_not_removed(closing):
    raw='<!--\n'+MARKER+'\n统计999。\n'+closing
    result=machine.extract_machine_sections(raw)
    assert result['clean_markdown']==raw and not result['removed_sections']


def test_comment_closing_restores_detection_for_the_next_real_reserved_heading():
    literal='<!--\n'+MARKER+'\n错误字面标题。\n-->\n'
    removed=MARKER+'\n机器槽999。\n'
    body='## 正文\n保存来源。[S001]'
    result=machine.extract_machine_sections(literal+removed+body)
    assert result['clean_markdown']==literal+body
    assert [row['raw'] for row in result['removed_sections']]==[removed]
