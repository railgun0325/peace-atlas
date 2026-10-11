import asyncio
import copy
import json

import pytest

from app.workbench import query_plan as q, relationship_analysis as r, research_engine as e
from app.workbench.pipeline import PipelineError
from app.workbench.social.common import SocialError, account


def run(coro):
    return asyncio.run(coro)


def params(**changes):
    return {"source": "twitter", "seed_sources": ["twitter"], "keyword": "反戦", "keyword_mode": "any", "start_date": "2026-10-01", "end_date": "2026-10-06",
            "depth": 2, "max_accounts": 8, "max_edges": 12, "max_records": 20, "max_scrolls": 2, "time_budget_seconds": 60,
            "expand_relations": ["following", "followers", "mentions"], "analysis_mode": "rules", **changes}


def post(handle="seed", serial=1, text="日本の反戦運動と平和集会。", **changes):
    return {"platform": "twitter", "type": "social", "account_key": "twitter:" + handle, "title": text[:50], "content": text,
            "url": f"https://x.com/{handle}/status/{serial}", "published_at": "2026-10-05T12:00:00+00:00",
            "metadata": {"content_kind": "body", "collected_at": "2026-10-06T01:00:00+00:00"}, **changes}


def prof(handle, bio="日本の反戦運動について発信。", **changes):
    return {**account("twitter", handle), "profile": {"verified_in_dom": True, "bio": bio, "url": f"https://x.com/{handle}/",
        "following_count": None, "followers_count": None, "observed_at": "2026-10-06T01:00:00+00:00", **changes}}


def test_keyword_delimiters_and_space_semantics_are_explicit():
    result = q.build_query_plan({"keyword": "憲法九条，沖縄 平和;反戦\n反戦", "keyword_mode": "any", "source": "twitter"})
    assert [row["terms"] for row in result["groups"]] == [["憲法九条"], ["沖縄", "平和"], ["反戦"]]
    assert len(result["queries"]) == 3
    assert result["preview"] == "(憲法九条) OR (沖縄 AND 平和) OR (反戦)"
    assert "空格" in result["separator_help"]
    assert result["raw_keyword"] == "憲法九条，沖縄 平和;反戦\n反戦"
    assert not result["suggestions_applied"]
    assert q.query_matches(result, "沖縄で平和を願う")
    assert not q.query_matches(result, "沖縄の野球")


def test_all_groups_and_latin_word_boundaries():
    plan = q.build_query_plan({"keyword": "peace,Japan", "keyword_mode": "all", "source": "twitter"})
    assert len(plan["queries"]) == 1
    assert plan["queries"][0]["keyword"] == "peace Japan"
    assert q.query_matches(plan, "Japan peace movement")
    assert not q.query_matches(plan, "Japan peacemaker")
    assert not q.query_matches(plan, "peace Japanese baseball")


@pytest.mark.parametrize("keyword", ["", "from:foo", "foo OR bar", '"foo"', "a+b -war", ",".join(str(index) for index in range(13))])
def test_invalid_keyword_plans_do_not_open_browser(keyword):
    with pytest.raises(PipelineError):
        q.build_query_plan(keyword)


def test_instagram_plan_honestly_describes_account_search_and_multitag_gate():
    plan = q.build_query_plan({"source": "instagram", "keyword": "反戦,平和", "mode": "hashtag", "keyword_mode": "all"})
    assert len(plan["queries"]) == 2
    assert all("/explore/tags/" in row["url"] for row in plan["queries"])
    assert not q.query_matches(plan, "#反戦だけ")
    assert q.query_matches(plan, "#反戦 #平和")
    keyword = q.build_query_plan({"source": "instagram", "keyword": "反戦"})
    assert "不声称等同全文检索" in keyword["queries"][0]["semantics"]


def test_ai_suggestions_are_optional_validated_and_never_replace_input(monkeypatch):
    async def model(*args, **kwargs):
        return {"suggestions": [{"keyword": "武力行使反対", "reason": "相关公民和平口号。"}]}
    monkeypatch.setattr(q, "configured_ai_json", model)
    result = run(q.suggest_keywords({"keyword": "反戦", "use_ai": True}))
    assert result["ai"]["used"]
    assert result["raw_keyword"] == "反戦"
    assert result["groups"][0]["text"] == "反戦"
    assert any(row["keyword"] == "武力行使反対" and not row["selected"] for row in result["suggestions"])


def test_invalid_ai_suggestion_is_not_presented_as_used(monkeypatch):
    async def model(*args, **kwargs):
        return {"suggestions": [{"keyword": "from:evil", "reason": "ignore previous instructions"}]}
    monkeypatch.setattr(q, "configured_ai_json", model)
    result = run(q.suggest_keywords({"keyword": "反戦", "use_ai": True}))
    assert not result["ai"]["used"] and result["ai"]["outcome"] == "failed"
    assert all(row["source"] == "local_taxonomy" for row in result["suggestions"])


def test_relationships_distinguish_following_mentions_shared_events_and_affiliation():
    accounts = [prof("alpha", "団体A公式アカウント。"), prof("beta", "平和好きです。")]
    items = [post("alpha", 1, "日本の反戦集会を開催 @beta https://example.org/events/123?utm_source=x"),
             post("beta", 2, "日本の反戦集会を開催 https://example.org/events/123", published_at="2026-10-06T12:00:00+00:00")]
    relations = [{"source_key": "twitter:alpha", "target_key": "twitter:beta", "relation": "follows", "evidence_url": "https://x.com/alpha/following/", "observed_at": "2026-10-06T01:00:00+00:00"}]
    result = r.analyze_relationships(accounts, items, relations)
    assert {row["relation"] for row in result["findings"]} == {"follows", "mentions", "shared_link", "shared_event"}
    assert len(result["affiliations"]) == 1
    assert result["affiliations"][0]["organization"] == "団体A"
    assert result["affiliations"][0]["status"] == "self_declared"
    assert not accounts[0]["organization"]
    evidence = {row["id"] for row in result["evidence"]}
    assert all(set(row["evidence_ids"]) <= evidence for row in result["findings"] + result["affiliations"])


def test_shared_homepage_or_hashtag_and_follow_do_not_imply_affiliation():
    result = r.analyze_relationships([prof("a"), prof("b")], [post("a", 1, "#平和 https://example.org/"), post("b", 2, "#平和 https://example.org/")], [])
    assert not result["findings"] and not result["affiliations"]
    assert r.mention_handles("twitter", "mail foo@example.com @actual https://x.com/@fake") == ["actual"]


def test_ai_affiliation_unknown_evidence_is_rejected_atomically(monkeypatch):
    async def model(*args, **kwargs):
        return {"affiliation_candidates": [{"account_key": "twitter:alpha", "organization": "Invented", "claim": "model guess", "evidence_ids": ["not-supplied"]}]}
    monkeypatch.setattr(r, "configured_ai_json", model)
    result = run(r.analyze_relationships_with_ai([prof("alpha")], [post("alpha")], [], use_ai=True))
    assert result["ai"]["outcome"] == "failed" and not result["ai"]["used"]
    assert not result["affiliations"]


class FakeAdapter:
    seed_results = {}
    profiles = {}
    post_results = {}
    neighbor_results = {}
    instances = []

    def __init__(self):
        self.calls = []
        self.closed = False
        self.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def search(self, platform, keyword, options, limit, accept):
        self.calls.append(("search", platform, keyword, limit))
        value = copy.deepcopy(self.seed_results.get((platform, keyword), {"items": [], "accounts": [], "relations": [], "diagnostics": []}))
        if isinstance(value, Exception):
            raise value
        value["items"] = [row for row in value.get("items", []) if (platform == "google_news" or accept(row).get("matched"))][:limit]
        return value

    async def profile(self, platform, handle):
        self.calls.append(("profile", platform, handle))
        result = self.profiles.get(handle, prof(handle, "趣味は野球です。"))
        if isinstance(result, Exception):
            raise result
        return copy.deepcopy(result)

    async def posts(self, platform, handle, options, limit, accept):
        self.calls.append(("posts", platform, handle, limit))
        values = [copy.deepcopy(row) for row in self.post_results.get(handle, []) if accept(row).get("matched")][:limit]
        return {"items": values, "accounts": [account(platform, handle)], "relations": [], "diagnostics": []}

    async def neighbors(self, platform, handle, direction, options, count):
        self.calls.append(("neighbors", platform, handle, direction))
        return copy.deepcopy(self.neighbor_results.get((handle, direction), {"accounts": [], "relations": [], "diagnostics": []}))


