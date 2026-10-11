import pytest

from app.workbench import report_analysis as reports
from app.workbench.pipeline import PipelineError


DATE_LIST = '以下区分发布日、文本所述活动日、观察日和计划日。'
DATE_METHOD = ('发布日优先使用 publication_day 的 JST 日期，不把活动日充作发布日，'
               '也不直接截取 published_at 的 UTC 日期。')
SOURCES = [{'citation_id': 'S001', 'title': '保存公开材料', 'source_name': '保存来源',
            'url': 'https://public.example/S001', 'content': '公开保存正文。'}]
LABEL_METHOD = ('工作台自动关键词标签只是研究上下文，本报告未将其作为活动地点或事实；'
                '规则误匹配不能表述为原始报道矛盾。')
PERSON_METHOD = ('不从个人账号关注或提及推断政治归属；'
                 '普通个人政治身份不在本报告推断范围内。')


@pytest.mark.parametrize('text', [
    '以下区分发布日期、文本所述活动日期、观察日期和计划日期。',
    DATE_LIST,
    DATE_LIST + DATE_METHOD,
    DATE_METHOD,
])
def test_saved_native_pure_date_method_sentences_do_not_require_source_citation(text):
    assert reports._date_terminology_only(text)
    assert not [row for row in reports.report_audit(text, SOURCES)['issues'] if row['blocking']]
    rendered = reports.render_citations(text + '\n\n保存资料。[S001]', SOURCES)
    assert rendered.startswith(text + '\n\n')
    assert SOURCES[0]['url'] in rendered


@pytest.mark.parametrize('extra', [
    '会议已经举行。',
    '2026-10-09。',
    '来源称该组织已经举办集会。',
    '该组织举办了大阪集会。',
    '已有1000人参与会议。',
    '本次覆盖停止原因是 account_budget，整体 partial。',
    '社会趋势显示抗议人数增加。',
    '发布日均已经核验。',
    '该账号没有提供相反立场材料。',
    '本次所有来源都已经独立核验。',
])
def test_method_sentence_prefix_never_exempts_appended_uncited_assertion(extra):
    text = DATE_LIST + DATE_METHOD + extra
    assert not reports._date_terminology_only(text)
    assert any(row['code'] == 'uncited_assertion_paragraph' and row['blocking']
               for row in reports.report_audit(text, SOURCES)['issues'])
    with pytest.raises(PipelineError):
        reports.render_citations(text, SOURCES)


@pytest.mark.parametrize('text', [
    DATE_METHOD.replace('publication_day', 'event_day'),
    DATE_METHOD.replace('published_at', 'observed_at'),
    DATE_METHOD.replace('JST', 'UTC'),
    DATE_LIST.replace('、观察日', ''),
    DATE_LIST + DATE_METHOD.removesuffix('。'),
    DATE_LIST + DATE_METHOD.replace('优先使用', '已经证实'),
])
def test_only_complete_whitelisted_method_sentences_are_exempt(text):
    assert not reports._date_terminology_only(text)
    with pytest.raises(PipelineError):
        reports.render_citations(text, SOURCES)


def test_method_paragraph_does_not_legalize_unknown_id():
    with pytest.raises(PipelineError):
        reports.render_citations(DATE_LIST + DATE_METHOD + '[S099]', SOURCES)


def test_ordinary_uncited_fact_and_false_independence_remain_blocked():
    for text in ('会议已经举行，1000人参与。', '两篇转载报道是独立核验材料。'):
        with pytest.raises(PipelineError):
            reports.render_citations(text, SOURCES)


@pytest.mark.parametrize('text', [LABEL_METHOD, PERSON_METHOD, LABEL_METHOD + PERSON_METHOD])
def test_exact_saved_metadata_and_personal_identity_method_boundaries_are_not_source_facts(text):
    assert reports._date_terminology_only(text)
    assert not [row for row in reports.report_audit(text, SOURCES)['issues'] if row['blocking']]
    rendered = reports.render_citations(text + '\n\n保存资料。[S001]', SOURCES)
    assert rendered.startswith(text + '\n\n')


@pytest.mark.parametrize('method', [LABEL_METHOD, PERSON_METHOD])
@pytest.mark.parametrize('extra', [
    '某组织已经举办集会，1000人参与。',
    '某账号发布了声明。',
    '原始报道说活动已经举行。',
    '某个人宣布自己属于该政党。',
    '某个人的政治归属已经通过核验。',
    '2026-10-09举行会议。',
])
def test_exact_method_boundary_never_exempts_appended_specific_source_or_political_assertion(method, extra):
    text = method + extra
    assert not reports._date_terminology_only(text)
    assert any(row['code'] == 'uncited_assertion_paragraph' and row['blocking']
               for row in reports.report_audit(text, SOURCES)['issues'])
    with pytest.raises(PipelineError):
        reports.render_citations(text, SOURCES)


@pytest.mark.parametrize('text', [
    LABEL_METHOD.replace('只是研究上下文', '已经证实该活动举行'),
    PERSON_METHOD.replace('不从', '可以从'),
    LABEL_METHOD + PERSON_METHOD.removesuffix('。'),
])
def test_method_boundary_whitelist_does_not_accept_changed_claim_or_incomplete_sentence(text):
    assert not reports._date_terminology_only(text)
    with pytest.raises(PipelineError):
        reports.render_citations(text, SOURCES)


def test_exact_method_boundary_cannot_legalize_unknown_id():
    with pytest.raises(PipelineError):
        reports.render_citations(LABEL_METHOD + PERSON_METHOD + '[S999]', SOURCES)
