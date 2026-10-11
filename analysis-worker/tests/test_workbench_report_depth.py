import asyncio
import copy

import pytest

from app.workbench import report_analysis as reports
from app.workbench.pipeline import PipelineError


def material(index=1, **changes):
    return {"citation_id": f"S{index:03}", "title": "和平会议已举行三百人参与",
            "content": f"来源{index}更正：会议取消，尚未举行，没有参与人数统计。",
            "source_name": "同一出版方", "url": f"https://public.example/{index}",
            "metadata": {"content_kind": "public_article_body"}, **changes}


def response(sources):
    return {"source_reviews": [{"citation_id": row["citation_id"],
        "quote": next((part["text"][:40] for part in row["content_fragments"] if part["text"]), row["title"]),
        "reading": "来源正文更正原先说法，不能按标题确认活动。", "limitations": "来源主张尚未独立核验。"}
        for row in sources]}


def run(coroutine):
    return asyncio.run(coroutine)


def test_saved_body_with_title_reversal_requires_a_body_quote():
    sources, _ = reports.source_samples([material()])
    value = response(sources)
    value["source_reviews"][0]["quote"] = sources[0]["title"]
    with pytest.raises(PipelineError, match="不能只摘标题"):
        reports.validate_source_reviews(value, sources)
    review = reports.validate_source_reviews(response(sources), sources)[0]
    assert review["quote_basis"] == "sent_body_fragment"
    assert review["quote_verification_scope"] == "exact_quote_match_only"


@pytest.mark.parametrize("content,metadata", [("", {}), ("标题文本", {"content_kind": "headline_only"}),
                                             ("   ", {})])
def test_only_explicit_headline_or_missing_body_can_quote_title(content, metadata):
    sources, _ = reports.source_samples([material(content=content, metadata=metadata)])
    value = response(sources)
    value["source_reviews"][0]["quote"] = sources[0]["title"]
    assert reports.validate_source_reviews(value, sources)[0]["quote_basis"] == "headline"


def test_short_actual_body_is_not_rejected_for_having_less_than_eight_characters():
    sources, _ = reports.source_samples([material(content="中止。")])
    assert reports.validate_source_reviews(response(sources), sources)[0]["quote"] == "中止。"
    value = response(sources)
    value["source_reviews"][0]["reading"] = "   "
    with pytest.raises(PipelineError):
        reports.validate_source_reviews(value, sources)


def test_coverage_exposes_sent_and_omitted_ranges_separate_from_original_completeness():
    source = material(content="开始" * 6000 + "中间反证" * 4000 + "结尾" * 6000,
                      metadata={"content_kind": "public_article_body", "body_completeness": "public_extract_not_guaranteed_full_article"})
    unchanged = copy.deepcopy(source)
    sources, coverage = reports.source_samples([source])
    assert source == unchanged
    row = coverage[0]
    assert row["sent_ranges"] == [{"start": start, "end": min(start+12000,len(source['content']))}
                                  for start in range(0,len(source['content']),12000)]
    assert row["omitted_ranges"] == []
    assert row["original_document_completeness"] == "public_extract_not_guaranteed_full_article"
    assert row["complete_text_sent"]
    assert ''.join(part['text'] for part in sources[0]['content_fragments']) == source['content']
    assert all(source["content"][part["start"]:part["end"]] == part["text"] for part in sources[0]["content_fragments"])


def test_same_publisher_or_search_feed_does_not_merge_distinct_reports():
    rows = [material(1), material(2)]
    for row in rows:
        row["metadata"]["source_url"] = "https://search.example/common-feed"
    dependencies = reports.source_dependencies(rows)
    assert dependencies["S001"]["support_group_id"] != dependencies["S002"]["support_group_id"]
    assert all(row["independence"] == "not_established" for row in dependencies.values())
    assert reports.render_citations("两份独立采访有不同内容。[S001,S002]", rows)


def test_same_body_with_changed_title_and_explicit_reprint_have_shared_support():
    rows = [material(1), material(2, title="不同编辑标题", content=material(1)["content"]),
            material(3, metadata={"reprint_of_url": "https://public.example/2"})]
    dependencies = reports.source_dependencies(rows)
    assert len({row["support_group_id"] for row in dependencies.values()}) == 1
    assert dependencies["S003"]["overlapping_citation_ids"] == ["S001", "S002", "S003"]
    assert "shared_document_or_explicit_reprint" in dependencies["S003"]["overlap_basis"]
    with pytest.raises(PipelineError, match="独立核验"):
        reports.render_citations("两家媒体已经独立交叉证实会场有300人。[S001,S002]", rows)
    assert reports.render_citations("相同正文不能当作独立交叉证实。[S001,S002]", rows)


