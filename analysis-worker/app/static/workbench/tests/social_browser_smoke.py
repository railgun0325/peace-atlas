"""Offline DOM integration check. Run: python tests/social_browser_smoke.py

Requires installed Python Playwright + Chromium. Every request is intercepted;
no platform login, collection, AI call, credential, live service or file write.
The fixtures test UI behavior, not whether external collection currently works.
"""
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def main():
    jobs = []
    saved_draft = {"value": None, "revision": 0}
    browser_opens = []
    errors = []
    profiles = [{"id": "opaque-default", "name": "默认研究配置", "default": True, "available": True}, {"id": "opaque-other", "name": "其他本机配置", "default": False, "available": True}, {"id": "opaque-missing", "name": "缺失配置", "default": False, "available": False}]
    state = {"ig_session": False, "tw_session": True, "runtime_ready": True, "profiles": profiles, "profile_reads": 0, "profile_error": False, "browser_error": False, "auto_state": {}, "firefox_auto": {"twitter": False, "instagram": False}, "bridge_reads": 0, "bridge_error": False}
    account = {"id": "account-test", "name": "Public Research Organization", "handle": "public_org", "platform": "twitter", "status": "candidate", "url": "https://example.org/account", "tags": []}
    accounts = [account] + [{**account, "id": f"account-{index}", "name": f"Archive organization{index:03d}", "handle": f"archive_{index:03d}", "status": "verified" if index == 60 else "candidate"} for index in range(1, 61)]
    item = {"id": "item-test", "title": "公开资料测试", "type": "social", "source_name": "Instagram", "url": "https://example.org/post", "content": "用于界面测试的公开材料。", "published_at": None, "metadata": {"content_kind": "image_alt", "engagement": None, "engagement_verified": False, "legacy_engagement": {"likes": 0}, "relevance": {"mode": "broad", "reasons": ["匹配检索条件"]}}}

    def route(request):
        path = urlsplit(request.request.url).path
        method = request.request.method
        if path == "/":
            return request.fulfill(content_type="text/html", body=(ROOT / "index.html").read_text(encoding="utf-8"))
        if path.startswith("/assets/"):
            file = ROOT / path.removeprefix("/assets/")
            assert file.resolve().is_relative_to(ROOT)
            return request.fulfill(content_type={".css": "text/css", ".woff2": "font/woff2", ".json": "application/json", ".txt": "text/plain"}.get(file.suffix, "text/javascript"), body=file.read_bytes())
        data = {}
        if path == "/api/v2/health":
            data = {"status": "ok"}
        elif path == "/api/v2/capabilities":
            data = {"collectors": [], "llm": {"configured": False}}
        elif path == "/api/v2/drafts/collection":
            if method == "PUT":
                saved_draft["revision"] += 1
                saved_draft["value"] = {**request.request.post_data_json, "revision": saved_draft["revision"]}
            elif method == "DELETE":
                saved_draft["value"] = None
            data = {"draft": saved_draft["value"]}
        elif path == "/api/v2/research/plan":
            data = {"groups": [], "queries": [], "suggestions": [{"keyword": "反戦", "reason": "研究相关", "source": "rules", "selected": False}], "ai": {"used": False}}
        elif path == "/api/v2/social":
            data = {"runtime": {"ready": state["runtime_ready"], "playwright_installed": state["runtime_ready"], "browser_installed": state["runtime_ready"]}, "platforms": [{"id": platform, "name": platform, "state": "session_unverified" if available else "login_required", "session_available": available, "modes": modes, "detail": "隔离测试状态"} for platform, available, modes in [("twitter", state["tw_session"], ["keyword", "account"]), ("instagram", state["ig_session"], ["keyword", "hashtag", "account"])]]}
        elif path == "/api/v2/social/browser/open" and method == "POST":
            browser_opens.append(request.request.post_data_json)
            if state["browser_error"]:
                return request.fulfill(status=503, content_type="application/json", body=json.dumps({"error": {"code": "browser_open_failed", "message": "无法打开系统默认浏览器。"}}))
            data = {"opened": True, "platform": browser_opens[-1]["platform"], "mode": "system_default", "login_verified": False, "session_imported": False}
        elif path == "/api/v2/social/bridge/start" and method == "POST":
            state["bridge_reads"] += 1
            if state["bridge_error"]:
                return request.fulfill(status=503, content_type="application/json", body=json.dumps({"error": {"code": "bridge_unavailable", "message": "本地连接暂不可用"}}))
            platform = request.request.post_data_json["platform"]
            data = {"platform": platform, "pairing_url": "http://127.0.0.1:18769/api/v2/social/bridge/connect?code=" + "a" * 64 + "&platform=" + platform, "expires_in": 300, "verified": False}
        elif path == "/api/v2/social/firefox-profiles":
            state["profile_reads"] += 1
            if state["profile_error"]:
                return request.fulfill(status=500, content_type="application/json", body=json.dumps({"error": {"code": "profile_list_failed", "message": "配置列表暂时无法读取"}}))
            data = {"items": state["profiles"]}
        elif path == "/api/v2/jobs" and method == "POST":
            data = {**request.request.post_data_json, "id": f"job-{len(jobs)}", "status": "queued", "progress": 0, "created_at": "2026-05-18T07:00:00Z", "cancel_requested": False, "result": None, "events": []}
            if data["kind"] == "login" and data["params"].get("method") == "auto":
                platform = data["params"]["platform"]
                available = state["tw_session"] if platform == "twitter" else state["ig_session"]
                code = state["auto_state"].get(platform, "ready" if available or state["firefox_auto"][platform] else "requires_login")
                source = "workbench" if available else "firefox" if state["firefox_auto"][platform] else "none"
                if code == "ready":
                    state["tw_session" if platform == "twitter" else "ig_session"] = True
                data.update(status="succeeded", result={"method": "auto", "verified": code == "ready", "saved": code == "ready", "source": source, "state": code, "requires_login": code == "requires_login", "message": "平台限制请求，请稍后重试。" if code == "rate_limited" else "隔离测试检查结果"})
            if data["kind"] == "collect":
                data.update(status="succeeded", result={"inserted": 1, "updated": 2, "relations_updated": 3})
            jobs.append(data)
        elif path == "/api/v2/jobs":
            data = {"items": jobs, "total": len(jobs)}
        elif path == "/api/v2/job-batches":
            data = {"items": [], "total": 0}
        elif path.startswith("/api/v2/jobs/"):
            job_id = path.split("/")[4]
            data = next(job for job in jobs if job["id"] == job_id)
            if path.endswith("/cancel"):
                data["status"] = "cancelled"
        elif path == "/api/v2/research":
            data = {"items": [], "total": 0}
        elif path == "/api/v2/accounts":
            data = {"items": accounts, "total": len(accounts)}
        elif path.endswith("/relations"):
            data = {"items": [{"id": "relation-test", "source_name": "Public Research Organization", "target_name": "Another Public Organization", "relation": "follows", "evidence_url": "https://example.org/evidence", "observed_at": "2026-05-18T07:00:00Z", "last_observed_at": "2026-10-06T00:00:00Z"}, {"id": "legacy-relation", "source_name": "Legacy Source", "target_name": "Legacy Target", "relation": "follows", "evidence_url": "", "observed_at": None}], "total": 2}
        elif path == "/api/v2/items":
            data = {"items": [item], "total": 1}
        elif path.startswith("/api/v2/items/"):
            data = item
        else:
            return request.fulfill(status=404, content_type="application/json", body=json.dumps({"error": {"message": "Unexpected test request: " + path}}))
        request.fulfill(content_type="application/json", body=json.dumps(data, ensure_ascii=False))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.add_init_script("Object.defineProperty(navigator, 'clipboard', {value: {writeText: async value => {window.__copiedPairing = value;}}});")
        page.route("**/*", route)
        page.on("pageerror", lambda error: errors.append(str(error)))
        def launch_collection():
            if state["tw_session"]:
                page.locator("#navigation a[data-route=settings]").click()
                expect(page.get_by_role("heading", name="社媒来源与登录")).to_be_visible()
                page.get_by_role("button", name="新建采集", exact=True).first.click()
            else:
                page.goto("http://workbench.test/#network?compose=collect&source=twitter")
            expect(page.get_by_role("dialog").locator("[name=batch_kind]")).to_have_value("collect")
            expect(page.get_by_role("dialog").locator("[name=workflow]")).to_have_value("single")
        page.goto("http://workbench.test/#settings")
        try:
            expect(page.get_by_role("heading", name="社媒来源与登录")).to_be_visible()
        except AssertionError as exc:
            raise AssertionError(f"Settings failed to mount: browser errors={errors}; main={page.locator('#main').inner_text()}") from exc
        bridge = page.get_by_role("button", name="连接已登录的 Chrome / Edge", exact=True)
        expect(bridge).to_have_count(2)
        assert state["bridge_reads"] == 0
        bridge.last.click()
        bridge_dialog = page.get_by_role("dialog")
        expect(bridge_dialog).to_contain_text("首次使用需安装")
        expect(bridge_dialog).to_contain_text("5 分钟内有效")
        pairing_input = bridge_dialog.locator("[name=bridge_pairing_url]")
        expect(pairing_input).to_have_value("http://127.0.0.1:18769/api/v2/social/bridge/connect?code=" + "a" * 64 + "&platform=instagram")
        expect(pairing_input).to_have_attribute("readonly", "true")
        assert page.evaluate("window.__copiedPairing === undefined")
        bridge_dialog.get_by_role("button", name="复制配对地址", exact=True).click()
        expect(bridge_dialog).to_contain_text("已复制")
        assert page.evaluate("window.__copiedPairing") == pairing_input.input_value()
        assert state["bridge_reads"] == 1 and not browser_opens and not jobs
        page.set_viewport_size({"width": 390, "height": 844})
        assert bridge_dialog.evaluate("el => el.scrollWidth <= el.clientWidth"), "Pairing dialog overflows narrow viewport"
        bridge_dialog.get_by_role("button", name="关闭", exact=True).click()
        expect(page.locator("[name=bridge_pairing_url]")).to_have_count(0)
        assert state["bridge_reads"] == 1 and not browser_opens and not jobs
        page.set_viewport_size({"width": 1280, "height": 900})
        state["bridge_error"] = True
        bridge.first.click()
        expect(page.get_by_role("dialog").get_by_role("alert")).to_contain_text("本地连接暂不可用")
        expect(page.locator("[name=bridge_pairing_url]")).to_have_count(0)
        page.get_by_role("dialog").get_by_role("button", name="关闭", exact=True).click()
        state["bridge_error"] = False
        print("PASS explicit local Chrome/Edge pairing, click-only copy, cleared address, errors and no login/poll side effects")
        firefox = page.get_by_role("button", name="同步 Firefox 会话", exact=True)
        expect(firefox).to_have_count(1)  # Explicit Firefox import remains X-only.
        assert state["profile_reads"] == 0 and not jobs
        firefox.click()
        modal = page.get_by_role("dialog")
        expect(modal.locator("[name=profile_id]")).to_have_value("opaque-default")
        expect(modal.locator("option[value=opaque-missing]")).to_be_disabled()
        expect(modal.locator("[name=profile_id]")).to_be_focused()
        modal.locator("[name=profile_id]").select_option("opaque-other")
        assert state["profile_reads"] == 1 and not jobs
        modal.get_by_role("button", name="取消", exact=True).click()
        expect(firefox).to_be_focused()
        assert not jobs
        page.set_viewport_size({"width": 390, "height": 844})
        firefox.click()
        expect(page.get_by_role("dialog").locator("[name=profile_id]")).to_have_value("opaque-default")
        assert page.get_by_role("dialog").evaluate("el => el.scrollWidth <= el.clientWidth"), "Firefox dialog overflows narrow viewport"
        page.get_by_role("dialog").locator("[name=profile_id]").select_option("opaque-other")
        page.get_by_role("button", name="导入并验证 X 会话", exact=True).click()
        expect(page.get_by_role("dialog")).to_contain_text("Twitter / X · Firefox 会话导入")
        expect(page.get_by_role("dialog")).not_to_contain_text("最多等待 10 分钟")
        assert jobs[-1]["params"] == {"platform": "twitter", "method": "firefox", "profile_id": "opaque-other"}
        page.get_by_role("button", name="取消任务", exact=True).click()
        expect(page.get_by_role("dialog")).to_contain_text("已取消")
        page.get_by_role("button", name="关闭对话框").click()
        page.set_viewport_size({"width": 1280, "height": 900})
        page.locator("#navigation a[data-route=settings]").click()
        expect(firefox).to_be_visible()
        before_empty = len(jobs)
        state["profiles"] = []
        firefox.click()
        expect(page.get_by_role("dialog")).to_contain_text("未发现可用的 Firefox 配置")
        expect(page.get_by_role("dialog")).to_contain_text("本地会话连接扩展入口")
        expect(page.get_by_role("dialog").get_by_role("button", name="用系统默认浏览器登录", exact=True)).to_be_visible()
        assert len(jobs) == before_empty
        page.get_by_role("dialog").get_by_role("button", name="用系统默认浏览器登录", exact=True).click()
        expect(page.get_by_role("dialog")).to_have_count(0)
        assert browser_opens[-1] == {"platform": "twitter"} and len(jobs) == before_empty
        state["profiles"] = profiles
        state["profile_error"] = True
        firefox.click()
        expect(page.get_by_role("dialog").get_by_role("alert")).to_contain_text("配置列表暂时无法读取")
        assert len(jobs) == before_empty
        state["profile_error"] = False
        page.get_by_role("dialog").get_by_role("button", name="重试", exact=True).click()
        expect(page.get_by_role("dialog").locator("[name=profile_id]")).to_have_value("opaque-default")
        page.get_by_role("dialog").get_by_role("button", name="取消", exact=True).click()
        print("PASS explicit Firefox consent, opaque selection, cancel/focus, empty/error states and narrow layout")
        ig_card = page.locator(".social-session").filter(has=page.get_by_role("heading", name="instagram", exact=True))
        before_open = len(jobs)
        ig_login = ig_card.get_by_role("button", name="用系统默认浏览器登录", exact=True)
        ig_login.click()
        expect(ig_card).to_contain_text("当前采集会话尚未更新")
        expect(ig_login).to_be_enabled()
        expect(ig_card.get_by_role("button", name="新建采集", exact=True)).to_be_disabled()
        expect(page.get_by_role("dialog")).to_have_count(0)
        assert browser_opens[-1] == {"platform": "instagram"} and len(jobs) == before_open
        assert page.url.endswith("#settings")
        state["browser_error"] = True
        ig_login.click()
        expect(ig_card.get_by_role("alert")).to_contain_text("无法打开系统默认浏览器")
        expect(ig_login).to_be_enabled()
        assert len(jobs) == before_open
        state["browser_error"] = False
        state["runtime_ready"] = False
        page.get_by_role("button", name="刷新会话状态", exact=True).click()
        expect(ig_login).to_be_enabled()
        expect(page.locator(".social-runtime")).to_contain_text("未安装")
        ig_login.click()
        expect(ig_card).to_contain_text("当前采集会话尚未更新")
        assert len(jobs) == before_open
        state["runtime_ready"] = True
        page.get_by_role("button", name="刷新会话状态", exact=True).click()
        before_detect_open = len(browser_opens)
        state["auto_state"]["instagram"] = "rate_limited"
        ig_card.get_by_role("button", name="检测并复用已有登录", exact=True).click()
        expect(ig_card).to_contain_text("平台限制请求，请稍后重试")
        expect(ig_card.get_by_role("button", name="新建采集", exact=True)).to_be_disabled()
        assert len(browser_opens) == before_detect_open
        state["auto_state"].clear()
        before_collect_check = len(jobs)
        launch_collection()
        modal = page.get_by_role("dialog")
        expect(modal.locator("[name=batch_kind]")).to_have_value("collect")
        modal.locator("[name=source]").select_option("instagram")
        expect(modal).to_contain_text("未发现可用的现有会话")
        assert jobs[-1]["params"] == {"platform": "instagram", "method": "auto"}
        assert len(jobs) == before_collect_check + 2  # X shortcut checks X once, then source switch checks Instagram once.
        fallback_opens = len(browser_opens)
        modal.locator("[name=mode]").select_option("hashtag")
        modal.locator("[name=include_following]").check()
        assert len(browser_opens) == fallback_opens and len(jobs) == before_collect_check + 2
        modal.get_by_role("button", name="用系统默认浏览器登录", exact=True).click()
        expect(modal).to_contain_text("此操作尚未更新采集会话")
        expect(modal.get_by_role("button", name="开始资料采集", exact=True)).to_be_disabled()
        assert browser_opens[-1] == {"platform": "instagram"} and len(jobs) == before_collect_check + 2
        assert all(job["params"].get("method") in {"auto", "firefox"} for job in jobs if job["kind"] == "login")
        modal.get_by_role("button", name="关闭对话框").click()
        print("PASS system-default browser open without login jobs, false ready state or runtime dependence")

        before_tw_reuse_opens = len(browser_opens)
        state["tw_session"] = False
        state["firefox_auto"]["twitter"] = True
        launch_collection()
        modal = page.get_by_role("dialog")
        modal.locator("[name=source]").select_option("twitter")
        expect(modal).to_contain_text("已自动复用 Firefox 登录")
        expect(modal.get_by_role("button", name="开始资料采集", exact=True)).to_be_enabled()
        assert state["tw_session"] is True and len(browser_opens) == before_tw_reuse_opens
        before_verified_changes = len(jobs)
        modal.locator("[name=mode]").select_option("account")
        modal.locator("[name=include_following]").check()
        assert len(jobs) == before_verified_changes
        expect(modal.locator("[name=handle]")).to_be_visible()
        modal.get_by_role("button", name="关闭对话框").click()
        print("PASS automatic reuse stays in collection form and never repeats login for field changes")

        state["ig_session"] = False
        state["firefox_auto"]["instagram"] = True
        before_ig_reuse_opens = len(browser_opens)
        launch_collection()
        modal = page.get_by_role("dialog")
        modal.locator("[name=source]").select_option("instagram")
        expect(modal).to_contain_text("已自动复用 Firefox 登录")
        expect(modal.get_by_role("button", name="开始资料采集", exact=True)).to_be_enabled()
        assert state["ig_session"] is True and len(browser_opens) == before_ig_reuse_opens
        assert jobs[-1]["params"] == {"platform": "instagram", "method": "auto"}
        assert jobs[-1]["result"]["source"] == "firefox" and jobs[-1]["result"]["verified"] is True
        modal.get_by_role("button", name="关闭对话框").click()
        print("PASS Instagram automatically reuses the default Firefox session without a separate profile prompt or login")

        state["ig_session"] = True
        launch_collection()
        modal = page.get_by_role("dialog")
        modal.locator("[name=source]").select_option("instagram")
        expect(modal.get_by_role("button", name="开始资料采集")).to_be_enabled()
        modal.locator("[name=mode]").select_option("hashtag")
        modal.locator("[name=keyword]").fill("#平和")
        modal.locator("[name=max_records]").fill("12")
        modal.locator("[name=max_accounts]").fill("2")
        modal.locator("[name=max_scrolls]").fill("3")
        modal.locator("[name=relevance]").select_option("broad")
        modal.locator("[name=include_following]").check()
        expect(modal.locator("[name=max_following]")).to_be_visible()
        expect(modal.get_by_text("本次最多关注关系", exact=False)).to_be_visible()
        expect(modal.get_by_text("最多保存条数", exact=False)).to_be_visible()
        modal.locator("[name=max_following]").fill("4")
        modal.get_by_role("button", name="开始资料采集").click()
        expect(page.get_by_role("dialog")).to_contain_text("Instagram · 平和")
        expect(page.get_by_role("dialog")).to_contain_text("补全 2")
        expect(page.get_by_role("dialog")).to_contain_text("关系补全 3")
        params = jobs[-1]["params"]
        assert all(params[key] == value for key, value in {"source": "instagram", "mode": "hashtag", "keyword": "平和", "relevance": "broad", "include_following": True, "max_following": 4, "max_records": 12, "max_accounts": 2, "max_scrolls": 3}.items())
        assert "handle" not in params and "feed_url" not in params
        print("PASS complete social form payload and hidden-field exclusion")

        page.get_by_role("button", name="关闭对话框").click()
        page.locator("#navigation a[data-route=accounts]").click()
        expect(page.locator(".account-card")).to_have_count(30)
        expect(page.locator(".pagination")).to_contain_text("共 61 条")
        assert page.locator(".account-stats strong").all_text_contents() == ["60", "1", "0"]
        page.get_by_role("button", name="下一页", exact=True).click()
        expect(page.locator(".pagination")).to_contain_text("2 / 3")
        expect(page.get_by_role("heading", name="Archive organization030", exact=True)).to_be_visible()
        page.locator("[name=search]").fill("archive_060")
        expect(page.locator(".account-card")).to_have_count(1)
        expect(page.get_by_role("heading", name="Archive organization060", exact=True)).to_be_visible()
        expect(page.locator(".pagination")).to_contain_text("1 / 1")
        page.locator("[name=search]").fill("")
        expect(page.locator(".pagination")).to_contain_text("1 / 3")
        page.get_by_role("button", name="下一页", exact=True).click()
        page.get_by_role("button", name="下一页", exact=True).click()
        expect(page.locator(".account-card")).to_have_count(1)
        expect(page.locator(".pagination")).to_contain_text("3 / 3")
        page.locator("[name=status]").select_option("candidate")
        expect(page.locator(".pagination")).to_contain_text("1 / 2")
        expect(page.locator(".pagination")).to_contain_text("共 60 条")
        assert page.locator(".account-stats strong").all_text_contents() == ["60", "1", "0"]
        page.locator("[name=status]").select_option("")
        print("PASS 61-account pagination, full-data search and filter reset")
        page.get_by_role("button", name="采集", exact=True).first.click()
        modal = page.get_by_role("dialog")
        expect(modal.locator("[name=source]")).to_have_value("twitter")
        expect(modal.locator("[name=mode]")).to_have_value("account")
        expect(modal.locator("[name=handle]")).to_have_value("public_org")
        expect(modal.locator("[name=keyword]")).to_be_hidden()
        modal.locator("[name=source]").select_option("rss")
        expect(modal.locator("[name=feed_url]")).to_be_visible()
        expect(modal.locator("[name=handle]")).to_be_disabled()
        expect(modal.locator("[name=max_accounts]")).to_be_disabled()
        expect(modal.locator("[name=include_following]")).to_be_disabled()
        page.get_by_role("button", name="关闭对话框").click()
        page.locator("#navigation a[data-route=accounts]").click()
        page.get_by_role("button", name="关系", exact=True).first.click()
        expect(page.get_by_role("dialog")).to_contain_text("Another Public Organization")
        expect(page.get_by_role("dialog")).to_contain_text("首次观察 2026/05/18")
        expect(page.get_by_role("dialog")).to_contain_text("最近观察 2026/10/06")
        expect(page.get_by_role("dialog")).to_contain_text("旧库未保存关系证据链接")
        expect(page.get_by_role("dialog")).to_contain_text("观察时间未保存")
        expect(page.get_by_role("dialog")).not_to_contain_text("观察于 日期未知")
        expect(page.get_by_role("link", name="查看关系证据")).to_have_attribute("href", "https://example.org/evidence")
        print("PASS account shortcut, source switching and relation evidence")

        page.get_by_role("button", name="关闭对话框").click()
        page.locator("#navigation a[data-route=library]").click()
        page.get_by_role("button", name="查看资料：公开资料测试").click()
        expect(page.get_by_role("dialog")).to_contain_text("图片说明 / 线索")
        expect(page.get_by_role("dialog")).to_contain_text("发布时间未知")
        expect(page.get_by_role("dialog")).to_contain_text("历史库未保留可靠互动指标")
        expect(page.get_by_role("dialog")).not_to_contain_text("点赞 0")
        page.get_by_role("button", name="关闭对话框").click()
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator("#navigation a[data-route=settings]").click()
        expect(page.get_by_role("heading", name="社媒来源与登录")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "narrow settings horizontally overflow"
        launch_collection()
        expect(page.get_by_role("dialog")).to_be_visible()
        assert page.get_by_role("dialog").evaluate("el => el.scrollWidth <= el.clientWidth"), "narrow collection form horizontally overflows"
        assert not errors, errors
        browser.close()
        print("PASS provenance display, 390px layout, no browser errors")
        print("9 offline browser integration groups passed")


if __name__ == "__main__":
    main()
