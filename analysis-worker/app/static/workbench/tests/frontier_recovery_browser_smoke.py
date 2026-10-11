"""Real frontend DOM/fetch acceptance with loopback assets and synthetic API data.

Uses an isolated Chromium profile. No real application, database, session, model,
source collection or production settings are accessed.
"""
import argparse
import copy
import hashlib
import json
import threading
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright
import network_analysis_browser_smoke as assets

ROOT = Path(__file__).resolve().parents[1]
assets.HTML = assets.HTML.replace("run-analysis", "run-frontier")


def fixture(reason="account_budget"):
    return {
        "run": {"id": "run-frontier", "job_id": "frontier-original-job", "status": "partial",
                "params": {"source": "twitter", "seed_sources": ["twitter"], "mode": "account",
                           "handle": "public_root", "seed_accounts": ["https://x.com/second_root"],
                           "keyword": "反戦", "keyword_mode": "any", "research_goal": "继续已保存的公开账号线索",
                           "start_date": "2026-09-01", "end_date": "2026-10-09", "depth": 2,
                           "max_accounts": 12, "max_edges": 60, "max_records": 50,
                           "max_scrolls": 10, "time_budget_seconds": 1800, "analysis_mode": "rules",
                           "expand_relations": ["following", "mentions"], "relevance": "focused"},
                "summary": {"stop_reason": reason, "collection_stop_reason": reason,
                            "accounts": 12, "relations": 60, "records": 50,
                            "deferred_frontier_version": 1, "pending_work_version": 1, "deferred_count": 3,
                            "deferred_record_count": 2, "deferred_relation_count": 1},
                "frontier": [], "deferred_frontier": [{"key": "twitter:next", "admission_state": "deferred"}],
                "analysis": {"ai": {"requested": False}}},
        "nodes": [], "edges": [], "items": [], "claims": [],
        "total_nodes": 12, "total_edges": 60, "total_items": 50,
        "coverage": "模拟保存快照，只验证前端路径。",
    }


