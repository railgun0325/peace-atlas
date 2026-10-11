from research_confirmation import confirmed_post
"""Independent saved-frontier integration through actual API, runner and store.

Only the public-source adapter is synthetic. No network collection, credentials,
paid model, production database or application profile is used.
"""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.workbench import research_engine
from app.workbench.jobs import JobRunner
from app.workbench.server import create_app
from app.workbench.social.common import account
from app.workbench.store import Store


OBSERVED = "2026-10-09T03:00:00Z"
LONG_MARKER = "DEFERRED_ONLY_BODY_MARKER_20261009"
LONG_BODY = "公开来源正文。" * 8000 + LONG_MARKER


def parameters(**changes):
    return {"source": "twitter", "seed_sources": ["twitter"], "mode": "account", "handle": "alpha",
            "seed_accounts": [], "keyword": "", "keyword_mode": "any", "research_goal": "核对公开账号之间可追溯的关系",
            "start_date": "2026-10-01", "end_date": "2026-10-09", "depth": 2,
            "max_accounts": 1, "max_edges": 20, "max_records": 20, "max_scrolls": 2,
            "time_budget_seconds": 60, "expand_relations": ["following", "mentions"],
            "analysis_mode": "rules", "relevance": "focused", **changes}


def post(handle, serial, body):
    return {"type": "social", "platform": "twitter", "account_key": "twitter:" + handle,
            "source_name": "X / " + handle, "title": "公开来源 " + handle, "content": body,
            "url": f"https://x.com/{handle}/status/{serial}", "published_at": "2026-10-08T01:00:00Z",
            "metadata": {"content_kind": "body", "collected_at": OBSERVED}}


def profile(handle):
    return {**account("twitter", handle), "profile": {"verified_in_dom": True,
            "bio": "公开账号的活动资料。", "url": f"https://x.com/{handle}", "observed_at": OBSERVED,
            "links": [], "following_count": None, "followers_count": None}}


class PublicFixtureAdapter:
    """Deterministic source pages with a complete, inspectable read ledger."""
    calls = []
    instances = []
    scenario = "account"

    def __init__(self):
        self.closed = False
        self.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True

    def record(self, operation, platform, handle, options=None, **details):
        assert platform == "twitter", "Fixture collection is restricted to the chosen platform"
        self.calls.append({"operation": operation, "platform": platform, "handle": handle,
                           **({"start_date": options.start_date, "end_date": options.end_date,
                               "time_budget_seconds": options.time_budget_seconds,
                               "max_scrolls": options.max_scrolls} if options else {}), **details})

    async def search(self, *_):
        raise AssertionError("Account-scoped research must not launch keyword or news search")

    async def profile(self, platform, handle):
        self.record("profile", platform, handle)
        return profile(handle)

    async def posts(self, platform, handle, options, limit, accept):
        self.record("posts", platform, handle, options, limit=limit)
        if handle == "alpha" and self.scenario == "posts":
            rows = [post("alpha", 101, "第一条公开活动材料。"),
                    post("alpha", 102, "第二条活动材料提及 @beta。"),
                    post("alpha", 103, "第三条活动材料保留完整正文。")]
        else:
            text = "公开活动的第一条材料。"
            if handle == "alpha" and self.scenario in {"edge", "cleanup"}:
                text += " 提及 @beta。"
            elif handle == "beta":
                text += " 提及 @gamma。"
            rows = [post(handle, {"alpha": 101, "beta": 201, "gamma": 301}[handle], text)]
        rows = [copy.deepcopy(row) for row in rows if accept(row).get("matched")][:limit]
        return {"items": rows, "accounts": [account(platform, handle)], "relations": [], "diagnostics": []}

    async def neighbors(self, platform, handle, direction, options, count):
        self.record("neighbors", platform, handle, options, direction=direction)
        if handle != "alpha" or direction != "following":
            return {"accounts": [], "items": [], "relations": [], "diagnostics": []}
        result = {"accounts": [account(platform, "beta")], "items": [],
                  "relations": [{"source_key": "twitter:alpha", "target_key": "twitter:beta",
                                 "relation": "follows", "evidence_url": "https://x.com/alpha/following/",
                                 "observed_at": OBSERVED}], "diagnostics": []}
        if self.scenario == "account":
            result["items"] = [post("beta", 200, LONG_BODY)]
        return result


