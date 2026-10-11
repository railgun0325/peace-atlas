from research_confirmation import confirmed_post
"""The displayed research budgets must survive every validation/queue layer."""
import asyncio
import json

from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from app.workbench import research_engine
from app.workbench.batch_plan import build_batch_plan
from app.workbench.limits import budget_limits, research_branch_seconds
from app.workbench.jobs import CancelledByUser, JobRunner
from app.workbench.pipeline import PipelineError
from app.workbench.server import CollectParams, ResearchParams, create_app
from app.workbench.store import Store
from app.workbench.social.common import account


def parameters(**changes):
    return {"source": "google_news", "seed_sources": ["google_news"], "keyword": "反戦",
            "start_date": "2026-10-01", "end_date": "2026-10-06", "analysis_mode": "rules", **changes}


def test_api_and_engine_defaults_match_public_capabilities(tmp_path, monkeypatch):
    monkeypatch.delenv("WORKBENCH_SESSION_TOKEN", raising=False)
    direct, http = research_engine.ResearchParams.parse(parameters()), ResearchParams(**parameters())
    contract = budget_limits()["research"]
    for key, limits in contract.items():
        assert direct.public_limit(key) == getattr(http, key) == limits["default"]
    assert direct.max_scrolls == 10 and direct.time_budget_seconds == 0
    with TestClient(create_app(Store(tmp_path / "caps.sqlite3"), run_worker=False)) as client:
        result = client.get("/api/v2/capabilities")
        assert result.status_code == 200
        exposed = result.json()
        assert exposed["budget_limits"] == budget_limits()
        for key in ("max_accounts", "max_edges", "max_records", "max_scrolls", "time_budget_seconds"):
            assert exposed["research"][key] == contract[key]["max"]


@pytest.mark.parametrize("bound", ["min", "max"])
def test_research_lower_and_upper_bounds_accepted_by_both_parsers(bound):
    values = parameters(**{key: value[bound] for key, value in budget_limits()["research"].items()})
    for options in (ResearchParams(**values), research_engine.ResearchParams.parse(values)):
        assert (options.public_limit("max_records") if isinstance(options, research_engine.ResearchParams) else options.max_records) == values["max_records"]
        assert options.max_scrolls == values["max_scrolls"]
        assert options.time_budget_seconds == values["time_budget_seconds"]


@pytest.mark.parametrize("field,bound", [(field, bound) for field in budget_limits()["research"] for bound in ("below", "above")])
def test_out_of_range_research_budgets_rejected_by_both_parsers(field, bound):
    limits = budget_limits()["research"][field]
    value = limits["min"] - 1 if bound == "below" else limits["max"] + 1
    with pytest.raises(ValidationError):
        ResearchParams(**parameters(**{field: value}))
    with pytest.raises(PipelineError):
        research_engine.ResearchParams.parse(parameters(**{field: value}))


def test_expanded_job_and_task_series_preserve_large_budgets_atomically(tmp_path, monkeypatch):
    monkeypatch.delenv("WORKBENCH_SESSION_TOKEN", raising=False)
    store = Store(tmp_path / "large.sqlite3")
    values = parameters(max_records=10000, max_scrolls=200, time_budget_seconds=86400)
    with TestClient(create_app(store, run_worker=False)) as client:
        single = confirmed_post(client, "/api/v2/jobs", json={"kind": "research", "params": values})
        assert single.status_code == 202, single.text
        assert single.json()["params"]["max_records"] == 10000
        payload = {"kind": "research", "title": "大预算研究", "params": values, "total_records": 10000}
        preview = confirmed_post(client, "/api/v2/research/batch-plan", json=payload)
        assert preview.status_code == 200, preview.text
        assert len(preview.json()["tasks"]) == 1
        assert preview.json()["max_possible_records"] == 10000
        submitted = confirmed_post(client, "/api/v2/jobs/batch", json=payload)
        assert submitted.status_code == 202, submitted.text
        job = submitted.json()["jobs"][0]
        assert (job["params"]["max_records"], job["params"]["max_scrolls"], job["params"]["time_budget_seconds"]) == (10000, 200, 86400)
        assert submitted.json()["batch"]["planned_records"] == 10000
        assert submitted.json()["batch"]["records_saved"] == 0
        count = store.list_jobs()["total"]
        rejected = confirmed_post(client, "/api/v2/jobs/batch", json={**payload, "params": {**values, "max_records": 1000001}})
        assert rejected.status_code == 422 and store.list_jobs()["total"] == count


