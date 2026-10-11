"""Real router regression checks for later input and late request ownership.

Every request is intercepted; only synthetic fixtures and a fresh Chromium
profile are used. No database, credential or production browser is accessed.
"""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
KEY = "peace-atlas.collection-draft.v1"
ORIGIN = "http://daily-regression.test"


def main(artifacts=None):
    if artifacts:
        artifacts.mkdir(parents=True, exist_ok=True)
    state = {"draft": None, "revision": 0, "jobs": [], "held_jobs": [], "held_reads": [], "held_clears": [], "requests": [], "hold_read": False, "hold_clear": False}
    errors, results = [], []

    def fulfill(route, data, status=200):
        route.fulfill(status=status, content_type="application/json", body=json.dumps(data, ensure_ascii=False))

    def clear(route, body):
        if (body or {}).get("revision", 0) != state["revision"]:
            return fulfill(route, {"error": {"code": "draft_conflict", "message": "另一个窗口已更新草稿"}}, 409)
        state["draft"] = None
        state["revision"] = 0
        fulfill(route, {"cleared": True})

    def route(request):
        parsed = urlsplit(request.request.url)
        path, method = parsed.path, request.request.method
        assert parsed.netloc == "daily-regression.test", request.request.url
        if path == "/":
            return request.fulfill(content_type="text/html", body=(ROOT / "index.html").read_text(encoding="utf-8"))
        if path.startswith("/assets/"):
            file = ROOT / path.removeprefix("/assets/")
            assert file.resolve().is_relative_to(ROOT)
            return request.fulfill(content_type={".css": "text/css", ".woff2": "font/woff2"}.get(file.suffix, "text/javascript"), body=file.read_bytes())
        body = request.request.post_data_json if request.request.post_data else None
        state["requests"].append({"path": path, "method": method, "body": body})
        if path == "/api/v2/health":
            data = {"ok": True, "status": "ok"}
        elif path == "/api/v2/capabilities":
            data = {"llm": {"configured": False}, "collectors": []}
        elif path == "/api/v2/social":
            data = {"runtime": {"ready": False}, "platforms": []}
        elif path == "/api/v2/drafts/collection":
            if method == "GET" and state["hold_read"]:
                state["held_reads"].append(request)
                return
            if method == "PUT":
                if body.get("revision", 0) != state["revision"]:
                    return fulfill(request, {"error": {"code": "draft_conflict", "message": "另一个窗口已更新草稿"}}, 409)
                state["revision"] += 1
                state["draft"] = {**body, "revision": state["revision"]}
            elif method == "DELETE":
                if state["hold_clear"]:
                    state["held_clears"].append((request, body))
                    return
                return clear(request, body)
            data = {"draft": state["draft"]}
        elif path == "/api/v2/jobs" and method == "POST":
            state["held_jobs"].append((request, body))
            return
        elif path == "/api/v2/jobs":
            data = {"items": state["jobs"], "total": len(state["jobs"]), "page": 1, "page_size": 20}
        elif path.startswith("/api/v2/jobs/"):
            data = next(job for job in state["jobs"] if job["id"] == path.split("/")[4])
        elif path in {"/api/v2/job-batches", "/api/v2/research", "/api/v2/items"}:
            data = {"items": [], "total": 0, "page": 1, "page_size": 25}
        elif path == "/api/v2/research/plan":
            data = {"queries": [], "suggestions": [], "ai": {"used": False}}
        else:
            raise AssertionError(f"Unexpected fixture request: {method} {path}")
        fulfill(request, data)

    def accepted():
        held, payload = state["held_jobs"].pop(0)
        job = {**payload, "id": f"synthetic-{len(state['jobs'])}", "status": "queued", "progress": 0, "events": []}
        state["jobs"].append(job)
        fulfill(held, job)
        return job

    def record(page, name):
        results.append(name)
        print("PASS " + name)
        if artifacts:
            page.screenshot(path=str(artifacts / (f"daily-pass-{len(results):02d}.png")))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.route("**/*", route)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(ORIGIN + "/#network?compose=collect")
        modal = page.get_by_role("dialog")
        keyword = modal.locator("[name=keyword]")
        expect(modal).to_be_visible()
        keyword.fill("submitted theme A")
        expect(modal.locator(".draft-status")).to_contain_text("草稿已保存")
        modal.get_by_role("button", name="开始资料采集", exact=True).click()
        page.wait_for_function("!!document.querySelector('form[aria-busy=true]')")
        assert state["held_jobs"]
        expect(keyword).to_be_enabled()
        keyword.fill("unsent next theme B")
        expect(modal.locator(".draft-status")).to_contain_text("草稿已保存")
        first = accepted()
        expect(modal.locator(".accepted-task")).to_contain_text("当前输入仍保留为草稿")
        expect(keyword).to_have_value("unsent next theme B")
        assert page.url == ORIGIN + "/#network"
        assert page.evaluate("key=>JSON.parse(localStorage.getItem(key)).values.keyword", KEY) == "unsent next theme B"
        assert state["draft"]["values"]["keyword"] == "unsent next theme B"
        assert first["params"]["keyword"] == "submitted theme A"
        record(page, "accepted A preserves later B in the live editable composer, local storage and server")

        modal.get_by_role("button", name="查看已创建任务", exact=True).click()
        expect(page).to_have_url(ORIGIN + "/#jobs?selected=" + first["id"])
        page.get_by_role("button", name="关闭对话框", exact=True).click()
        page.goto(ORIGIN + "/#network?compose=collect")
        expect(keyword).to_have_value("unsent next theme B")
        modal.get_by_role("button", name="开始资料采集", exact=True).click()
        page.wait_for_function("!!document.querySelector('form[aria-busy=true]')")
        second = accepted()
        expect(page).to_have_url(ORIGIN + "/#jobs?selected=" + second["id"])
        assert state["draft"] is None
        assert page.evaluate("key=>localStorage.getItem(key)", KEY) is None
        record(page, "unchanged accepted input still clears its own draft and navigates to diagnostics")

        page.goto(ORIGIN + "/#network?compose=collect")
        keyword.fill("close-origin A")
        expect(modal.locator(".draft-status")).to_contain_text("草稿已保存")
        modal.get_by_role("button", name="开始资料采集", exact=True).click()
        page.wait_for_function("!!document.querySelector('form[aria-busy=true]')")
        modal.get_by_role("button", name="关闭对话框", exact=True).click()
        later = {"schema": 1, "values": {"keyword": "another window B", "source": "google_news"}, "modeBySource": {}, "updated_at": "2099-01-01T00:00:00Z", "revision": state["revision"] + 1}
        state["draft"], state["revision"] = later, later["revision"]
        page.evaluate("([key,draft])=>localStorage.setItem(key,JSON.stringify(draft))", [KEY, later])
        page.goto(ORIGIN + "/#library")
        page.locator(".page-heading").get_by_role("button", name="导入资料", exact=True).click()
        modal.locator("[name=json]").fill('[{"title":"new import window"}]')
        accepted()
        expect(page.locator("#notifications")).to_contain_text("采集任务已进入队列")
        expect(modal).to_have_attribute("aria-label", "导入公开资料")
        expect(modal.locator("[name=json]")).to_have_value('[{"title":"new import window"}]')
        assert page.url == ORIGIN + "/#library"
        assert state["draft"]["values"]["keyword"] == "another window B"
        assert page.evaluate("key=>JSON.parse(localStorage.getItem(key)).values.keyword", KEY) == "another window B"
        record(page, "late acceptance after close preserves another window's draft and current route/modal")

        page.evaluate("""() => { const native=File.prototype.text; window.__fileReads={}; File.prototype.text=function(){if(this.name.startsWith('held-'))return new Promise((resolve,reject)=>window.__fileReads[this.name]={resolve,reject});return native.call(this);}; }""")
        upload = modal.locator("input[type=file]")
        text = modal.locator("[name=json]")
        upload.set_input_files({"name": "held-A.json", "mimeType": "application/json", "buffer": b'[]'})
        page.wait_for_function("!!window.__fileReads['held-A.json']")
        text.fill('[{"title":"manual B"}]')
        page.evaluate("window.__fileReads['held-A.json'].resolve('[{\"title\":\"old A\"}]')")
        expect(text).to_have_value('[{"title":"manual B"}]')
        upload.set_input_files({"name": "held-old.json", "mimeType": "application/json", "buffer": b'[]'})
        upload.set_input_files({"name": "held-new.json", "mimeType": "application/json", "buffer": b'[]'})
        page.evaluate("window.__fileReads['held-new.json'].resolve('[{\"title\":\"new file\"}]')")
        expect(text).to_have_value('[{"title":"new file"}]')
        page.evaluate("window.__fileReads['held-old.json'].resolve('[{\"title\":\"old file\"}]')")
        expect(text).to_have_value('[{"title":"new file"}]')
        upload.set_input_files({"name": "held-error.json", "mimeType": "application/json", "buffer": b'[]'})
        text.fill('[{"title":"manual after error"}]')
        page.evaluate("window.__fileReads['held-error.json'].reject(new Error('obsolete read failure'))")
        expect(modal.locator(".form-error")).to_be_empty()
        record(page, "late file results and errors cannot overwrite manual input or a newer selection")
        modal.get_by_role("button", name="关闭对话框", exact=True).click()

        stale = {"schema": 1, "values": {"keyword": "stale local A", "workflow": "single", "batch_kind": "collect", "source": "google_news"}, "modeBySource": {}, "updated_at": "2026-10-08T00:00:00Z", "revision": 10}
        remote = {**stale, "values": {**stale["values"], "keyword": "another window B"}, "updated_at": "2026-10-08T00:01:00Z", "revision": 11}
        state["revision"], state["draft"], state["hold_read"] = 11, remote, True
        page.evaluate("""() => {window.__draftTimers=[];const native=window.setTimeout.bind(window);window.setTimeout=(task,delay,...args)=>delay===350?(window.__draftTimers.push(()=>task(...args)),990001):native(task,delay,...args);window.__flushDraftTimers=()=>window.__draftTimers.splice(0).forEach(task=>task());}""")
        page.evaluate("([key,draft])=>localStorage.setItem(key,JSON.stringify(draft))", [KEY, stale])
        page.goto(ORIGIN + "/#network?compose=collect")
        expect(keyword).to_have_value("stale local A")
        keyword.fill("new local C")
        page.wait_for_function("window.__draftTimers.length>0")
        for held in state["held_reads"]:
            fulfill(held, {"draft": remote})
        state["held_reads"], state["hold_read"] = [], False
        expect(modal.locator(".draft-status")).to_contain_text("另一个窗口")
        page.evaluate("window.__flushDraftTimers()")
        expect(keyword).to_have_value("new local C")
        assert state["draft"]["values"]["keyword"] == "another window B"
        modal.get_by_role("button", name="保留当前输入并重新同步", exact=True).click()
        expect(modal.locator(".draft-status")).to_contain_text("草稿已保存")
        assert state["draft"]["values"]["keyword"] == "new local C"
        record(page, "late initial GET preserves local C and remote B until explicit conflict resync")

        state["hold_clear"] = True
        modal.get_by_role("button", name="开始资料采集", exact=True).click()
        page.wait_for_function("!!document.querySelector('form[aria-busy=true]')")
        accepted()
        page.wait_for_function("!!document.querySelector('form[aria-busy=true]')")
        for _ in range(20):
            if state["held_clears"]:
                break
            page.wait_for_timeout(20)
        assert state["held_clears"]
        keyword.fill("new edit during DELETE")
        for held, body in state["held_clears"]:
            clear(held, body)
        state["held_clears"], state["hold_clear"] = [], False
        expect(modal.locator(".accepted-task")).to_contain_text("当前输入仍保留为草稿")
        page.evaluate("window.__flushDraftTimers()")
        expect(modal.locator(".draft-status")).to_contain_text("草稿已保存")
        expect(keyword).to_have_value("new edit during DELETE")
        assert state["draft"]["values"]["keyword"] == "new edit during DELETE"
        assert page.evaluate("key=>JSON.parse(localStorage.getItem(key)).values.keyword", KEY) == "new edit during DELETE"
        record(page, "input arriving during server DELETE persists locally and synchronizes after clear")
        assert not errors, errors
        browser.close()
    if artifacts:
        (artifacts / "daily-browser-results.json").write_text(json.dumps({"passed": results, "requests": state["requests"], "pageerrors": errors}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(results)} daily state ownership regression groups passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path)
    main(parser.parse_args().artifacts)