CAPABILITIES = {
    "research": {"available": True, "max_depth": 3, "max_accounts": 24, "max_edges": 120},
    "budget_limits": {"research": {"max_records": {"min": 1, "max": 700, "default": 50},
                                   "max_scrolls": {"min": 1, "max": 80, "default": 10},
                                   "time_budget_seconds": {"min": 30, "max": 7200, "default": 1800}}},
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    server = assets.ThreadingHTTPServer(("127.0.0.1", 0), assets.Assets)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state = {"graph": fixture(), "requests": [], "caps_error": False,
             "continue_error": False, "hold_continue": False, "held": []}
    errors, cases, unexpected = [], [], []

    def route(route):
        request = route.request
        path = urlsplit(request.url).path
        state["requests"].append({"path": path, "method": request.method, "body": request.post_data_json if request.post_data else None})
        status = 200
        if path == "/api/v2/research/run-frontier" and request.method == "GET":
            data = state["graph"]
        elif path == "/api/v2/capabilities":
            data = {"error": {"message": "模拟能力读取失败"}} if state["caps_error"] else CAPABILITIES
            status = 503 if state["caps_error"] else 200
        elif path == "/api/v2/research/run-frontier/continue" and request.method == "POST":
            if state["hold_continue"]:
                state["held"].append(route)
                return
            data = {"error": {"message": "该研究已有待执行或运行中的任务"}} if state["continue_error"] else {"id": "frontier-next-job", "status": "queued"}
            status = 409 if state["continue_error"] else 202
        elif path == "/api/v2/jobs" and request.method == "GET":
            data = {"items": [state["job"]], "total": 1}
        elif path == "/api/v2/jobs/frontier-original-job" and request.method == "GET":
            data = state["job"]
        elif path == "/api/v2/job-batches" and request.method == "GET":
            data = {"items": [], "total": 0}
        elif path == "/api/v2/jobs/frontier-original-job/retry" and request.method == "POST":
            data = {"id": "frontier-ai-only-job"}
        else:
            unexpected.append(path)
            data, status = {"error": {"message": "Unexpected fixture request: " + path}}, 404
        route.fulfill(status=status, content_type="application/json", body=json.dumps(data, ensure_ascii=False))

    def load(page, graph=None, jobs=False):
        state["graph"] = copy.deepcopy(graph or fixture())
        state["requests"] = []
        run = state["graph"]["run"]
        state["job"] = {"id": "frontier-original-job", "kind": "research", "status": run["status"],
                        "params": run["params"], "result": {"run_id": run["id"], "summary": run["summary"]}, "events": []}
        suffix = "/?view=jobs&selected=frontier-original-job" if jobs else "/"
        page.goto(f"http://127.0.0.1:{server.server_port}" + suffix)
        expect(page.get_by_role("heading", name="任务中心" if jobs else "研究结果", exact=True)).to_be_visible()

    def posts():
        return [request for request in state["requests"] if request["method"] == "POST"]

    def open_resume(page):
        page.get_by_role("button", name="继续研究", exact=True).click()
        modal = page.get_by_role("dialog", name="继续研究", exact=True)
        expect(modal).to_be_visible()
        expect(modal.locator('[name="max_accounts"]')).to_have_attribute("max", "24")
        return modal

    page = None
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(str(args.output / "qa-profile"),
                executable_path=playwright.chromium.executable_path, headless=True, viewport={"width": 1440, "height": 1000})
            page = context.pages[0]
            page.route("**/api/v2/**", route)
            page.on("pageerror", lambda error: errors.append(str(error)))
            load(page)
            page.screenshot(path=str(args.output / "initial-research-state.png"), full_page=True)
            expect(page.get_by_role("button", name="继续研究", exact=True)).to_be_visible(timeout=2500)
            expect(page.locator(".frontier-recovery-status")).to_contain_text("待准入账号 3")
            expect(page.locator(".frontier-recovery-status")).to_contain_text("待保存材料 2")
            expect(page.locator(".frontier-recovery-status")).to_contain_text("待保存关系 1")
            modal = open_resume(page)
            expect(modal).to_contain_text("public_root")
            expect(modal).to_contain_text("https://x.com/second_root")
            expect(modal).to_contain_text("2026-09-01 — 2026-10-09")
            expect(modal).to_contain_text("探索 2 层")
            assert modal.locator("input").count() == 4
            expect(modal.locator('[name="max_records"]')).to_have_attribute("max", "700")
            expect(modal.locator('[name="max_edges"]')).to_have_attribute("max", "120")
            expect(modal.locator('[name="time_budget_seconds"]')).to_have_attribute("max", "7200")
            expect(modal.get_by_role("button", name="开始继续研究", exact=True)).to_be_disabled()
            modal.locator('[name="time_budget_seconds"]').fill("3600")
            expect(modal.get_by_role("button", name="开始继续研究", exact=True)).to_be_disabled()
            expect(modal.locator(".submit-status")).to_contain_text("账号")
            modal.locator('[name="max_accounts"]').fill("24")
            expect(modal.get_by_role("button", name="开始继续研究", exact=True)).to_be_enabled()
            page.screenshot(path=str(args.output / "continue-desktop.png"), full_page=True)
            before = json.dumps(state["graph"], sort_keys=True)
            modal.get_by_role("button", name="开始继续研究", exact=True).click()
            page.wait_for_function("window.__navigation.length===1")
            assert page.evaluate("window.__navigation[0]") == ["jobs", {"selected": "frontier-next-job"}]
            assert posts() == [{"path": "/api/v2/research/run-frontier/continue", "method": "POST",
                               "body": {"max_accounts": 24, "max_edges": 60, "max_records": 50, "time_budget_seconds": 3600}}]
            assert json.dumps(state["graph"], sort_keys=True) == before
            cases.append({"name": "account_cap_budget_preserves_scope_and_snapshot", "ok": True, "requests": copy.deepcopy(state["requests"])})

            for reason, name in [("record_budget", "max_records"), ("edge_budget", "max_edges")]:
                load(page, fixture(reason))
                modal = open_resume(page)
                modal.locator('[name="max_accounts"]').fill("24")
                expect(modal.get_by_role("button", name="开始继续研究", exact=True)).to_be_disabled()
                modal.locator(f'[name="{name}"]').fill("100")
                expect(modal.get_by_role("button", name="开始继续研究", exact=True)).to_be_enabled()
                cases.append({"name": reason + "_requires_corresponding_increase", "ok": True})

            load(page)
            modal = open_resume(page)
            modal.locator('[name="max_accounts"]').fill("24")
            state["continue_error"] = True
            modal.get_by_role("button", name="开始继续研究", exact=True).click()
            expect(modal.get_by_role("alert")).to_contain_text("已有待执行")
            expect(modal.locator('[name="max_accounts"]')).to_have_value("24")
            expect(modal.get_by_role("button", name="开始继续研究", exact=True)).to_be_enabled()
            assert page.evaluate("window.__navigation.length") == 0
            cases.append({"name": "409_preserves_adjusted_limits", "ok": True})
            state["continue_error"] = False

            load(page)
            modal = open_resume(page)
            modal.locator('[name="max_accounts"]').fill("24")
            state["hold_continue"] = True
            control = modal.locator('button[type="submit"]')
            control.click()
            expect(control).to_be_disabled()
            modal.locator('[name="max_records"]').fill("100")
            expect(control).to_be_disabled()
            assert len(posts()) == 1
            page.get_by_role("button", name="关闭对话框", exact=True).click()
            state["held"].pop().fulfill(status=202, content_type="application/json", body=json.dumps({"id": "frontier-next-job"}))
            state["hold_continue"] = False
            expect(page.get_by_role("dialog")).to_have_count(0)
            assert page.evaluate("window.__navigation.length") == 0
            cases.append({"name": "single_submit_and_dismissed_late_response", "ok": True})

            load(page)
            state["caps_error"] = True
            page.get_by_role("button", name="继续研究", exact=True).click()
            modal = page.get_by_role("dialog", name="继续研究", exact=True)
            expect(modal.get_by_role("alert")).to_contain_text("模拟能力读取失败")
            expect(modal.get_by_role("button", name="开始继续研究", exact=True)).to_be_disabled()
            assert not posts()
            state["caps_error"] = False
            modal.get_by_role("button", name="重新读取", exact=True).click()
            expect(modal.locator('[name="max_accounts"]')).to_have_attribute("max", "24")
            cases.append({"name": "capabilities_failure_blocks_until_actual_limits_loaded", "ok": True})

            multiple = fixture()
            multiple["run"]["summary"]["limit_hits"] = ["account_budget", "edge_budget"]
            load(page, multiple)
            modal = open_resume(page)
            modal.locator('[name="max_accounts"]').fill("24")
            expect(modal.get_by_role("button", name="开始继续研究", exact=True)).to_be_disabled()
            expect(modal.locator(".submit-status")).to_contain_text("关系上限")
            modal.locator('[name="max_edges"]').fill("120")
            expect(modal.get_by_role("button", name="开始继续研究", exact=True)).to_be_enabled()
            modal.locator('[name="time_budget_seconds"]').fill("60")
            expect(modal.get_by_role("button", name="开始继续研究", exact=True)).to_be_disabled()
            expect(modal.locator(".submit-status")).to_contain_text("不能低于当前研究上限 1800")
            modal.locator("form").evaluate("form=>form.requestSubmit()")
            expect(modal.get_by_role("alert")).to_contain_text("不能低于当前研究上限")
            assert not posts()
            cases.append({"name": "all_limit_hits_increase_and_duration_cannot_shrink", "ok": True})

            legacy = fixture()
            legacy["run"].update(frontier=[], deferred_frontier=[])
            legacy["run"]["summary"] = {"stop_reason": "missing_deferred_frontier", "frontier_recovery_required": True,
                                        "account_budget_skipped": 49, "deferred_count": 0,
                                        "deferred_record_count": 0, "deferred_relation_count": 0}
            load(page, legacy)
            expect(page.locator(".frontier-recovery-status")).to_contain_text("缺口未知")
            expect(page.locator(".frontier-recovery-status")).to_contain_text("无法恢复")
            expect(page.get_by_role("button", name="继续研究", exact=True)).to_be_disabled()
            assert "待准入账号 49" not in page.locator(".frontier-recovery-status").inner_text()
            page.screenshot(path=str(args.output / "legacy-gap-desktop.png"), full_page=True)
            cases.append({"name": "legacy_missing_links_are_unknown_not_49_candidates", "ok": True})

            complete = fixture()
            complete["run"].update(status="succeeded", frontier=[], deferred_frontier=[])
            complete["run"]["summary"].update(deferred_count=0, deferred_record_count=0, deferred_relation_count=0, collection_complete=True)
            load(page, complete)
            expect(page.get_by_role("button", name="继续研究", exact=True)).to_be_disabled()
            cases.append({"name": "completed_without_saved_backlog_disabled", "ok": True})
            running = fixture()
            running["run"]["status"] = "running"
            load(page, running)
            expect(page.get_by_role("button", name="继续研究", exact=True)).to_have_count(0)
            cases.append({"name": "active_research_cannot_start_duplicate", "ok": True})

            load(page, jobs=True)
            page.get_by_role("dialog").get_by_role("button", name="继续研究", exact=True).click()
            modal = page.get_by_role("dialog", name="继续研究", exact=True)
            expect(modal.locator('[name="max_accounts"]')).to_have_attribute("max", "24")
            modal.locator('[name="max_accounts"]').fill("24")
            modal.get_by_role("button", name="开始继续研究", exact=True).click()
            page.wait_for_function("window.__navigation.length===1")
            assert posts()[0]["path"] == "/api/v2/research/run-frontier/continue"
            cases.append({"name": "job_detail_uses_saved_run_and_same_budget_dialog", "ok": True})

            cancelled = fixture("cancelled")
            cancelled["run"].update(status="cancelled", frontier=[{"key": "twitter:pending"}])
            load(page, cancelled)
            modal = open_resume(page)
            expect(modal.get_by_role("button", name="开始继续研究", exact=True)).to_be_enabled()
            cases.append({"name": "cancelled_pending_work_allows_same_bounds", "ok": True})
            news = fixture("record_budget")
            news["run"]["params"].update(source="google_news", seed_sources=["google_news"], mode="keyword",
                keyword="平和, 憲法九条", research_goal="继续原公开新闻检索", handle="", seed_accounts=[])
            news["run"]["summary"].update(accounts=0, relations=0, deferred_count=0, deferred_record_count=0,
                deferred_relation_count=0, pending_seed_queries_version=1, pending_seed_query_count=2,
                continuation_pending_queries=3, continuation_available=True)
            news["run"]["deferred_frontier"] = []
            news.update(total_nodes=0, total_edges=0)
            load(page, news)
            expect(page.locator(".frontier-recovery-status")).to_contain_text("待继续检索入口 2 个")
            expect(page.locator(".frontier-recovery-status")).to_contain_text("原查询尚未检索完")
            modal = open_resume(page)
            expect(modal).to_contain_text("平和, 憲法九条")
            expect(modal).to_contain_text("来源：公开新闻")
            expect(modal).to_contain_text("待继续检索入口 2 个")
            assert "pending_seed" not in modal.inner_text() and "continuation_pending" not in modal.inner_text()
            expect(modal.get_by_role("button", name="开始继续研究", exact=True)).to_be_disabled()
            modal.locator('[name="max_records"]').fill("100")
            expect(modal.get_by_role("button", name="开始继续研究", exact=True)).to_be_enabled()
            page.screenshot(path=str(args.output / "news-only-continue-desktop.png"), full_page=True)
            modal.get_by_role("button", name="开始继续研究", exact=True).click()
            page.wait_for_function("window.__navigation.length===1")
            assert posts() == [{"path": "/api/v2/research/run-frontier/continue", "method": "POST",
                "body": {"max_accounts": 12, "max_edges": 60, "max_records": 100, "time_budget_seconds": 1800}}]
            cases.append({"name": "news_only_pending_query_entrances_keep_original_scope_and_raise_record_cap", "ok": True})
            load(page, cancelled)
            modal = open_resume(page)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            assert modal.evaluate("element=>element.scrollWidth<=element.clientWidth")
            page.screenshot(path=str(args.output / "continue-mobile.png"), full_page=True)
            cases.append({"name": "390px_dialog_and_recovery_status_no_overflow", "ok": True})
            assert not errors, errors
            assert not unexpected, unexpected
            context.close()
        result = {"ok": True, "cases": cases, "pageErrors": errors, "unexpectedRequests": unexpected,
                  "sourceHashes": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                   for name in ["network.js", "research.js", "research_resume.js", "research_resume_model.js"]},
                  "boundaries": ["Actual frontend DOM, assets and fetch via loopback HTTP.",
                                 "Synthetic GET/continue/retry APIs; isolated Chromium profile.",
                                 "No real model, source collection, database, production app, sessions or settings."]}
        (args.output / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{len(cases)} frontier recovery UI cases passed; page errors: {len(errors)}")
    except Exception as error:
        if page is not None:
            try:
                page.screenshot(path=str(args.output / "failure.png"), full_page=True)
            except Exception:
                pass  # The initial state is already saved before the first assertion.
        (args.output / "failure.json").write_text(json.dumps({"ok": False, "error": str(error), "completedCases": cases, "pageErrors": errors}, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
