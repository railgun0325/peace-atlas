"""Offline acceptance of the real app.js router and cross-page work flows.

All APIs are intercepted. This proves UI integration, not live platform access.
"""
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from playwright.sync_api import sync_playwright, expect, TimeoutError as PlaywrightTimeoutError

ROOT = Path(__file__).resolve().parents[1]
KEY = "peace-atlas.collection-draft.v1"


def main():
    state = {"draft": None, "revision": 0, "jobs": [], "history_error": False,
             "hold_history": False, "held_history": [], "hold_draft": False, "held_drafts": [], "reads": []}
    errors = []
    account = {"id": "public-id", "name": "公开测试组织", "handle": "selected_org", "platform": "twitter", "status": "candidate", "url": "https://x.com/selected_org", "tags": []}
    run = {"id": "old-run", "job_id": "old-job", "status": "partial", "params": {"keyword": "反戦", "depth": 2, "start_date": "2026-09-01", "end_date": "2026-10-06"}, "summary": {"stop_reason": "record_budget"}, "analysis": {}, "diagnostics": []}
    graph = {"run": run, "nodes": [], "edges": [], "items": [], "claims": [], "coverage": "离线界面测试"}

    def fulfill(route, data, status=200):
        route.fulfill(status=status, content_type="application/json", body=json.dumps(data, ensure_ascii=False))

    def route(request):
        parsed = urlsplit(request.request.url)
        path, method, query = parsed.path, request.request.method, parse_qs(parsed.query)
        if path == "/":
            return request.fulfill(content_type="text/html", body=(ROOT / "index.html").read_text(encoding="utf-8"))
        if path.startswith("/assets/"):
            file = ROOT / path.removeprefix("/assets/")
            assert file.resolve().is_relative_to(ROOT)
            return request.fulfill(content_type={".css": "text/css", ".woff2": "font/woff2", ".json": "application/json", ".txt": "text/plain"}.get(file.suffix, "text/javascript"), body=file.read_bytes())
        state["reads"].append([method, path, parsed.query])
        if path == "/api/v2/health":
            data = {"ok": True, "status": "ok"}
        elif path == "/api/v2/capabilities":
            data = {"budget_limits": {"research": {"max_records": {"min": 1, "max": 10000, "default": 50}, "max_scrolls": {"min": 1, "max": 200, "default": 10}, "time_budget_seconds": {"min": 30, "max": 86400, "default": 1800}}}}
        elif path == "/api/v2/social":
            data = {"runtime": {"ready": True}, "platforms": [{"id": source, "name": source, "state": "ready", "session_available": True, "modes": ["keyword", "account", "hashtag"]} for source in ["twitter", "instagram"]]}
        elif path == "/api/v2/drafts/collection":
            if method == "GET" and state["hold_draft"]:
                state["held_drafts"].append(request)
                return
            if method == "PUT":
                state["revision"] += 1
                state["draft"] = {**request.request.post_data_json, "revision": state["revision"]}
            elif method == "DELETE":
                state["draft"] = None
            data = {"draft": state["draft"]}
        elif path == "/api/v2/research":
            if state["hold_history"]:
                state["held_history"].append(request)
                return
            if state["history_error"]:
                return fulfill(request, {"error": {"message": "已有研究暂时不可用"}}, 503)
            data = {"items": [run], "total": 26, "page": int(query.get("page", [1])[0]), "page_size": 25}
        elif path == "/api/v2/research/old-run":
            data = graph
        elif path == "/api/v2/research/plan":
            data = {"queries": [], "suggestions": [], "ai": {"used": False}}
        elif path == "/api/v2/research/batch-plan":
            params = request.request.post_data_json
            data = {"tasks": [{"kind": params["kind"], "params": params["params"], "label": "离线计划"}], "title": params["title"], "total_records": params["total_records"], "max_possible_records": params["params"]["max_records"], "warnings": []}
        elif path == "/api/v2/jobs" and method == "POST":
            params = request.request.post_data_json
            data = {**params, "id": f"test-{len(state['jobs'])}", "status": "succeeded" if params["kind"] == "login" else "queued", "progress": 0, "created_at": "2026-10-06T00:00:00Z", "result": {"verified": True, "requires_login": False, "source": "workbench"} if params["kind"] == "login" else None, "events": []}
            state["jobs"].append(data)
        elif path == "/api/v2/jobs":
            data = {"items": state["jobs"], "total": len(state["jobs"]), "page": 1, "page_size": 20}
        elif path.startswith("/api/v2/jobs/"):
            data = next(value for value in state["jobs"] if value["id"] == path.split("/")[4])
        elif path == "/api/v2/job-batches":
            data = {"items": [], "total": 0, "page": 1, "page_size": 10}
        elif path == "/api/v2/accounts":
            data = {"items": [account], "total": 1}
        elif path == "/api/v2/items":
            data = {"items": [], "total": 0, "page": 1, "page_size": 20}
        else:
            raise AssertionError(f"Unexpected fixture request: {method} {path}")
        fulfill(request, data)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.route("**/*", route)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto("http://app-flow.test/#jobs")
        expect(page.get_by_role("heading", name="任务中心", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="新建采集", exact=True)).to_have_count(0)
        expect(page.get_by_role("button", name="批量研究", exact=True)).to_have_count(0)
        expect(page.get_by_role("dialog")).to_have_count(0)
        page.locator(".page-heading").get_by_role("link", name="去研究工作台", exact=True).click()
        expect(page.get_by_role("heading", name="研究工作台", exact=True)).to_be_visible()
        assert page.locator("#navigation a").all_text_contents() == ["情报总览01", "研究工作台02", "任务中心03", "资料库04", "组织与账号05", "研究报告06", "能力与设置07"]
        expect(page.locator(".workspace-choice")).to_have_count(2)
        print("PASS actual app router: creation workspace precedes execution; task center has no parallel composer")

        page.get_by_role("button", name="采集公开材料", exact=True).click()
        modal = page.get_by_role("dialog")
        def close_composer():
            modal.get_by_role("button", name="关闭对话框", exact=True).click()
            page.wait_for_function("key => Number.isInteger(JSON.parse(localStorage.getItem(key))?.revision)", arg=KEY)
        expect(modal).to_have_attribute("aria-label", "新建研究或采集")
        expect(modal.locator("[name=batch_kind]")).to_have_value("collect")
        expect(modal.locator("[name=workflow]")).to_have_value("single")
        expect(modal.locator("[name=source]")).to_be_visible()
        expect(modal.locator("[name=seed_twitter]")).to_be_hidden()
        modal.locator("[name=keyword]").fill("反戦, 憲法九条")
        modal.locator("[name=max_records]").fill("73")
        modal.locator("[name=workflow]").select_option("batch")
        expect(modal.locator("[name=max_records]")).to_have_value("73")
        expect(modal.get_by_role("button", name="预览任务串", exact=True)).to_be_enabled()
        modal.locator("[name=batch_kind]").select_option("research")
        expect(modal.locator("[name=source]")).to_be_hidden()
        expect(modal.locator("[name=seed_news]")).to_be_checked()
        expect(modal.locator("[name=seed_twitter]")).not_to_be_checked()
        assert not state["jobs"], "News-only research must not silently start a social check"
        modal.locator("[name=seed_news]").uncheck()
        modal.locator("[name=seed_twitter]").check()
        expect(modal.locator("[name=source]")).to_have_value("twitter")
        modal.locator("[name=seed_news]").check()
        expect(modal.locator("[name=source]")).to_have_value("google_news")
        expect(modal.locator("[name=max_records]")).to_have_value("73")
        print("PASS orthogonal content/execution choices, unchanged budgets and primary source from visible seed selections")
        close_composer()

        def seed_draft(workflow="research", kind="research"):
            values = {"workflow": workflow, "batch_kind": kind, "source": "instagram", "handle": "old_account", "keyword": "保存的主题, 第二主题", "research_goal": "保留的研究目标", "start_date": "2026-09-02", "end_date": "2026-10-05", "max_records": "88", "total_records": "1200", "seed_news": True, "seed_twitter": False, "seed_instagram": False}
            draft = {"schema": 1, "values": values, "modeBySource": {"instagram": "hashtag"}, "updated_at": "2099-01-01T00:00:00Z"}
            state["draft"] = draft
            page.evaluate("([key,draft])=>localStorage.setItem(key,JSON.stringify(draft))", [KEY, draft])
            return draft

        seed_draft()
        page.reload()
        expect(page.get_by_role("button", name="继续草稿", exact=True)).to_be_visible()
        page.get_by_role("button", name="采集公开材料", exact=True).click()
        expect(modal.locator("[name=batch_kind]")).to_have_value("collect")
        expect(modal.locator("[name=workflow]")).to_have_value("single")
        expect(modal.locator("[name=keyword]")).to_have_value("保存的主题, 第二主题")
        expect(modal.locator("[name=max_records]")).to_have_value("88")
        expect(modal.locator("[name=start_date]")).to_have_value("2026-09-02")
        close_composer()
        print("PASS explicit collection overrides legacy research kind without losing keyword, dates or budget")

        draft = seed_draft("batch")
        page.locator("#navigation a[data-route=accounts]").click()
        page.get_by_role("button", name="采集", exact=True).click()
        expect(modal).to_be_visible()
        assert modal.evaluate("node => node.matches(':modal')"), "Composer must use a native modal dialog, which makes background content inert"
        expect(page.locator("#main h1")).to_have_text("研究工作台")
        assert page.evaluate("""() => {
            const before = document.activeElement;
            document.querySelector('#navigation a[data-route=accounts]').focus();
            return document.activeElement === before && !!document.activeElement.closest('dialog[open]');
        }"""), "Background navigation must not take keyboard focus from the modal"
        before_blocked_click = page.url
        try:
            page.locator("#navigation a[data-route=accounts]").click(timeout=1000)
        except PlaywrightTimeoutError:
            pass  # Native modal background is visible through the backdrop, but it cannot receive input.
        else:
            raise AssertionError("Background navigation was clickable while the modal was open")
        assert page.url == before_blocked_click
        expect(modal.locator("[name=batch_kind]")).to_have_value("collect")
        expect(modal.locator("[name=workflow]")).to_have_value("single")
        expect(modal.locator("[name=source]")).to_have_value("twitter")
        expect(modal.locator("[name=mode]")).to_have_value("account")
        expect(modal.locator("[name=handle]")).to_have_value("selected_org")
        expect(modal.locator("[name=max_records]")).to_have_value("88")
        assert page.url == "http://app-flow.test/#network"
        close_composer()
        page.reload()
        expect(modal).to_have_count(0)
        page.get_by_role("button", name="继续草稿", exact=True).click()
        expect(modal.locator("[name=handle]")).to_have_value("selected_org")
        expect(modal.locator("[name=workflow]")).to_have_value("single")
        close_composer()
        print("PASS account shortcut overrides research/batch drafts, opens one composer and does not reopen after reload")

        page.evaluate("key=>localStorage.removeItem(key)", KEY)
        state["draft"] = None
        state["hold_draft"] = True
        page.goto("http://app-flow.test/#network?compose=collect&source=twitter&handle=selected_org")
        expect(modal.locator("[name=handle]")).to_have_value("selected_org")
        page.wait_for_function("!!document.querySelector('dialog[open]')")
        assert state["held_drafts"]
        for held in state["held_drafts"]:
            fulfill(held, {"draft": draft})
        state["held_drafts"] = []
        state["hold_draft"] = False
        expect(modal.locator("[name=keyword]")).to_have_value("保存的主题, 第二主题")
        expect(modal.locator("[name=source]")).to_have_value("twitter")
        expect(modal.locator("[name=handle]")).to_have_value("selected_org")
        expect(modal.locator("[name=batch_kind]")).to_have_value("collect")
        close_composer()
        print("PASS delayed server draft preserves explicit entry source, handle and content choice")

        page.evaluate("key=>localStorage.removeItem(key)", KEY)
        state["draft"] = None
        state["hold_draft"] = True
        page.goto("http://app-flow.test/#network?compose=research")
        modal.locator("[name=keyword]").fill("本人刚输入的新关键词")
        for held in state["held_drafts"]:
            fulfill(held, {"draft": draft})
        state["held_drafts"] = []
        state["hold_draft"] = False
        expect(modal.locator("[name=keyword]")).to_have_value("本人刚输入的新关键词")
        modal.get_by_role("button", name="开始深度研究", exact=True).click()
        expect(page.get_by_role("dialog")).to_contain_text("深度研究 · 本人刚输入的新关键词")
        assert state["jobs"][-1]["kind"] == "research"
        assert state["jobs"][-1]["params"]["seed_sources"] == ["google_news"]
        assert page.url.endswith("#jobs?selected=" + state["jobs"][-1]["id"])
        page.get_by_role("button", name="关闭对话框", exact=True).click()
        print("PASS late drafts cannot replace typing; accepted research navigates to task diagnostics and clears draft")

        page.goto("http://app-flow.test/#network?run=old-run")
        expect(page.get_by_role("heading", name="研究结果", exact=True)).to_be_visible()
        expect(page.get_by_role("link", name="查看执行记录", exact=True)).to_have_attribute("href", "#jobs?selected=old-job")
        page.get_by_role("button", name="规划后续任务串", exact=True).click()
        expect(modal.locator("[name=workflow]")).to_have_value("batch")
        expect(page.get_by_role("dialog")).to_have_count(1)
        close_composer()
        print("PASS existing run deep links and budget follow-up route to the shared workspace")

        state["hold_history"] = True
        page.reload()
        expect(page.get_by_role("button", name="探索账号与关系", exact=True)).to_be_visible()
        expect(page.locator(".workspace-history")).to_contain_text("正在读取已有研究结果")
        page.get_by_role("button", name="探索账号与关系", exact=True).click()
        expect(modal.locator("[name=batch_kind]")).to_have_value("research")
        for held in state["held_history"]:
            fulfill(held, {"error": {"message": "已有研究暂时不可用"}}, 503)
        state["held_history"] = []
        state["hold_history"] = False
        close_composer()
        expect(page.locator(".workspace-history")).to_contain_text("上方创建入口仍可使用")
        for width in [1440, 390]:
            page.set_viewport_size({"width": width, "height": 1000})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.get_by_role("button", name="采集公开材料", exact=True).click()
            assert modal.evaluate("node => node.scrollWidth <= node.clientWidth")
            close_composer()
        assert not errors, errors
        browser.close()
        print("PASS slow/failed history never blocks creation; desktop and 390px layout have no horizontal overflow")
        print("8 cross-page application integration groups passed")


if __name__ == "__main__":
    main()