@pytest.mark.parametrize("kind,record_limit", [("research", 1000000), ("collect", 100)])
def test_plan_and_repository_share_child_record_ceiling(tmp_path, kind, record_limit):
    values = parameters(max_records=record_limit)
    plan = build_batch_plan("上限", kind, values, 10000)
    if kind == "research":
        assert len(plan["tasks"]) == 1 and plan["max_possible_records"] == 10000
    else:
        assert all(task["params"]["max_records"] <= 100 for task in plan["tasks"])
        assert plan["max_possible_records"] == 600
    store = Store(tmp_path / f"{kind}.sqlite3")
    batch = store.create_job_batch(plan["title"], plan["tasks"], plan["total_records"])
    assert batch["planned_records"] == plan["max_possible_records"]
    invalid = [{"kind": kind, "params": {**values, "max_records": record_limit + 1}}]
    with pytest.raises(ValueError):
        store.create_job_batch("不合法", invalid, 10000)
    with pytest.raises(ValueError):
        build_batch_plan("不合法", kind, invalid[0]["params"], 10000)
    store.close()


def test_simple_collection_keeps_its_existing_smaller_budget():
    options = CollectParams(
        source="google_news", keyword="反戦", start_date="2026-10-01", end_date="2026-10-06", max_records=100, max_scrolls=20)
    assert options.max_records == 100 and options.max_scrolls == 20
    with pytest.raises(ValidationError):
        CollectParams(source="google_news", keyword="反戦", start_date="2026-10-01", end_date="2026-10-06", max_records=101)


def test_large_time_and_scroll_budgets_do_not_force_wait_after_exhaustion(monkeypatch):
    calls, snapshots = [], []

    class Exhausted:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            calls.append("closed")

        async def search(self, source, keyword, options, limit, accept):
            calls.append((source, options.max_scrolls, limit))
            return {"items": [], "accounts": [], "relations": [], "diagnostics": [{"ok": True, "outcome": "no_results"}]}

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(research_engine, "BrowserResearchAdapter", Exhausted)

    async def run():
        return await asyncio.wait_for(research_engine.run_research(parameters(max_records=10000, max_scrolls=200, time_budget_seconds=86400), checkpoint=snapshots.append), timeout=2)

    result = asyncio.run(run())
    assert calls == [("google_news", 200, 10000), "closed"]
    assert result["summary"]["outcome"] == "completed" and result["summary"]["stop_reason"] == "no_matches"
    assert result["summary"]["record_count"] == 0
    assert snapshots[0]["kind"] == "seed" and snapshots[-1]["kind"] == "finished"
    assert research_branch_seconds(10) == 90
    assert research_branch_seconds(200) == 1230


