import copy

import pytest

from app.workbench.pipeline import PipelineError
from app.workbench.report_analysis import source_samples, validate_source_reviews, render_citations


def sources():
    return source_samples([{"citation_id": "S001", "title": "和平会议来源报道", "content": "主办方说会议计划举行，尚未公布参与人数。", "metadata": {"content_kind": "rss_summary"}},
                           {"citation_id": "S002", "title": "项目立场另一份原文", "content": "发起方表达另一种建议，并未说明双方达成协议。"}])[0]


def response():
    return {"source_reviews": [{"citation_id": "S001", "quote": "尚未公布参与人数", "reading": "来源没有给出人数。", "limitations": "计划不代表已经举行。"},
                               {"citation_id": "S002", "quote": "并未说明双方达成协议", "reading": "另一份来源表达建议。", "limitations": "不足以判断是否一致。"}]}


def test_every_source_is_read_and_exact_quote_is_preserved_without_mutation():
    original = sources()
    untouched = copy.deepcopy(original)
    accepted = validate_source_reviews(response(), original)
    assert [row["citation_id"] for row in accepted] == ["S001", "S002"]
    assert accepted[0]["quote"] == "尚未公布参与人数"
    assert original == untouched


@pytest.mark.parametrize("change", ["unknown", "duplicate", "omission", "fabricated", "translated", "destination", "punctuation"])
def test_ungrounded_incomplete_or_useless_review_rejected(change):
    value = response()
    if change == "unknown": value["source_reviews"][0]["citation_id"] = "S099"
    elif change == "duplicate": value["source_reviews"][1]["citation_id"] = "S001"
    elif change == "omission": value["source_reviews"].pop()
    elif change == "fabricated": value["source_reviews"][0]["quote"] = "共有五百人参与会议"
    elif change == "translated": value["source_reviews"][0]["quote"] = "No attendance count was announced."
    elif change == "destination": value["source_reviews"][0]["reading"] = "另见 https://unprovided.example/"
    else: value["source_reviews"][0]["quote"] = "。"
    with pytest.raises(PipelineError): validate_source_reviews(value, sources())


def test_long_source_covers_every_fragment_and_middle_quote_is_checked():
    text = "开头报道" * 2000 + "中间未发送的重要差异" * 1400 + "结尾否认已达成协议" * 600
    material, coverage = source_samples([{"citation_id": "S001", "title": "长篇源材料", "content": text}])
    assert coverage[0]["sent_characters"] == len(text) and coverage[0]["complete_text_sent"]
    assert not coverage[0]["omitted_ranges"]
    assert ''.join(part['text'] for part in material[0]['content_fragments']) == text
    assert material[0]["content_fragments"][-1]["text"].endswith("结尾否认已达成协议")
    value = {"source_reviews": [{"citation_id": "S001", "quote": "结尾否认已达成协议", "reading": "结尾否认。", "limitations": "原文完整性仍须独立核对。"}]}
    assert validate_source_reviews(value, material)
    value["source_reviews"][0]["quote"] = "中间未发送的重要差异"
    assert validate_source_reviews(value, material)
    value["source_reviews"][0]["quote"] = "来源没有给出的捏造中段"
    with pytest.raises(PipelineError): validate_source_reviews(value, material)


def test_normal_article_middle_and_large_library_all_saved_text_are_supplied():
    text = "来源开头的报道。" * 250 + "中段明确反对将计划当成已发生事实。" + "来源结尾说明。" * 500
    material, coverage = source_samples([{"citation_id": "S001", "title": "完整普通正文", "content": text}])
    assert coverage[0]["complete_text_sent"] and material[0]["content_fragments"][0]["text"] == text
    many = [{"citation_id": f"S{index+1:03}", "title": "来源", "content": "公开正文" * 8000} for index in range(30)]
    sample, budgets = source_samples(many + [{"citation_id": "S031", "title": "短材料", "content": "简短来源说法。"}])
    assert sum(row["sent_characters"] for row in budgets) == 960007
    assert all(row['complete_text_sent'] and not row['omitted_ranges'] for row in budgets)
    assert all(len(part['text']) <= 12000 for row in sample for part in row['content_fragments'])


def test_citation_ids_resolve_locally_to_exact_urls_including_long_aggregator_ids():
    items = [{"citation_id": "S001", "source_name": "来源一", "url": "https://public.example/a"},
             {"citation_id": "S002", "source_name": "来源二", "url": "https://news.google.com/rss/articles/" + "token" * 60}]
    rendered = render_citations("来源存在不同主张。[S001, S002] 尚未独立核实。", items)
    assert all(row["url"] in rendered for row in items) and "[S001," not in rendered
    with pytest.raises(PipelineError): render_citations("编造引用。[S099]", items)
    with pytest.raises(PipelineError): render_citations("自行生成地址 https://public.example/a", items)


def test_model_input_does_not_need_to_reproduce_opaque_source_urls():
    items, coverage = source_samples([{"citation_id": "S001", "title": "新闻公开报道", "source_name": "出版方", "url": "https://secretly-long.example/opaque",
                                      "content": "来源说法已记录。", "metadata": {"source_url": "https://query.example/", "content_kind": "rss_summary"}}])
    assert "https://" not in repr(items)
    assert items[0]["metadata"]["content_kind"] == "rss_summary"
