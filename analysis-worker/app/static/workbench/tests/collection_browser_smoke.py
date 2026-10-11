"""Offline acceptance of the actual collection composer, with all API calls intercepted.

Run from the repository root: python -X utf8 -B analysis-worker/app/static/workbench/tests/collection_browser_smoke.py
No account login, live collection, real model call, user database or persistent browser profile is touched.
"""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
KEY = "peace-atlas.collection-draft.v1"
BUDGETS = {
    "research": {
        "max_records": {"min": 1, "max": 10000, "default": 50},
        "max_scrolls": {"min": 1, "max": 200, "default": 10},
        "time_budget_seconds": {"min": 30, "max": 86400, "default": 1800},
    },
    "collect": {
        "max_records": {"min": 1, "max": 100, "default": 30},
        "max_scrolls": {"min": 1, "max": 20, "default": 8},
    },
    "batch": {"total_records": {"min": 1, "max": 10000, "default": 1000}, "max_tasks": 50},
}


def main():
    state = {"draft": None, "revision": 0, "jobs": [], "plans": [], "batch_plans": [], "batches": [], "deletes": 0, "fail_job": False, "fail_plan": False, "hold_plan": False, "held_plans": [], "conflict": False, "runtime_ready": True, "tw_session": True}
    errors = []
    html = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><link rel="stylesheet" href="/assets/styles.css"><link rel="stylesheet" href="/assets/typography.css"></head><body>
      <main id="main"><button id="launch">新建研究任务</button></main><div id="notifications"></div>
      <script type="module">
      import {openCollect} from '/assets/actions.js';
      window.__navigation=[];
      const ctx={signal:new AbortController().signal,refresh(){},navigate(...args){window.__navigation.push(args)}};
      window.openCollection=(initial={})=>openCollect(ctx,initial);
      document.getElementById('launch').addEventListener('click',()=>window.openCollection());
      </script></body></html>"""

    def route(request):
        path = urlsplit(request.request.url).path
        method = request.request.method
        if path == "/":
            return request.fulfill(content_type="text/html", body=html)
        if path.startswith("/assets/"):
            file = ROOT / path.removeprefix("/assets/")
            assert file.resolve().is_relative_to(ROOT)
            return request.fulfill(content_type={".css": "text/css", ".woff2": "font/woff2", ".json": "application/json", ".txt": "text/plain"}.get(file.suffix, "text/javascript"), body=file.read_bytes())
        data = {}
        if path == "/api/v2/social":
            data = {"runtime": {"ready": state["runtime_ready"]}, "platforms": [{"id": platform, "state": "session_unverified", "session_available": state["tw_session"] if platform == "twitter" else True, "modes": ["keyword", "hashtag", "account"], "detail": "隔离测试会话"} for platform in ["twitter", "instagram"]]}
        elif path == "/api/v2/capabilities":
            data = {"budget_limits": BUDGETS}
        elif path == "/api/v2/drafts/collection":
            if method == "PUT":
                if state["conflict"]:
                    return request.fulfill(status=409, content_type="application/json", body=json.dumps({"error": {"code": "draft_conflict", "message": "另一个窗口已更新草稿"}}))
                state["revision"] += 1
                state["draft"] = {**request.request.post_data_json, "revision": state["revision"]}
            elif method == "DELETE":
                state["draft"] = None
                state["deletes"] += 1
            data = {"draft": state["draft"]}
        elif path == "/api/v2/research/plan" and method == "POST":
            params = request.request.post_data_json
            state["plans"].append(params)
            data = {"groups": [], "queries": [], "suggestions": [{"keyword": "日本 反戦", "reason": "公开研究主题", "source": "rules", "selected": False}], "ai": {"used": False, "message": "未配置模型"}}
        elif path in {"/api/v2/research/batch-plan", "/api/v2/jobs/batch"} and method == "POST":
            params = request.request.post_data_json
            tasks = [{"kind": params["kind"], "params": {**params["params"], "keyword": term}, "label": "独立主题 · " + term} for term in ["反戦", "憲法九条"]]
            plan = {"tasks": tasks, "total_records": params["total_records"], "max_possible_records": sum(task["params"]["max_records"] for task in tasks), "warnings": ["目标是保存上限，可能没有足量材料"], "title": params["title"]}
            if path.endswith("batch-plan"):
                state["batch_plans"].append(params)
                if state["fail_plan"]:
                    return request.fulfill(status=503, content_type="application/json", body=json.dumps({"error": {"message": "测试：计划暂时无法生成"}}))
                if state["hold_plan"]:
                    state["held_plans"].append((request, plan))
                    return
                data = plan
            else:
                child_jobs = [{**task, "id": f"batch-child-{len(state['jobs']) + index}", "status": "queued", "progress": 0} for index, task in enumerate(tasks)]
                state["jobs"].extend(child_jobs)
                batch = {"id": f"batch-{len(state['batches'])}", "jobs": child_jobs, "title": params["title"]}
                state["batches"].append(batch)
                data = {"batch": batch, "jobs": child_jobs, "plan": plan}
        elif path == "/api/v2/jobs" and method == "POST":
            data = request.request.post_data_json
            if state["fail_job"] and data["kind"] != "login":
                return request.fulfill(status=422, content_type="application/json", body=json.dumps({"error": {"message": "测试：任务未能创建"}}))
            verified = state["tw_session"] if data["kind"] == "login" and data["params"].get("platform") == "twitter" else True
            data = {**data, "id": f"offline-{len(state['jobs'])}", "status": "succeeded", "result": {"method": "auto", "verified": verified, "saved": False, "source": "workbench", "requires_login": False}}
            state["jobs"].append(data)
        elif path.startswith("/api/v2/jobs/"):
            data = next(job for job in state["jobs"] if job["id"] == path.split("/")[4])
        else:
            return request.fulfill(status=404, content_type="application/json", body=json.dumps({"error": {"message": "Unexpected test request: " + path}}))
        request.fulfill(content_type="application/json", body=json.dumps(data, ensure_ascii=False))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        page.route("**/*", route)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto("http://collection.test/")
        page.get_by_role("button", name="新建研究任务", exact=True).click()
        modal = page.get_by_role("dialog")
        expect(modal.locator("[name=workflow]")).to_have_value("single")
        expect(modal.locator("[name=research_goal]")).to_be_visible()
        expect(modal.locator("[name=depth]")).to_be_hidden()
        expect(modal.get_by_role("button", name="开始深度研究", exact=True)).to_be_enabled()
        initial_logins = len([job for job in state["jobs"] if job["kind"] == "login"])
        assert initial_logins == 0
        terms = "革命, 労働者, 矢嶋尋, 戦争阻止, 全学連, 階級闘争"
        modal.locator("[name=keyword]").fill(terms)
        expect(modal.locator(".query-preview li")).to_have_count(6)
        assert page.evaluate("key => JSON.parse(localStorage.getItem(key)).values.keyword", KEY) == terms
        expect(modal).to_contain_text("空格表示同时包含")
        modal.locator("[name=keyword_mode]").select_option("all")
        expect(modal.locator(".query-preview li")).to_have_count(1)
        modal.locator("[name=keyword_mode]").select_option("any")
        modal.get_by_role("button", name="生成相关词", exact=True).click()
        suggestion = modal.get_by_role("button", name="日本 反戦", exact=True)
        expect(suggestion).to_be_visible()
        suggestion.click()
        assert modal.locator("[name=keyword]").input_value().startswith("革命\n労働者\n矢嶋尋")
        assert "日本 反戦" in modal.locator("[name=keyword]").input_value()
        modal.get_by_role("button", name="撤销最近添加", exact=True).click()
        expect(modal.locator("[name=keyword]")).to_have_value(terms)
        modal.get_by_role("button", name="AI 扩展研究词", exact=True).click()
        expect(modal.locator(".keyword-assist")).to_contain_text("未配置模型")
        assert state["plans"][-1]["use_ai"] is True
        assert len([job for job in state["jobs"] if job["kind"] == "login"]) == initial_logins
        print("PASS default research, six independent keyword groups, AND/OR preview, additive suggestions/undo and honest AI state")

        page.mouse.click(2, 2)
        expect(modal).to_be_visible()
        modal.locator("[name=research_goal]").fill("识别公开活动组织与账号联动，要求给出证据。")
        modal.get_by_role("button", name="近 7 天", exact=True).click()
        chosen_date = modal.locator("[name=start_date]").input_value()
        assert page.evaluate("key => JSON.parse(localStorage.getItem(key)).values.start_date", KEY) == chosen_date
        page.keyboard.press("Escape")
        expect(modal).to_have_count(0)
        page.get_by_role("button", name="新建研究任务", exact=True).click()
        expect(modal.locator("[name=keyword]")).to_have_value(terms)
        expect(modal.locator("[name=research_goal]")).to_have_value("识别公开活动组织与账号联动，要求给出证据。")
        expect(modal.locator("[name=start_date]")).to_have_value(chosen_date)
        modal.locator("[name=batch_kind]").select_option("collect")
        modal.locator("[name=source]").select_option("twitter")
        modal.locator("[name=mode]").select_option("account")
        modal.locator("[name=handle]").fill("public_org")
        modal.locator("[name=source]").select_option("rss")
        modal.locator("[name=feed_url]").fill("https://example.org/rss")
        expect(modal.locator("[name=handle]")).to_be_disabled()
        modal.locator("[name=source]").select_option("twitter")
        expect(modal.locator("[name=mode]")).to_have_value("account")
        expect(modal.locator("[name=handle]")).to_have_value("public_org")
        expect(modal.locator("[name=keyword]")).to_have_value(terms)
        page.keyboard.press("Escape")
        page.reload()
        page.get_by_role("button", name="新建研究任务", exact=True).click()
        expect(modal.locator("[name=source]")).to_have_value("twitter")
        expect(modal.locator("[name=mode]")).to_have_value("account")
        expect(modal.locator("[name=handle]")).to_have_value("public_org")
        expect(modal.locator("[name=start_date]")).to_have_value(chosen_date)
        print("PASS immediate local persistence, protected backdrop, ESC/reopen/refresh restoration, date shortcuts and source/mode preservation")

        modal.locator("[name=source]").select_option("google_news")
        state["fail_job"] = True
        modal.get_by_role("button", name="开始资料采集", exact=True).click()
        expect(modal.get_by_role("alert")).to_contain_text("任务未能创建")
        expect(modal.locator("[name=keyword]")).to_have_value(terms)
        assert state["deletes"] == 0
        state["fail_job"] = False
        modal.get_by_role("button", name="开始资料采集", exact=True).click()
        expect(modal).to_have_count(0)
        assert state["jobs"][-1]["kind"] == "collect"
        assert state["jobs"][-1]["params"]["keyword_mode"] == "any"
        assert state["jobs"][-1]["params"]["keyword"] == terms
        assert state["draft"] is None and state["deletes"] == 1
        assert page.evaluate("key => localStorage.getItem(key)", KEY) is None
        print("PASS failed submit retains draft, accepted submit clears only after queue acknowledgement and preserves query semantics")

        page.get_by_role("button", name="新建研究任务", exact=True).click()
        expect(modal.locator("[name=workflow]")).to_have_value("single")
        modal.locator("[name=keyword]").fill("反戦, 憲法九条")
        modal.locator("[name=research_goal]").fill("追踪公开账号的活动联系和共同议题。")
        modal.locator("summary").filter(has_text="运行上限与探索方向").click()
        for name, value in {"max_records": "50", "research_max_scrolls": "10", "time_budget_seconds": "1800"}.items():
            expect(modal.locator(f"[name={name}]")).to_have_value(value)
        for name, value in {"max_records": "10000", "research_max_scrolls": "200", "time_budget_seconds": "86400"}.items():
            expect(modal.locator(f"[name={name}]")).to_have_attribute("max", value)
        modal.locator("[name=time_budget_seconds]").fill("86401")
        modal.locator("summary").filter(has_text="运行上限与探索方向").click()
        expect(modal.locator("[name=time_budget_seconds]")).to_be_hidden()
        before_invalid = len(state["jobs"])
        modal.get_by_role("button", name="开始深度研究", exact=True).click()
        expect(modal.locator("[name=time_budget_seconds]")).to_be_visible()
        expect(modal.locator(".submit-status")).to_contain_text("运行时间上限")
        assert len(state["jobs"]) == before_invalid, "Hidden invalid budget must be revealed without enqueueing a job"
        modal.locator("[name=max_records]").fill("500")
        modal.locator("[name=research_max_scrolls]").fill("25")
        modal.locator("[name=time_budget_seconds]").fill("3600")
        modal.locator("[name=expand_followers]").check()
        modal.locator("[name=seed_instagram]").check()
        expect(modal.get_by_role("button", name="开始深度研究", exact=True)).to_be_enabled()
        modal.get_by_role("button", name="开始深度研究", exact=True).click()
        expect(modal).to_have_count(0)
        params = state["jobs"][-1]["params"]
        assert state["jobs"][-1]["kind"] == "research"
        assert params["seed_sources"] == ["google_news", "instagram"]
        assert params["expand_relations"] == ["following", "followers", "mentions"]
        assert {key: params[key] for key in ["depth", "max_accounts", "max_edges", "max_records", "max_scrolls", "time_budget_seconds"]} == {"depth": 2, "max_accounts": 12, "max_edges": 60, "max_records": 500, "max_scrolls": 25, "time_budget_seconds": 3600}
        assert params["analysis_mode"] == "auto" and params["research_goal"] == "追踪公开账号的活动联系和共同议题。"
        assert "feed_url" not in params and "include_following" not in params
        print("PASS shared budget metadata/defaults, 500 records/25 scrolls/3600 seconds payload, all three relation directions and selected platforms")

        page.get_by_role("button", name="新建研究任务", exact=True).click()
        modal.locator("[name=keyword]").fill("我的本机输入")
        state["conflict"] = True
        expect(modal.locator(".draft-status")).to_contain_text("另一个窗口")
        expect(modal.locator("[name=keyword]")).to_have_value("我的本机输入")
        expect(modal.get_by_role("button", name="保留当前输入并重新同步", exact=True)).to_be_visible()
        state["conflict"] = False
        modal.get_by_role("button", name="保留当前输入并重新同步", exact=True).click()
        expect(modal.locator(".draft-status")).to_contain_text("草稿已保存")
        assert state["draft"]["values"]["keyword"] == "我的本机输入"
        print("PASS conflict preserves user edits and explicit resync")

        modal.locator("[name=workflow]").select_option("batch")
        expect(modal.locator("[name=max_records]")).to_have_value("50")
        modal.locator("[name=keyword]").fill("反戦, 憲法九条")
        modal.locator("[name=batch_title]").fill("公开反战资料任务串")
        expect(modal.locator("[name=total_records]")).to_have_value("1000")
        modal.locator("summary").filter(has_text="运行上限与探索方向").click()
        modal.locator("[name=max_records]").fill("500")
        modal.locator("[name=research_max_scrolls]").fill("25")
        modal.locator("[name=time_budget_seconds]").fill("3600")
        expect(modal.get_by_role("button", name="预览任务串", exact=True)).to_be_enabled()
        before_batch_jobs = len(state["jobs"])
        state["fail_plan"] = True
        modal.get_by_role("button", name="预览任务串", exact=True).click()
        expect(modal.locator(".batch-plan")).to_contain_text("计划暂时无法生成")
        expect(modal.get_by_role("button", name="预览任务串", exact=True)).to_be_enabled()
        assert not state["batches"] and len(state["jobs"]) == before_batch_jobs
        state["fail_plan"] = False
        modal.get_by_role("button", name="预览任务串", exact=True).click()
        expect(modal.locator(".batch-plan")).to_contain_text("2 个排队任务")
        expect(modal.locator(".batch-plan")).to_contain_text("保存上限")
        expect(modal.locator(".batch-duration")).to_contain_text("2 小时")
        expect(modal.get_by_role("button", name="创建研究任务串", exact=True)).to_be_enabled()
        assert not state["batches"] and len(state["jobs"]) == before_batch_jobs, "A preview must never enqueue work"
        modal.locator("[name=research_goal]").fill("要求公开证据与逐层路径")
        expect(modal.get_by_role("button", name="预览任务串", exact=True)).to_be_enabled()
        state["hold_plan"] = True
        modal.get_by_role("button", name="生成任务串预览", exact=True).click()
        expect(modal.locator(".batch-plan")).to_contain_text("正在拆分")
        assert state["held_plans"]
        modal.locator("[name=research_goal]").fill("要求公开证据与逐层路径，并排除没有证据的关联")
        for held_request, held_plan in state["held_plans"]:
            held_request.fulfill(content_type="application/json", body=json.dumps(held_plan, ensure_ascii=False))
        state["held_plans"] = []
        state["hold_plan"] = False
        expect(modal.get_by_role("button", name="预览任务串", exact=True)).to_be_enabled()
        expect(modal.get_by_role("button", name="创建研究任务串", exact=True)).to_have_count(0)
        modal.get_by_role("button", name="生成任务串预览", exact=True).click()
        expect(modal.get_by_role("button", name="创建研究任务串", exact=True)).to_be_enabled()
        assert state["batch_plans"][-1]["params"]["research_goal"] == "要求公开证据与逐层路径，并排除没有证据的关联"
        modal.get_by_role("button", name="创建研究任务串", exact=True).click()
        expect(modal).to_have_count(0)
        assert len(state["batches"]) == 1 and len(state["batches"][0]["jobs"]) == 2
        assert all({key: child["params"][key] for key in ["max_records", "max_scrolls", "time_budget_seconds"]} == {"max_records": 500, "max_scrolls": 25, "time_budget_seconds": 3600} for child in state["batches"][0]["jobs"])
        assert page.evaluate("window.__navigation.at(-1)") == ["jobs", {"batch": "batch-0"}]
        assert state["draft"] is None
        print("PASS enabled primary preview, recoverable plan failure, preview does not enqueue, edited plans expire and confirmed series enqueue once")
        page.get_by_role("button", name="新建研究任务", exact=True).click()
        page.keyboard.press("Escape")
        state["runtime_ready"] = False
        page.reload()
        page.get_by_role("button", name="新建研究任务", exact=True).click()
        modal.locator("[name=keyword]").fill("公开新闻研究")
        modal.locator("[name=seed_twitter]").check()
        expect(modal.get_by_role("button", name="开始深度研究", exact=True)).to_be_disabled()
        expect(modal.locator(".submit-status")).to_contain_text(re.compile(r"X|Twitter"))
        modal.locator("[name=workflow]").select_option("batch")
        expect(modal.get_by_role("button", name="预览任务串", exact=True)).to_be_enabled()
        modal.get_by_role("button", name="预览任务串", exact=True).click()
        expect(modal.get_by_role("button", name="创建研究任务串", exact=True)).to_be_disabled()
        expect(modal.locator(".submit-status")).to_contain_text(re.compile(r"X|Twitter"))
        assert len(state["batches"]) == 1, "Unavailable social runtime may permit planning, but not enqueueing"
        before_news_only = len([job for job in state["jobs"] if job["kind"] == "login"])
        modal.locator("[name=seed_twitter]").uncheck()
        expect(modal.get_by_role("button", name="预览任务串", exact=True)).to_be_enabled()
        modal.get_by_role("button", name="预览任务串", exact=True).click()
        expect(modal.get_by_role("button", name="创建研究任务串", exact=True)).to_be_enabled()
        modal.get_by_role("button", name="创建研究任务串", exact=True).click()
        expect(modal).to_have_count(0)
        assert state["jobs"][-1]["params"]["seed_sources"] == ["google_news"]
        assert len([job for job in state["jobs"] if job["kind"] == "login"]) == before_news_only
        state["runtime_ready"] = True
        state["tw_session"] = False
        page.reload()
        page.get_by_role("button", name="新建研究任务", exact=True).click()
        modal.locator("[name=keyword]").fill("验证未通过的社媒任务")
        modal.locator("[name=seed_twitter]").check()
        expect(modal.locator(".submit-status")).to_contain_text("尚未通过会话检测")
        expect(modal.locator(".submit-status")).to_contain_text(re.compile(r"X|Twitter"))
        modal.locator("[name=workflow]").select_option("batch")
        expect(modal.get_by_role("button", name="预览任务串", exact=True)).to_be_enabled()
        modal.get_by_role("button", name="预览任务串", exact=True).click()
        expect(modal.get_by_role("button", name="创建研究任务串", exact=True)).to_be_disabled()
        expect(modal.locator(".submit-status")).to_contain_text("尚未通过会话检测")
        assert len(state["batches"]) == 2
        page.set_viewport_size({"width": 390, "height": 844})
        assert modal.evaluate("node => node.scrollWidth <= node.clientWidth"), "Research composer overflows narrow viewport"
        modal.locator("[name=batch_kind]").select_option("collect")
        modal.locator("[name=source]").select_option("google_news")
        expect(modal.get_by_role("button", name="预览任务串", exact=True)).to_be_enabled()
        modal.get_by_role("button", name="预览任务串", exact=True).click()
        expect(modal.get_by_role("button", name="创建采集任务串", exact=True)).to_be_enabled()
        assert len(state["batches"]) == 2, "Simple collection preview must not enqueue work"
        modal.get_by_role("button", name="创建采集任务串", exact=True).click()
        expect(modal).to_have_count(0)
        assert len(state["batches"]) == 3 and all(child["kind"] == "collect" for child in state["batches"][-1]["jobs"])
        assert not errors, errors
        browser.close()
        print("PASS missing social runtime has visible submit reason, still permits preview and never enqueues blocked sources")
        print("PASS news-only research and simple collection series use two-step creation without redundant session checks; 390px composer has no overflow")
        print("8 collection composer integration groups passed")


if __name__ == "__main__":
    main()
