"""Offline acceptance of the desktop backup restore control in the real app.

Every request is intercepted, and window.desktop is an explicit synthetic stub.
No native file picker, local database, credential or desktop service is accessed.
"""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://restore.test"
ARCHIVE_PATH = "C:\\Synthetic workspace\\研究资料\\restore-archive-20261008\\" + "very-long-old-database-folder-" * 5
DESKTOP_STUB = """
window.__restoreState = { calls: 0, argumentCounts: [], openDataCalls: 0 };
let pendingRestore;
window.desktop = {
  restoreBackup(...args) {
    window.__restoreState.calls++;
    window.__restoreState.argumentCounts.push(args.length);
    return new Promise((resolve, reject) => { pendingRestore = { resolve, reject }; });
  },
  async openData() { window.__restoreState.openDataCalls++; }
};
window.__finishRestore = (result, failure = false) => {
  if (!pendingRestore) throw new Error('No pending synthetic restore');
  const pending = pendingRestore;
  pendingRestore = null;
  if (failure) pending.reject(new Error(result)); else pending.resolve(result);
};
"""


def main(artifacts=None):
    if artifacts:
        artifacts.mkdir(parents=True, exist_ok=True)
    state = {"backup_available": True, "unexpected": [], "reads": []}
    errors = []

    def route(request):
        parsed = urlsplit(request.request.url)
        path = parsed.path
        if parsed.netloc != "restore.test":
            state["unexpected"].append(request.request.url)
            return request.abort()
        if path == "/":
            return request.fulfill(content_type="text/html", body=(ROOT / "index.html").read_text(encoding="utf-8"))
        if path.startswith("/assets/"):
            file = ROOT / path.removeprefix("/assets/")
            assert file.resolve().is_relative_to(ROOT)
            return request.fulfill(content_type={".css": "text/css", ".woff2": "font/woff2"}.get(file.suffix, "text/javascript"), body=file.read_bytes())
        state["reads"].append((request.request.method, path))
        if path == "/api/v2/health":
            data = {"ok": True, "status": "ok"}
        elif path == "/api/v2/capabilities":
            data = {"collectors": [], "llm": {"configured": False}, "backup": {"available": state["backup_available"], "detail": "可保存合成研究快照"}}
        elif path == "/api/v2/social":
            data = {"runtime": {"ready": False}, "platforms": []}
        else:
            state["unexpected"].append(f"{request.request.method} {path}")
            return request.fulfill(status=404, content_type="application/json", body='{"error":{"message":"Unexpected offline request"}}')
        request.fulfill(content_type="application/json", body=json.dumps(data, ensure_ascii=False))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)

        def create_page(stub=None):
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            if stub:
                page.add_init_script(stub)
            page.route("**/*", route)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(ORIGIN + "/#settings")
            expect(page.get_by_role("heading", name="能力与设置", exact=True)).to_be_visible()
            return page

        def restore_button(page):
            return page.get_by_role("button", name="恢复研究备份", exact=True)

        def backup_section(page):
            return page.locator('section[aria-label="研究数据备份"]')

        def finish(page, result, failure=False):
            page.evaluate("([result, failure]) => window.__finishRestore(result, failure)", [result, failure])

        def contained(page):
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            assert backup_section(page).evaluate("node => node.scrollWidth <= node.clientWidth + 1")

        def screenshot(page, filename, section=False):
            if artifacts:
                target = backup_section(page) if section else page
                target.screenshot(path=str(artifacts / filename), **({} if section else {"full_page": True}))

        page = create_page()
        expect(restore_button(page)).to_have_count(0)
        page.get_by_text("如何恢复备份", exact=True).click()
        manual = backup_section(page).locator("details")
        expect(manual.locator("li")).to_have_count(5)
        expect(manual).to_contain_text("workbench.sqlite3-wal")
        expect(manual).to_contain_text("排队任务按正常队列继续执行")
        screenshot(page, "restore-browser-manual.png", section=True)
        page.close()
        invalid_bridge = create_page("window.desktop = { restoreBackup: true };")
        expect(restore_button(invalid_bridge)).to_have_count(0)
        invalid_bridge.close()
        print("PASS browser and non-callable desktop bridge show only accurate five-step manual restore help")

        state["backup_available"] = False
        page = create_page(DESKTOP_STUB)
        control = restore_button(page)
        section = backup_section(page)
        expect(control).to_have_attribute("data-action", "restore-backup")
        expect(control).to_be_enabled()
        expect(page.get_by_role("button", name="备份研究数据", exact=True)).to_be_disabled()
        expect(section).to_contain_text("恢复前的旧库会自动存档")
        expect(section).to_contain_text("浏览器登录会话和 AI 连接保持不变")
        expect(section).to_contain_text("排队和运行任务恢复后需要在任务中心手动重试")
        page.get_by_text("如何恢复备份", exact=True).click()
        expect(section.locator("details")).to_contain_text("以下步骤适用于手动替换数据文件")
        expect(section.locator("details")).to_contain_text("排队任务按正常队列继续执行")
        contained(page)
        screenshot(page, "restore-desktop-ready.png", section=True)
        print("PASS callable desktop restore has stable control and distinct desktop/manual task instructions")

        control.click()
        expect(control).to_be_disabled()
        expect(control).to_have_attribute("aria-busy", "true")
        expect(section.locator(".backup-restore-status")).to_contain_text("请选择备份")
        expect(section.locator(".backup-restore-status")).to_contain_text("检查文件、保存旧库并恢复研究数据")
        control.evaluate("node => { node.dispatchEvent(new MouseEvent('click', { bubbles: true })); node.dispatchEvent(new MouseEvent('click', { bubbles: true })); }")
        assert page.evaluate("window.__restoreState.calls") == 1
        assert page.evaluate("window.__restoreState.argumentCounts") == [0]
        screenshot(page, "restore-desktop-pending.png", section=True)
        finish(page, {"cancelled": True})
        expect(control).to_be_enabled()
        expect(control).not_to_have_attribute("aria-busy", "true")
        expect(section.locator(".backup-restore-status")).to_contain_text("已取消恢复")
        expect(section.locator(".error-box")).to_have_count(0)
        print("PASS pending restore disables itself, calls native method without arguments once, and cancellation is harmless")

        control.click()
        finish(page, "备份文件损坏，当前资料库保留。", failure=True)
        expect(section.locator(".error-box")).to_contain_text("恢复未完成：备份文件损坏")
        expect(control).to_be_enabled()
        expect(section.locator(".backup-restore-status")).to_be_empty()
        screenshot(page, "restore-desktop-failure-retry.png", section=True)
        control.click()
        expect(section.locator(".error-box")).to_have_count(0)
        expect(control).to_be_disabled()
        assert page.evaluate("window.__restoreState.calls") == 3
        finish(page, {"cancelled": True})
        expect(control).to_be_enabled()
        page.get_by_role("button", name="打开数据目录", exact=True).click()
        assert page.evaluate("window.__restoreState.openDataCalls") == 1
        assert page.url == ORIGIN + "/#settings"
        print("PASS Chinese restore failure remains reviewable, retry clears it, and page operations stay available")

        control.click()
        finish(page, {})
        expect(section.locator(".error-box")).to_contain_text("应用尚未确认恢复完成")
        expect(control).to_be_enabled()
        expect(page.locator("#notifications .notice.success")).to_have_count(0)
        print("PASS incomplete native acknowledgement cannot be reported as successful restore")

        control.click()
        finish(page, {"restored": True, "archive_path": ARCHIVE_PATH, "summary": {"items": 2, "jobs": 1}})
        expect(section.locator(".error-box")).to_have_count(0)
        expect(section.locator(".backup-restore-status")).to_contain_text("研究备份已恢复")
        expect(section.locator(".backup-archive-path span")).to_have_text(ARCHIVE_PATH)
        expect(section.locator(".backup-archive-path a")).to_have_count(0)
        expect(control).to_be_disabled()
        expect(page.locator("#notifications .notice.success")).to_contain_text("恢复前的资料库已自动存档")
        control.evaluate("node => node.dispatchEvent(new MouseEvent('click', { bubbles: true }))")
        assert page.evaluate("window.__restoreState.calls") == 5
        contained(page)
        screenshot(page, "restore-desktop-success.png", section=True)
        page.set_viewport_size({"width": 390, "height": 844})
        contained(page)
        screenshot(page, "restore-desktop-success-390.png")
        page.locator("#notifications .notice").evaluate_all("nodes => nodes.forEach(node => node.remove())")
        screenshot(page, "restore-desktop-success-section-390.png", section=True)
        print("PASS confirmed restore shows completion and inert archive path, blocks repeat submission and contains 390px layout")
        page.close()

        state["backup_available"] = True
        page = create_page(DESKTOP_STUB)
        restore_button(page).click()
        page.evaluate("""() => {
          sessionStorage.setItem('synthetic-native-restore-complete', 'true');
          window.__finishRestore({ restored: true, archive_path: 'C:\\\\Synthetic archive' });
          setTimeout(() => location.assign('/?desktop_token=restored-fixture#settings'), 0);
        }""")
        expect(page).to_have_url(ORIGIN + "/?desktop_token=restored-fixture#settings")
        expect(page.get_by_role("heading", name="能力与设置", exact=True)).to_be_visible()
        expect(restore_button(page)).to_be_enabled()
        assert page.evaluate("sessionStorage.getItem('synthetic-native-restore-complete')") == "true"
        expect(page.get_by_role("button", name="备份研究数据", exact=True)).to_be_enabled()
        assert not errors, errors
        assert not state["unexpected"], state["unexpected"]
        assert all(method == "GET" for method, _ in state["reads"])
        print("PASS native navigation with new desktop token reopens settings cleanly without a browser restore endpoint")
        print("7 restore browser acceptance groups passed; all requests intercepted; desktop method is a synthetic stub")
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path)
    args = parser.parse_args()
    main(args.artifacts)
