from research_confirmation import confirmed_post
"""Explicit account roots, account edits and truthful mention edges."""
import asyncio

from fastapi.testclient import TestClient
import pytest

from app.workbench import research_engine as engine
from app.workbench.pipeline import PipelineError
from app.workbench.server import create_app
from app.workbench.store import Store
from test_research_engine import adapter, following, params, post, prof
from test_workbench_research_api import terminal


def test_account_walk_preserves_multiple_roots_without_an_implicit_topic(adapter):
    adapter.profiles["zengakuren"] = prof("zengakuren", "")
    adapter.post_results["zengakuren"] = [post("zengakuren", text="学生自治会からのお知らせ @zengakuren_west")]
    adapter.post_results["zengakuren_west"] = [post("zengakuren_west", 2, "関西の公開集会について。")]
    result = asyncio.run(engine.run_research(params(mode="account", handle="Zengakuren", keyword="",
        seed_accounts=["https://x.com/Zengakuren_west/", "@zengakuren"])))
    assert result["query_plan"]["raw_keyword"] == "" and result["query_plan"]["queries"] == []
    assert result["summary"]["account_scope"] is True
    assert {row["account_key"]: row["depth"] for row in result["nodes"]} == {"twitter:zengakuren": 0, "twitter:zengakuren_west": 0}
    assert len(result["items"]) == 2 and all(row["metadata"]["relevance_basis"] == "account_scope" for row in result["items"])
    assert any(row["relation"] == "mentions" and row["target_key"] == "twitter:zengakuren_west" for row in result["relations"])
    assert not any(call[0] == "search" for call in adapter.instances[0].calls)
    assert all(row["status"] == "candidate" and row["organization"] == "" for row in result["accounts"])


def test_account_walk_can_read_observed_lists_without_topic_posts(adapter):
    adapter.profiles["seed"] = prof("seed", "")
    adapter.neighbor_results[("seed", "following")] = following("seed", "beta")
    result = asyncio.run(engine.run_research(params(mode="account", handle="seed", keyword="", expand_relations=["following"])))
    assert {row["account_key"] for row in result["nodes"]} == {"twitter:seed", "twitter:beta"}
    assert len(result["relations"]) == 1 and result["relations"][0]["evidence_url"] == "https://x.com/seed/following/"


def test_optional_account_topic_still_filters_original_posts(adapter):
    adapter.post_results["seed"] = [post(text="学生自治会の公開集会"), post(serial=2, text="無関係な野球のニュース")]
    result = asyncio.run(engine.run_research(params(mode="account", handle="seed", keyword="学生", expand_relations=[])))
    assert result["query_plan"]["raw_keyword"] == "学生" and result["summary"]["account_scope"] is False
    assert [row["content"] for row in result["items"]] == ["学生自治会の公開集会"]


def test_explicit_roots_cannot_be_silently_dropped_by_account_budget(adapter):
    with pytest.raises(PipelineError, match="研究起点"):
        asyncio.run(engine.run_research(params(mode="account", handle="seed", seed_accounts=["beta"], max_accounts=1)))
    assert not adapter.instances


def test_resume_cannot_change_the_account_roots(adapter):
    original = params(mode="account", handle="seed", keyword="", expand_relations=[])
    snapshot = asyncio.run(engine.run_research(original))
    with pytest.raises(PipelineError, match="原指定账号"):
        asyncio.run(engine.run_research({**original, "handle": "another"}, resume=snapshot))
    assert len(adapter.instances) == 1


@pytest.mark.parametrize("seeds", ["alpha", [True], ["https://evil.example/alpha"], ["https://x.com/search"],
    ["https://x.com/alpha/status/1"], ["https://x.com/alpha?next=instagram.com"],
    ["https://x.com:1234/alpha"], ["https://www.instagram.com/alpha/"]])
def test_invalid_or_unselected_seed_never_opens_a_browser(adapter, seeds):
    with pytest.raises(PipelineError):
        asyncio.run(engine.run_research(params(mode="account", handle="seed", seed_accounts=seeds)))
    assert not adapter.instances