@pytest.mark.parametrize("markdown", [
    "报道情况。[S001]\n\n参与人数增长证明日本社会支持扩大。",
    "来源情况。[S001] 后续结论。[S1000]",
    "来源情况。[S001] 后续结论。[S099]",
    "来源情况。[S001] 后续结论对应S002。",
])
def test_valid_link_does_not_legalize_uncited_assertions_or_unresolved_ids(markdown):
    with pytest.raises(PipelineError):
        reports.render_citations(markdown, [material(1), material(2)])


def test_scope_headings_and_insufficiency_are_not_missing_fact_citations():
    markdown = ("# 活动趋势报告\n\n## 1. 研究范围\n\n本报告覆盖2026年10月的新闻报道与公开资料。\n\n"
                "资料不足，不能判断参与规模。\n\n后续核验：\n\n建议核验原始声明与实际举行时间。\n\n"
                "来源明确更正会议取消。[S001]")
    assert reports.render_citations(markdown, [material()])


def test_current_insufficiency_note_is_not_an_unreferenced_event_assertion():
    assert reports.render_citations("现有资料不足，无法判断参与规模。\n\n会议已取消。[S001]", [material()])


def test_unrelated_negative_statement_does_not_allow_duplicate_independence_claim():
    rows = [material(1), material(2, content=material(1)["content"])]
    with pytest.raises(PipelineError, match="独立核验"):
        reports.render_citations("事件尚未结束，两家媒体已独立证实活动举行。[S001,S002]", rows)


def test_offline_audit_recognizes_historical_saved_url_citations():
    audit = reports.report_audit("来源报道会议已取消。[原始公开材料](https://public.example/1)", [material()])
    assert audit["referenced_citation_ids"] == ["S001"]
    assert not any(row["blocking"] for row in audit["issues"])


def test_rendered_ids_survive_a_shared_following_destination():
    rows = [material(1, url="https://x.com/public_a/following"),
            material(2, url="https://x.com/public_a/following", content="另一条保存的关注观察。")]
    markdown = reports.render_citations("分别记录两条观察。[S001,S002]", rows)
    audit = reports.report_audit(markdown, rows)
    assert audit["referenced_citation_ids"] == ["S001", "S002"]
    assert not any(row["blocking"] for row in audit["issues"])


@pytest.mark.parametrize("label,destination", [
    ("S001 · 原始材料", "https://public.example/2"),
    ("S999 · 未知材料", "https://public.example/1"),
])
def test_rendered_citation_label_cannot_be_overridden_by_destination(label, destination):
    audit = reports.report_audit(f"来源报道会议取消。[{label}]({destination})", [material(1), material(2)])
    assert any(row["blocking"] for row in audit["issues"])


def test_legacy_shared_url_requires_an_explicit_saved_identity():
    rows = [material(1), material(2, url=material(1)["url"])]
    audit = reports.report_audit("来源报道会议取消。[公开原文](https://public.example/1)", rows)
    assert audit["referenced_citation_ids"] == []
    assert any(row["code"] == "ambiguous_saved_citation" and row["blocking"] for row in audit["issues"])


def test_report_context_retains_goal_and_declines_invented_independence_or_opposition():
    sources, coverage = reports.source_samples([material(1), material(2)])
    research = {"run_id": "saved-run", "research_goal": "比较诉求、行动方式及相反解释"}
    context = reports.report_context(sources, coverage, research)
    assert context["research"] == research and context["independent_source_count"] is None
    assert any("不补造对方观点" in text for text in context["analysis_questions"])
    context["research"]["run_id"] = "changed"
    assert research["run_id"] == "saved-run"


