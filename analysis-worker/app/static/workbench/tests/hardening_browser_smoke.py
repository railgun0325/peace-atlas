"""Offline acceptance for exports, restore help and interrupted UI actions.

Run from the repository root. Every request is intercepted and only synthetic
fixtures are used; no local service, database, model or browser session is read.
Optional --artifacts writes screenshots of the tested UI to the supplied folder.
"""
import argparse
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://hardening.test"
REPORT_MARKDOWN = """# 隔离报告

| 账号 | 关系与证据 | 状态 |
| :--- | :---: | ---: |
| **公开组织** | 提及\\|共同活动 [S001](<https://example.org/item(1)>) | 待核验 |
| <img src=x onerror=bad> | [bad](javascript:bad) | 候选 |

表格后说明：公开线索需要进一步核对。
"""


def main(artifacts=None):
    if artifacts:
        artifacts.mkdir(parents=True, exist_ok=True)
    state = {"fail_lists": set(), "download_error": False, "backup_available": True,
             "held_job_retry": [], "held_batch_cancel": [], "hold_job_retry": False,
             "hold_batch_cancel": False, "reads": [], "job_retry_calls": 0,
             "batch_cancel_calls": 0, "unexpected": []}
    errors = []

    def job(index, status="succeeded"):
        return {"id": f"job-{index}", "kind": "collect", "params": {"source": "google_news", "keyword": f"隔离主题 {index}"}, "status": status, "progress": 0 if status == "running" else 100,
                "created_at": "2026-10-08T00:00:00Z", "events": [], "result": None}

    jobs = [job(index) for index in range(21)]
    jobs[0] = job(0, "failed")
    jobs[1] = job(1, "running")
    batches = [{"id": f"batch-{index}", "title": f"隔离任务串 {index}", "status": "succeeded", "task_count": 1, "counts": {"succeeded": 1}, "progress": 100, "total_records": 5, "records_saved": 1, "jobs": [jobs[2]]} for index in range(11)]
    batches[0].update(status="running", task_count=2, counts={"running": 1, "failed": 1}, active_count=1, retryable_count=1, succeeded_count=0, completed_count=1, jobs=jobs[:2])
    items = [{"id": f"item-{index}", "title": f"隔离资料 {index}", "type": "news", "source_name": "Fixture", "content": "合成材料", "published_at": None} for index in range(26)]
    reports = [{"id": f"report-{index}", "title": f"隔离报告 {index}", "mode": "evidence", "source_count": 2, "markdown": REPORT_MARKDOWN, "snapshot": {"sources": []}} for index in range(21)]
    runs = [{"id": f"run-{index}", "job_id": "job-0", "status": "succeeded", "params": {"keyword": f"隔离研究 {index}", "depth": 2, "start_date": "2026-10-01", "end_date": "2026-10-08"}, "summary": {}, "analysis": {}, "diagnostics": []} for index in range(26)]
    accounts = [{"id": f"account-{index}", "name": f"Archive organization {index:02d}", "handle": f"archive_{index}", "platform": "twitter", "status": "verified", "organization": "隔离公开组织", "url": "https://example.org/account", "tags": []} for index in range(61)]

    def fulfill(route, data, status=200):
        route.fulfill(status=status, content_type="application/json", body=json.dumps(data, ensure_ascii=False))

    def paged(rows, query, default):
        page = int(query.get("page", [1])[0])
        size = int(query.get("page_size", [default])[0])
        return {"items": rows[(page - 1) * size:page * size], "total": len(rows), "page": page, "page_size": size}

    def route(request):
        parsed = urlsplit(request.request.url)
        path, method, query = parsed.path, request.request.method, parse_qs(parsed.query)
        if parsed.netloc != "hardening.test":
            state["unexpected"].append(request.request.url)
            return request.abort()
        if path == "/":
            return request.fulfill(content_type="text/html", body=(ROOT / "index.html").read_text(encoding="utf-8"))
        if path.startswith("/assets/"):
            file = ROOT / path.removeprefix("/assets/")
            assert file.resolve().is_relative_to(ROOT)
            return request.fulfill(content_type={".css": "text/css", ".woff2": "font/woff2"}.get(file.suffix, "text/javascript"), body=file.read_bytes())
        state["reads"].append((method, path, query))
        if path in {"/api/v2/" + name for name in state["fail_lists"]}:
            return fulfill(request, {"error": {"message": "隔离服务暂时不可用"}}, 503)
        if path == "/api/v2/health":
            data = {"ok": True, "status": "ok"}
        elif path == "/api/v2/capabilities":
            data = {"collectors": [], "llm": {"configured": False}, "storage": {"engine": "sqlite"}, "backup": {"available": state["backup_available"], "detail": "可保存隔离研究快照" if state["backup_available"] else "当前数据库不支持备份"}}
        elif path == "/api/v2/social":
            data = {"runtime": {"ready": False}, "platforms": []}
        elif path == "/api/v2/accounts":
            data = {"items": accounts, "total": len(accounts)}
        elif path.startswith("/api/v2/accounts/") and method == "PATCH":
            account = next(value for value in accounts if value["id"] == path.split("/")[4])
            account.update(request.request.post_data_json)
            data = account
        elif path in {"/api/v2/items", "/api/v2/reports", "/api/v2/research", "/api/v2/jobs", "/api/v2/job-batches"}:
            rows, size = {"items": (items, 25), "reports": (reports, 20), "research": (runs, 25), "jobs": (jobs, 20), "job-batches": (batches, 10)}[path.rsplit("/", 1)[1]]
            data = paged(rows, query, size)
        elif path == "/api/v2/backup" or path.endswith(("/download", "/report", "/export")):
            if state["download_error"]:
                return fulfill(request, {"error": {"message": "导出暂时不可用，请稍后重试。"}}, 503)
            if path == "/api/v2/backup":
                filename, body, mime = "peace-atlas-fixture.sqlite3", b"OFFLINE-FIXTURE-NOT-A-DATABASE", "application/octet-stream"
            elif path.endswith("/export"):
                filename, body, mime = "peace-atlas-fixture.json", b'{"fixture":true,"nodes":[],"edges":[]}', "application/json"
            else:
                filename, body, mime = "peace-atlas-fixture.md", REPORT_MARKDOWN.encode(), "text/markdown"
            return request.fulfill(headers={"Content-Disposition": f'attachment; filename="{filename}"'}, content_type=mime, body=body)
        elif path.startswith("/api/v2/reports/"):
            data = next(value for value in reports if value["id"] == path.split("/")[4])
        elif path.startswith("/api/v2/research/"):
            run = next(value for value in runs if value["id"] == path.split("/")[4])
            data = {"run": run, "nodes": [], "edges": [], "items": [], "claims": [], "coverage": "隔离测试"}
        elif path == "/api/v2/jobs/job-0/retry" and method == "POST":
            state["job_retry_calls"] += 1
            if state["hold_job_retry"]:
                state["held_job_retry"].append(request)
                return
            data = job(99, "queued")
            jobs.insert(0, data)
        elif path.startswith("/api/v2/jobs/"):
            data = next(value for value in jobs if value["id"] == path.split("/")[4])
        elif path == "/api/v2/job-batches/batch-0/cancel" and method == "POST":
            state["batch_cancel_calls"] += 1
            if state["hold_batch_cancel"]:
                state["held_batch_cancel"].append(request)
                return
            data = batches[0]
        elif path.startswith("/api/v2/job-batches/"):
            data = next(value for value in batches if value["id"] == path.split("/")[4])
        else:
            state["unexpected"].append(f"{method} {path}")
            return fulfill(request, {"error": {"message": "Unexpected offline request"}}, 404)
        fulfill(request, data)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
        page.add_init_script("""window.__polls=[];const nativeTimeout=window.setTimeout.bind(window);
          window.setTimeout=(task,delay,...args)=>{if(delay===5000){window.__polls.push(()=>task(...args));return nativeTimeout(()=>{},300000);}return nativeTimeout(task,delay,...args);};
          window.__runPolls=()=>Promise.all(window.__polls.splice(0).map(task=>task()));
          window.__holdDialogClose=false;window.__heldDialogClose=[];
          const nativeListener=HTMLDialogElement.prototype.addEventListener;
          HTMLDialogElement.prototype.addEventListener=function(type,listener,options){
            if(type==='close') {const dialog=this;return nativeListener.call(this,type,event=>{
              if(window.__holdDialogClose)window.__heldDialogClose.push(()=>listener.call(dialog,event));
              else listener.call(dialog,event);
            },options);}return nativeListener.call(this,type,listener,options);};
          window.__releaseDialogClose=()=>{window.__holdDialogClose=false;window.__heldDialogClose.splice(0).forEach(task=>task());};""")
        page.route("**/*", route)
        page.on("pageerror", lambda error: errors.append(str(error)))

        for route_name, selector, title, expected_last in [("library", ".item-row", "资料库", 2), ("reports", ".report-card", "研究报告", 2), ("network", ".research-run-card", "研究工作台", 2)]:
            page.goto(ORIGIN + f"/#{route_name}?page=999")
            expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
            expect(page.locator(selector)).to_have_count(1)
            expect(page).to_have_url(ORIGIN + f"/#{route_name}?page={expected_last}")
        page.goto(ORIGIN + "/#jobs?page=999&batch_page=999")
        expect(page.locator(".job-row")).to_have_count(1)
        expect(page.locator(".batch-card")).to_have_count(1)
        expect(page).to_have_url(ORIGIN + "/#jobs?page=2&batch_page=2")
        print("PASS obsolete URLs recover library, reports, research, jobs and batches directly to the last page")

        for resource, route_name in [("items", "library"), ("reports", "reports"), ("jobs", "jobs")]:
            state["fail_lists"].add(resource)
            page.goto(ORIGIN + "/#" + route_name)
            expect(page.locator(".page-failure")).to_contain_text("隔离服务暂时不可用")
            expect(page.locator(".page-failure")).to_contain_text("不能据此判断资料库为空")
            state["fail_lists"].clear()
            page.get_by_role("button", name="重试", exact=True).click()
            expect(page.locator(".page-failure")).to_have_count(0)
        print("PASS failed lists remain visible errors and recover through retry")

        page.goto(ORIGIN + "/#accounts?query=Archive&status=verified&page=2")
        expect(page.locator(".account-card")).to_have_count(30)
        expect(page.locator(".account-card").first).to_contain_text("organization 30")
        saved_url = page.url
        page.locator(".account-card").first.get_by_role("button", name="编辑", exact=True).click()
        modal = page.get_by_role("dialog")
        modal.locator("[name=name]").fill("Archive organization 30 edited")
        modal.get_by_role("button", name="保存修改", exact=True).click()
        expect(modal).to_have_count(0)
        expect(page.locator(".account-card").first).to_contain_text("organization 30 edited")
        assert page.url == saved_url
        page.reload()
        expect(page.locator("[name=search]")).to_have_value("Archive")
        expect(page.locator("[name=status]")).to_have_value("verified")
        expect(page.locator(".account-card").first).to_contain_text("organization 30 edited")
        print("PASS account search, verification filter and page survive editing and reload")

        page.goto(ORIGIN + "/#reports?selected=report-0")
        modal = page.get_by_role("dialog")
        expect(modal.locator(".prose table")).to_have_count(1)
        expect(modal.locator(".prose th")).to_have_count(3)
        expect(modal.locator(".prose tbody tr")).to_have_count(2)
        expect(modal.locator(".prose td").nth(1)).to_contain_text("提及|共同活动")
        expect(modal.locator(".prose img, .prose script")).to_have_count(0)
        expect(modal.locator(".prose a")).to_have_count(1)
        expect(modal.locator(".prose a")).to_have_attribute("href", "https://example.org/item(1)")
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
        if artifacts:
            page.screenshot(path=str(artifacts / "hardening-report-table-desktop.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        assert modal.evaluate("node=>node.scrollWidth<=node.clientWidth+1")
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
        if artifacts:
            page.screenshot(path=str(artifacts / "hardening-report-table-390.png"))
        with page.expect_download() as event:
            modal.get_by_role("button", name="下载 Markdown", exact=True).click()
        downloaded = event.value
        assert downloaded.suggested_filename == "peace-atlas-fixture.md"
        assert Path(downloaded.path()).read_text(encoding="utf-8") == REPORT_MARKDOWN
        expect(page.get_by_role("button", name="下载 Markdown", exact=True)).to_be_enabled()
        page.set_viewport_size({"width": 1440, "height": 1000})
        modal.get_by_role("button", name="关闭对话框", exact=True).click()
        print("PASS real report reader renders safe tables with escaped pipes, 390px containment and Markdown download")

        state["download_error"] = True
        page.locator(".report-card").first.get_by_role("button", name="下载", exact=True).click()
        expect(page.locator("#notifications .notice.error").last).to_contain_text("导出暂时不可用")
        expect(page.locator(".report-card").first.get_by_role("button", name="下载", exact=True)).to_be_enabled()
        state["download_error"] = False
        with page.expect_download() as event:
            page.locator(".report-card").first.get_by_role("button", name="下载", exact=True).click()
        assert event.value.suggested_filename.endswith(".md")
        page.goto(ORIGIN + "/#network?run=run-0")
        for name, suffix in [("下载研究摘要", ".md"), ("导出关系与证据", ".json")]:
            with page.expect_download() as event:
                page.get_by_role("button", name=name, exact=True).click()
            assert event.value.suggested_filename.endswith(suffix)
        print("PASS report-card errors re-enable download and research summary/relations export as attachments")

        state["backup_available"] = False
        page.goto(ORIGIN + "/#settings")
        backup = page.get_by_role("button", name="备份研究数据", exact=True)
        expect(backup).to_be_disabled()
        expect(page.locator('section[aria-label="研究数据备份"]')).to_contain_text("当前数据库不支持备份")
        assert not any(path == "/api/v2/backup" for _, path, _ in state["reads"])
        state["backup_available"] = True
        page.reload()
        expect(backup).to_be_enabled()
        page.get_by_text("如何恢复备份", exact=True).click()
        restore = page.locator('section[aria-label="研究数据备份"] details')
        expect(restore.locator("li")).to_have_count(5)
        expect(restore).to_contain_text("workbench.sqlite3-wal")
        expect(restore).to_contain_text("正在运行的任务会标记为中断")
        with page.expect_download() as event:
            backup.click()
        assert event.value.suggested_filename == "peace-atlas-fixture.sqlite3"
        assert Path(event.value.path()).read_bytes() == b"OFFLINE-FIXTURE-NOT-A-DATABASE"
        state["download_error"] = True
        backup.click()
        expect(page.locator("#notifications .notice.error").last).to_contain_text("导出暂时不可用")
        expect(backup).to_be_enabled()
        state["download_error"] = False
        if artifacts:
            page.screenshot(path=str(artifacts / "hardening-backup-restore-help.png"), full_page=True)
        print("PASS unavailable backups stay disabled, available snapshots download, failures recover and restore help has five steps")

        state["hold_job_retry"] = True
        page.goto(ORIGIN + "/#jobs?selected=job-0")
        modal = page.get_by_role("dialog")
        modal.get_by_role("button", name="创建重试任务", exact=True).click()
        expect(modal.get_by_role("button", name="创建重试任务", exact=True)).to_be_disabled()
        page.wait_for_function("window.__polls.length>0")
        page.evaluate("window.__runPolls()")
        expect(modal.get_by_role("button", name="创建重试任务", exact=True)).to_be_disabled()
        assert state["job_retry_calls"] == 1 and state["held_job_retry"]
        # Hold the browser's queued close listeners to exercise the exact gap
        # where the drawer is dismissed but an HTTP acknowledgement can arrive.
        page.evaluate("window.__holdDialogClose=true")
        modal.get_by_role("button", name="关闭对话框", exact=True).click()
        page.wait_for_function("window.__heldDialogClose.length>0")
        assert page.evaluate("document.querySelector('dialog[open]')===null")
        retry_job = job(99, "queued")
        jobs.insert(0, retry_job)
        for held in state["held_job_retry"]:
            fulfill(held, retry_job)
        state["held_job_retry"] = []
        state["hold_job_retry"] = False
        expect(page.locator(".job-row").first).to_contain_text("隔离主题 99")
        expect(page.get_by_role("dialog")).to_have_count(0)
        page.evaluate("window.__releaseDialogClose()")
        expect(page.get_by_role("dialog")).to_have_count(0)
        print("PASS single-job retry stays disabled through polling and late acknowledgement cannot reopen a dismissed drawer")

        state["hold_batch_cancel"] = True
        page.goto(ORIGIN + "/#jobs?batch=batch-0")
        selected = page.locator(".batch-detail")
        selected.get_by_role("button", name="取消未完成任务", exact=True).click()
        page.wait_for_function("window.__polls.length>0")
        page.evaluate("window.__runPolls()")
        expect(selected.get_by_role("button", name="取消未完成任务", exact=True)).to_be_disabled()
        expect(selected.get_by_role("button", name="重试未完成任务", exact=True)).to_be_disabled()
        selected.get_by_role("button", name="取消未完成任务", exact=True).evaluate("node=>node.click()")
        assert state["batch_cancel_calls"] == 1 and state["held_batch_cancel"]
        for held in state["held_batch_cancel"]:
            fulfill(held, {"error": {"message": "取消暂时不可用"}}, 503)
        state["held_batch_cancel"] = []
        state["hold_batch_cancel"] = False
        expect(selected).to_contain_text("取消暂时不可用")
        expect(selected.get_by_role("button", name="取消未完成任务", exact=True)).to_be_enabled()
        expect(selected.get_by_role("button", name="重试未完成任务", exact=True)).to_be_enabled()
        assert not errors, errors
        assert not state["unexpected"], state["unexpected"]
        print("PASS batch actions remain disabled across polling, suppress duplicate requests and recover from failure")
        print("8 hardening browser acceptance groups passed; all requests intercepted; no real session or service accessed")
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path)
    args = parser.parse_args()
    main(args.artifacts)