def test_account_api_accepts_roots_and_edit_preserves_all_evidence(tmp_path, adapter):
    adapter.profiles["seed"] = prof("seed", "学生自治会")
    adapter.post_results["seed"] = [post(text="学生自治会の公開集会 @beta")]
    adapter.post_results["beta"] = [post("beta", 2, "学生の公開活動")]
    db = Store(tmp_path / "account-roots.sqlite3")
    with TestClient(create_app(db)) as client:
        response = confirmed_post(client, "/api/v2/jobs", json={"kind": "research", "params": params(mode="account", handle="seed", keyword="",
            seed_accounts=["https://x.com/BETA/", "https://x.com/seed/"], expand_relations=["mentions"])})
        assert response.status_code == 202, response.text
        job = terminal(client, response.json()["id"])
        assert job["status"] == "succeeded", job
        assert job["params"]["handle"] == "seed" and job["params"]["seed_accounts"] == ["https://x.com/beta/"]
        graph = client.get("/api/v2/research/" + job["id"]).json()
        seed_node = next(row for row in graph["nodes"] if row["account_key"] == "twitter:seed")
        identity = seed_node["account_id"]
        item_ids = [row["id"] for row in graph["items"]]
        edge_sources = [row["evidence_urls"] for row in graph["edges"]]
        response = client.patch("/api/v2/accounts/" + identity, json={"name": "人工核对显示名称", "organization": "公开资料记录", "tags": ["人工记录"]})
        assert response.status_code == 200, response.text
        # Recollection must not undo curation or create a second identity.
        db.import_social([], [prof("seed", "新读取的简介")], [])
        research = client.get("/api/v2/research/" + job["id"]).json()
        registry = client.get("/api/v2/accounts/network").json()
        for data in (research, registry):
            node = next(row for row in data["nodes"] if row["account_key"] == "twitter:seed")
            assert node["account_id"] == identity and node["name"] == "人工核对显示名称" and node["organization"] == "公开资料记录"
        assert [row["id"] for row in research["items"]] == item_ids
        assert [row["evidence_urls"] for row in research["edges"]] == edge_sources
        assert client.patch("/api/v2/accounts/" + identity, json={"handle": "another_identity"}).status_code == 422
        assert db.get_account(identity)["handle"] == "seed"
        with db._transaction() as connection:
            assert not connection.execute("PRAGMA foreign_key_check").fetchall()


def test_registry_does_not_turn_email_or_url_text_into_mentions(tmp_path):
    db = Store(tmp_path / "mentions.sqlite3")
    try:
        identities = [db.save_account({"name": handle, "handle": handle, "platform": "twitter", "url": "https://x.com/" + handle}) for handle in ("alpha", "beta", "fake")]
        db.import_items([{"type": "social", "account_id": identities[0]["id"], "title": "原帖", "content": "Contact foo@beta.example https://example.org/@fake",
            "url": "https://x.com/alpha/status/200"}])
        assert db.registry_graph()["edges"] == []
        db.import_items([{"type": "social", "account_id": identities[0]["id"], "title": "原帖", "content": "公開活動 @beta",
            "url": "https://x.com/alpha/status/201"}])
        edges = db.registry_graph()["edges"]
        assert len(edges) == 1 and edges[0]["target_key"] == "twitter:beta"
    finally:
        db.close()


def test_discovered_placeholder_name_updates_but_manual_name_is_protected(tmp_path):
    db = Store(tmp_path / "public-names.sqlite3")
    try:
        first = db.import_social([], [{"platform": "twitter", "handle": "seed", "name": "seed", "url": "https://x.com/seed"}], [])
        identity = first["account_ids"]["twitter:seed"]
        observed = {**prof("seed", "学生自治会の公開案内"), "name": "公開学生自治会"}
        update = db.import_social([], [observed], [])
        saved = db.get_account(identity)
        assert update["accounts_updated"] == 1 and saved["name"] == "公開学生自治会"
        assert saved["metadata"]["last_public_profile"]["url"] == "https://x.com/seed/"
        assert saved["status"] == "candidate" and not saved["organization"]
        db.save_account({"name": "seed", "organization": "人工记录", "tags": ["保留"]}, id=identity)
        db.import_social([], [{**observed, "name": "新的公开显示名"}], [])
        saved = db.get_account(identity)
        assert saved["name"] == "seed" and saved["organization"] == "人工记录" and saved["tags"] == ["保留"]
        assert saved["metadata"]["last_public_profile"]["display_name"] == "新的公开显示名"
        assert saved["id"] == identity
    finally:
        db.close()


