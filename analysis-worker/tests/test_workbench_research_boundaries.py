import asyncio
from datetime import date, timedelta

import pytest

from app.workbench.batch_plan import build_batch_plan
from app.workbench.research_export import checkpoint_payload, research_markdown
from app.workbench.query_execution import collect_queries


def parameters(**changes):
    return {"keyword": "反戦,憲法九条", "keyword_mode": "any", "source": "google_news", "mode": "keyword", "start_date": "2026-09-07", "end_date": "2026-10-06", "max_records": 200, **changes}


def test_batch_can_exceed_100_without_changing_dates_or_task_budget():
    plan = build_batch_plan("研究任务串", "research", parameters(max_records=50), 1000)
    assert len(plan["tasks"]) == 20
    assert sum(task["params"]["max_records"] for task in plan["tasks"]) == 1000
    for group in ("反戦", "憲法九条"):
        days = []
        for task in plan["tasks"]:
            row = task["params"]
            if row["keyword"] != group:
                continue
            start, end = date.fromisoformat(row["start_date"]), date.fromisoformat(row["end_date"])
            days.extend(start + timedelta(days=offset) for offset in range((end - start).days + 1))
            assert row["max_records"] <= 50
        assert len(set(days)) == len(days) == 30
        assert min(days) == date(2026, 9, 7) and max(days) == date(2026, 10, 6)


def test_all_combination_is_not_silently_changed_to_any_by_batch_split():
    plan = build_batch_plan("研究", "research", parameters(keyword_mode="all"), 999)
    assert len(plan["tasks"]) == 5
    assert all(task["params"]["keyword"] == "反戦,憲法九条" and task["params"]["keyword_mode"] == "all" for task in plan["tasks"])
    assert sum(task["params"]["max_records"] for task in plan["tasks"]) == 999


def test_single_day_plan_explains_unfillable_target_and_does_not_duplicate_window():
    plan = build_batch_plan("研究", "research", parameters(keyword="反戦", start_date="2026-10-06"), 1000)
    assert len(plan["tasks"]) == 1
    assert plan["max_possible_records"] == 200
    assert any("无法继续拆" in warning for warning in plan["warnings"])


def test_batch_too_small_for_all_groups_or_too_many_jobs_rejects_explicitly():
    with pytest.raises(ValueError, match="不能少于"):
        build_batch_plan("研究", "research", parameters(), 1)
    with pytest.raises(ValueError, match="200"):
        build_batch_plan("研究", "research", parameters(max_records=1, start_date="2025-01-01"), 1000)


def test_findings_cannot_create_unknown_nodes_or_exceed_graph_budget():
    a, b, c = "twitter:alpha", "twitter:beta", "twitter:gamma"
    event = {"nodes": [{"account_key": a}, {"account_key": b}, {"account_key": c}],
             "relations": [{"source_key": a, "target_key": b, "relation": "follows", "evidence_url": "https://x.com/alpha/following/"}],
             "analysis": {"findings": [
                 {"source_key": a, "target_key": c, "relation": "mentions", "method": "rules", "evidence_urls": ["https://x.com/i/status/1"]},
                 {"source_key": a, "target_key": "twitter:unknown", "relation": "mentions", "method": "rules", "evidence_urls": ["https://x.com/i/status/2"]}]},
             "decisions": [{"action": "reorder", "basis": "公开证据", "ai_used": True}]}
    payload = checkpoint_payload(event, max_edges=1)
    assert len(payload["edges"]) == 1
    assert payload["edges"][0]["evidence_urls"] == ["https://x.com/alpha/following/"]
    assert payload["analysis"]["decisions"][0]["reason"] == "公开证据"
    assert payload["analysis"]["decisions"][0]["method"] == "ai"
    assert len(payload["analysis"]["findings"]) == 2


def test_ai_claim_stays_candidate_and_public_organization_is_not_a_fake_account():
    payload = checkpoint_payload({"nodes": [{"account_key": "twitter:alpha"}], "analysis": {"affiliations": [{"account_key": "twitter:alpha", "organization": "公開組織", "claim": "公式アカウントと自己記載", "status": "self_declared", "method": "rules", "evidence_urls": ["https://x.com/alpha/"]}]}})
    assert payload["claims"][0]["object_label"] == "公開組織"
    assert payload["claims"][0]["basis"] == "reported"
    assert not payload["edges"]


def test_export_records_absence_of_affiliation_evidence_and_exact_public_sources():
    result = research_markdown({"run": {"id": "case", "status": "partial", "params": parameters()}, "items": [{"title": "日本の反戦運動", "url": "https://example.org/evidence", "published_at": "2026-10-06"}], "claims": []})
    assert "尚不能支持组织隶属判断" in result
    assert "https://example.org/evidence" in result
    assert "partial" in result


def test_news_groups_share_budget_deduplicate_and_filter_irrelevant_results(monkeypatch):
    from app.workbench import pipeline
    calls = []
    async def source(params):
        calls.append(params)
        return {"items": [{"url": "https://example.org/1", "title": "日本の反戦と憲法九条", "content": "平和運動"}, {"url": "https://example.org/2", "title": "野球の試合", "content": "得点"}], "diagnostics": [{"ok": True}]}
    monkeypatch.setattr(pipeline, "collect", source)
    result = asyncio.run(collect_queries(parameters(max_records=2)))
    assert len(result["items"]) == 1
    assert [call["keyword"] for call in calls] == ["反戦", "憲法九条"]
    assert [call["max_records"] for call in calls] == [2, 1]