@pytest.mark.parametrize("cancel_after_first_slice,long_material,force_analysis_failure", [(False, False, False), (True, False, False), (False, True, False), (False, True, True)])
def test_large_source_branch_saves_in_slices_and_reports_counts_beyond_graph_sample(tmp_path, monkeypatch, cancel_after_first_slice, long_material, force_analysis_failure):
    closed, saved_sizes, requested_limits = [], [], []

    class LargeNews:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            closed.append(True)

        async def search(self, source, keyword, options, limit, accept):
            requested_limits.append(limit)
            return {"items": [{"type": "news", "source_name": "公开新闻",
                     "title": f"反戦の記事 {serial}", "content": f"反戦の公開記事 {serial}。" + ("公民活動の公開資料。" * 240 if long_material else ""),
                     "url": "https://example.org/news/" + ("a" * 1700 + "/" if long_material else "") + str(serial), "published_at": "2026-10-05T00:00:00+00:00"}
                    for serial in range(min(3000, limit))], "accounts": [], "relations": [], "diagnostics": [{"ok": True}]}

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(research_engine, "BrowserResearchAdapter", LargeNews)
    store = Store(tmp_path / "large-checkpoint.sqlite3")
    values = parameters(max_records=3000, max_scrolls=200, time_budget_seconds=86400)
    job = store.create_job("research", values)
    job = store.update_job(job["id"], status="running")
    original = store.checkpoint_research
    forced = []

    def capture(run_id, **payload):
        if force_analysis_failure and payload.get("analysis", {}).get("evidence") and not forced:
            forced.append(True)
            # Exercise the real repository guard against merged historic
            # analysis rather than replacing it with a mocked exception.
            too_large = {**payload["analysis"], "historical_observations": ["x" * 2097153]}
            return original(run_id, **{**payload, "analysis": too_large})
        saved = original(run_id, **payload)
        if payload.get("items"):
            saved_sizes.append(len(payload["items"]))
            if cancel_after_first_slice:
                store.request_cancel(job["id"])
        return saved

    monkeypatch.setattr(store, "checkpoint_research", capture)
    runner = JobRunner(store)
    if cancel_after_first_slice:
        with pytest.raises(CancelledByUser):
            asyncio.run(runner._research(job))
        assert saved_sizes == [1000]
        partial = store.get_research_run(job["id"])
        assert len(partial["items"]) == partial["summary"]["record_count"] == 1000
        assert partial["summary"]["completed_queries"] == []
        assert partial["summary"]["seed_query_stats"] == {}
        assert partial["visited"] == []
        assert store.get_job(job["id"])["result"]["inserted"] == 1000
        assert store.get_job(job["id"])["result"]["summary"]["record_count"] == 1000
        store.update_job(job["id"], status="cancelled")
        resumed_job = store.create_job("research", {**values, "resume_run_id": job["id"]})
        resumed_job = store.update_job(resumed_job["id"], status="running")
        recovered = asyncio.run(runner._research(resumed_job))
        assert recovered["inserted"] == 2000 and recovered["fetched"] == 3000
        restored = store.get_research_run(job["id"])
        assert len(restored["items"]) == restored["summary"]["record_count"] == 3000
        # Recovering all retained rows at the exact cap does not establish
        # that the source is exhausted. Keep the original query pending
        # until a larger read proves that no tail remains.
        assert restored["summary"]["completed_queries"] == []
        assert restored["summary"]["pending_seed_query_count"] == 1
        assert restored["summary"]["collection_complete"] is False
        assert requested_limits == [3000, 3000]
        assert saved_sizes == [1000, 1000, 1000]
        store.update_job(resumed_job["id"], status="partial")
        extended_job = store.continue_research_job(job["id"], {"max_records": 4000})
        extended_job = store.update_job(extended_job["id"], status="running")
        exhausted = asyncio.run(runner._research(extended_job))
        final = store.get_research_run(job["id"])
        assert exhausted["inserted"] == 0 and exhausted["fetched"] == 3000
        assert len(final["items"]) == final["summary"]["record_count"] == 3000
        assert len(final["summary"]["completed_queries"]) == 1
        assert final["summary"]["pending_seed_query_count"] == 0
        assert final["summary"]["collection_complete"] is True
        assert requested_limits == [3000, 3000, 4000]
        assert saved_sizes == [1000, 1000, 1000]
    else:
        result = asyncio.run(runner._research(job))
        assert saved_sizes == [1000, 1000, 1000]
        assert result["fetched"] == result["inserted"] == 3000
        assert result["duplicates"] == 0
        assert result["nodes"] == result["edges"] == 0
        assert result["_status"] == "partial" and result["summary"]["stop_reason"] == "record_budget"
        assert result["summary"]["analysis_catalog_scope"] == "all_saved_evidence"
        assert result["summary"]["analysis_available_items"] == 3000
        assert "analysis_sample_limit" not in result["summary"]
        assert not any(row.get("outcome") == "sampled" for row in result["diagnostics"])
        graph = store.research_graph(job["id"], limit=300)
        assert len(graph["items"]) == 1200 and graph["total_items"] == 3000
        assert graph["items_truncated"] is True and graph["truncated"] is True
        stored = store.get_research_run(job["id"])
        assert len(stored["items"]) == 3000
        assert len(json.dumps(stored["analysis"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= 2097152
        if long_material:
            assert all(len(row["content"]) > 2400 for row in stored["items"])
            assert all(len(row["url"]) > 1700 for row in stored["items"])
            if force_analysis_failure:
                assert forced == [True] and result["summary"]["analysis_storage"] == "partial"
                assert any(row.get("stage") == "analysis_storage" and row.get("ok") is False for row in result["diagnostics"])
            else:
                compact = stored["analysis"]
                assert compact["serialization"]["excerpt_compacted_count"] == 3000
                assert compact["serialization"]["original_counts"]["evidence"] == 3000
                assert 0 < compact["serialization"]["stored_counts"]["evidence"] < 3000
                assert compact["serialization"]["omitted_counts"]["evidence"] == 3000 - compact["serialization"]["stored_counts"]["evidence"]
                assert all(len(row["excerpt"]) <= 240 and len(row["url"]) > 1700 for row in compact["evidence"])
                assert any(row.get("outcome") == "analysis_size_budget" for row in result["diagnostics"])
    assert closed == ([True, True, True] if cancel_after_first_slice else [True])
    store.close()


def test_timeline_evidence_quota_scales_with_selected_record_budget():
    small = research_engine.ResearchParams.parse(parameters(max_records=50, max_accounts=12))
    large = research_engine.ResearchParams.parse(parameters(max_records=10000, max_accounts=40))
    assert small.max_records_per_account == 6
    assert large.max_records_per_account == 250
    assert large.max_neighbors_per_account == 100


def test_research_adapter_passes_expanded_budgets_and_internal_deadlines_to_collectors(monkeypatch):
    from types import SimpleNamespace
    received = []
    page = object()
    elapsed = [100.0]
    # Model the page visit's elapsed time independently of the OS scheduler;
    # changing the module binding leaves asyncio's own clock untouched.
    monkeypatch.setattr(research_engine, "time", SimpleNamespace(monotonic=lambda: elapsed[0]))

    async def existing_page(self, platform):
        elapsed[0] += .25
        return page

    async def collect(selected_page, values, **kwargs):
        assert selected_page is page
        received.append((values, kwargs))
        return {"items": [], "accounts": [], "relations": [], "diagnostics": []}

    async def neighbors(selected_page, platform, handle, direction, **kwargs):
        assert selected_page is page
        received.append(({"source": platform, "mode": direction}, kwargs))
        return {"accounts": [], "relations": [], "diagnostics": []}

    monkeypatch.setattr(research_engine.BrowserResearchAdapter, "page", existing_page)
    monkeypatch.setattr(research_engine.collector, "collect_on_page", collect)
    monkeypatch.setattr(research_engine.collector, "read_neighbors", neighbors)
    options = research_engine.ResearchParams.parse(parameters(source="twitter", seed_sources=["twitter"], max_records=10000, max_scrolls=200, time_budget_seconds=50))

    async def scenario():
        async with research_engine.BrowserResearchAdapter() as adapter:
            await adapter.search("twitter", "反戦", options, 500, lambda item: True)
            await adapter.posts("twitter", "alpha", options, 200, lambda item: True)
            await adapter.neighbors("twitter", "alpha", "following", options, 12)

    asyncio.run(scenario())
    assert [entry[0].get("max_records") for entry in received[:2]] == [500, 200]
    assert [entry[0]["max_scrolls"] for entry in received[:2]] == [200, 200]
    assert received[2][1]["max_scrolls"] == 200
    assert all(0 < entry[1]["time_budget_seconds"] < 50 for entry in received)


def test_compact_analysis_prioritizes_complete_supporting_sources_before_other_catalogue_rows():
    evidence = [{"id": str(number), "type": "post", "url": "https://example.org/" + "a" * 1800 + str(number),
                 "account_key": "twitter:alpha", "excerpt": "公開証拠。" * 400} for number in range(100)]
    finding = {"id": "related", "source_key": "twitter:alpha", "target_key": "twitter:beta", "relation": "shared_link",
               "evidence_ids": ["99", "98"], "evidence_urls": [evidence[99]["url"], evidence[98]["url"]], "summary": "共有公開リンク。"}
    compact = research_engine.compact_analysis_catalog({"version": 1, "evidence": evidence, "findings": [finding], "affiliations": [], "ai": {"used": False}}, max_bytes=64000)
    assert compact["findings"] == [finding]
    retained = {row["id"]: row for row in compact["evidence"]}
    assert set(finding["evidence_ids"]) <= retained.keys()
    assert retained["99"]["url"] == evidence[99]["url"]
    assert compact["serialization"]["omitted_counts"]["evidence"] > 0
    assert compact["serialization"]["omitted_counts"]["findings"] == 0
    assert len(json.dumps(compact, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= 64000


def test_partial_account_timeline_retains_frontier_and_resume_reads_beyond_saved_prefix(monkeypatch):
    requested, closed = [], []
    posts = [{"platform": "twitter", "type": "social", "account_key": "twitter:alpha", "title": "反戦公開活動",
              "content": f"反戦公開活動 {number}", "url": f"https://x.com/alpha/status/{number}",
              "published_at": "2026-10-05T00:00:00+00:00", "metadata": {"content_kind": "body"}} for number in range(1, 4)]

    class PartialTimeline:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            closed.append(True)

        async def profile(self, platform, handle):
            return {**account(platform, handle), "profile": {"verified_in_dom": True, "bio": "反戦公開活動",
                    "url": "https://x.com/alpha", "observed_at": "2026-10-06T00:00:00Z"}}

        async def posts(self, platform, handle, options, limit, accept):
            requested.append(limit)
            incomplete = len(requested) == 1
            return {"items": posts[:min(1 if incomplete else len(posts), limit)], "accounts": [account(platform, handle)], "relations": [],
                    "diagnostics": [{"ok": False, "outcome": "timeout", "partial": True}] if incomplete else [{"ok": True}]}

    monkeypatch.setattr(research_engine, "BrowserResearchAdapter", PartialTimeline)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    values = parameters(source="twitter", seed_sources=["twitter"], mode="account", handle="alpha",
                        max_records=3, max_accounts=1, expand_relations=[], time_budget_seconds=86400)
    partial = asyncio.run(research_engine.run_research(values))
    assert len(partial["items"]) == 1
    assert partial["visited"] == [] and len(partial["frontier"]) == 1
    assert partial["nodes"][0]["status"] == "failed"
    resumed = asyncio.run(research_engine.run_research(values, resume=partial))
    assert requested == [3, 3]
    assert len(resumed["items"]) == 3 and resumed["summary"]["record_count"] == 3
    # Returning exactly the global record allowance does not establish that
    # the original per-account timeline scope was exhausted.
    assert resumed["visited"] == [] and len(resumed["frontier"]) == 1
    assert resumed["summary"]["stop_reason"] == "record_budget"
    completed = asyncio.run(research_engine.run_research({**values, "max_records": 6}, resume=resumed))
    assert requested == [3, 3, 6]
    assert completed["visited"] == ["twitter:alpha"] and completed["frontier"] == []
    assert len(completed["items"]) == 3
    assert closed == [True, True, True]
