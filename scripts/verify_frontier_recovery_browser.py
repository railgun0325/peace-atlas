"""Actual loopback API/runner/engine/store and frontend continuation acceptance.

Only the public source adapter is synthetic. Chromium uses a fresh profile,
SQLite lives in the specified QA directory, and no API is intercepted.
"""
import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis-worker"))
sys.path.insert(0, str(ROOT / "analysis-worker/tests"))

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright
from app.workbench import research_engine
from app.workbench.server import create_app
from app.workbench.store import Store

spec = importlib.util.spec_from_file_location("frontier_pipeline_fixture", ROOT / "analysis-worker/tests/test_workbench_frontier_pipeline.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def read_json(base, path):
    with urlopen(base + path, timeout=10) as response:
        return json.loads(response.read())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    database = args.output / "isolated-pipeline.sqlite3"
    if database.exists():
        raise RuntimeError("Use a fresh QA directory; this script never overwrites an existing database")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("WORKBENCH_SESSION_TOKEN", raising=False)
    adapter = fixture.PublicFixtureAdapter
    adapter.calls, adapter.instances, adapter.scenario = [], [], "account"
    monkeypatch.setattr(research_engine, "BrowserResearchAdapter", adapter)
    news_reads, news_requests, news_responses = [], [], []
    original_search = adapter.search

    async def public_search(self, platform, keyword, options, limit, accept):
        if platform != "google_news":
            return await original_search(self, platform, keyword, options, limit, accept)
        news_reads.append({"source": platform, "keyword": keyword, "limit": limit,
            "start_date": options.start_date, "end_date": options.end_date,
            "time_budget_seconds": options.time_budget_seconds, "max_scrolls": options.max_scrolls})
        items = [{"id": f"news-fixture-{index}", "title": f"平和の公開資料 {index}", "content": f"平和に関する公開討論の資料 {index}",
                  "url": f"https://example.org/frontier-news/{index}", "type": "news", "source_name": "Synthetic public news",
                  "published_at": "2026-10-08T00:00:00Z", "publication_date_unknown": False,
                  "metadata": {"collection_method": "public_fixture", "source_url": "https://example.org/frontier-news/search"}}
                 for index in range(1, 4)][:limit]
        return {"items": items, "accounts": [], "relations": [], "diagnostics": [{"source": platform, "ok": True, "fetched": len(items)}]}

    monkeypatch.setattr(adapter, "search", public_search)
    store = Store(database)
    server = None
    thread = None
    errors, unexpected_network, continue_requests, continue_responses = [], [], [], []
    cases = []
    try:
        original, stopped, first_run = fixture.initial_run(store, fixture.parameters())
        assert stopped["status"] == "partial"
        assert first_run["visited"] == ["twitter:alpha"] and first_run["summary"]["deferred_count"] == 1
        first_calls = copy.deepcopy(adapter.calls)
        run_id = original["id"]
        news_params = fixture.parameters()
        news_params.update(source="google_news", seed_sources=["google_news"], mode="keyword", keyword="平和",
            seed_accounts=[], handle="", max_records=1, research_goal="继续原公开新闻检索")
        news_original, news_stopped, news_first = fixture.initial_run(store, news_params)
        news_id = news_original["id"]
        assert news_stopped["status"] == "partial" and not news_first["nodes"]
        assert news_first["summary"]["pending_seed_query_count"] == 1
        store.close()
        store = Store(database)
        assert store.get_research_run(run_id)["deferred_frontier"] == first_run["deferred_frontier"]
        app = create_app(store, run_worker=True)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, name="isolated-frontier-qa-api", daemon=True)
        thread.start()
        deadline = time.monotonic() + 15
        while not server.started:
            if not thread.is_alive():
                raise RuntimeError("Isolated API exited before startup")
            if time.monotonic() >= deadline:
                raise TimeoutError("Isolated API did not start")
            time.sleep(0.03)
        port = server.servers[0].sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        assert read_json(base, "/health")["ok"] is True
        initial_graph = read_json(base, f"/api/v2/research/{run_id}")
        assert fixture.LONG_MARKER not in json.dumps(initial_graph, ensure_ascii=False)
        assert initial_graph["run"]["summary"]["continuation_available"] is True

        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(str(args.output / "qa-profile"),
                executable_path=playwright.chromium.executable_path, headless=True, viewport={"width": 1440, "height": 1000})
            page = context.pages[0]
            page.on("pageerror", lambda error: errors.append(str(error)))

            def guard(route):
                host = urlsplit(route.request.url).hostname
                if host in {"127.0.0.1", "localhost"}:
                    route.continue_()
                else:
                    unexpected_network.append(route.request.url)
                    route.abort()

            context.route("**/*", guard)

            def request_seen(request):
                if urlsplit(request.url).path == f"/api/v2/research/{run_id}/continue":
                    continue_requests.append({"method": request.method, "body": request.post_data_json})
                elif urlsplit(request.url).path == f"/api/v2/research/{news_id}/continue":
                    news_requests.append({"method": request.method, "body": request.post_data_json})

            def response_seen(response):
                if urlsplit(response.url).path == f"/api/v2/research/{run_id}/continue":
                    continue_responses.append(response.status)
                elif urlsplit(response.url).path == f"/api/v2/research/{news_id}/continue":
                    news_responses.append(response.status)

            page.on("request", request_seen)
            page.on("response", response_seen)
            page.goto(base + f"/#network?run={run_id}")
            expect(page.get_by_role("heading", name="研究结果", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="继续研究", exact=True)).to_be_enabled()
            expect(page.locator(".frontier-recovery-status")).to_contain_text("待准入账号 1")
            page.screenshot(path=str(args.output / "01-saved-frontier-real-api.png"), full_page=True)
            cases.append({"name": "actual_api_saved_frontier_not_graph_evidence", "ok": True})

            page.get_by_role("button", name="继续研究", exact=True).click()
            modal = page.get_by_role("dialog", name="继续研究", exact=True)
            expect(modal.locator('[name="max_accounts"]')).to_have_attribute("max", "10000")
            expect(modal.get_by_role("button", name="预估续查用时", exact=True)).to_be_disabled()
            expect(modal).to_contain_text("@alpha")
            expect(modal).to_contain_text("2026-10-01 — 2026-10-09")
            assert modal.locator("input").count() == 4
            modal.locator('[name="max_accounts"]').fill("3")
            modal.locator('[name="time_budget_seconds"]').fill("30")
            expect(modal.get_by_role("button", name="预估续查用时", exact=True)).to_be_disabled()
            expect(modal.locator(".submit-status")).to_contain_text("不能低于当前研究上限 60")
            assert continue_requests == []
            modal.locator('[name="time_budget_seconds"]').fill("60")
            expect(modal.get_by_role("button", name="预估续查用时", exact=True)).to_be_enabled()
            page.screenshot(path=str(args.output / "02-continue-dialog-real-api.png"), full_page=True)
            modal.get_by_role("button", name="预估续查用时", exact=True).click()
            expect(modal.get_by_role("heading", name="续查预计用时", exact=True)).to_be_visible()
            expect(modal.get_by_role("button", name="确认预计用时并继续", exact=True)).to_be_enabled()
            assert continue_requests == []
            assert store.list_jobs()["total"] == 2
            page.screenshot(path=str(args.output / "02b-continue-estimate-real-api.png"), full_page=True)
            modal.get_by_role("button", name="确认预计用时并继续", exact=True).click()
            page.wait_for_function("() => location.hash.startsWith('#jobs?')")
            selected = parse_qs(page.url.split("?", 1)[1])["selected"][0]
            deadline = time.monotonic() + 15
            while True:
                completed = read_json(base, "/api/v2/jobs/" + selected)
                if completed["status"] not in {"queued", "running"}:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("Actual continuation worker did not finish")
                time.sleep(0.03)
            assert completed["status"] == "succeeded", completed
            assert completed["result"]["run_id"] == run_id
            assert len(continue_requests) == 1 and continue_requests[0]["body"].get("preview_token")
            assert [{"method": row["method"], "body": {key: value for key, value in row["body"].items() if key != "preview_token"}}
                for row in continue_requests] == [{"method": "POST", "body": {
                "max_accounts": 3, "max_edges": 20, "max_records": 20, "time_budget_seconds": 60}}]
            assert continue_responses == [202]
            assert store.list_jobs()["total"] == 3
            new_calls = adapter.calls[len(first_calls):]
            assert {row["handle"] for row in new_calls} == {"beta", "gamma"}
            assert not any(row["handle"] == "alpha" for row in new_calls)
            fixture.assert_scope_and_sources(adapter.calls, fixture.parameters())
            cases.append({"name": "browser_four_budget_request_creates_actual_job_and_new_accounts_only", "ok": True,
                          "request": continue_requests[0], "responseStatus": continue_responses[0], "jobStatus": completed["status"]})

            page.goto(base + f"/#network?run={run_id}")
            expect(page.get_by_role("heading", name="研究结果", exact=True)).to_be_visible()
            expect(page.locator(".research-stats strong").first).to_have_text("3")
            expect(page.get_by_role("button", name="继续研究", exact=True)).to_be_disabled()
            expect(page.locator(".frontier-recovery-status")).to_contain_text("待准入账号 0")
            final_graph = read_json(base, f"/api/v2/research/{run_id}")
            assert final_graph["run"]["summary"]["collection_complete"] is True
            assert final_graph["run"]["summary"]["continuation_available"] is False
            assert final_graph["run"]["params"]["max_accounts"] == 3
            assert store.get_job(original["id"])["params"]["max_accounts"] == 1
            assert {row["account_key"] for row in final_graph["nodes"]} == {"twitter:alpha", "twitter:beta", "twitter:gamma"}
            assert any(row["relationship"] == "follows" and row["source_key"] == "twitter:alpha" for row in final_graph["edges"])
            assert any(row["relationship"] == "mentions" and row["source_key"] == "twitter:beta" for row in final_graph["edges"])
            page.screenshot(path=str(args.output / "03-completed-real-api.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            page.screenshot(path=str(args.output / "04-completed-mobile-real-api.png"), full_page=True)
            cases.append({"name": "actual_saved_three_account_graph_no_backlog_and_mobile_containment", "ok": True})
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.goto(base + f"/#network?run={news_id}")
            expect(page.get_by_role("button", name="继续研究", exact=True)).to_be_enabled()
            expect(page.locator(".frontier-recovery-status")).to_contain_text("待继续检索入口 1 个")
            expect(page.locator(".frontier-recovery-status")).to_contain_text("原查询尚未检索完")
            page.get_by_role("button", name="继续研究", exact=True).click()
            modal = page.get_by_role("dialog", name="继续研究", exact=True)
            expect(modal).to_contain_text("来源：公开新闻")
            expect(modal).to_contain_text("起点：平和")
            expect(modal).to_contain_text("待继续检索入口 1 个")
            expect(modal.get_by_role("button", name="预估续查用时", exact=True)).to_be_disabled()
            modal.locator('[name="max_records"]').fill("4")
            expect(modal.get_by_role("button", name="预估续查用时", exact=True)).to_be_enabled()
            assert "pending_seed" not in modal.inner_text() and "continuation_pending" not in modal.inner_text()
            page.screenshot(path=str(args.output / "05-news-only-continue-real-api.png"), full_page=True)
            modal.get_by_role("button", name="预估续查用时", exact=True).click()
            expect(modal.get_by_role("button", name="确认预计用时并继续", exact=True)).to_be_enabled()
            assert news_requests == []
            modal.get_by_role("button", name="确认预计用时并继续", exact=True).click()
            page.wait_for_function("() => location.hash.startsWith('#jobs?')")
            news_job_id = parse_qs(page.url.split("?", 1)[1])["selected"][0]
            deadline = time.monotonic() + 15
            while True:
                news_done = read_json(base, "/api/v2/jobs/" + news_job_id)
                if news_done["status"] not in {"queued", "running"}:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("Actual news continuation worker did not finish")
                time.sleep(0.03)
            assert news_done["status"] == "succeeded", news_done
            assert len(news_requests) == 1 and news_requests[0]["body"].get("preview_token")
            assert [{"method": row["method"], "body": {key: value for key, value in row["body"].items() if key != "preview_token"}}
                for row in news_requests] == [{"method": "POST", "body": {"max_accounts": 1, "max_edges": 20, "max_records": 4, "time_budget_seconds": 60}}]
            assert news_responses == [202]
            assert all(row["keyword"] == "平和" and row["source"] == "google_news" and row["start_date"] == "2026-10-01" and row["end_date"] == "2026-10-09" for row in news_reads)
            assert news_reads[-1]["limit"] > news_reads[0]["limit"]
            cases.append({"name": "actual_news_only_saved_query_dialog_and_four_budget_submission", "ok": True})
            page.goto(base + f"/#network?run={news_id}")
            expect(page.get_by_role("button", name="继续研究", exact=True)).to_be_disabled()
            expect(page.locator(".frontier-recovery-status")).to_contain_text("待继续检索入口 0 个")
            news_final = read_json(base, f"/api/v2/research/{news_id}")
            assert news_final["total_nodes"] == 0 and news_final["total_edges"] == 0 and news_final["total_items"] == 3
            assert news_final["run"]["summary"]["collection_complete"] is True
            assert news_final["run"]["summary"]["pending_seed_query_count"] == 0
            page.screenshot(path=str(args.output / "06-news-only-completed-real-api.png"), full_page=True)
            cases.append({"name": "actual_news_query_prefix_completed_without_account_candidates", "ok": True})
            assert not errors, errors
            assert not unexpected_network, unexpected_network
            context.close()

        result = {"ok": True, "cases": cases, "pageErrors": errors, "unexpectedExternalNetwork": unexpected_network,
                  "continueRequests": continue_requests, "continueResponseStatus": continue_responses,
                  "publicFixtureReads": adapter.calls, "firstReads": first_calls,
                  "newsFixtureReads": news_reads, "newsContinueRequests": news_requests, "newsContinueResponseStatus": news_responses,
                  "finalJob": {"status": completed["status"], "run_id": completed["result"]["run_id"]},
                  "finalCounts": {key: final_graph[key] for key in ["total_nodes", "total_edges", "total_items"]},
                  "boundaries": ["Actual loopback FastAPI endpoints, Uvicorn, JobRunner, research engine, checkpoint_payload, SQLite reopen and frontend.",
                                 "No API responses intercepted; only public source pages are synthetic.",
                                 "Fresh Chromium profile and QA-only SQLite; no production application/profile/session/settings touched.",
                                 "No paid model or real platform collection was used."]}
        (args.output / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("5 actual API/runner/engine/browser continuation groups passed; page errors: 0; external network: 0")
    except Exception as error:
        (args.output / "failure.json").write_text(json.dumps({"ok": False, "error": str(error), "completedCases": cases,
            "pageErrors": errors, "requests": continue_requests, "responses": continue_responses,
            "reads": adapter.calls}, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    finally:
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError("Isolated QA API failed to stop")
        store.close()
        monkeypatch.undo()


if __name__ == "__main__":
    main()
