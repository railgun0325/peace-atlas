from research_confirmation import confirmed_post
"""Real API/runner/engine/checkpoint integration; no live browser or paid AI."""
import asyncio
import copy
import json
import threading
import time

from fastapi.testclient import TestClient
import pytest

from app.workbench import pipeline, relationship_analysis, research_engine
from app.workbench.jobs import CancelledByUser, JobRunner
from app.workbench.server import create_app
from app.workbench.social.common import SocialError, account
from app.workbench.store import Store


def parameters(**changes):
    return {"source": "twitter", "seed_sources": ["twitter"], "keyword": "反戦", "start_date": "2026-10-01", "end_date": "2026-10-06",
            "depth": 2, "max_accounts": 8, "max_edges": 12, "max_records": 20, "max_scrolls": 2,
            "time_budget_seconds": 60, "expand_relations": ["following"], "analysis_mode": "rules", **changes}


def post(handle="alpha", number=10, text=None):
    text = text or "日本の反戦運動の平和集会を開催 https://example.org/event/123"
    return {"type": "social", "platform": "twitter", "account_key": "twitter:" + handle, "source_name": "X / " + handle,
            "title": text[:50], "content": text, "url": f"https://x.com/{handle}/status/{number}",
            "published_at": "2026-10-05T00:00:00Z", "metadata": {"content_kind": "body"}}


def profile(handle):
    return {**account("twitter", handle), "profile": {"verified_in_dom": True,
        "bio": "公開平和団体の公式アカウント。日本の反戦運動について発信。" if handle == "alpha" else "日本の反戦運動について発信。",
        "url": f"https://x.com/{handle}", "observed_at": "2026-10-06T00:00:00Z", "links": []}}


class EvidenceAdapter:
    instances = []
    calls = []

    def __init__(self):
        self.closed = False
        self.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def search(self, platform, keyword, options, limit, accept):
        self.calls.append(("search", platform, keyword))
        return {"items": [post(text="日本の反戦運動の集会 @unknown_account https://example.org/event/123")],
                "accounts": [account("twitter", "alpha")], "relations": [], "diagnostics": [{"source": platform, "ok": True}]}

    async def profile(self, platform, handle):
        self.calls.append(("profile", platform, handle))
        return profile(handle)

    async def posts(self, platform, handle, options, limit, accept):
        self.calls.append(("posts", platform, handle))
        return {"items": [post(handle, 11 if handle == "alpha" else 12)], "accounts": [account(platform, handle)], "relations": [], "diagnostics": []}

    async def neighbors(self, platform, handle, direction, options, count):
        self.calls.append(("neighbors", platform, handle, direction))
        if handle != "alpha":
            return {"accounts": [], "relations": [], "diagnostics": []}
        return {"accounts": [account(platform, "beta")], "relations": [{"source_key": "twitter:alpha", "target_key": "twitter:beta",
            "relation": "follows", "evidence_url": "https://x.com/alpha/following", "observed_at": "2026-10-06T00:00:00Z"}], "diagnostics": []}


@pytest.fixture
def adapter(monkeypatch):
    EvidenceAdapter.instances, EvidenceAdapter.calls = [], []
    monkeypatch.setattr(research_engine, "BrowserResearchAdapter", EvidenceAdapter)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("WORKBENCH_SESSION_TOKEN", raising=False)
    return EvidenceAdapter


def terminal(client, id, timeout=7):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v2/jobs/{id}")
        assert response.status_code == 200, response.text
        record = response.json()
        if record["status"] not in {"queued", "running"}:
            return record
        time.sleep(0.02)
    raise AssertionError(f"research did not finish: {record}")