@pytest.fixture
def public_adapter(monkeypatch, request):
    PublicFixtureAdapter.calls, PublicFixtureAdapter.instances, PublicFixtureAdapter.scenario = [], [], "account"
    monkeypatch.setattr(research_engine, "BrowserResearchAdapter", PublicFixtureAdapter)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("WORKBENCH_SESSION_TOKEN", raising=False)
    yield PublicFixtureAdapter
    assert all(instance.closed for instance in PublicFixtureAdapter.instances)
    destination = os.environ.get("FRONTIER_PIPELINE_QA_DIR")
    if destination:
        directory = Path(destination)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{request.node.name}-reads.json").write_text(json.dumps({
            "synthetic_public_source": True, "scenario": PublicFixtureAdapter.scenario,
            "reads": PublicFixtureAdapter.calls, "all_adapters_closed": True}, ensure_ascii=False, indent=2), encoding="utf-8")


def execute_next(store):
    claimed = store.claim_job()
    assert claimed and claimed["kind"] == "research"
    asyncio.run(JobRunner(store)._execute(claimed))
    completed = store.get_job(claimed["id"])
    assert completed["status"] not in {"queued", "running"}, completed
    assert completed["status"] != "failed", completed
    return completed


def initial_run(store, values):
    with TestClient(create_app(store, run_worker=False)) as client:
        response = confirmed_post(client, "/api/v2/jobs", json={"kind": "research", "params": values})
        assert response.status_code == 202, response.text
        original = response.json()
    completed = execute_next(store)
    assert completed["id"] == original["id"]
    return original, completed, store.get_research_run(original["id"])


def continue_run(store, run_id, **budgets):
    params = store.get_research_run(run_id)["params"]
    request = {key: budgets.get(key, params[key]) for key in
               ("max_accounts", "max_edges", "max_records", "time_budget_seconds")}
    with TestClient(create_app(store, run_worker=False)) as client:
        response = confirmed_post(client, f"/api/v2/research/{run_id}/continue", json=request)
        assert response.status_code == 202, response.text
        child = response.json()
        repeated = confirmed_post(client, f"/api/v2/research/{run_id}/continue", json=request)
        assert repeated.status_code == 409, repeated.text
        assert store.list_jobs()["total"] == 2
    completed = execute_next(store)
    assert completed["id"] == child["id"]
    return completed, store.get_research_run(run_id)


def read_pairs(calls):
    return [(row["operation"], row["handle"]) for row in calls]


def assert_scope_and_sources(calls, params):
    for call in calls:
        assert call["platform"] == "twitter"
        if "start_date" in call:
            assert call["start_date"] == params["start_date"] and call["end_date"] == params["end_date"]
            assert 0 < call["time_budget_seconds"] <= params["time_budget_seconds"]
            assert call["max_scrolls"] == params["max_scrolls"]


def clean(store, item_id, action):
    preview = store.cleanup_preview([item_id], action)
    return store.cleanup_apply([item_id], action, preview["preview_digest"], "Synthetic user source review",
                               confirm_purge=action == "purge")


