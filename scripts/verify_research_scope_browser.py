"""Real Chromium and HTTP acceptance in a fresh, disposable local profile."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis-worker"))

import uvicorn
from playwright.sync_api import expect, sync_playwright
from app.workbench.server import create_app
from app.workbench.store import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    database = output / "isolated-scope.sqlite3"
    if database.exists():
        raise RuntimeError("Choose a fresh QA directory; existing evidence is preserved")
    for name in ("WORKBENCH_SESSION_TOKEN", "WORKBENCH_DATABASE_URL", "DEEPSEEK_API_KEY"):
        os.environ.pop(name, None)
    store = Store(database)
    app = create_app(store, run_worker=False)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    errors, external, requests, checks = [], [], [], []
    try:
        deadline = time.monotonic() + 15
        while not server.started:
            if time.monotonic() >= deadline:
                raise RuntimeError("Isolated service failed to start")
            time.sleep(.03)
        port = server.servers[0].sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1080})
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: requests.append({"url": request.url, "method": request.method}))
            def guard(route):
                if urlsplit(route.request.url).hostname in {"127.0.0.1", "localhost"}:
                    route.continue_()
                else:
                    external.append(route.request.url)
                    route.abort()
            context.route("**/*", guard)
            page.goto(base + "/#network")
            expect(page.get_by_role("heading", name="研究工作台", exact=True)).to_be_visible()
            page.get_by_role("button", name="新建研究或采集", exact=True).click()
            modal = page.get_by_role("dialog")
            modal.locator('[name="keyword"]').fill("平和, 労働者")
            modal.locator('details.research-budget').evaluate("node => node.open = true")
            modal.locator('[name="analysis_mode"]').select_option("rules")
            for name in ("research_max_accounts", "max_edges", "max_records", "time_budget_seconds"):
                expect(modal.locator(f'[name="{name}"]')).to_have_value("0")
            modal.locator('[name="depth"]').fill("5")
            modal.locator('[name="research_max_scrolls"]').fill("300")
            page.screenshot(path=str(output / "01-scope-optional-stops.png"), full_page=True)
            assert store.list_jobs()["total"] == 0
            modal.get_by_role("button", name="预估用时与范围", exact=True).click()
            expect(modal.locator(".research-estimate")).to_contain_text("预计用时与开始确认")
            expect(modal.locator(".research-estimate")).to_contain_text("尚未用本机")
            assert "false" not in modal.locator(".research-estimate").inner_text()
            expect(modal.get_by_role("button", name="确认预计用时并开始", exact=True)).to_be_enabled()
            assert store.list_jobs()["total"] == 0
            checks.append("estimate_does_not_enqueue_and_shows_uncalibrated_range")
            page.screenshot(path=str(output / "02-parameter-bound-estimate.png"), full_page=True)
            modal.locator('[name="depth"]').fill("6")
            expect(modal.get_by_role("button", name="预估用时与范围", exact=True)).to_be_visible()
            expect(modal.locator(".research-estimate")).to_be_hidden()
            modal.get_by_role("button", name="预估用时与范围", exact=True).click()
            expect(modal.get_by_role("button", name="确认预计用时并开始", exact=True)).to_be_enabled()
            checks.append("editing_scope_invalidates_old_confirmation")
            page.set_viewport_size({"width": 980, "height": 900})
            page.screenshot(path=str(output / "03-confirmation-980px.png"), full_page=True)
            modal.get_by_role("button", name="确认预计用时并开始", exact=True).click()
            expect(page.get_by_role("heading", name="任务中心", exact=True)).to_be_visible()
            assert store.list_jobs()["total"] == 1
            job = store.list_jobs()["items"][0]
            assert job["params"]["depth"] == 6 and job["params"]["max_records"] == 0 and job["params"]["time_budget_seconds"] == 0
            checks.append("confirm_enqueues_exact_latest_scope_with_zero_total_stops")
            page.screenshot(path=str(output / "04-confirmed-queued-job.png"), full_page=True)
            page.goto(base + "/#reports")
            page.get_by_role("button", name="生成报告", exact=True).first.click()
            report = page.get_by_role("dialog")
            expect(report.locator('[name="report_minutes"]')).to_have_value("0")
            expect(report.locator('[name="report_minutes"]')).to_have_attribute("min", "0")
            checks.append("report_zero_time_default_visible_and_valid")
            page.screenshot(path=str(output / "05-report-natural-scope.png"), full_page=True)
            progress_job = store.create_job("report", {"mode": "ai", "time_budget_seconds": 0,
                "start_date": "2026-10-01", "end_date": "2026-10-10"})
            checkpoint = {"coverage_progress": {"reviewed_source_count": 601, "input_source_count": 601, "pending_source_count": 0},
                "progress": {"stage": "evidence_notes", "level": 2, "planned_batch_count": 4, "current_batch_number": 3,
                             "completed_unit_count": 401, "total_unit_count": 601, "completed_request_count": 5,
                             "reused_reviewed_batch_count": 2}}
            store.update_job(progress_job["id"], status="running", progress=70, result={"report_checkpoint": checkpoint})
            page.goto(base + "/#jobs?selected=" + progress_job["id"])
            details = page.get_by_role("dialog", name="任务详情", exact=True)
            expect(details.locator(".report-reading-progress")).to_contain_text("601 / 601")
            expect(details.locator(".report-synthesis-progress")).to_contain_text("当前批次 3 / 4")
            expect(details.locator(".report-synthesis-progress")).to_contain_text("401 / 601")
            page.screenshot(path=str(output / "06-detailed-synthesis-progress.png"), full_page=True)
            checkpoint["report_generation"] = {"phase": "review_pending", "draft_attempt_count": 1}
            store.update_job(progress_job["id"], progress=88, result={"report_checkpoint": checkpoint})
            expect(details.locator(".report-generation-progress")).to_contain_text("来源与研究目标复核", timeout=15000)
            page.screenshot(path=str(output / "07-draft-source-review-progress.png"), full_page=True)
            checks.append("saved_progress_displays_reading_synthesis_batches_and_draft_source_review")
            assert not errors, errors
            assert not external, external
            browser.close()
        result = {"ok": True, "checks": checks, "page_errors": errors, "external_requests": external,
                  "requests": requests, "database": str(database), "production_data_used": False,
                  "live_platform_or_model_calls": False, "worker_started": False,
                  "progress_ui_fixture": "Report checkpoint values are a saved-state display fixture; no model execution or external fact acceptance is implied."}
        (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "checks": checks, "output": str(output)}, ensure_ascii=False))
    except Exception as error:
        (output / "failed-result.json").write_text(json.dumps({"ok": False, "error": str(error), "checks": checks,
                  "page_errors": errors, "external_requests": external, "requests": requests}, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        store.close()


if __name__ == "__main__":
    main()