def test_real_engine_api_stores_tree_evidence_claims_and_portable_exports(tmp_path, adapter):
    store = Store(tmp_path / "api.sqlite3")
    with TestClient(create_app(store)) as client:
        response = confirmed_post(client, "/api/v2/jobs", json={"kind": "research", "params": parameters()})
        assert response.status_code == 202, response.text
        job = terminal(client, response.json()["id"])
        assert job["status"] == "succeeded", job
        graph = client.get("/api/v2/research/" + job["id"]).json()
        assert {row["account_key"]: row["depth"] for row in graph["nodes"]} == {"twitter:alpha": 0, "twitter:beta": 1}
        assert len(graph["items"]) == 3
        assert {row["relationship"] for row in graph["edges"]} >= {"follows", "shared_link", "shared_event"}
        assert all(row["target_key"] != "twitter:unknown_account" for row in graph["edges"])
        assert len(graph["claims"]) == 1 and graph["claims"][0]["status"] == "candidate"
        assert all(not row["organization"] and row["status"] == "candidate" for row in graph["accounts"])
        claim = graph["claims"][0]
        route = f"/api/v2/research/{job['id']}/claims/{claim['id']}/review"
        assert confirmed_post(client, route, json={"status": "confirmed"}).status_code == 422
        assert confirmed_post(client, route, json={"status": "confirmed", "evidence_url": "https://example.org/organization/about", "note": "公开来源确认"}).status_code == 200
        checked = client.get("/api/v2/research/" + job["id"]).json()
        assert checked["claims"][0]["status"] == "confirmed"
        assert all(not row["organization"] for row in checked["accounts"])
        exported = client.get(f"/api/v2/research/{job['id']}/export")
        assert exported.status_code == 200 and exported.json()["run"]["id"] == job["id"]
        report = client.get(f"/api/v2/research/{job['id']}/report")
        assert report.status_code == 200 and "https://x.com/i/status/10" in report.text
        assert "关注关系不等于组织隶属" in report.text and "共同链接" in report.text
        assert client.get("/api/v2/research?page_size=1").json()["total"] == 1
    assert all(instance.closed for instance in adapter.instances)


def test_record_budget_finishes_partial_with_saved_evidence(tmp_path, adapter):
    with TestClient(create_app(Store(tmp_path / "budget.sqlite3"))) as client:
        response = confirmed_post(client, "/api/v2/jobs", json={"kind": "research", "params": parameters(max_records=1)})
        job = terminal(client, response.json()["id"])
        assert job["status"] == "partial", job
        graph = client.get(f"/api/v2/research/{job['id']}").json()
        assert len(graph["items"]) == 1
        assert graph["summary"]["stop_reason"] == "record_budget"


def test_ai_shape_maps_to_candidate_claim_without_charging_provider(tmp_path, adapter, monkeypatch):
    calls = []
    async def organization_profile(self, platform, handle):
        result = profile(handle)
        if handle == "alpha":
            result["profile"]["bio"] = "東都学生連盟の公式アカウント。日本の反戦運動について発信。"
        return result
    monkeypatch.setattr(adapter, "profile", organization_profile)
    async def model(*args, **kwargs):
        calls.append(args)
        own = next(row for row in args[1]["evidence"] if row["account_key"] == "twitter:alpha" and row["type"] == "profile")
        return {"affiliation_candidates": [{"account_key": "twitter:alpha", "organization": "東都学生連盟",
            "claim": "自述与署名支持的待核对候选", "evidence_ids": [own["id"]]}]}
    monkeypatch.setattr(relationship_analysis, "configured_ai_json", model)
    async def analysis(accounts, items, relations, *, use_ai=False, **kwargs):
        return await relationship_analysis.analyze_relationships_with_ai(accounts, items, relations, use_ai=True, **kwargs)
    monkeypatch.setattr(research_engine, "analyze_relationships_with_ai", analysis)
    with TestClient(create_app(Store(tmp_path / "ai.sqlite3"))) as client:
        response = confirmed_post(client, "/api/v2/jobs", json={"kind": "research", "params": parameters()})
        job = terminal(client, response.json()["id"])
        assert job["status"] == "succeeded", job
        graph = client.get(f"/api/v2/research/{job['id']}").json()
        inferred = next(row for row in graph["claims"] if row["object_label"] == "東都学生連盟" and row["basis"] == "inferred")
        assert inferred["basis"] == "inferred" and inferred["status"] == "candidate"
        assert inferred["evidence_urls"] and calls
        assert all(not row["organization"] for row in graph["accounts"])


