from research_confirmation import confirmed_post
"""Real SQLite transactions and HTTP contracts; no platform or model calls."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import time

import pytest
from fastapi.testclient import TestClient

from app.workbench.job_retries import RetryConflict
from app.workbench.research_continuation import continuation_state
from app.workbench.research_export import checkpoint_payload
from app.workbench.server import create_app
from app.workbench.social.common import account
from app.workbench.store import Store


def parameters(**changes):
    return {"source": "twitter", "seed_sources": ["twitter"], "mode": "account", "handle": "alpha", "keyword": "",
            "start_date": "2026-10-01", "end_date": "2026-10-09", "depth": 2, "max_accounts": 1,
            "max_edges": 4, "max_records": 20, "max_scrolls": 2, "time_budget_seconds": 30,
            "max_records_per_account": 3, "expand_relations": ["following"], "analysis_mode": "rules", **changes}


def saved_run(store, *, reason="account_budget", cache=True, pending=False, status="partial"):
    params = parameters()
    original = store.create_job("research", params)
    store.create_research_run(original["id"], params)
    summary = {"outcome": "partial", "collection_outcome": "partial", "stop_reason": reason,
               "collection_stop_reason": reason, "collection_complete": False, "limit_hits": [reason],
               "deferred_frontier_version": 1, "pending_work_version": 1, "max_accounts": 1,
               "max_edges": 4, "max_records": 20, "account_budget_skipped": 9, "completed_queries": []}
    node = {"account_key": "twitter:alpha", "depth": 0, "parent_key": None, "score": 90,
            "status": "queued" if pending else "completed", "discovery_reason": "user_supplied_account",
            "evidence_urls": ["https://x.com/alpha/"],
            "pending_work": {"profile": False, "posts": pending, "mentions": False, "neighbors": []}}
    deferred = {"account_key": "twitter:beta", "account": account("twitter", "beta"), "depth": 1,
                "parent_key": "twitter:alpha", "score": 55, "status": "deferred", "admission_state": "deferred",
                "defer_reason": "account_budget", "discovery_reason": "following",
                "evidence_urls": ["https://x.com/alpha/following/"], "items": [], "relations": [],
                "discovery_paths": [{"parent_key": "twitter:alpha", "reason": "following", "depth": 1,
                                     "evidence_urls": ["https://x.com/alpha/following/"], "observed_at": "2026-10-08T03:00:00+00:00"}]}
    event = {"kind": "finished", "summary": summary, "nodes": [node], "accounts": [account("twitter", "alpha")],
             "items": [], "relations": [], "deferred_frontier": [deferred] if cache else [],
             "visited": [] if pending else ["twitter:alpha"], "frontier": [node] if pending else [],
             "query_plan": {"scope": "account", "raw_keyword": "", "combination": "any", "queries": []}}
    store.checkpoint_research(original["id"], status=status, **checkpoint_payload(event))
    store.update_job(original["id"], status=status, result={"run_id": original["id"], "summary": summary})
    return original, event


def test_direct_continuation_extends_canonical_run_and_preserves_original_job_params(tmp_path):
    store = Store(tmp_path / "extend.sqlite3")
    original, event = saved_run(store)
    before = store.get_research_run(original["id"])
    child = store.continue_research_job(original["id"], {"max_accounts": 2, "time_budget_seconds": 60})
    after = store.get_research_run(original["id"])
    assert child["status"] == "queued" and after["id"] == original["id"]
    assert after["current_job_id"] == child["id"] and after["status"] == "queued"
    assert after["params"]["max_accounts"] == 2 and after["params"]["time_budget_seconds"] == 60
    assert after["params"]["depth"] == before["params"]["depth"]
    assert after["params"]["max_scrolls"] == before["params"]["max_scrolls"]
    assert child["params"]["resume_run_id"] == original["id"]
    assert child["params"]["_workbench_retry"] == {"parent_job_id": original["id"], "root_job_id": original["id"]}
    assert store.get_job(original["id"])["params"] == original["params"]
    assert after["deferred_frontier"] == before["deferred_frontier"]
    assert after["summary"]["account_budget_skipped"] == 9
    assert store.list_research_runs()["total"] == 1


def test_concurrent_continuations_reserve_one_child(tmp_path):
    store = Store(tmp_path / "race.sqlite3")
    original, _ = saved_run(store)
    def submit(_):
        try:
            return store.continue_research_job(original["id"], {"max_accounts": 2})
        except RetryConflict:
            return "conflict"
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(8)))
    assert sum(isinstance(row, dict) for row in results) == 1
    assert results.count("conflict") == 7 and store.list_jobs()["total"] == 2
    assert Store(store.path).get_research_run(original["id"])["params"]["max_accounts"] == 2


def test_older_capped_ancestor_does_not_override_latest_failed_child_or_new_limits(tmp_path):
    store = Store(tmp_path / "latest.sqlite3")
    original, _ = saved_run(store)
    child = store.continue_research_job(original["id"], {"max_accounts": 2})
    store.update_job(child["id"], status="failed")
    second = store.continue_research_job(original["id"], {})
    assert second["params"]["max_accounts"] == 2
    assert second["params"]["_workbench_retry"] == {"parent_job_id": child["id"], "root_job_id": original["id"]}
    assert store.get_job(original["id"])["params"]["max_accounts"] == 1
    assert store.get_job(child["id"])["params"]["max_accounts"] == 2


@pytest.mark.parametrize("changes", [{}, {"max_accounts": 1}, {"max_edges": 3}, {"max_records": 19}, {"time_budget_seconds": 29}])
def test_cap_must_increase_and_other_limits_cannot_regress_atomically(tmp_path, changes):
    store = Store(tmp_path / "refusal.sqlite3")
    original, _ = saved_run(store)
    before = store.get_research_run(original["id"])
    with pytest.raises(ValueError):
        store.continue_research_job(original["id"], changes)
    assert store.list_jobs()["total"] == 1
    assert store.get_research_run(original["id"])["params"] == before["params"]
    assert store.get_research_run(original["id"])["revision"] == before["revision"]


def test_unknown_legacy_counter_without_saved_work_is_not_continuable(tmp_path):
    store = Store(tmp_path / "unknown.sqlite3")
    original, event = saved_run(store, cache=False)
    event["summary"].update(frontier_recovery_required=True, legacy_deferred_unknown=True)
    store.checkpoint_research(original["id"], summary=event["summary"])
    state = continuation_state(store.get_research_run(original["id"]))
    assert state["continuation_available"] is False and "没有保存" in state["continuation_reason"]
    with pytest.raises(ValueError, match="没有保存"):
        store.continue_research_job(original["id"], {"max_accounts": 4})
    assert store.list_jobs()["total"] == 1


def test_interrupted_real_pending_work_can_continue_same_limits(tmp_path):
    store = Store(tmp_path / "interrupted.sqlite3")
    original, event = saved_run(store, reason="time_budget", cache=False, pending=True, status="interrupted")
    child = store.continue_research_job(original["id"], {})
    assert child["params"]["max_accounts"] == 1
    assert child["params"]["time_budget_seconds"] == 30


def test_analysis_only_backlog_uses_existing_retry_and_not_collection_continuation(tmp_path):
    store = Store(tmp_path / "analysis-only.sqlite3")
    original, event = saved_run(store, cache=False)
    event["summary"].update(analysis_status="partial", analysis_pending_evidence=80)
    store.checkpoint_research(original["id"], summary=event["summary"])
    state = continuation_state(store.get_research_run(original["id"]))
    assert state["continuation_available"] is False and "分析重试" in state["continuation_reason"]
    with pytest.raises(ValueError):
        store.continue_research_job(original["id"], {"max_accounts": 2})
    retried = store.retry_job(original["id"])
    assert retried["params"]["_analysis_only"] is True


@pytest.mark.parametrize("changes", [{"depth": 3}, {"max_scrolls": 3}, {"keyword": "other"}, {"source": "instagram"},
                                     {"start_date": "2026-09-01"}, {"research_goal": "other"}, {"resume_run_id": "other"},
                                     {"max_accounts": 10001}, {"max_edges": 100001}, {"max_records": 1000001},
                                     {"time_budget_seconds": 2592001}, {"max_accounts": True}, {"max_accounts": "2"}])
def test_api_rejects_scope_and_invalid_budget_changes_without_queuing(tmp_path, changes):
    store = Store(tmp_path / "api-refusal.sqlite3")
    original, _ = saved_run(store)
    with TestClient(create_app(store, run_worker=False)) as client:
        response = confirmed_post(client, f"/api/v2/research/{original['id']}/continue", json=changes)
        assert response.status_code == 422, response.text
        assert store.list_jobs()["total"] == 1


def test_http_continuation_returns_job_and_active_duplicate_conflicts(tmp_path):
    store = Store(tmp_path / "http.sqlite3")
    original, _ = saved_run(store)
    with TestClient(create_app(store, run_worker=False)) as client:
        response = confirmed_post(client, f"/api/v2/research/{original['id']}/continue", json={"max_accounts": 2})
        assert response.status_code == 202, response.text
        child = response.json()
        assert child["params"]["resume_run_id"] == original["id"]
        repeated = confirmed_post(client, f"/api/v2/research/{original['id']}/continue", json={"max_accounts": 3})
        assert repeated.status_code == 409, repeated.text
        assert confirmed_post(client, "/api/v2/research/missing/continue", json={"max_accounts": 2}).status_code == 404


def test_api_continuation_race_creates_one_job_for_run_aliases(tmp_path):
    store = Store(tmp_path / "http-race.sqlite3")
    original, _ = saved_run(store)
    with TestClient(create_app(store, run_worker=False)) as client:
        def submit(_):
            return confirmed_post(client, f"/api/v2/research/{original['id']}/continue", json={"max_accounts": 2})
        with ThreadPoolExecutor(max_workers=6) as pool:
            responses = list(pool.map(submit, range(6)))
        assert [response.status_code for response in responses].count(202) == 1
        assert all(response.status_code in {202, 409} for response in responses)
        assert store.list_jobs()["total"] == 2


def test_failed_seed_query_plan_is_real_work_while_complete_collection_is_not(tmp_path):
    store = Store(tmp_path / "seed-query.sqlite3")
    params = parameters(mode="keyword", keyword="反戦", handle="", max_accounts=3)
    original = store.create_job("research", params)
    store.create_research_run(original["id"], params)
    store.checkpoint_research(original["id"], status="failed", summary={"collection_complete": False, "completed_queries": []})
    store.update_job(original["id"], status="failed")
    assert continuation_state(store.get_research_run(original["id"]))["continuation_pending_queries"] == 1
    child = store.continue_research_job(original["id"], {})
    assert child["params"]["resume_run_id"] == original["id"]
    store.update_job(child["id"], status="succeeded")
    store.checkpoint_research(original["id"], status="succeeded", summary={"collection_complete": True})
    with pytest.raises(ValueError):
        store.continue_research_job(original["id"], {})


def test_legacy_string_query_plan_is_safe_and_uses_original_keyword_scope():
    run = {"status": "failed", "params": parameters(mode="keyword", keyword="反戦", handle=""),
           "summary": {"collection_complete": False, "completed_queries": []},
           "query_plan": {"queries": ["憲法九条", "反戦"]}, "accounts": [], "nodes": [], "visited": []}
    state = continuation_state(run)
    assert state["continuation_available"] is True and state["continuation_pending_queries"] == 1


def test_raw_legacy_skip_counter_explains_missing_work_without_inventing_candidates():
    original_summary = {"account_budget_skipped": 49, "stop_reason": "account_budget", "collection_complete": False}
    run = {"status": "partial", "params": parameters(), "summary": deepcopy(original_summary),
           "accounts": [account("twitter", "alpha")], "nodes": [{"account_key": "twitter:alpha", "status": "completed"}],
           "visited": ["twitter:alpha"], "frontier": [], "deferred_frontier": []}
    state = continuation_state(run)
    assert state["continuation_available"] is False and "没有保存" in state["continuation_reason"]
    assert state["continuation_deferred_accounts"] == 0 and state["continuation_pending_accounts"] == 0
    assert run["summary"] == original_summary


def test_durable_totals_prevent_budget_below_saved_graph_even_for_old_inconsistent_caps(tmp_path):
    store = Store(tmp_path / "durable.sqlite3")
    original, event = saved_run(store)
    for handle in ("beta", "gamma"):
        event["accounts"].append(account("twitter", handle))
        event["nodes"].append({"account_key": "twitter:" + handle, "depth": 1, "status": "completed", "score": 55})
    event["nodes"][0]["pending_work"]["posts"] = True
    event["nodes"][0]["status"] = "queued"
    event["visited"] = []
    event["frontier"] = [event["nodes"][0]]
    store.checkpoint_research(original["id"], **checkpoint_payload(event))
    with pytest.raises(ValueError, match="已经保存"):
        store.continue_research_job(original["id"], {"max_accounts": 2})
    assert store.list_jobs()["total"] == 1


def test_parameter_update_and_child_queue_roll_back_together_if_attachment_fails(tmp_path, monkeypatch):
    store = Store(tmp_path / "rollback.sqlite3")
    original, _ = saved_run(store)
    before = store.get_research_run(original["id"])
    def fail_attachment(*args):
        raise RuntimeError("fixture attachment failure")
    monkeypatch.setattr(store, "_attach_research_job", fail_attachment)
    with pytest.raises(RuntimeError, match="attachment"):
        store.continue_research_job(original["id"], {"max_accounts": 2})
    after = store.get_research_run(original["id"])
    assert after["params"] == before["params"] and after["current_job_id"] == original["id"]
    assert after["revision"] == before["revision"] and store.list_jobs()["total"] == 1


def test_maximum_public_budget_values_are_accepted_without_running_collection(tmp_path):
    store = Store(tmp_path / "max-bounds.sqlite3")
    original, _ = saved_run(store)
    with TestClient(create_app(store, run_worker=False)) as client:
        budgets = {"max_accounts": 40, "max_edges": 200, "max_records": 10000, "time_budget_seconds": 86400}
        response = confirmed_post(client, f"/api/v2/research/{original['id']}/continue", json=budgets)
        assert response.status_code == 202, response.text
        assert all(response.json()["params"][key] == value for key, value in budgets.items())


def test_http_worker_continuation_collects_new_only_and_preserves_export_and_report_sources(tmp_path, monkeypatch):
    from app.workbench import research_engine
    class FixtureAdapter:
        calls = []
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def profile(self, platform, handle):
            self.calls.append(("profile", handle))
            return {**account(platform, handle), "profile": {"verified_in_dom": True, "bio": "公開活動。", "url": f"https://x.com/{handle}/"}}
        async def posts(self, platform, handle, options, limit, accept):
            self.calls.append(("posts", handle))
            serial = 1 if handle == "alpha" else 2
            return {"accounts": [account(platform, handle)], "items": [{"type": "social", "platform": platform,
                "account_key": f"{platform}:{handle}", "url": f"https://x.com/{handle}/status/{serial}",
                "content": f"{handle} の公開活動本文。", "title": f"{handle} の公開活動", "published_at": "2026-10-05T12:00:00+00:00"}],
                "relations": [], "diagnostics": []}
        async def neighbors(self, platform, handle, direction, options, count):
            self.calls.append(("neighbors", handle))
            if handle != "alpha":
                return {"accounts": [], "items": [], "relations": [], "diagnostics": []}
            return {"accounts": [account(platform, "beta")], "items": [], "diagnostics": [],
                    "relations": [{"source_key": "twitter:alpha", "target_key": "twitter:beta", "relation": "follows",
                                   "evidence_url": "https://x.com/alpha/following/", "observed_at": "2026-10-08T03:00:00+00:00"}]}
    monkeypatch.setattr(research_engine, "BrowserResearchAdapter", FixtureAdapter)
    store = Store(tmp_path / "worker-flow.sqlite3")
    def terminal(client, job_id):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            job = client.get(f"/api/v2/jobs/{job_id}").json()
            if job["status"] not in {"queued", "running"}:
                return job
            time.sleep(.01)
        raise AssertionError("fixture worker did not terminate")
    with TestClient(create_app(store)) as client:
        initial_params = parameters()
        initial_params.pop("max_records_per_account")
        initial = confirmed_post(client, "/api/v2/jobs", json={"kind": "research", "params": initial_params})
        assert initial.status_code == 202, initial.text
        first = terminal(client, initial.json()["id"])
        assert first["status"] == "partial", first
        run_id = first["result"]["run_id"]
        before = client.get(f"/api/v2/research/{run_id}").json()
        assert before["summary"]["deferred_count"] == 1
        old_calls = list(FixtureAdapter.calls)
        queued = confirmed_post(client, f"/api/v2/research/{run_id}/continue", json={"max_accounts": 2})
        assert queued.status_code == 202, queued.text
        second = terminal(client, queued.json()["id"])
        assert second["status"] == "succeeded", second
        assert FixtureAdapter.calls[len(old_calls):] == [("profile", "beta"), ("posts", "beta"), ("neighbors", "beta")]
        exported = client.get(f"/api/v2/research/{run_id}/export").json()
        assert exported["run"]["id"] == run_id and exported["run"]["params"]["max_accounts"] == 2
        assert len(exported["items"]) == 2 and len(exported["nodes"]) == 2
        assert exported["summary"]["account_budget_skipped"] == 1
        assert exported["summary"]["collection_complete"] is True
        report = client.get(f"/api/v2/research/{run_id}/report")
        assert report.status_code == 200
        assert "https://x.com/alpha/status/1" in report.text and "https://x.com/beta/status/2" in report.text


def test_saved_unexhausted_query_prefix_is_work_even_with_stale_completed_markers():
    from app.workbench.research_engine import build_research_plan
    values = parameters(source="google_news", seed_sources=["google_news"], mode="keyword", keyword="反戦", handle="", expand_relations=[])
    plan = build_research_plan(values)
    key = "google_news:" + plan["queries"][0]["id"]
    run = {"params": values, "status": "partial", "query_plan": plan, "summary": {"collection_complete": False,
        "completed_queries": [key, key + "-news-refill"], "seed_query_stats": {
            key: {"requested_limit": 2, "returned_count": 2, "exhausted": False, "failed": False},
            key + "-news-refill": {"requested_limit": 4, "returned_count": 4, "exhausted": False, "failed": False}}}}
    state = continuation_state(run)
    assert state["continuation_available"] is True and state["continuation_pending_queries"] == 1
    run["summary"]["collection_complete"], run["status"] = True, "succeeded"
    assert continuation_state(run)["continuation_available"] is True
    run["summary"]["seed_query_stats"][key + "-news-refill"]["exhausted"] = True
    assert continuation_state(run)["continuation_available"] is False


def test_current_successful_seed_completion_overrides_larger_failed_legacy_refill():
    from app.workbench.research_engine import build_research_plan, retained_seed_queries
    values = parameters(source="google_news", seed_sources=["google_news"], mode="keyword", keyword="反戦", handle="", expand_relations=[])
    plan = build_research_plan(values)
    key = "google_news:" + plan["queries"][0]["id"]
    summary = {"collection_complete": True, "pending_seed_queries_version": 1, "pending_seed_queries": [], "completed_queries": [key],
        "seed_query_stats": {key: {"requested_limit": 2, "returned_count": 1, "exhausted": True, "failed": False},
                            key + "-news-refill": {"requested_limit": 20, "returned_count": 0, "exhausted": False, "failed": True}}}
    assert retained_seed_queries(values, plan, summary) == []
    assert continuation_state({"status": "succeeded", "params": values, "query_plan": plan, "summary": summary})["continuation_available"] is False


@pytest.mark.parametrize("legacy_first", [False, True])
def test_http_worker_multiple_news_record_extensions_keep_original_query_and_durable_prefix(tmp_path, monkeypatch, legacy_first):
    from app.workbench import research_engine
    class NewsFixtureAdapter:
        calls = []
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def search(self, platform, keyword, options, limit, accept):
            self.calls.append((platform, keyword, options.start_date, options.end_date, options.max_scrolls, limit))
            return {"items": [{"type": "news", "url": f"https://example.org/http-prefix/{index}", "title": "反戦の公開活動",
                "content": "反戦の公開活動の合成本文。", "published_at": "2026-10-05T12:00:00+00:00"} for index in range(min(limit, 10))],
                "accounts": [], "relations": [], "diagnostics": []}
    monkeypatch.setattr(research_engine, "BrowserResearchAdapter", NewsFixtureAdapter)
    store = Store(tmp_path / "news-prefix-http.sqlite3")
    def terminal(client, job_id):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            job = client.get(f"/api/v2/jobs/{job_id}").json()
            if job["status"] not in {"queued", "running"}:
                return job
            time.sleep(.01)
        raise AssertionError("news fixture worker did not terminate")
    values = parameters(source="google_news", seed_sources=["google_news"], mode="keyword", keyword="反戦", handle="", expand_relations=[], max_records=2)
    values.pop("max_records_per_account")
    with TestClient(create_app(store)) as client:
        first = confirmed_post(client, "/api/v2/jobs", json={"kind": "research", "params": values})
        assert first.status_code == 202, first.text
        finished = terminal(client, first.json()["id"])
        assert finished["status"] == "partial", finished
        run_id = finished["result"]["run_id"]
        if legacy_first:
            legacy = deepcopy(store.get_research_run(run_id)["summary"])
            for field in ("pending_seed_queries", "pending_seed_queries_version", "pending_seed_query_count", "pending_work_version"):
                legacy.pop(field, None)
            query_key = next(iter(legacy["seed_query_stats"]))
            legacy["completed_queries"] = [query_key, query_key + "-news-refill"]
            legacy["seed_query_stats"][query_key + "-news-refill"] = deepcopy(legacy["seed_query_stats"][query_key])
            store.checkpoint_research(run_id, status="partial", summary=legacy)
        for cap, count in [(4, 4), (6, 6), (11, 10)]:
            before = client.get(f"/api/v2/research/{run_id}").json()
            assert before["run"]["continuation_available"] is True and before["run"]["continuation_pending_queries"] == 1
            if cap != 4 or not legacy_first:
                assert before["summary"]["pending_seed_query_count"] == 1
            assert not before["summary"].get("collection_recovery_required")
            calls = len(NewsFixtureAdapter.calls)
            queued = confirmed_post(client, f"/api/v2/research/{run_id}/continue", json={"max_records": cap})
            assert queued.status_code == 202, queued.text
            finished = terminal(client, queued.json()["id"])
            assert finished["status"] == ("succeeded" if cap == 11 else "partial"), finished
            assert len(NewsFixtureAdapter.calls) > calls
            exported = client.get(f"/api/v2/research/{run_id}/export").json()
            assert len(exported["items"]) == count and len({row["url"] for row in exported["items"]}) == count
            assert exported["run"]["id"] == run_id and exported["run"]["params"]["max_records"] == cap
            assert exported["summary"]["collection_complete"] is (cap == 11)
        final = client.get(f"/api/v2/research/{run_id}").json()
        assert final["run"]["continuation_available"] is False and final["summary"]["pending_seed_query_count"] == 0
        assert not final["summary"].get("collection_recovery_required")
        assert len(final["summary"]["completed_queries"]) == 1
        assert {call[:5] for call in NewsFixtureAdapter.calls} == {("google_news", "反戦", values["start_date"], values["end_date"], values["max_scrolls"])}
        assert store.list_research_runs()["total"] == 1 and store.list_jobs()["total"] == 4
        report = client.get(f"/api/v2/research/{run_id}/report")
        assert report.status_code == 200 and "https://example.org/http-prefix/9" in report.text