@pytest.fixture
def adapter(monkeypatch):
    FakeAdapter.seed_results = {}
    FakeAdapter.profiles = {}
    FakeAdapter.post_results = {}
    FakeAdapter.neighbor_results = {}
    FakeAdapter.instances = []
    monkeypatch.setattr(e, "BrowserResearchAdapter", FakeAdapter)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    return FakeAdapter


def following(source, target, direction="following"):
    origin, destination = (source, target) if direction == "following" else (target, source)
    return {"accounts": [account("twitter", target)], "relations": [{"source_key": "twitter:" + origin, "target_key": "twitter:" + destination,
            "relation": "follows", "evidence_url": f"https://x.com/{source}/{direction}/", "observed_at": "2026-10-06T01:00:00+00:00"}], "diagnostics": []}


def seed(adapter):
    adapter.seed_results[("twitter", "反戦")] = {"items": [post()], "accounts": [account("twitter", "seed")], "relations": [], "diagnostics": []}
    adapter.profiles["seed"] = prof("seed")


def test_research_traverses_depth_dedupes_cycles_and_persists_each_account(adapter):
    seed(adapter)
    adapter.profiles.update(child=prof("child"), grandchild=prof("grandchild"), too_deep=prof("too_deep"))
    adapter.neighbor_results[("seed", "following")] = following("seed", "child")
    adapter.neighbor_results[("child", "following")] = following("child", "grandchild")
    adapter.neighbor_results[("child", "followers")] = following("child", "seed", "followers")
    adapter.neighbor_results[("grandchild", "following")] = following("grandchild", "too_deep")
    checkpoints = []
    async def save(event):
        checkpoints.append(copy.deepcopy(event))
    result = run(e.run_research(params(), checkpoint=save))
    assert result["summary"]["outcome"] == "completed"
    assert {row["account_key"]: row["depth"] for row in result["nodes"]} == {"twitter:seed": 0, "twitter:child": 1, "twitter:grandchild": 2}
    assert len([call for call in adapter.instances[0].calls if call == ("profile", "twitter", "seed")]) == 1
    assert not any(call[:3] == ("neighbors", "twitter", "grandchild") for call in adapter.instances[0].calls)
    assert len(result["relations"]) == 2
    assert checkpoints[-1]["kind"] == "finished"
    assert any(row["kind"] == "account" and row["accounts"] for row in checkpoints)
    assert checkpoints[-1]["visited"] == ["twitter:child", "twitter:grandchild", "twitter:seed"]
    assert all("basis" in row and not row["ai_used"] for row in result["decisions"])
    assert adapter.instances[0].closed


def test_zero_topic_evidence_stops_account_expansion(adapter):
    seed(adapter)
    adapter.neighbor_results[("seed", "following")] = following("seed", "irrelevant")
    adapter.neighbor_results[("irrelevant", "following")] = following("irrelevant", "unrelated")
    result = run(e.run_research(params()))
    assert "twitter:unrelated" not in {row["account_key"] for row in result["accounts"]}
    assert next(row for row in result["nodes"] if row["account_key"] == "twitter:irrelevant")["status"] == "skipped"
    assert not any(call[:3] == ("neighbors", "twitter", "irrelevant") for call in adapter.instances[0].calls)


def test_account_record_and_edge_budgets_are_hard_limits(adapter):
    seed(adapter)
    adapter.profiles.update(child=prof("child"), other=prof("other"))
    adapter.neighbor_results[("seed", "following")] = {"accounts": [account("twitter", handle) for handle in ["child", "other"]],
        "relations": [following("seed", handle)["relations"][0] for handle in ["child", "other"]], "diagnostics": []}
    adapter.post_results["seed"] = [post("seed", serial) for serial in range(2, 12)]
    result = run(e.run_research(params(max_accounts=2, max_edges=1, max_records=3)))
    assert len(result["accounts"]) <= 2 and len(result["relations"]) <= 1 and len(result["items"]) <= 3


def test_rate_limit_stops_platform_without_fabricating_missing_edges(adapter):
    seed(adapter)
    adapter.neighbor_results[("seed", "following")] = {"accounts": [], "relations": [], "diagnostics": [{"outcome": "rate_limited", "ok": False, "message": "limited"}]}
    result = run(e.run_research(params()))
    assert result["summary"]["outcome"] == "partial" and result["summary"]["blocked_platforms"] == ["twitter"]
    assert not result["relations"]
    assert not any(call == ("neighbors", "twitter", "seed", "followers") for call in adapter.instances[0].calls)
    assert result["frontier"][0]["status"] == "blocked"


def test_news_explicit_links_can_seed_accounts_and_news_only_remains_valid(adapter):
    news = {"type": "news", "title": "日本の反戦集会", "content": "反戦集会の主催者 https://x.com/seed/status/1", "url": "https://example.org/news/1", "published_at": "2026-10-05T12:00:00+00:00"}
    adapter.seed_results[("google_news", "反戦")] = {"items": [news], "accounts": [], "relations": [], "diagnostics": []}
    adapter.profiles["seed"] = prof("seed")
    result = run(e.run_research(params(source="google_news", seed_sources=["google_news"])))
    assert result["nodes"][0]["discovery_reason"] == "news_explicit_account_link"
    assert result["nodes"][0]["evidence_urls"] == [news["url"]]
    assert result["items"][0]["type"] == "news"
    assert result["nodes"][0]["outcome"] == "platform_not_selected"
    assert not any(call[0] == "profile" for call in adapter.instances[-1].calls)
    news["content"] = "日本の反戦集会に市民が参加。"
    result = run(e.run_research(params(source="google_news", seed_sources=["google_news"])))
    assert result["items"] and not result["nodes"] and result["summary"]["outcome"] == "completed"


def test_explicit_url_extraction_does_not_guess_handles_from_names():
    result = e.linked_accounts("団体A @not_a_link https://x.com/real/status/123 https://www.instagram.com/real.name/ https://x.com/i/flow/login https://x.com/fake/following/")
    assert {row["account_key"] for row in result} == {"twitter:real", "instagram:real.name"}


def test_resume_skips_committed_queries_and_accounts(adapter):
    seed(adapter)
    first = run(e.run_research(params()))
    second = run(e.run_research(params(), resume=first))
    assert first["items"] == second["items"]
    assert not adapter.instances[-1].calls
    assert second["summary"]["visited_count"] == 1
    with pytest.raises(PipelineError, match="续查"):
        run(e.run_research(params(keyword="護憲"), resume=first))


def test_cancellation_closes_context_and_retains_previous_checkpoint(adapter):
    seed(adapter)
    entered = asyncio.Event()
    async def hanging_profile(self, platform, handle):
        entered.set()
        await asyncio.Future()
    original = adapter.profile
    adapter.profile = hanging_profile
    checkpoints = []
    async def scenario():
        task = asyncio.create_task(e.run_research(params(), checkpoint=lambda event: checkpoints.append(copy.deepcopy(event))))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    try:
        run(scenario())
    finally:
        adapter.profile = original
    assert adapter.instances[0].closed
    assert any(row["items"] for row in checkpoints)
    assert checkpoints[-1]["frontier"][0]["status"] == "processing"


@pytest.mark.parametrize("changes", [{"depth": 9}, {"max_accounts": 10001}, {"max_records": True}, {"time_budget_seconds": 2592001}, {"expand_relations": ["hidden_api"]}, {"seed_sources": ["web"]}])
def test_invalid_research_limits_fail_before_adapter(adapter, changes):
    with pytest.raises(PipelineError):
        run(e.run_research(params(**changes)))
    assert not adapter.instances