def test_required_ai_failure_is_partial_then_retry_uses_saved_snapshot_only(tmp_path, adapter, monkeypatch):
    calls = []
    success = [False]
    async def analyze(accounts, items, relations, *, use_ai=False, **kwargs):
        calls.append((len(items), use_ai))
        result = await relationship_analysis.analyze_relationships_with_ai(accounts, items, relations, use_ai=False)
        result["ai"] = {"requested": use_ai, "used": use_ai and success[0], "complete": use_ai and success[0],
                        "available_evidence_count": len(result["evidence"]), "input_evidence_count": len(result["evidence"]) if success[0] else 0,
                        "pending_evidence_count": 0 if success[0] else len(result["evidence"]),
                        "outcome": "completed" if success[0] else "provider_error", "message": "离线合成模型状态"}
        return result
    monkeypatch.setattr(research_engine, "analyze_relationships_with_ai", analyze)
    store = Store(tmp_path / "analysis-only.sqlite3")
    first = store.create_job("research", parameters(analysis_mode="ai"))
    asyncio.run(JobRunner(store)._execute(store.claim_job()))
    stopped = store.get_job(first["id"])
    assert stopped["status"] == "partial", stopped
    assert stopped["result"]["summary"]["collection_complete"] is True
    assert stopped["result"]["summary"]["analysis_status"] == "failed"
    visited = list(adapter.calls)
    before = store.list_items()["total"]
    retry = store.retry_job(first["id"])
    assert retry["params"]["_analysis_only"] is True
    success[0] = True
    asyncio.run(JobRunner(store)._execute(store.claim_job()))
    completed = store.get_job(retry["id"])
    assert completed["status"] == "succeeded", completed
    assert completed["result"]["summary"]["analysis_status"] == "completed"
    assert completed["result"]["summary"]["analysis_only"] is True
    assert adapter.calls == visited, "Analysis-only retry must not open a browser, seed search, profile or timeline"
    assert store.list_items()["total"] == before and calls[-1][0] > 0
    assert not any(row.get("stage") == "relationship_analysis" and not row.get("ok") for row in completed["result"]["diagnostics"])
    store.close()