def test_all_sources_are_read_in_output_bounded_batches(monkeypatch):
    sources, _ = reports.source_samples([material(index) for index in range(1, 31)])
    calls = []
    async def model(system, data, **options):
        calls.append(copy.deepcopy(data["sources"]))
        assert "不能只摘标题" in system and len(data["sources"]) <= 12
        assert options["max_input_chars"] is None
        return response(data["sources"])
    monkeypatch.setattr(reports, "configured_ai_json", model)
    state = run(reports.read_sources(sources, return_state=True))
    assert len(calls) == 3 and state["complete"] and state["status"] == "completed"
    assert state["coverage_progress"] == {"input_source_count": 30, "reviewed_source_count": 30, "pending_source_count": 0}
    assert {row["citation_id"] for batch in calls for row in batch} == {row["citation_id"] for row in sources}


def test_failed_late_batch_is_checkpointed_and_retry_only_reads_pending(monkeypatch):
    sources, _ = reports.source_samples([material(index) for index in range(1, 26)])
    calls, checkpoints = [], []
    async def failing_model(system, data, **options):
        calls.append(copy.deepcopy(data["sources"]))
        if len(calls) == 2:
            raise PipelineError("受控第二批模型失败。")
        return response(data["sources"])
    monkeypatch.setattr(reports, "configured_ai_json", failing_model)
    state = run(reports.read_sources(sources, checkpoint=checkpoints.append, return_state=True))
    assert not state["complete"] and state["status"] == "partial" and len(state["source_reviews"]) == 12
    assert len(state["pending_citation_ids"]) == 13 and checkpoints[-1]["error"]
    calls.clear()
    async def model(system, data, **options):
        calls.append(copy.deepcopy(data["sources"]))
        return response(data["sources"])
    monkeypatch.setattr(reports, "configured_ai_json", model)
    resumed = run(reports.read_sources(sources, previous_review=state, return_state=True))
    assert resumed["complete"] and resumed["resumed_source_count"] == 12
    assert {row["citation_id"] for batch in calls for row in batch} == set(state["pending_citation_ids"])


def test_changed_text_is_reread_and_completed_unchanged_state_uses_no_model(monkeypatch):
    sources, _ = reports.source_samples([material()])
    calls = []
    async def model(system, data, **options):
        calls.append(copy.deepcopy(data["sources"]))
        return response(data["sources"])
    monkeypatch.setattr(reports, "configured_ai_json", model)
    previous = run(reports.read_sources(sources, return_state=True))
    calls.clear()
    assert run(reports.read_sources(sources, previous_review=previous, return_state=True))["complete"]
    assert calls == []
    changed, _ = reports.source_samples([material(content="新的更正正文：会议延期，并未举行。")])
    state = run(reports.read_sources(changed, previous_review=previous, return_state=True))
    assert len(calls) == 1 and state["resumed_source_count"] == 0


def test_cancellation_propagates_after_prior_successful_checkpoint(monkeypatch):
    sources, _ = reports.source_samples([material(index) for index in range(1, 26)])
    checkpoints, calls = [], []
    async def model(system, data, **options):
        calls.append(data)
        if len(calls) == 2:
            raise asyncio.CancelledError()
        return response(data["sources"])
    monkeypatch.setattr(reports, "configured_ai_json", model)
    with pytest.raises(asyncio.CancelledError):
        run(reports.read_sources(sources, checkpoint=checkpoints.append, return_state=True))
    assert checkpoints[-1]["coverage_progress"]["reviewed_source_count"] == 12
    assert checkpoints[-1]["coverage_progress"]["pending_source_count"] == 13


def test_time_budget_returns_pending_state_without_model_request(monkeypatch):
    sources, _ = reports.source_samples([material()])
    async def model(*args, **kwargs):
        raise AssertionError("No request beyond the budget")
    monkeypatch.setattr(reports, "configured_ai_json", model)
    state = run(reports.read_sources(sources, time_budget_seconds=0, return_state=True))
    assert not state["complete"] and state["stop_reason"] == "time_budget"
    assert state["pending_citation_ids"] == ["S001"]


def test_default_failure_attaches_recoverable_state(monkeypatch):
    sources, _ = reports.source_samples([material(index) for index in range(1, 14)])
    calls = []
    async def model(system, data, **options):
        calls.append(data)
        if len(calls) == 2:
            raise PipelineError("受控失败")
        return response(data["sources"])
    monkeypatch.setattr(reports, "configured_ai_json", model)
    with pytest.raises(PipelineError) as caught:
        run(reports.read_sources(sources))
    assert caught.value.source_review_state["coverage_progress"]["reviewed_source_count"] == 12