def test_account_cap_checkpoint_restart_export_continue_reads_only_new_accounts(tmp_path, public_adapter):
    path = tmp_path / "account-pipeline.sqlite3"
    store = Store(path)
    original, stopped, saved = initial_run(store, parameters())
    assert stopped["status"] == "partial" and saved["visited"] == ["twitter:alpha"]
    assert {row["account_key"] for row in saved["nodes"]} == {"twitter:alpha"}
    assert saved["summary"]["deferred_count"] == 1
    cached = next(row for row in saved["deferred_frontier"] if row["account_key"] == "twitter:beta")
    assert cached["items"][0]["content"] == LONG_BODY
    assert cached["discovery_paths"][0]["parent_key"] == "twitter:alpha"
    assert store.export_research(original["id"])["run"]["deferred_frontier"] == saved["deferred_frontier"]
    first_calls = copy.deepcopy(public_adapter.calls)
    assert read_pairs(first_calls).count(("profile", "alpha")) == 1
    with TestClient(create_app(store, run_worker=False)) as client:
        graph = client.get(f"/api/v2/research/{original['id']}")
        assert graph.status_code == 200
        assert LONG_MARKER not in graph.text
        assert {row["account_key"] for row in graph.json()["nodes"]} == {"twitter:alpha"}
        assert graph.json()["run"]["summary"]["deferred_count"] == 1
    store.close()
    reopened = Store(path)
    try:
        assert reopened.get_research_run(original["id"])["deferred_frontier"] == saved["deferred_frontier"]
        resumed_job, resumed = continue_run(reopened, original["id"], max_accounts=3)
        assert resumed_job["status"] == "succeeded", resumed_job
        new_calls = public_adapter.calls[len(first_calls):]
        assert {row["handle"] for row in new_calls} == {"beta", "gamma"}
        assert read_pairs(new_calls).count(("profile", "beta")) == read_pairs(new_calls).count(("profile", "gamma")) == 1
        assert set(resumed["visited"]) == {"twitter:alpha", "twitter:beta", "twitter:gamma"}
        assert resumed["deferred_frontier"] == []
        assert resumed["summary"]["collection_complete"] is True
        assert resumed["params"]["max_accounts"] == 3
        assert reopened.get_job(original["id"])["params"]["max_accounts"] == 1
        assert next(row for row in resumed["items"] if row["url"].endswith("/200"))["content"] == LONG_BODY
        follow = next(row for row in resumed["relations"] if row["relation"] == "follows")
        assert follow["evidence_url"] == "https://x.com/alpha/following" and follow["observed_at"].startswith("2026-10-09T03:00:00")
        assert any(row["source_key"] == "twitter:beta" and row["target_key"] == "twitter:gamma" and row["relation"] == "mentions" for row in resumed["relations"])
        assert_scope_and_sources(public_adapter.calls, parameters())
        with reopened._transaction() as connection:
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        reopened.close()


def test_record_cap_pending_posts_survive_restart_keep_mentions_and_source_dates(tmp_path, public_adapter):
    public_adapter.scenario = "posts"
    path = tmp_path / "posts-pipeline.sqlite3"
    store = Store(path)
    original, stopped, saved = initial_run(store, parameters(max_accounts=3, max_records=1))
    assert stopped["status"] == "partial"
    alpha = next(row for row in saved["nodes"] if row["account_key"] == "twitter:alpha")
    assert alpha["pending_work"]["posts"] is True and alpha["pending_work"]["profile"] is False
    first_source = next(row for row in saved["items"] if row["url"].endswith("/101"))
    before = len(public_adapter.calls)
    store.close()
    reopened = Store(path)
    try:
        done, resumed = continue_run(reopened, original["id"], max_records=10)
        assert done["status"] == "succeeded", done
        assert ("profile", "alpha") not in read_pairs(public_adapter.calls[before:])
        assert ("posts", "alpha") in read_pairs(public_adapter.calls[before:])
        assert len(resumed["items"]) == 5
        assert len({row["url"] for row in resumed["items"]}) == 5
        assert next(row for row in resumed["items"] if row["id"] == first_source["id"])["content"] == first_source["content"]
        mention = next(row for row in resumed["relations"] if row["source_key"] == "twitter:alpha" and row["relation"] == "mentions")
        assert mention["target_key"] == "twitter:beta" and mention["evidence_url"].endswith("/102")
        assert all(row["published_at"].startswith("2026-10-08T01:00:00") for row in resumed["items"])
        assert resumed["summary"]["collection_complete"] is True
        assert_scope_and_sources(public_adapter.calls, parameters())
    finally:
        reopened.close()