def test_failed_second_ai_batch_is_durable_and_retry_reuses_review_without_browser(tmp_path, adapter, monkeypatch):
    calls, fail = [], [True]
    async def many_posts(self, platform, handle, options, limit, accept):
        self.calls.append(("posts", platform, handle))
        return {"items": [post(handle, index) for index in range(130)], "accounts": [account(platform, handle)],
                "relations": [], "diagnostics": []}
    monkeypatch.setattr(adapter, "posts", many_posts)
    async def model(system, data, **kwargs):
        calls.append([row["id"] for row in data["evidence"]])
        if fail[0] and len(calls) == 2:
            raise pipeline.PipelineError("合成第二批模型读取失败；第一批必须保留。")
        return {"affiliation_candidates": []}
    monkeypatch.setattr(relationship_analysis, "configured_ai_json", model)
    store = Store(tmp_path / "two-batch.sqlite3")
    try:
        values = parameters(mode="account", handle="alpha", keyword="", max_records=140, max_records_per_account=140,
                            max_accounts=1, expand_relations=[], analysis_mode="ai", time_budget_seconds=180)
        first = store.create_job("research", values)
        asyncio.run(JobRunner(store)._execute(store.claim_job()))
        stopped = store.get_job(first["id"])
        captured = store.get_research_run(first["id"])
        assert stopped["status"] == "partial"
        assert captured["analysis"]["ai"]["completed_batches"] == 1
        assert captured["analysis"]["ai"]["complete"] is False
        assert captured["summary"]["analysis_status"] == "partial"
        assert captured["summary"]["analysis_pending_evidence"] == 51
        assert len(captured["items"]) == 130
        first_capture = copy.deepcopy(captured)
        browser_calls = list(adapter.calls)
        fail[0] = False
        child = store.retry_job(first["id"])
        assert child["params"]["_analysis_only"] is True
        asyncio.run(JobRunner(store)._execute(store.claim_job()))
        done, captured = store.get_job(child["id"]), store.get_research_run(first["id"])
        assert done["status"] == "succeeded", done
        assert adapter.calls == browser_calls
        if len(calls) != 3:
            current_evidence = {row["id"]: row for row in relationship_analysis.analyze_relationships(first_capture["accounts"], first_capture["items"], first_capture["relations"])["evidence"]}
            prior_evidence = {row["id"]: row for row in first_capture["analysis"]["evidence"]}
            expected = first_capture["analysis"]["ai"]["review_batches"][0]["evidence_hashes"]
            mismatches = [eid for eid, digest in expected.items() if eid not in current_evidence or relationship_analysis._review_hash(current_evidence[eid]) != digest]
            print(json.dumps({"before_context": first_capture["analysis"]["ai"]["context_fingerprint"],
                "after_context": captured["analysis"]["ai"]["context_fingerprint"],
                "resumed_batches": captured["analysis"]["ai"]["resumed_batches"], "hash_mismatch_count": len(mismatches),
                "examples": [{"id": eid, "before": prior_evidence[eid], "after": current_evidence.get(eid) or next((row for row in current_evidence.values() if row["type"] == prior_evidence[eid]["type"] and row["url"].rsplit("/", 1)[-1] == prior_evidence[eid]["url"].rsplit("/", 1)[-1]), None)} for eid in mismatches[:2]]}, ensure_ascii=False))
        assert len(calls) == 3, {"message": "The first successful batch must not be charged/read again",
                              "before_ai": stopped["result"].get("ai"), "after_ai": captured["analysis"]["ai"]}
        assert captured["analysis"]["ai"]["complete"] is True
        assert captured["analysis"]["ai"]["resumed_batches"] == 1
        assert captured["summary"]["analysis_pending_evidence"] == 0
        assert captured["summary"]["analysis_status"] == "completed"
        assert len(captured["items"]) == 130
    finally:
        store.close()


def test_draft_api_conflict_and_restart_keep_exact_multikeyword_input(tmp_path, adapter):
    path = tmp_path / "draft.sqlite3"
    draft = {"schema": 1, "values": {"keyword": "憲法九条，沖縄 平和;反戦\n反戦", "workflow": "research", "depth": "2"}, "revision": 0}
    with TestClient(create_app(Store(path))) as client:
        assert client.get("/api/v2/drafts/collection").json() == {"draft": None}
        saved = client.put("/api/v2/drafts/collection", json=draft)
        assert saved.status_code == 200, saved.text
        revision = saved.json()["draft"]["revision"]
        newer = {**draft, "values": {"keyword": "日本 平和，反戦"}, "revision": revision}
        assert client.put("/api/v2/drafts/collection", json=newer).status_code == 200
        assert client.put("/api/v2/drafts/collection", json=draft).status_code == 409
        assert client.request("DELETE", "/api/v2/drafts/collection", json={"revision": revision}).status_code == 409
        assert client.put("/api/v2/drafts/collection", json={"values": {"password": "not-a-password"}}).status_code == 422
    with TestClient(create_app(Store(path))) as client:
        assert client.get("/api/v2/drafts/collection").json()["draft"]["values"]["keyword"] == "日本 平和，反戦"