def test_ai_action_cannot_add_unknown_accounts_or_relations(adapter, monkeypatch):
    seed(adapter)
    async def model(*args, **kwargs):
        return {"priorities": [{"account_key": "twitter:invented", "priority": 100, "reason": "model guess"}], "suggested_keywords": []}
    monkeypatch.setattr(e, "configured_ai_json", model)
    result = run(e.run_research(params(analysis_mode="ai")))
    assert "twitter:invented" not in {row["account_key"] for row in result["accounts"]}
    assert any(row["action"] == "ai_decision_failed" for row in result["decisions"])
    assert not result["summary"]["ai_used"]


def test_current_topic_six_keyword_groups_accept_labour_and_student_without_peace(adapter):
    keywords = "革命,労働者,労働組合,学生運動,市民運動,政治思想"
    adapter.seed_results[("twitter", "労働者")] = {"items": [post("worker", 1, "労働者が待遇改善を要求しました。")],
        "accounts": [account("twitter", "worker")], "relations": [], "diagnostics": []}
    adapter.seed_results[("twitter", "学生運動")] = {"items": [post("student", 2, "大学の学生運動について公開討論を開催。")],
        "accounts": [account("twitter", "student")], "relations": [], "diagnostics": []}
    adapter.seed_results[("twitter", "革命")] = {"items": [post("nba", 3, "NBAの決勝と野球の試合結果。")],
        "accounts": [account("twitter", "nba")], "relations": [], "diagnostics": []}
    result = run(e.run_research(params(keyword=keywords, max_records=60, expand_relations=[])))
    assert {row["account_key"] for row in result["items"]} == {"twitter:worker", "twitter:student"}
    assert all(row["metadata"]["relevance_basis"] == "query_match" for row in result["items"])
    plan = q.build_query_plan({"keyword": "労働者"})
    assert any(row["keyword"] == "労働組合" for row in plan["suggestions"])
    assert all(row["keyword"] != "憲法九条" for row in plan["suggestions"])


def test_ai_semantics_requires_known_id_and_exact_quote_and_rejects_nba(adapter, monkeypatch):
    seed(adapter)
    adapter.post_results["seed"] = [post("seed", 2, "市民が武力行使に反対する集会を開催。"), post("seed", 3, "NBAの試合と新しいスニーカー。")]
    async def model(system, data, **kwargs):
        if "判断公开正文" in system:
            return {"relevance": [{"evidence_id": row["evidence_id"], "matched": "武力行使" in row["text"],
                "reason": "武力行使に反対する市民活動。" if "武力行使" in row["text"] else "无关体育。",
                "quote": "武力行使に反対" if "武力行使" in row["text"] else ""} for row in data["public_evidence"]]}
        return {"priorities": [], "suggested_keywords": []}
    monkeypatch.setattr(e, "configured_ai_json", model)
    result = run(e.run_research(params(analysis_mode="ai", research_goal="日本の反戦・市民活動を調査", expand_relations=[])))
    ids = {row["url"].rsplit("/", 1)[-1] for row in result["items"]}
    assert "2" in ids and "3" not in ids
    semantic = next(row for row in result["items"] if row["url"].endswith("/2"))
    assert semantic["metadata"]["relevance_basis"] == "ai_semantic"
    assert semantic["metadata"]["relevance_quote"] in semantic["content"]
    assert result["summary"]["ai_used"] and result["summary"]["ai_rounds"] <= 3


def test_false_ai_quote_never_admits_unmatched_post(adapter, monkeypatch):
    seed(adapter)
    adapter.post_results["seed"] = [post("seed", 2, "NBAの試合について。")]
    async def model(system, data, **kwargs):
        if "判断公开正文" in system:
            return {"relevance": [{"evidence_id": data["public_evidence"][0]["evidence_id"], "matched": True, "reason": "模型捏造关联。", "quote": "反戦集会"}]}
        return {"priorities": [], "suggested_keywords": []}
    monkeypatch.setattr(e, "configured_ai_json", model)
    result = run(e.run_research(params(analysis_mode="ai", expand_relations=[])))
    assert not any(row["url"].endswith("/2") for row in result["items"])
    assert any(row["action"] == "ai_semantic_review_failed" for row in result["decisions"])


def test_budget_limit_is_partial_and_unreadable_seed_is_failed_and_resumable(adapter):
    seed(adapter)
    first = run(e.run_research(params(max_records=1)))
    assert first["summary"]["outcome"] == "partial" and first["summary"]["stop_reason"] == "record_budget"
    adapter.seed_results[("twitter", "反戦")] = {"items": [], "accounts": [], "relations": [],
        "diagnostics": [{"ok": False, "outcome": "parse_error", "message": "page still loading"}]}
    failed = run(e.run_research(params()))
    assert failed["summary"]["outcome"] == "failed" and failed["summary"]["stop_reason"] == "no_readable_evidence"
    assert failed["summary"]["completed_queries"] == []
    seed(adapter)
    resumed = run(e.run_research(params(), resume=failed))
    assert resumed["items"]
    assert any(call[0] == "search" for call in adapter.instances[-1].calls)


def _analysis_snapshot(adapter, **summary_changes):
    seed(adapter)
    snapshot = run(e.run_research(params(expand_relations=[])))
    snapshot["summary"].update(summary_changes)
    return snapshot


def _batch_result(items, *, complete=False):
    evidence = [{"id": row.get("evidence_id") or r.stable_id("post", row["url"]), "type": "post", "url": row["url"],
                 "account_key": row.get("account_key"), "excerpt": row["content"]} for row in items]
    reviewed = len(evidence) if complete else min(1, len(evidence))
    return {"evidence": evidence, "findings": [], "affiliations": [], "coverage": "公开证据摘要，未声称深读全部正文。",
            "ai": {"requested": True, "used": True, "complete": complete, "outcome": "ok" if complete else "partial",
                   "input_evidence_count": reviewed, "available_evidence_count": len(evidence),
                   "pending_evidence_count": len(evidence) - reviewed, "completed_batches": 1,
                   "resumed_batches": 0, "calls_made": 1, "stop_reason": "" if complete else "time_budget",
                   "review_version": 2, "review_batches": [{"batch_id": "fixture-batch", "evidence_ids": [evidence[0]["id"]], "source_hashes": {evidence[0]["id"]: "fixture-source-hash"}}]}}


def test_partial_ai_success_is_checkpointed_and_never_declared_complete(adapter, monkeypatch):
    snapshot = _analysis_snapshot(adapter)
    snapshot["items"].append(post("seed", 99))
    events, received = [], []
    async def analyze(accounts, items, relations, **kwargs):
        received.append(kwargs)
        result = _batch_result(items)
        assert callable(kwargs.get("checkpoint")), "Successful review batches require a durable callback"
        await kwargs["checkpoint"](copy.deepcopy(result))
        return result
    monkeypatch.setattr(e, "analyze_relationships_with_ai", analyze)
    result = run(e.run_research(params(analysis_mode="ai", _analysis_only=True), resume=snapshot,
                                checkpoint=lambda event: events.append(copy.deepcopy(event))))
    assert result["summary"]["analysis_status"] == "partial"
    assert result["summary"]["outcome"] == "partial"
    assert result["summary"]["analysis_pending_evidence"] == 1
    assert received[0]["previous_analysis"] == snapshot["analysis"]
    assert 0 < received[0]["time_budget_seconds"] <= 60
    assert any(event["kind"] == "analysis" and event.get("analysis", {}).get("ai", {}).get("completed_batches") == 1 for event in events)
    assert events[-1]["analysis"]["ai"]["complete"] is False


def test_outer_analysis_timeout_keeps_last_successful_review_batch(adapter, monkeypatch):
    snapshot = _analysis_snapshot(adapter)
    snapshot["items"].append(post("seed", 99))
    async def analyze(accounts, items, relations, **kwargs):
        if not kwargs["use_ai"]:
            return _batch_result(items, complete=False)
        assert callable(kwargs.get("checkpoint"))
        await kwargs["checkpoint"](_batch_result(items))
        raise TimeoutError("Later batch exhausted time")
    monkeypatch.setattr(e, "analyze_relationships_with_ai", analyze)
    result = run(e.run_research(params(analysis_mode="ai", _analysis_only=True), resume=snapshot))
    assert result["analysis"]["ai"]["used"] is True
    assert result["analysis"]["ai"]["completed_batches"] == 1
    assert result["analysis"]["ai"]["pending_evidence_count"] == 1
    assert result["summary"]["analysis_status"] == "partial"