def test_edge_cap_pending_follow_branch_survives_restart_without_repeating_profile_or_posts(tmp_path, public_adapter):
    public_adapter.scenario = "edge"
    path = tmp_path / "edge-pipeline.sqlite3"
    store = Store(path)
    original, stopped, saved = initial_run(store, parameters(max_accounts=3, max_edges=1))
    assert stopped["status"] == "partial"
    alpha = next(row for row in saved["nodes"] if row["account_key"] == "twitter:alpha")
    assert "following" in alpha["pending_work"]["neighbors"]
    assert ("neighbors", "alpha") not in read_pairs(public_adapter.calls)
    old_mention = next(row for row in saved["relations"] if row["relation"] == "mentions")
    before = len(public_adapter.calls)
    store.close()
    reopened = Store(path)
    try:
        done, resumed = continue_run(reopened, original["id"], max_edges=8)
        assert done["status"] == "succeeded", done
        new_calls = public_adapter.calls[before:]
        assert ("profile", "alpha") not in read_pairs(new_calls) and ("posts", "alpha") not in read_pairs(new_calls)
        assert any(row["operation"] == "neighbors" and row["handle"] == "alpha" and row["direction"] == "following" for row in new_calls)
        retained = next(row for row in resumed["relations"] if row["source_key"] == "twitter:alpha" and row["relation"] == "mentions")
        assert retained["evidence_url"] == old_mention["evidence_url"]
        assert retained["first_observed_at"] == old_mention["first_observed_at"]
        follow = next(row for row in resumed["relations"] if row["relation"] == "follows")
        assert follow["source_key"] == "twitter:alpha" and follow["target_key"] == "twitter:beta"
        assert follow["evidence_url"] == "https://x.com/alpha/following"
        assert follow["observed_at"].startswith("2026-10-09T03:00:00")
        assert resumed["summary"]["collection_complete"] is True
        assert_scope_and_sources(public_adapter.calls, parameters())
    finally:
        reopened.close()


def test_fresh_runner_respects_existing_manual_exclusion(tmp_path, public_adapter):
    public_adapter.scenario = "cleanup"
    store = Store(tmp_path / "excluded-pipeline.sqlite3")
    excluded = store.save_account({"name": "Excluded public fixture", "platform": "twitter", "handle": "beta",
                                   "url": "https://x.com/beta", "status": "excluded", "tags": []})
    try:
        _, done, saved = initial_run(store, parameters(max_accounts=3))
        assert done["status"] == "succeeded", done
        assert {row["handle"] for row in public_adapter.calls} == {"alpha"}
        assert {row["account_key"] for row in saved["nodes"]} == {"twitter:alpha"}
        assert not saved["deferred_frontier"] and not saved["relations"]
        assert store.get_account(excluded["id"])["status"] == "excluded"
    finally:
        store.close()


def test_purged_mention_source_does_not_revive_but_independent_follow_path_can_continue(tmp_path, public_adapter):
    public_adapter.scenario = "cleanup"
    path = tmp_path / "cleanup-pipeline.sqlite3"
    store = Store(path)
    original, _, saved = initial_run(store, parameters())
    beta = next(row for row in saved["deferred_frontier"] if row["account_key"] == "twitter:beta")
    assert {row["reason"] for row in beta["discovery_paths"]} >= {"post_mention", "following"}
    source = saved["items"][0]
    clean(store, source["id"], "trash")
    clean(store, source["id"], "purge")
    after_cleanup = store.get_research_run(original["id"])
    beta = next(row for row in after_cleanup["deferred_frontier"] if row["account_key"] == "twitter:beta")
    assert source["url"] not in json.dumps(beta)
    assert any(row["reason"] == "following" for row in beta["discovery_paths"])
    before = len(public_adapter.calls)
    store.close()
    reopened = Store(path)
    try:
        done, resumed = continue_run(reopened, original["id"], max_accounts=3)
        assert done["status"] == "succeeded", done
        assert not any(row["handle"] == "alpha" for row in public_adapter.calls[before:])
        assert reopened.get_item(source["id"]) is None
        exported = reopened.export_research(original["id"])
        assert source["url"] in exported["run"]["metadata"]["purged_source_keys"], "Retain the tombstone that prevents resurrection"
        assert not any(row["url"] == source["url"] for row in exported["items"])
        assert all(source["url"] not in row.get("evidence_urls", []) for row in exported["edges"] + exported["claims"])
        assert all(row.get("url") != source["url"] for row in exported["run"]["analysis"].get("evidence", []))
        assert any(row["source_key"] == "twitter:alpha" and row["target_key"] == "twitter:beta" and row["relation"] == "follows" for row in resumed["relations"])
        assert not any(row["source_key"] == "twitter:alpha" and row["relation"] == "mentions" for row in resumed["relations"])
        with reopened._transaction() as connection:
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        reopened.close()