def test_plan_api_explains_space_and_delimiters_without_changing_input(tmp_path, adapter):
    keyword = "憲法九条，沖縄 平和;反戦"
    with TestClient(create_app(Store(tmp_path / "plan.sqlite3"))) as client:
        response = confirmed_post(client, "/api/v2/research/plan", json={"keyword": keyword, "source": "twitter"})
        assert response.status_code == 200, response.text
        plan = response.json()
        assert plan["raw_keyword"] == keyword and "空格" in plan["separator_help"]
        assert plan["suggestions"] and not plan["suggestions_applied"]


def test_no_checkpoint_yet_has_honest_not_found_response(tmp_path, adapter, monkeypatch):
    async def wait(*args, **kwargs):
        await asyncio.sleep(30)
    monkeypatch.setattr(pipeline, "collect", wait)
    store = Store(tmp_path / "waiting.sqlite3")
    # A collect job has no associated research state and a None result.
    pending = store.create_job("collect", {"source": "google_news", "keyword": "平和"})
    with TestClient(create_app(store)) as client:
        response = client.get(f"/api/v2/research/{pending['id']}")
        assert response.status_code == 404, response.text


def test_batch_api_executes_ordered_tasks_and_reports_true_saved_counts(tmp_path, adapter):
    with TestClient(create_app(Store(tmp_path / "batch-api.sqlite3"))) as client:
        payload = {"title": "关键词任务串", "kind": "research", "params": parameters(keyword="反戦，平和", max_records=200), "total_records": 300}
        preview = confirmed_post(client, "/api/v2/research/batch-plan", json=payload)
        assert preview.status_code == 200, preview.text
        assert len(preview.json()["tasks"]) == 2
        submitted = confirmed_post(client, "/api/v2/jobs/batch", json=payload)
        assert submitted.status_code == 202, submitted.text
        batch = submitted.json()["batch"]
        for job in batch["jobs"]:
            terminal(client, job["id"])
        actual = client.get(f"/api/v2/job-batches/{batch['id']}").json()
        assert actual["completed_count"] == 2 and actual["active_count"] == 0
        assert actual["records_saved"] < actual["total_records"]
        assert actual["target_reached"] is False
        assert actual["counts"].get("succeeded", 0) + actual["counts"].get("partial", 0) == 2
        assert client.get("/api/v2/job-batches?page_size=1").json()["total"] == 1
        assert client.get("/api/v2/jobs?page_size=1").json()["total"] == 2


@pytest.mark.parametrize("title,expected", [(None, "批量公开资料研究"), ("", "批量公开资料研究"), ("  \t", "批量公开资料研究"), ("  自定义任务串  ", "自定义任务串")])
def test_optional_batch_title_previews_and_creates_with_same_name(tmp_path, title, expected):
    store = Store(tmp_path / "optional-title.sqlite3")
    with TestClient(create_app(store, run_worker=False)) as client:
        payload = {"kind": "research", "params": parameters(keyword="反戦，平和", max_records=200), "total_records": 300}
        if title is not None:
            payload["title"] = title
        preview = confirmed_post(client, "/api/v2/research/batch-plan", json=payload)
        assert preview.status_code == 200, preview.text
        assert preview.json()["title"] == expected
        assert store.list_jobs()["total"] == 0
        submitted = confirmed_post(client, "/api/v2/jobs/batch", json=payload)
        assert submitted.status_code == 202, submitted.text
        result = submitted.json()
        assert result["batch"]["title"] == result["plan"]["title"] == expected
        assert len(result["jobs"]) == 2
        assert all(job["status"] == "queued" for job in result["jobs"])