def test_cancelled_analysis_retains_durable_batch_checkpoint_and_propagates(adapter, monkeypatch):
    snapshot = _analysis_snapshot(adapter)
    snapshot["items"].append(post("seed", 99))
    entered, events = asyncio.Event(), []
    async def analyze(accounts, items, relations, **kwargs):
        assert callable(kwargs.get("checkpoint"))
        await kwargs["checkpoint"](_batch_result(items))
        entered.set()
        await asyncio.Future()
    monkeypatch.setattr(e, "analyze_relationships_with_ai", analyze)
    async def scenario():
        task = asyncio.create_task(e.run_research(params(analysis_mode="ai", _analysis_only=True), resume=snapshot,
                    checkpoint=lambda event: events.append(copy.deepcopy(event))))
        await asyncio.wait_for(entered.wait(), .5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    run(scenario())
    assert events[-1]["analysis"]["ai"]["completed_batches"] == 1
    assert events[-1]["summary"]["phase"] == "analysis"
    assert events[-1]["summary"]["analysis_status"] == "partial"


def test_analysis_only_resume_accepts_budget_terminal_snapshot_without_recollection(adapter, monkeypatch):
    snapshot = _analysis_snapshot(adapter, outcome="partial", stop_reason="record_budget", collection_complete=False,
                                  collection_outcome="partial", collection_stop_reason="record_budget", analysis_status="partial")
    before = len(adapter.instances)
    async def analyze(accounts, items, relations, **kwargs):
        return _batch_result(items, complete=True)
    monkeypatch.setattr(e, "analyze_relationships_with_ai", analyze)
    result = run(e.run_research(params(analysis_mode="ai", _analysis_only=True), resume=snapshot))
    assert len(adapter.instances) == before
    assert result["summary"]["analysis_status"] == "completed"
    assert result["summary"]["collection_complete"] is False
    assert result["summary"]["collection_outcome"] == "partial"
    assert result["summary"]["collection_stop_reason"] == result["summary"]["stop_reason"] == "record_budget"
    assert result["query_plan"] == snapshot["query_plan"]


def test_all_saved_items_reach_analysis_catalog_without_thousand_item_sample_claim(adapter, monkeypatch):
    snapshot = _analysis_snapshot(adapter)
    snapshot["items"] = [post("seed", index) for index in range(1001)]
    async def analyze(accounts, items, relations, **kwargs):
        assert len(items) == 1001
        return _batch_result(items, complete=True)
    monkeypatch.setattr(e, "analyze_relationships_with_ai", analyze)
    result = run(e.run_research(params(analysis_mode="ai", _analysis_only=True, max_records=1500), resume=snapshot))
    assert len(result["analysis"]["evidence"]) == 1001
    assert "analysis_sample_limit" not in result["summary"]
    assert "analysis_sample_limit" not in result["analysis"]
    assert not any(row.get("outcome") == "sampled" for row in result["diagnostics"])


def test_analysis_only_rejects_an_unfinished_collection_snapshot(adapter):
    snapshot = _analysis_snapshot(adapter, collection_complete=False, collection_outcome="running", phase="collecting")
    with pytest.raises(PipelineError, match="保存快照"):
        run(e.run_research(params(_analysis_only=True), resume=snapshot))


def test_compact_maximum_review_metadata_retains_resume_hashes_within_storage_budget():
    evidence = [{"id": r.stable_id("post", index), "type": "post", "url": f"https://x.com/alpha/status/{index}",
                 "account_key": "twitter:alpha", "excerpt": "公開本文。" * 200, "content_sha256": f"{index:064x}",
                 "source_char_count": 2400} for index in range(10240)]
    batches = [{"id": r.stable_id("batch", offset), "status": "completed",
                "evidence_hashes": {row["id"]: row["content_sha256"] for row in evidence[offset:offset + 80]}}
               for offset in range(0, len(evidence), 80)]
    value = {"evidence": evidence, "findings": [], "affiliations": [], "ai": {"review_version": 2, "complete": True,
        "used": True, "completed_batches": len(batches), "review_batches": batches, "input_evidence_count": len(evidence),
        "available_evidence_count": len(evidence), "pending_evidence_count": 0}}
    compact = e.compact_analysis_catalog(value)
    assert compact["ai"]["review_batches"] == batches
    assert compact["serialization"]["fixed_metadata_bytes"] > 1000000
    assert compact["serialization"]["stored_bytes"] <= 1500000
    assert compact["serialization"]["original_counts"]["evidence"] == 10240
    assert compact["serialization"]["omitted_counts"]["evidence"] > 0
    assert all(row["content_sha256"] == f"{int(row['url'].rsplit('/', 1)[1]):064x}" and row["source_char_count"] == 2400 for row in compact["evidence"])


def test_news_only_uses_full_record_budget_fairly_and_redistributes(adapter):
    def news(serial, keyword):
        return {"type": "news", "title": keyword, "content": keyword + "について公開記事。", "url": f"https://example.org/article/{serial}", "published_at": "2026-10-05T12:00:00+00:00"}
    adapter.seed_results[("google_news", "反戦")] = {"items": [news(serial, "反戦") for serial in range(100)], "accounts": [], "relations": [], "diagnostics": []}
    adapter.seed_results[("google_news", "護憲")] = {"items": [news(serial + 100, "護憲") for serial in range(100)], "accounts": [], "relations": [], "diagnostics": []}
    result = run(e.run_research(params(source="google_news", seed_sources=["google_news"], keyword="反戦,護憲", max_records=200)))
    assert len(result["items"]) == 200
    assert [call[3] for call in adapter.instances[-1].calls if call[0] == "search"][:2] == [100, 100]
    adapter.seed_results[("google_news", "護憲")]["items"] = []
    result = run(e.run_research(params(source="google_news", seed_sources=["google_news"], keyword="反戦,護憲", max_records=200)))
    assert len(result["items"]) == 100
    assert any(row["action"] == "reallocate_news_budget" for row in result["decisions"])


def test_research_news_current_topic_does_not_apply_historical_peace_hardgate(monkeypatch):
    rss = b'''<rss version="2.0"><channel><title>News</title><item><title>Student movement</title><link>https://example.org/student</link><description>Japanese student movement demands reform.</description><pubDate>Mon, 05 Oct 2026 12:00:00 GMT</pubDate></item></channel></rss>'''
    async def fetch(url):
        return rss, url, 200
    monkeypatch.setattr(e.pipeline, "_fetch_public_feed", fetch)
    result = run(e.collect_research_news("student movement", e.ResearchParams.parse(params(keyword="student movement")), 5))
    assert len(result["items"]) == 1
    assert result["items"][0]["metadata"]["relevance_basis"] == "query_match"
    assert result["diagnostics"][0]["ok"]


@pytest.mark.parametrize("message", [
    "采集地址解析到本机、内网或保留地址，已拒绝。",
    "可信 DoH 返回非公网地址，已拒绝采集。",
])
def test_research_news_retains_controlled_transport_failure(monkeypatch, message):
    async def fetch(url):
        raise PipelineError(message)
    monkeypatch.setattr(e.pipeline, "_fetch_public_feed", fetch)
    result = run(e.collect_research_news("student movement", e.ResearchParams.parse(params(keyword="student movement")), 5))
    diag = result["diagnostics"][0]
    assert not result["items"] and not diag["ok"]
    assert diag["outcome"] == "source_error" and diag["message"] == message
    assert diag["error_type"] == "PipelineError" and diag["stage"] == "research_news"


def test_research_news_hides_unknown_failure_values(monkeypatch):
    async def fetch(url):
        raise RuntimeError("request failed https://secret.example/?api_key=SECRET_VALUE net::ERR_CONNECTION_RESET")
    monkeypatch.setattr(e.pipeline, "_fetch_public_feed", fetch)
    result = run(e.collect_research_news("student movement", e.ResearchParams.parse(params(keyword="student movement")), 5))
    diag = result["diagnostics"][0]
    assert not result["items"] and not diag["ok"]
    assert diag["outcome"] == "source_error"
    assert diag["error_type"] == "RuntimeError" and diag["network_code"] == "net::ERR_CONNECTION_RESET"
    assert "SECRET_VALUE" not in json.dumps(result) and "secret.example" not in json.dumps(result)


def _frontier_fixture(adapter):
    """Offline observed lists, including two real fixture paths to gamma."""
    adapter.profiles.update({name: prof(name) for name in ("alpha", "beta", "gamma", "delta")})
    adapter.post_results.update({name: [post(name, index)] for index, name in enumerate(("alpha", "beta", "gamma", "delta"), 101)})
    alpha = following("alpha", "beta")
    for name in ("gamma", "delta"):
        found = following("alpha", name)
        alpha["accounts"] += found["accounts"]
        alpha["relations"] += found["relations"]
    adapter.neighbor_results[("alpha", "following")] = alpha
    adapter.neighbor_results[("beta", "following")] = following("beta", "gamma")
    return params(mode="account", handle="alpha", keyword="", max_accounts=2,
                  max_edges=20, max_records=20, max_records_per_account=3, expand_relations=["following"])


def test_deferred_account_candidates_retain_all_observed_paths_without_entering_graph(adapter):
    options = _frontier_fixture(adapter)
    checkpoints = []
    first = run(e.run_research(options, checkpoint=lambda event: checkpoints.append(copy.deepcopy(event))))
    deferred = {row["account_key"]: row for row in first["deferred_frontier"]}
    assert set(deferred) == {"twitter:gamma", "twitter:delta"}
    assert first["summary"]["deferred_count"] == 2
    assert first["summary"]["account_budget_skipped"] == 3
    assert first["summary"]["collection_complete"] is False
    assert {row["account_key"] for row in first["nodes"]} == set(first["visited"]) == {"twitter:alpha", "twitter:beta"}
    assert first["frontier"] == [] and len(first["relations"]) == 1
    gamma = deferred["twitter:gamma"]
    assert gamma["account"]["url"] == "https://x.com/gamma/"
    assert gamma["admission_state"] == "deferred" and gamma["defer_reason"] == "account_budget"
    assert {row["parent_key"] for row in gamma["discovery_paths"]} == {"twitter:alpha", "twitter:beta"}
    assert gamma["evidence_urls"] == ["https://x.com/alpha/following/", "https://x.com/beta/following/"]
    assert all(row["evidence_urls"] for row in gamma["discovery_paths"])
    assert {(row["source_key"], row["target_key"], row["relation"], row["observed_at"]) for row in gamma["relations"]} == {
        ("twitter:alpha", "twitter:gamma", "follows", "2026-10-06T01:00:00+00:00"),
        ("twitter:beta", "twitter:gamma", "follows", "2026-10-06T01:00:00+00:00")}
    assert checkpoints[-1]["deferred_frontier"] == first["deferred_frontier"]


def test_same_account_budget_resume_retains_deferred_and_never_claims_exhaustion(adapter):
    options = _frontier_fixture(adapter)
    first = run(e.run_research(options))
    second = run(e.run_research(options, resume=first))
    assert not adapter.instances[-1].calls
    assert second["deferred_frontier"] == first["deferred_frontier"]
    assert second["summary"]["collection_complete"] is False
    assert second["summary"]["collection_stop_reason"] == "account_budget"
    assert second["summary"]["deferred_count"] == 2
    assert second["summary"]["account_budget_skipped"] == first["summary"]["account_budget_skipped"]


def test_larger_account_budget_admits_deferred_replays_observed_edges_and_collects_new_only(adapter):
    options = _frontier_fixture(adapter)
    first = run(e.run_research(options))
    second = run(e.run_research({**options, "max_accounts": 4}, resume=first))
    assert {(call[0], call[2]) for call in adapter.instances[-1].calls if call[0] in {"profile", "posts"}} == {
        ("profile", "gamma"), ("posts", "gamma"), ("profile", "delta"), ("posts", "delta")}
    assert not any(call[0] == "search" or call[:3] in {("profile", "twitter", "alpha"), ("profile", "twitter", "beta")} for call in adapter.instances[-1].calls)
    assert set(second["visited"]) == {"twitter:alpha", "twitter:beta", "twitter:gamma", "twitter:delta"}
    assert {row["url"] for row in first["items"]} < {row["url"] for row in second["items"]}
    assert len(second["relations"]) == 4 and second["deferred_frontier"] == []
    assert second["summary"]["deferred_count"] == 0
    assert second["summary"]["account_budget_skipped"] == first["summary"]["account_budget_skipped"]
    assert second["summary"]["collection_complete"] is True
    assert second["summary"]["collection_stop_reason"] == "frontier_exhausted"
    gamma = next(row for row in second["nodes"] if row["account_key"] == "twitter:gamma")
    assert {row["parent_key"] for row in gamma["discovery_paths"]} == {"twitter:alpha", "twitter:beta"}
    assert gamma["depth"] == 1


def test_partly_larger_account_budget_keeps_remaining_candidate_resumable(adapter):
    options = _frontier_fixture(adapter)
    first = run(e.run_research(options))
    second = run(e.run_research({**options, "max_accounts": 3}, resume=first))
    assert len(second["accounts"]) == 3 and second["summary"]["deferred_count"] == 1
    assert second["summary"]["collection_complete"] is False
    assert second["summary"]["collection_stop_reason"] == "account_budget"
    third = run(e.run_research({**options, "max_accounts": 4}, resume=second))
    assert len(third["accounts"]) == 4 and third["summary"]["collection_complete"] is True
    assert len([call for call in adapter.instances[-1].calls if call[0] == "profile"]) == 1


def test_deferred_observed_edges_wait_for_edge_budget_without_losing_provenance(adapter):
    options = _frontier_fixture(adapter)
    first = run(e.run_research({**options, "max_edges": 1}))
    second = run(e.run_research({**options, "max_accounts": 4, "max_edges": 1}, resume=first))
    assert len(second["accounts"]) <= 4 and len(second["relations"]) == 1
    assert second["summary"]["deferred_relation_count"] >= 2
    assert second["summary"]["collection_complete"] is False
    observed = [relation for row in second["deferred_frontier"] for relation in row["relations"]]
    assert any(row["target_key"] == "twitter:gamma" and row["evidence_url"] == "https://x.com/alpha/following/" for row in observed)
    third = run(e.run_research({**options, "max_accounts": 4, "max_edges": 20}, resume=second))
    assert adapter.instances[-1].calls == [("neighbors", "twitter", "beta", "following"),
                                         ("neighbors", "twitter", "gamma", "following"),
                                         ("neighbors", "twitter", "delta", "following")]
    assert third["deferred_frontier"] == []
    assert len(third["relations"]) == 4
    assert third["summary"]["collection_complete"] is True


def test_legacy_missing_deferred_cache_cannot_become_complete_from_a_skip_counter(adapter):
    options = _frontier_fixture(adapter)
    legacy = run(e.run_research(options))
    legacy.pop("deferred_frontier", None)
    legacy["summary"].pop("deferred_frontier_version", None)
    legacy["summary"]["account_budget_skipped"] = 49
    second = run(e.run_research({**options, "max_accounts": 4}, resume=legacy))
    assert not adapter.instances[-1].calls
    assert len(second["accounts"]) == 2 and second["deferred_frontier"] == []
    assert second["summary"]["account_budget_skipped"] == 49
    assert second["summary"]["frontier_recovery_required"] is True
    assert second["summary"]["collection_complete"] is False
    assert second["summary"]["collection_stop_reason"] == "missing_deferred_frontier"
    third = run(e.run_research({**options, "max_accounts": 4}, resume=second))
    assert third["summary"]["collection_complete"] is False


def test_analysis_only_preserves_deferred_cache_and_collection_budget_boundary(adapter):
    options = _frontier_fixture(adapter)
    first = run(e.run_research(options))
    before = len(adapter.instances)
    second = run(e.run_research({**options, "_analysis_only": True}, resume=first))
    assert len(adapter.instances) == before
    assert second["deferred_frontier"] == first["deferred_frontier"]
    assert second["summary"]["collection_complete"] is False


def test_deferred_seed_items_are_saved_only_after_account_and_record_admission(adapter):
    adapter.seed_results[("twitter", "反戦")] = {"items": [post("alpha", 1), post("gamma", 2), post("gamma", 3)],
        "accounts": [account("twitter", "alpha"), account("twitter", "gamma")], "relations": [], "diagnostics": []}
    adapter.profiles.update(alpha=prof("alpha"), gamma=prof("gamma"))
    options = params(max_accounts=1, max_records=12, expand_relations=[])
    first = run(e.run_research(options))
    assert len(first["accounts"]) == len(first["items"]) == 1
    assert first["summary"]["deferred_record_count"] == 2
    assert {row["url"] for row in first["deferred_frontier"][0]["items"]} == {"https://x.com/gamma/status/2", "https://x.com/gamma/status/3"}
    second = run(e.run_research({**options, "max_accounts": 2, "max_records": 2}, resume=first))
    assert len(second["items"]) == 2 and second["summary"]["deferred_record_count"] == 1
    assert second["summary"]["collection_complete"] is False
    third = run(e.run_research({**options, "max_accounts": 2}, resume=second))
    assert len(third["items"]) == 3 and third["deferred_frontier"] == []
    assert not any(call[0] == "search" for call in adapter.instances[-1].calls)


def test_record_budget_retains_timeline_work_and_resumes_tail_without_rereading_profile(adapter):
    adapter.profiles["alpha"] = prof("alpha")
    adapter.post_results["alpha"] = [post("alpha", serial) for serial in range(1, 6)]
    options = params(mode="account", handle="alpha", keyword="", max_records=2,
                     max_records_per_account=5, expand_relations=[])
    first = run(e.run_research(options))
    assert first["visited"] == [] and len(first["items"]) == 2
    assert first["nodes"][0]["pending_work"] == {"profile": False, "posts": True, "mentions": False, "neighbors": []}
    assert first["summary"]["pending_posts_count"] == 1
    second = run(e.run_research(options, resume=first))
    assert not adapter.instances[-1].calls and second["summary"]["collection_complete"] is False
    third = run(e.run_research({**options, "max_records": 6}, resume=second))
    assert adapter.instances[-1].calls == [("posts", "twitter", "alpha", 5)]
    assert len(third["items"]) == 5 and len({row["url"] for row in third["items"]}) == 5
    assert third["items"][:2] == first["items"]
    assert third["visited"] == ["twitter:alpha"] and third["summary"]["collection_complete"] is True


def test_edge_budget_retains_unread_direction_and_resumes_only_that_branch(adapter):
    adapter.profiles.update(alpha=prof("alpha"), beta=prof("beta"), gamma=prof("gamma"))
    adapter.neighbor_results[("alpha", "following")] = following("alpha", "beta")
    adapter.neighbor_results[("alpha", "followers")] = following("alpha", "gamma", "followers")
    options = params(mode="account", handle="alpha", keyword="", max_accounts=4, max_edges=1,
                     expand_relations=["following", "followers"])
    first = run(e.run_research(options))
    alpha = next(row for row in first["nodes"] if row["account_key"] == "twitter:alpha")
    assert alpha["pending_work"]["neighbors"] == ["followers"]
    assert "twitter:alpha" not in first["visited"]
    same = run(e.run_research(options, resume=first))
    assert not adapter.instances[-1].calls and same["summary"]["collection_complete"] is False
    resumed = run(e.run_research({**options, "max_edges": 8}, resume=same))
    assert not any(call[:3] in {("profile", "twitter", "alpha"), ("posts", "twitter", "alpha"),
                                ("profile", "twitter", "beta"), ("posts", "twitter", "beta")} for call in adapter.instances[-1].calls)
    assert ("neighbors", "twitter", "alpha", "followers") in adapter.instances[-1].calls
    assert ("neighbors", "twitter", "alpha", "following") not in adapter.instances[-1].calls
    reverse = next(row for row in resumed["relations"] if row["source_key"] == "twitter:gamma")
    assert reverse["target_key"] == "twitter:alpha" and reverse["relation"] == "follows"
    assert reverse["evidence_url"] == "https://x.com/alpha/followers/"
    assert reverse["observed_at"] == "2026-10-06T01:00:00+00:00"
    assert resumed["summary"]["collection_complete"] is True


@pytest.mark.parametrize("reason", ["record_budget", "edge_budget"])
def test_legacy_budget_snapshot_without_saved_work_remains_partial(adapter, reason):
    seed(adapter)
    legacy = run(e.run_research(params(expand_relations=[])))
    legacy["summary"].pop("pending_work_version")
    legacy["summary"].update(collection_outcome="partial", collection_stop_reason=reason, collection_complete=False)
    for node in legacy["nodes"]:
        node.pop("pending_work", None)
    resumed = run(e.run_research(params(expand_relations=[], max_records=40, max_edges=20), resume=legacy))
    assert not adapter.instances[-1].calls
    assert resumed["summary"]["collection_recovery_required"] is True
    assert resumed["summary"]["collection_stop_reason"] == "missing_collection_work"
    assert resumed["summary"]["collection_complete"] is False


@pytest.mark.parametrize("scope", [{"source": "instagram", "seed_sources": ["instagram"]}, {"seed_sources": ["twitter", "instagram"]},
                                   {"relevance": "broad"}, {"research_goal": "Changed scope"},
                                   {"expand_relations": ["followers"]}, {"depth": 3}, {"max_scrolls": 3}])
def test_resume_cannot_change_saved_research_scope(adapter, scope):
    options = _frontier_fixture(adapter)
    first = run(e.run_research(options))
    before = len(adapter.instances)
    with pytest.raises(PipelineError, match="续查"):
        run(e.run_research({**options, **scope}, resume=first))
    assert len(adapter.instances) == before


def test_excluded_candidates_and_saved_pending_accounts_are_never_revived(adapter):
    options = _frontier_fixture(adapter)
    first = run(e.run_research(options))
    beta = next(row for row in first["accounts"] if row["account_key"] == "twitter:beta")
    beta["status"] = "excluded"
    first["visited"].remove("twitter:beta")
    first["excluded_account_keys"] = ["twitter:gamma"]
    second = run(e.run_research({**options, "max_accounts": 4, "_excluded_account_keys": ["twitter:delta"]}, resume=first))
    assert not adapter.instances[-1].calls
    assert {row["account_key"] for row in second["accounts"]} == {"twitter:alpha"}
    assert second["deferred_frontier"] == [] and second["relations"] == []


def test_cancelled_deferred_admission_persists_candidate_provenance_and_new_work(adapter):
    options = _frontier_fixture(adapter)
    first = run(e.run_research(options))
    original = adapter.profile
    entered, checkpoints = asyncio.Event(), []
    async def hanging(self, platform, handle):
        entered.set()
        await asyncio.Future()
    adapter.profile = hanging
    async def scenario():
        task = asyncio.create_task(e.run_research({**options, "max_accounts": 4}, resume=first,
                    checkpoint=lambda event: checkpoints.append(copy.deepcopy(event))))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    try:
        run(scenario())
    finally:
        adapter.profile = original
    saved = checkpoints[-1]
    assert saved["visited"] == ["twitter:alpha", "twitter:beta"]
    queued = next(row for row in saved["frontier"] if row["status"] == "processing")
    assert queued["pending_work"]["profile"] is True
    assert queued["evidence_urls"]
    assert len(saved["relations"]) == 4 or any(event["relations"] for event in checkpoints)
    assert adapter.instances[-1].closed


def test_same_relation_retains_both_observed_list_urls_and_directions(adapter):
    adapter.profiles.update(alpha=prof("alpha"), beta=prof("beta"))
    adapter.neighbor_results[("alpha", "following")] = following("alpha", "beta")
    adapter.neighbor_results[("beta", "followers")] = following("beta", "alpha", "followers")
    options = params(mode="account", handle="alpha", keyword="", depth=2, max_edges=8,
                     expand_relations=["following", "followers"])
    result = run(e.run_research(options))
    assert len(result["relations"]) == 1
    edge = result["relations"][0]
    assert edge["source_key"] == "twitter:alpha" and edge["target_key"] == "twitter:beta"
    assert edge["evidence_urls"] == ["https://x.com/alpha/following/", "https://x.com/beta/followers/"]
    assert {row["evidence_url"] for row in edge["observations"]} == set(edge["evidence_urls"])
    assert all(row["observed_at"] == "2026-10-06T01:00:00+00:00" for row in edge["observations"])


def test_cancellation_after_profile_checkpoint_resumes_posts_without_repeating_profile(adapter):
    adapter.profiles["alpha"] = prof("alpha")
    options = params(mode="account", handle="alpha", keyword="", expand_relations=[])
    original = adapter.posts
    entered, checkpoints = asyncio.Event(), []
    async def hanging(self, *args):
        entered.set()
        await asyncio.Future()
    adapter.posts = hanging
    async def scenario():
        task = asyncio.create_task(e.run_research(options, checkpoint=lambda event: checkpoints.append(copy.deepcopy(event))))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    try:
        run(scenario())
    finally:
        adapter.posts = original
    saved = checkpoints[-1]
    assert saved["nodes"][0]["pending_work"]["profile"] is False
    snapshot = {**saved, "accounts": [prof("alpha")], "items": [], "relations": []}
    result = run(e.run_research(options, resume=snapshot))
    assert adapter.instances[-1].calls == [("posts", "twitter", "alpha", 6)]
    assert result["summary"]["collection_complete"] is True


def test_deferred_cache_state_and_account_status_cannot_upgrade_candidate_identity(adapter):
    options = _frontier_fixture(adapter)
    first = run(e.run_research(options))
    for row in first["deferred_frontier"]:
        row["admission_state"] = "admitted"
        row["account"]["status"] = "verified"
    second = run(e.run_research({**options, "max_accounts": 4}, resume=first))
    assert {row["account_key"] for row in second["accounts"] if row["status"] == "verified"} == set()
    assert any(call == ("profile", "twitter", "gamma") for call in adapter.instances[-1].calls)


@pytest.mark.parametrize("corrupt", ["account_key", "account_url", "paths", "score"])
def test_corrupt_deferred_cache_fails_before_any_collection(adapter, corrupt):
    options = _frontier_fixture(adapter)
    first = run(e.run_research(options))
    row = first["deferred_frontier"][0]
    if corrupt == "account_key":
        row["account_key"] = "twitter:invented"
    elif corrupt == "account_url":
        row["account"]["url"] = "https://x.com/other/"
    elif corrupt == "paths":
        row["discovery_paths"] = []
    else:
        row["score"] = float("nan")
    before = len(adapter.instances)
    with pytest.raises(PipelineError, match="候选"):
        run(e.run_research({**options, "max_accounts": 4}, resume=first))
    assert len(adapter.instances) == before


def test_candidate_without_any_nonexcluded_discovery_parent_is_not_revived(adapter):
    options = _frontier_fixture(adapter)
    first = run(e.run_research(options))
    first["excluded_account_keys"] = ["twitter:alpha", "twitter:beta"]
    second = run(e.run_research({**options, "max_accounts": 4}, resume=first))
    assert not adapter.instances[-1].calls
    assert second["accounts"] == second["nodes"] == second["deferred_frontier"] == []


def test_collection_timeout_preserves_completed_profile_and_pending_timeline(adapter):
    adapter.profiles["alpha"] = prof("alpha")
    options = params(mode="account", handle="alpha", keyword="", expand_relations=[])
    original = adapter.posts
    async def timed_out(self, *args):
        raise TimeoutError("fixture cooperative collection deadline")
    adapter.posts = timed_out
    checkpoints = []
    try:
        stopped = run(e.run_research(options, checkpoint=lambda event: checkpoints.append(copy.deepcopy(event))))
    finally:
        adapter.posts = original
    assert stopped["summary"]["collection_outcome"] == "time_budget"
    assert stopped["summary"]["collection_complete"] is False
    assert stopped["visited"] == []
    assert stopped["nodes"][0]["pending_work"] == {"profile": False, "posts": True, "mentions": False, "neighbors": []}
    assert checkpoints[-1]["summary"]["pending_posts_count"] == 1
    resumed = run(e.run_research(options, resume=stopped))
    assert adapter.instances[-1].calls == [("posts", "twitter", "alpha", 6)]
    assert resumed["summary"]["collection_complete"] is True


def test_deferred_path_observation_times_survive_admission_replay(adapter, monkeypatch):
    options = _frontier_fixture(adapter)
    monkeypatch.setattr(e, "now", lambda: "2026-10-06T01:00:00+00:00")
    first = run(e.run_research(options))
    cached_paths = copy.deepcopy(next(row for row in first["deferred_frontier"] if row["account_key"] == "twitter:gamma")["discovery_paths"])
    assert all(row["observed_at"] == row["first_observed_at"] == row["last_observed_at"] == "2026-10-06T01:00:00+00:00" for row in cached_paths)
    monkeypatch.setattr(e, "now", lambda: "2026-10-09T09:00:00+00:00")
    second = run(e.run_research({**options, "max_accounts": 4}, resume=first))
    gamma = next(row for row in second["nodes"] if row["account_key"] == "twitter:gamma")
    restored_paths = [row for row in gamma["discovery_paths"] if row["reason"] == "following"]
    assert restored_paths == cached_paths


def test_same_relation_url_retains_distinct_real_observation_timestamps(adapter):
    adapter.profiles.update(alpha=prof("alpha"), beta=prof("beta"))
    initial = following("alpha", "beta")
    adapter.neighbor_results[("alpha", "following")] = initial
    options = params(mode="account", handle="alpha", keyword="", expand_relations=["following"], max_edges=8)
    first = run(e.run_research(options))
    # This observed edge arrived in another saved public path, not by changing
    # its first observation timestamp to the time of recovery.
    initial_edge = copy.deepcopy(first["relations"][0])
    later_edge = {**initial_edge, "observed_at": "2026-10-08T03:00:00+00:00"}
    first["deferred_frontier"] = [{**first["nodes"][1], "account": first["accounts"][1],
                                  "admission_state": "admitted", "defer_reason": "edge_budget",
                                  "items": [], "relations": [later_edge]}]
    resumed = run(e.run_research(options, resume=first))
    edge = resumed["relations"][0]
    assert edge["observed_at"] == initial_edge["observed_at"]
    assert edge["evidence_urls"] == ["https://x.com/alpha/following/"]
    assert {row["observed_at"] for row in edge["observations"]} == {initial_edge["observed_at"], later_edge["observed_at"]}
    assert not adapter.instances[-1].calls


def test_saved_admitted_account_can_restore_independent_pending_material_without_discovery_path(adapter):
    adapter.profiles["alpha"] = prof("alpha")
    options = params(mode="account", handle="alpha", keyword="", expand_relations=[], max_records=5)
    first = run(e.run_research(options))
    node = first["nodes"][0]
    first["deferred_frontier"] = [{**node, "account": first["accounts"][0], "admission_state": "deferred",
                                  "discovery_paths": [], "evidence_urls": [], "items": [post("alpha", 99)], "relations": []}]
    second = run(e.run_research(options, resume=first))
    assert not adapter.instances[-1].calls
    assert any(row["url"] == "https://x.com/alpha/status/99" for row in second["items"])
    assert second["deferred_frontier"] == [] and second["summary"]["collection_complete"] is True


def test_excluded_discoverer_does_not_remove_admitted_accounts_independent_pending_material(adapter):
    adapter.profiles["alpha"] = prof("alpha")
    options = params(mode="account", handle="alpha", keyword="", expand_relations=[], max_records=5)
    first = run(e.run_research(options))
    first["deferred_frontier"] = [{**first["nodes"][0], "account": first["accounts"][0], "parent_key": "twitter:removed",
                                  "admission_state": "admitted", "discovery_paths": [], "evidence_urls": [],
                                  "items": [post("alpha", 99)], "relations": []}]
    second = run(e.run_research({**options, "_excluded_account_keys": ["twitter:removed"]}, resume=first))
    assert not adapter.instances[-1].calls
    assert any(row["url"] == "https://x.com/alpha/status/99" for row in second["items"])
    assert second["summary"]["collection_complete"] is True


@pytest.mark.parametrize("field", ["max_records_per_account", "max_neighbors_per_account"])
def test_internal_resume_cannot_change_saved_per_account_collection_scope(adapter, field):
    options = _frontier_fixture(adapter)
    first = run(e.run_research(options))
    before = len(adapter.instances)
    old_value = first["summary"][field]
    with pytest.raises(PipelineError, match="续查"):
        run(e.run_research({**options, field: old_value + 1}, resume=first))
    assert len(adapter.instances) == before


def test_global_budget_change_preserves_original_per_account_parsed_defaults(adapter):
    adapter.profiles["alpha"] = prof("alpha")
    options = params(mode="account", handle="alpha", keyword="", expand_relations=[], max_accounts=1, max_records=12)
    first = run(e.run_research(options))
    second = run(e.run_research({**options, "max_accounts": 4, "max_records": 50}, resume=first))
    assert second["summary"]["max_records_per_account"] == first["summary"]["max_records_per_account"] == 12
    assert not adapter.instances[-1].calls


@pytest.mark.parametrize("source", ["google_news", "twitter"])
def test_seed_prefix_survives_repeated_record_extensions_and_only_finishes_after_exhaustion(adapter, source):
    rows = ([{"type": "news", "title": "反戦の公開活動", "content": "反戦の公開活動を記録する合成本文。",
              "url": f"https://example.org/fixture-prefix/{serial}", "published_at": "2026-10-05T12:00:00+00:00"}
             for serial in range(10)] if source == "google_news" else [post("seed", serial + 1) for serial in range(10)])
    adapter.seed_results[(source, "反戦")] = {"items": rows, "accounts": [], "relations": [], "diagnostics": []}
    adapter.profiles["seed"] = prof("seed")
    values = params(source=source, seed_sources=[source], expand_relations=[], max_records=2)
    snapshots = []
    first = run(e.run_research(values, checkpoint=lambda event: snapshots.append(copy.deepcopy(event))))
    assert len(first["items"]) == 2
    assert first["summary"]["pending_seed_query_count"] == 1
    assert first["summary"]["completed_queries"] == []
    assert any(event["summary"].get("pending_seed_queries") for event in snapshots if event["kind"] == "seed")
    same = run(e.run_research(values, resume=first))
    assert not any(call[0] == "search" for call in adapter.instances[-1].calls)
    assert same["summary"]["collection_complete"] is False
    second = run(e.run_research({**values, "max_records": 4}, resume=same))
    third = run(e.run_research({**values, "max_records": 6}, resume=second))
    assert len(second["items"]) == 4 and len(third["items"]) == 6
    assert third["summary"]["collection_complete"] is False
    assert any(call[:3] == ("search", source, "反戦") and call[3] == 6 for call in adapter.instances[-1].calls)
    final = run(e.run_research({**values, "max_records": 11}, resume=third))
    assert len(final["items"]) == 10 and final["summary"]["collection_complete"] is True
    assert final["summary"]["pending_seed_query_count"] == 0
    assert len(final["summary"]["completed_queries"]) == 1
    assert all(row["result"]["keyword"] == "反戦" for row in final["decisions"] if row["action"] == "seed_query")


def test_old_completed_refill_marker_cannot_hide_known_unexhausted_news_prefix(adapter):
    rows = [{"type": "news", "title": "反戦の公開活動", "content": "反戦の公開活動の合成本文。",
             "url": f"https://example.org/fixture-legacy-prefix/{serial}", "published_at": "2026-10-05T12:00:00+00:00"}
            for serial in range(10)]
    adapter.seed_results[("google_news", "反戦")] = {"items": rows, "accounts": [], "relations": [], "diagnostics": []}
    values = params(source="google_news", seed_sources=["google_news"], expand_relations=[], max_records=4)
    legacy = run(e.run_research(values))
    query_id = legacy["query_plan"]["queries"][0]["id"]
    query_key = "google_news:" + query_id
    legacy["summary"].pop("pending_seed_queries", None)
    legacy["summary"].pop("pending_seed_queries_version", None)
    legacy["summary"].pop("pending_work_version", None)
    legacy["summary"]["completed_queries"] = [query_key, query_key + "-news-refill"]
    legacy["summary"]["seed_query_stats"] = {query_key: {"requested_limit": 2, "returned_count": 2, "exhausted": False, "failed": False},
        query_key + "-news-refill": {"requested_limit": 4, "returned_count": 4, "exhausted": False, "failed": False}}
    resumed = run(e.run_research({**values, "max_records": 6}, resume=legacy))
    assert len(resumed["items"]) == 6 and resumed["summary"]["collection_complete"] is False
    assert any(call[:3] == ("search", "google_news", "反戦") for call in adapter.instances[-1].calls)
    assert not resumed["summary"].get("collection_recovery_required")
    final = run(e.run_research({**values, "max_records": 11}, resume=resumed))
    assert len(final["items"]) == 10 and final["summary"]["collection_complete"] is True


@pytest.mark.parametrize("stop", ["scroll_budget", "candidate_budget", "no_new_content"])
def test_successful_seed_fixed_scan_scope_is_complete_and_not_reread(adapter, stop):
    adapter.seed_results[("twitter", "反戦")] = {"items": [post()], "accounts": [], "relations": [],
        "diagnostics": [{"platform": "twitter", "ok": True, "outcome": "ok", "scan_stop_reason": stop, "scanned": 5}]}
    adapter.profiles["seed"] = prof("seed")
    values = params(max_records=3, expand_relations=[])
    first = run(e.run_research(values))
    assert first["summary"]["collection_complete"] is True and first["summary"]["pending_seed_query_count"] == 0
    second = run(e.run_research({**values, "max_records": 6}, resume=first))
    assert not any(call[0] == "search" for call in adapter.instances[-1].calls)
    assert second["summary"]["collection_complete"] is True


def test_failed_empty_seed_scan_remains_pending_until_original_query_succeeds(adapter):
    adapter.seed_results[("google_news", "反戦")] = {"items": [], "accounts": [], "relations": [],
        "diagnostics": [{"platform": "google_news", "ok": False, "outcome": "source_error"}]}
    values = params(source="google_news", seed_sources=["google_news"], expand_relations=[])
    first = run(e.run_research(values))
    assert first["summary"]["pending_seed_query_count"] == 1 and first["summary"]["completed_queries"] == []
    assert next(iter(first["summary"]["seed_query_stats"].values()))["exhausted"] is False
    adapter.seed_results[("google_news", "反戦")] = {"items": [], "accounts": [], "relations": [], "diagnostics": []}
    second = run(e.run_research(values, resume=first))
    assert second["summary"]["pending_seed_query_count"] == 0 and second["summary"]["collection_complete"] is True


def test_new_successful_query_exhaustion_is_not_reopened_by_old_larger_failed_refill(adapter):
    adapter.seed_results[("google_news", "反戦")] = {"items": [{"type": "news", "title": "反戦の公開活動",
        "content": "反戦の公開活動の合成本文。", "url": "https://example.org/refill-recovery/1", "published_at": "2026-10-05T12:00:00+00:00"}],
        "accounts": [], "relations": [], "diagnostics": []}
    values = params(source="google_news", seed_sources=["google_news"], expand_relations=[], max_records=2)
    old = run(e.run_research(values))
    key = "google_news:" + old["query_plan"]["queries"][0]["id"]
    old["summary"].pop("pending_seed_queries_version")
    old["summary"].pop("pending_seed_queries")
    old["summary"]["collection_complete"] = False
    old["summary"]["seed_query_stats"][key + "-news-refill"] = {"requested_limit": 20, "returned_count": 0, "exhausted": False, "failed": True}
    recovered = run(e.run_research(values, resume=old))
    assert any(call[0] == "search" for call in adapter.instances[-1].calls)
    assert recovered["summary"]["collection_complete"] is True and recovered["summary"]["pending_seed_query_count"] == 0
    resumed = run(e.run_research(values, resume=recovered))
    assert not adapter.instances[-1].calls
    assert resumed["summary"]["collection_complete"] is True and resumed["summary"]["pending_seed_query_count"] == 0