def test_another_profiles_name_cannot_replace_the_discovered_account(tmp_path):
    db = Store(tmp_path / "wrong-profile.sqlite3")
    try:
        first = db.import_social([], [{"platform": "twitter", "handle": "seed", "name": "seed", "url": "https://x.com/seed"}], [])
        forged = {**prof("seed"), "name": "不属于该账号的名称", "profile": {"verified_in_dom": True, "url": "https://x.com/another/"}}
        db.import_social([], [forged], [])
        saved = db.get_account(first["account_ids"]["twitter:seed"])
        assert saved["name"] == "seed" and "last_public_profile" not in saved["metadata"]
    finally:
        db.close()


def test_unrelated_checkpoint_cannot_erase_the_captured_profile_name(tmp_path):
    db = Store(tmp_path / "captured-name.sqlite3")
    try:
        job = db.create_job("research", params())
        research = db.create_research_run(job["id"], params())
        node = {"account_key": "twitter:seed", "depth": 0, "status": "completed", "score": 80, "evidence_urls": []}
        observed = {**prof("seed"), "name": "公开学生组织"}
        db.checkpoint_research(research["id"], accounts=[observed], nodes=[node])
        db.checkpoint_research(research["id"], nodes=[node], summary={"record_count": 0})
        saved = db.get_research_run(research["id"])
        assert saved["nodes"][0]["account_capture"]["name"] == "公开学生组织"
        assert saved["accounts"][0]["name"] == "公开学生组织"
        assert saved["accounts"][0]["profile"]["bio"] == observed["profile"]["bio"]
    finally:
        db.close()


def test_long_collection_reserves_time_for_final_ai_within_total_budget(adapter, monkeypatch):
    from types import SimpleNamespace
    from app.workbench.relationship_analysis import analyze_relationships
    clock = SimpleNamespace(value=0.0)
    monkeypatch.setattr(engine, "time", SimpleNamespace(monotonic=lambda: clock.value))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-test-value")
    async def profile(self, platform, handle):
        clock.value = 500
        return prof(handle)
    monkeypatch.setattr(adapter, "profile", profile)
    calls = []
    async def final_analysis(accounts, items, relations, *, use_ai, **options):
        calls.append(use_ai)
        result = analyze_relationships(accounts, items, relations)
        result["ai"] = {"requested": use_ai, "used": use_ai, "complete": use_ai, "outcome": "ok"}
        return result
    monkeypatch.setattr(engine, "analyze_relationships_with_ai", final_analysis)
    result = asyncio.run(engine.run_research(params(mode="account", handle="seed", seed_accounts=["beta"], keyword="",
        analysis_mode="auto", time_budget_seconds=600, expand_relations=[])))
    assert calls == [True] and result["summary"]["analysis_status"] == "completed"
    assert result["summary"]["collection_outcome"] == "time_budget"
    assert result["summary"]["elapsed_seconds"] < result["summary"]["time_budget_seconds"]
    assert result["summary"]["final_analysis_reserve_seconds"] == 120


def test_active_author_cannot_crowd_another_accounts_profile_out_of_ai_review(monkeypatch):
    from app.workbench import relationship_analysis as relationships
    batches = []
    async def model(system, data, **options):
        batches.append(data["evidence"])
        profile_evidence = next(row for row in data["evidence"] if row["type"] == "profile" and row["account_key"] == "twitter:beta")
        if len(batches) == 1:
            assert any(row["account_key"] == "twitter:beta" and row["type"] == "post" for row in data["evidence"])
        return {"affiliation_candidates": [{"account_key": "twitter:beta", "organization": "公开团体B", "claim": "公开简介有明确自述，仍需核对。", "evidence_ids": [profile_evidence["id"]]}]}
    monkeypatch.setattr(relationships, "configured_ai_json", model)
    accounts = [prof("alpha"), prof("beta", "公开团体B的自述")]
    items = [post("alpha", serial=index) for index in range(100)] + [post("beta", serial=201)]
    result = asyncio.run(relationships.analyze_relationships_with_ai(accounts, items, [], use_ai=True))
    assert result["ai"]["used"] and result["ai"]["complete"]
    assert result["ai"]["available_evidence_count"] == result["ai"]["input_evidence_count"]
    assert {row["id"] for batch in batches for row in batch} == {row["id"] for row in result["evidence"]}
    assert len([row for row in result["affiliations"] if row["method"] == "configured_ai"]) == 1
    assert result["affiliations"][-1]["account_key"] == "twitter:beta" and result["affiliations"][-1]["status"] == "candidate"