async def until(predicate, timeout=5):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("action,expected", [("cancel", "cancelled"), ("exit", "interrupted")])
def test_runner_cancel_or_exit_preserves_checkpoint_material_and_frontier(tmp_path, adapter, monkeypatch, action, expected):
    waiting = threading.Event()
    original_profile = adapter.profile
    async def delayed(self, platform, handle):
        if handle == "beta":
            waiting.set()
            await asyncio.Event().wait()
        return await original_profile(self, platform, handle)
    monkeypatch.setattr(adapter, "profile", delayed)
    store = Store(tmp_path / f"{action}.sqlite3")
    job = store.create_job("research", parameters())
    runner = JobRunner(store)
    async def scenario():
        await runner.start()
        try:
            await until(waiting.is_set)
            previous = store.get_research_run(job["id"])
            assert previous["items"] and previous["frontier"]
            if action == "cancel":
                store.request_cancel(job["id"])
                await until(lambda: store.get_job(job["id"])["status"] == "cancelled")
            else:
                await runner.stop()
            finished = store.get_job(job["id"])
            saved = store.get_research_run(job["id"])
            assert finished["status"] == expected and saved["status"] == expected
            assert saved["items"] and saved["frontier"] and finished["result"]["run_id"] == job["id"]
            assert all(instance.closed for instance in adapter.instances)
        finally:
            await runner.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize("stop_reason", ["record_budget", "account_budget", "edge_budget"])
def test_hard_budget_retry_api_rejects_without_creating_job(tmp_path, stop_reason):
    store = Store(tmp_path / "hard-budget-api.sqlite3")
    job = store.create_job("research", parameters())
    store.create_research_run(job["id"], job["params"])
    store.checkpoint_research(job["id"], status="partial", summary={"stop_reason": stop_reason})
    store.update_job(job["id"], status="partial", result={"run_id": job["id"], "summary": {"stop_reason": stop_reason}})
    with TestClient(create_app(store)) as client:
        response = confirmed_post(client, f"/api/v2/jobs/{job['id']}/retry")
        assert response.status_code == 422, response.text
        assert "任务串" in response.text
        assert client.get("/api/v2/jobs").json()["total"] == 1
        assert client.get("/api/v2/research").json()["total"] == 1
        assert client.get(f"/api/v2/research/{job['id']}").json()["run"]["job_id"] == job["id"]


def test_time_budget_retry_api_keeps_original_run_and_material(tmp_path, adapter):
    store = Store(tmp_path / "resume-api.sqlite3")
    params = parameters()
    first = store.create_job("research", params)
    store.create_research_run(first["id"], params)
    summary = {"source": "twitter", "start_date": params["start_date"], "end_date": params["end_date"],
               "stop_reason": "time_budget", "outcome": "time_budget"}
    store.checkpoint_research(first["id"], status="partial", summary=summary,
        query_plan=research_engine.build_query_plan(params), items=[post()], accounts=[account("twitter", "alpha")],
        nodes=[{"account_key": "twitter:alpha", "depth": 0, "status": "completed"}], visited=["twitter:alpha"])
    store.update_job(first["id"], status="partial", result={"run_id": first["id"], "summary": summary, "inserted": 1})
    with TestClient(create_app(store)) as client:
        retried = confirmed_post(client, f"/api/v2/jobs/{first['id']}/retry")
        assert retried.status_code == 202, retried.text
        child = terminal(client, retried.json()["id"])
        assert child["result"]["run_id"] == first["id"]
        assert client.get("/api/v2/research").json()["total"] == 1
        graph = client.get(f"/api/v2/research/{child['id']}").json()
        assert graph["run"]["id"] == first["id"]
        assert any(item["url"] == "https://x.com/i/status/10" for item in graph["items"])


