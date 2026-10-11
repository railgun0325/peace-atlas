"""Offline task-series UI acceptance using the actual task-center module.

All API calls are intercepted. No platform, model, credentials, user database
or installed application is accessed.
"""
import copy
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def main():
    def job(index, status="succeeded"):
        return {"id": f"queue-{index}", "kind": "research", "params": {"source": "google_news", "keyword": f"反戦 {index}", "max_records": 200, "start_date": "2026-09-01", "end_date": "2026-10-06"}, "status": status, "progress": 100 if status in {"succeeded", "partial", "failed", "cancelled"} else 30 if status == "running" else 0, "created_at": "2026-10-06T00:00:00Z", "result": {"inserted": 5, "run_id": "run-evidence"} if status == "succeeded" else None, "error": None, "events": [], "cancel_requested": False}

    queue = [job(index) for index in range(125)]
    children = [job(200, "succeeded"), job(201, "failed"), job(202, "running"), job(203, "queued")]
    batches = [{"id": f"series-{index}", "title": f"公开材料任务串 {index}", "status": "succeeded", "task_count": 1, "counts": {"succeeded": 1}, "progress": 100, "total_records": 200, "records_saved": 5, "jobs": [job(300 + index)]} for index in range(23)]
    batches[0] = {"id": "series-0", "title": "真实状态测试任务串", "status": "running", "task_count": 4, "counts": {"succeeded": 1, "failed": 1, "running": 1, "queued": 1}, "completed_count": 2, "active_count": 2, "retryable_count": 1, "succeeded_count": 1, "progress": 58, "total_records": 1000, "records_saved": 5, "target_reached": False, "jobs": children}
    state = {"cancel_calls": 0, "retry_calls": 0, "job_retry_calls": 0, "draft": None}
    errors = []
    html = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><link rel="stylesheet" href="/assets/styles.css"><link rel="stylesheet" href="/assets/typography.css"></head><body><main id="main"></main><div id="notifications"></div>
      <script type="module">import {renderJobs} from '/assets/research.js';
      window.__polls=[]; window.__pollDelays=[]; window.__navigation=[]; let controller;
      window.renderTest=async()=>{controller?.abort(); controller=new AbortController();window.__polls=[];
        const params=new URLSearchParams(location.hash.split('?')[1]||'');
        const ctx={params,signal:controller.signal,refresh:()=>window.renderTest(),navigate:(route,values={})=>{window.__navigation.push([route,values]);location.hash='#'+route+'?'+new URLSearchParams(values);if(route==='jobs')window.renderTest();},poll:(task,delay)=>{window.__polls.push(task);window.__pollDelays.push(delay);}};
        const content=await renderJobs(ctx); if(!controller.signal.aborted) document.getElementById('main').replaceChildren(content);};window.renderTest();</script></body></html>"""

    def route(request):
        parsed = urlsplit(request.request.url)
        path = parsed.path
        query = parse_qs(parsed.query)
        method = request.request.method
        if path == "/":
            return request.fulfill(content_type="text/html", body=html)
        if path.startswith("/assets/"):
            file = ROOT / path.removeprefix("/assets/")
            assert file.resolve().is_relative_to(ROOT)
            return request.fulfill(content_type={".css": "text/css", ".woff2": "font/woff2", ".json": "application/json", ".txt": "text/plain"}.get(file.suffix, "text/javascript"), body=file.read_bytes())
        if path == "/api/v2/jobs":
            page, size = int(query.get("page", [1])[0]), int(query.get("page_size", [20])[0])
            data = {"items": queue[(page - 1) * size:page * size], "total": len(queue), "page": page, "page_size": size}
        elif path == "/api/v2/job-batches":
            page, size = int(query.get("page", [1])[0]), int(query.get("page_size", [10])[0])
            data = {"items": batches[(page - 1) * size:page * size], "total": len(batches), "page": page, "page_size": size}
        elif path.startswith("/api/v2/job-batches/"):
            batch_id = path.split("/")[4]
            batch = next(value for value in batches if value["id"] == batch_id)
            if method == "POST" and path.endswith("/cancel"):
                state["cancel_calls"] += 1
                batch["jobs"][-1]["status"] = "cancelled"
                batch["jobs"][-2]["cancel_requested"] = True
                batch.update(counts={"succeeded": 1, "failed": 1, "running": 1, "cancelled": 1}, completed_count=3, active_count=1, retryable_count=2)
                data = batch
            elif method == "POST" and path.endswith("/retry"):
                state["retry_calls"] += 1
                retry = copy.deepcopy(batch)
                retry.update(id="retry-series", title=batch["title"] + " · 重试", status="queued", task_count=2, counts={"queued": 2}, succeeded_count=0, completed_count=0, active_count=2, retryable_count=0, progress=0, records_saved=0, jobs=[job(500, "queued"), job(501, "queued")])
                batches.insert(0, retry)
                data = {"batch": retry, "jobs": retry["jobs"]}
            else:
                data = batch
        elif path.startswith("/api/v2/jobs/"):
            job_id = path.split("/")[4]
            data = next(value for value in queue + children + [item for batch in batches for item in batch["jobs"]] if value["id"] == job_id)
            if method == "POST" and path.endswith("/retry"):
                state["job_retry_calls"] += 1
                data = job(900, "queued")
                queue.insert(0, data)
        elif path == "/api/v2/social":
            data = {"runtime": {"ready": False}, "platforms": []}
        elif path == "/api/v2/capabilities":
            data = {}
        elif path == "/api/v2/drafts/collection":
            if method == "PUT":
                state["draft"] = {**request.request.post_data_json, "revision": 1}
            data = {"draft": state["draft"]}
        elif path == "/api/v2/research/plan":
            data = {"groups": [], "queries": [], "suggestions": [], "ai": {"used": False}}
        else:
            return request.fulfill(status=404, content_type="application/json", body=json.dumps({"error": {"message": "Unexpected test request: " + path}}))
        request.fulfill(content_type="application/json", body=json.dumps(data, ensure_ascii=False))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        page.route("**/*", route)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto("http://batch.test/#jobs?page=7&batch_page=3")
        expect(page.locator(".job-row")).to_have_count(5)
        expect(page.locator(".batch-card")).to_have_count(3)
        expect(page.locator(".job-row").first).to_contain_text("反戦 120")
        expect(page.get_by_role("link", name="去研究工作台", exact=True).first).to_have_attribute("href", "#network")
        expect(page.get_by_role("button", name="批量研究", exact=True)).to_have_count(0)
        expect(page.get_by_role("button", name="新建采集", exact=True)).to_have_count(0)
        expect(page.get_by_role("dialog")).to_have_count(0)
        assert all(delay == 5000 for delay in page.evaluate("window.__pollDelays"))
        print("PASS complete queue beyond 100 records, independent batch pagination and 5-second poll contract")

        page.goto("http://batch.test/#jobs?batch=series-0")
        page.evaluate("async () => {await window.renderTest();}")
        selected = page.locator(".batch-detail")
        expect(selected).to_contain_text("成功 1 / 4 个任务")
        expect(selected).to_contain_text("实际新增 5 条")
        expect(selected).to_contain_text("不代表全部成功")
        expect(selected.locator(".batch-job-list > li")).to_have_count(4)
        expect(selected.get_by_role("link", name="查看发现树与关系", exact=True)).to_have_attribute("href", "#network?run=run-evidence")
        selected.get_by_role("button", name="查看任务详情", exact=True).first.click()
        expect(page.get_by_role("dialog")).to_contain_text("深度研究 · 反戦 200")
        page.get_by_role("button", name="关闭对话框", exact=True).click()
        selected.get_by_role("button", name="取消未完成任务", exact=True).click()
        expect(selected).to_contain_text("执行或排队 1")
        assert state["cancel_calls"] == 1
        expect(selected).to_contain_text("不代表全部成功")
        selected.get_by_role("button", name="重试未完成任务", exact=True).click()
        expect(selected).to_contain_text("成功 0 / 2 个任务")
        expect(selected).to_contain_text("执行或排队 2")
        assert state["retry_calls"] == 1 and "batch=retry-series" in page.url
        print("PASS truthful aggregate states, child diagnostics/graph access, bounded cancellation and retry navigation")

        retry = batches[0]
        for item in retry["jobs"]:
            item.update(status="partial", progress=100, result={"inserted": 2})
        retry.update(status="partial", progress=100, counts={"partial": 2}, succeeded_count=0, completed_count=2, active_count=0, retryable_count=2, records_saved=4)
        page.evaluate("async () => {await Promise.all(window.__polls.map(task=>task()));}")
        expect(selected).to_contain_text("成功 0 / 2 个任务")
        expect(selected).to_contain_text("不代表全部成功")
        expect(selected.get_by_role("button", name="取消未完成任务", exact=True)).to_have_count(0)
        expect(selected.get_by_role("button", name="重试未完成任务", exact=True)).to_be_visible()
        # The server's retryable count is authoritative; hard count budgets
        # cannot be retried without a saved research ID; do not invent a graph.
        for reason in ["record_budget", "account_budget", "edge_budget"]:
            retry["jobs"][0]["result"] = {"inserted": 2, "summary": {"stop_reason": reason}}
            selected.get_by_role("button", name="查看任务详情", exact=True).first.click()
            modal = page.get_by_role("dialog")
            expect(modal).to_contain_text("没有可读取的研究记录")
            expect(modal.get_by_role("button", name="创建重试任务", exact=True)).to_have_count(0)
            expect(modal.get_by_role("button", name="继续研究", exact=True)).to_have_count(0)
            page.get_by_role("button", name="关闭对话框", exact=True).click()
            expect(page.get_by_role("dialog")).to_have_count(0)
        assert state["job_retry_calls"] == 0
        retry["jobs"][0]["result"] = {"inserted": 2, "summary": {"stop_reason": "time_budget"}}
        selected.get_by_role("button", name="查看任务详情", exact=True).first.click()
        expect(page.get_by_role("dialog").get_by_role("button", name="创建重试任务", exact=True)).to_be_visible()
        expect(page.get_by_role("dialog").get_by_role("button", name="规划后续任务串", exact=True)).to_have_count(0)
        page.get_by_role("button", name="关闭对话框", exact=True).click()
        for item in retry["jobs"]:
            item["result"] = {"inserted": 2, "summary": {"stop_reason": "record_budget"}}
        retry["retryable_count"] = 0
        page.evaluate("async () => {await Promise.all(window.__polls.map(task=>task()));}")
        expect(selected.get_by_role("button", name="重试未完成任务", exact=True)).to_have_count(0)
        expect(selected).to_contain_text("可重试 0")
        expect(selected).to_contain_text("逐个调整上限后继续研究")
        expect(selected.get_by_role("button", name="规划后续任务串", exact=True)).to_have_count(0)
        expect(page.get_by_role("dialog")).to_have_count(0)
        assert state["retry_calls"] == 1 and state["job_retry_calls"] == 0
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Task series view horizontally overflows"
        assert not errors, errors
        browser.close()
        print("PASS live polling, hard-budget retries suppressed, explicit unavailable saved-run state, per-run continuation guidance and 390px layout")
        print("3 batch task-center integration groups passed")


if __name__ == "__main__":
    main()