def test_partial_retry_real_engine_skips_successful_branches_and_resets_blocked_platform(tmp_path, adapter, monkeypatch):
    original = adapter.profile
    blocked = {"once": True}
    async def profile_once(self, platform, handle):
        if handle == "beta" and blocked["once"]:
            blocked["once"] = False
            raise SocialError("rate_limited", "测试平台限流，保留分支")
        return await original(self, platform, handle)
    monkeypatch.setattr(adapter, "profile", profile_once)
    with TestClient(create_app(Store(tmp_path / "branch-resume.sqlite3"))) as client:
        response = confirmed_post(client, "/api/v2/jobs", json={"kind": "research", "params": parameters()})
        first = terminal(client, response.json()["id"])
        assert first["status"] == "partial", first
        initial = client.get(f"/api/v2/research/{first['id']}").json()
        assert initial["summary"]["stop_reason"] == "platform_blocked"
        calls_before = list(adapter.calls)
        retried = confirmed_post(client, f"/api/v2/jobs/{first['id']}/retry")
        assert retried.status_code == 202, retried.text
        second = terminal(client, retried.json()["id"])
        assert second["status"] == "partial", second  # prior source diagnostics remain visible
        assert second["result"]["run_id"] == first["id"]
        extra_calls = adapter.calls[len(calls_before):]
        assert ("profile", "twitter", "beta") in extra_calls
        assert ("profile", "twitter", "alpha") not in extra_calls
        assert not any(row[0] == "search" for row in extra_calls)
        saved = client.get(f"/api/v2/research/{second['id']}").json()
        assert len(saved["items"]) == 3
        assert not saved["run"]["frontier"]


def test_batch_api_cancel_and_retry_retains_saved_checkpoints_and_skips_successful_jobs(tmp_path, adapter, monkeypatch):
    waiting = threading.Event()
    original = adapter.profile
    async def wait_on_beta(self, platform, handle):
        if handle == "beta":
            waiting.set()
            await asyncio.Event().wait()
        return await original(self, platform, handle)
    monkeypatch.setattr(adapter, "profile", wait_on_beta)
    with TestClient(create_app(Store(tmp_path / "batch-cancel.sqlite3"))) as client:
        response = confirmed_post(client, "/api/v2/jobs/batch", json={"title": "任务串取消续查", "kind": "research", "params": parameters(keyword="反戦，平和"), "total_records": 40})
        assert response.status_code == 202, response.text
        batch = response.json()["batch"]
        assert len(batch["jobs"]) == 2
        assert waiting.wait(5)
        first_id = batch["jobs"][0]["id"]
        observed = client.get(f"/api/v2/research/{first_id}")
        assert observed.status_code == 200, (observed.text, client.get(f"/api/v2/job-batches/{batch['id']}").json())
        before = observed.json()
        assert before["items"] and before["run"]["frontier"]
        cancel = confirmed_post(client, f"/api/v2/job-batches/{batch['id']}/cancel")
        assert cancel.status_code == 200, cancel.text
        for job in batch["jobs"]:
            assert terminal(client, job["id"])["status"] == "cancelled"
        kept = client.get(f"/api/v2/research/{first_id}").json()
        assert len(kept["items"]) == len(before["items"])
        monkeypatch.setattr(adapter, "profile", original)
        retry = confirmed_post(client, f"/api/v2/job-batches/{batch['id']}/retry")
        assert retry.status_code == 202, retry.text
        children = retry.json()["jobs"]
        assert children[0]["params"]["resume_run_id"] == first_id
        for job in children:
            assert terminal(client, job["id"])["status"] == "succeeded"
        assert confirmed_post(client, f"/api/v2/job-batches/{batch['id']}/retry").status_code == 422
        listing = client.get("/api/v2/job-batches?page=1&page_size=1").json()
        assert listing["total"] == 2 and len(listing["items"]) == 1
        assert listing["items"][0]["completed_count"] == 2
        assert client.get("/api/v2/job-batches?page=2&page_size=1").json()["items"][0]["id"] == batch["id"]


@pytest.mark.parametrize("marker", [{"method": "configured_ai"}, {"method": "ai"}, {"ai_used": True}, {"basis": "inferred", "method": "rules"}])
def test_model_findings_are_candidate_claims_and_never_observed_edges(tmp_path, marker):
    from app.workbench.research_export import checkpoint_payload
    store = Store(tmp_path / "candidate.sqlite3")
    job = store.create_job("research", parameters())
    store.create_research_run(job["id"], job["params"])
    event = {"nodes": [{"account_key": "twitter:alpha", "depth": 0}, {"account_key": "twitter:beta", "depth": 1}],
             "analysis": {"findings": [{"source_key": "twitter:alpha", "target_key": "twitter:beta", "relation": "shared_link",
                 "summary": "需要核对", "source_url": "https://example.org/event/123", **marker}]}}
    payload = checkpoint_payload(event)
    assert payload["edges"] == [] and payload["claims"][0]["basis"] == "inferred"
    store.checkpoint_research(job["id"], **payload)
    result = store.get_research_run(job["id"])
    assert result["edges"] == [] and result["claims"][0]["status"] == "candidate"
    assert result["claims"][0]["evidence_urls"] == ["https://example.org/event/123"]


def test_checkpoint_user_cancel_never_enters_ai_recovery_or_analysis(tmp_path, adapter, monkeypatch):
    calls = []
    async def analysis(*args, **kwargs):
        calls.append("final-analysis")
        raise AssertionError("cancellation must not call a provider")
    monkeypatch.setattr(research_engine, "analyze_relationships_with_ai", analysis)
    store = Store(tmp_path / "checkpoint-cancel.sqlite3")
    job = store.create_job("research", parameters())
    runner = JobRunner(store)
    original_check = store.get_job
    def stop_at_callback(id):
        record = original_check(id)
        # Runner's first research callback observes a user stop immediately.
        if record and record["status"] == "running":
            record["cancel_requested"] = True
        return record
    monkeypatch.setattr(store, "get_job", stop_at_callback)
    async def scenario():
        await runner.start()
        try:
            await until(lambda: original_check(job["id"])["status"] == "cancelled")
            assert calls == []
            assert store.get_research_run(job["id"])["status"] == "cancelled"
        finally:
            await runner.stop()
    asyncio.run(scenario())


def test_direct_engine_user_cancel_bypasses_error_recovery_and_closes_adapter(adapter, monkeypatch):
    calls = []
    async def analysis(*args, **kwargs):
        calls.append("unexpected-provider-call")
        raise AssertionError("a cancellation is not an analysis recovery request")
    async def cancel_seed(event):
        if event["kind"] == "seed":
            raise CancelledByUser()
    monkeypatch.setattr(research_engine, "analyze_relationships_with_ai", analysis)
    with pytest.raises(CancelledByUser):
        asyncio.run(research_engine.run_research(parameters(), checkpoint=cancel_seed))
    assert calls == [] and adapter.instances
    assert all(instance.closed for instance in adapter.instances)


def test_cancel_during_checkpoint_preserves_actual_saved_count_in_job_and_batch(tmp_path, adapter, monkeypatch):
    store = Store(tmp_path / "commit-count.sqlite3")
    batch = store.create_job_batch("取消时保存计数", [{"kind": "research", "params": parameters()}], 20)
    job = batch["jobs"][0]
    committed, release = threading.Event(), threading.Event()
    original = store.checkpoint_research
    def delayed_checkpoint(run_id, **payload):
        saved = original(run_id, **payload)
        if payload.get("items"):
            committed.set()
            if not release.wait(5):
                raise AssertionError("test checkpoint was not released")
        return saved
    monkeypatch.setattr(store, "checkpoint_research", delayed_checkpoint)
    runner = JobRunner(store)
    async def scenario():
        await runner.start()
        try:
            await until(committed.is_set)
            assert len(store.get_research_run(job["id"])["items"]) == 1
            store.request_cancel(job["id"])
            await asyncio.sleep(0.4)  # The monitor has entered cancellation cleanup.
            release.set()
            await until(lambda: store.get_job(job["id"])["status"] == "cancelled")
            saved = store.get_job(job["id"])
            assert saved["result"]["inserted"] == 1
            assert store.get_job_batch(batch["id"])["records_saved"] == 1
            assert len(store.get_research_run(job["id"])["items"]) == 1
        finally:
            release.set()
            await runner.stop()
    asyncio.run(scenario())
