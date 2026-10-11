"""Offline settings acceptance through the real app router and desktop bridge.

Every HTTP request is intercepted. The bridge is an explicit synthetic stub;
no user configuration, secret, profile, local database or native file is read.
"""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://configuration.test"
ARCHIVE_PATH = r"C:\Synthetic workspace\研究资料\connection-recovery-20261008" + "\\" + "very-long-encrypted-settings-folder-" * 6 + r"\settings.json.encrypted"
HIDDEN_KEY = "SYNTHETIC-ORIGINAL-KEY-NEVER-RENDER"
HIDDEN_CAUSE = "SYNTHETIC-RUNTIME-CAUSE-NEVER-RENDER"
DESKTOP_STUB = """
window.__connectionState = { settings: INITIAL_SETTINGS, getCalls: 0, saves: [], dataCalls: 0, logsCalls: 0 };
let pendingSave;
window.desktop = {
  async getConnection() { window.__connectionState.getCalls++; return structuredClone(window.__connectionState.settings); },
  saveConnection(payload) {
    window.__connectionState.saves.push(structuredClone(payload));
    return new Promise((resolve, reject) => { pendingSave = { resolve, reject, payload }; });
  },
  async openData() { window.__connectionState.dataCalls++; },
  async openLogs() { window.__connectionState.logsCalls++; }
};
window.__finishSave = (result, failure = false) => {
  if (!pendingSave) throw new Error('No pending synthetic save');
  const pending = pendingSave;
  pendingSave = null;
  if (failure) { pending.reject(new Error(result)); return; }
  const previous = window.__connectionState.settings;
  window.__connectionState.settings = { base_url: pending.payload.base_url, model: pending.payload.model,
    configured: pending.payload.clear_key ? false : !!pending.payload.api_key || previous.configured,
    secure_storage: true };
  pending.resolve(result);
};
"""


def main(artifacts=None):
    if artifacts:
        artifacts.mkdir(parents=True, exist_ok=True)
    state = {"reads": [], "unexpected": [], "downloads": 0}
    errors, results, bridge_evidence = [], [], []

    def route(request):
        parsed = urlsplit(request.request.url)
        path = parsed.path
        if parsed.netloc != "configuration.test":
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
            data = {"collectors": [], "llm": {"configured": False}, "backup": {"available": True, "detail": "可保存合成研究快照"}}
        elif path == "/api/v2/social":
            data = {"runtime": {"ready": False}, "platforms": []}
        elif path == "/api/v2/items":
            data = {"items": [], "total": 0, "page": 1, "page_size": 25}
        elif path == "/api/v2/backup":
            state["downloads"] += 1
            return request.fulfill(headers={"Content-Disposition": 'attachment; filename="synthetic-research.sqlite3"'}, content_type="application/octet-stream", body=b"SYNTHETIC-TEST-NOT-A-DATABASE")
        else:
            state["unexpected"].append(f"{request.request.method} {path}")
            return request.fulfill(status=404, content_type="application/json", body='{"error":{"message":"Unexpected offline request"}}')
        request.fulfill(content_type="application/json", body=json.dumps(data, ensure_ascii=False))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)

        def create_page(settings):
            page = browser.new_page(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
            page.add_init_script(DESKTOP_STUB.replace("INITIAL_SETTINGS", json.dumps(settings, ensure_ascii=False)))
            page.route("**/*", route)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(ORIGIN + "/#settings")
            expect(page.get_by_role("heading", name="AI 连接设置", exact=True)).to_be_visible()
            return page

        def form(page):
            return page.locator(".connection-settings form")

        def save(page):
            return form(page).get_by_role("button", name="保存 AI 连接", exact=True)

        def finish(page, result=None, failure=False):
            page.evaluate("([result,failure])=>window.__finishSave(result,failure)", [result or {}, failure])

        def record(page, name, filename):
            results.append(name)
            bridge_evidence.append({"case": name, "state": page.evaluate("window.__connectionState")})
            if artifacts:
                form(page).screenshot(path=str(artifacts / filename))
            print("PASS " + name)

        healthy = {"base_url": "https://synthetic.example/api", "model": "synthetic-model", "configured": True, "secure_storage": True, "api_key": HIDDEN_KEY}
        broken = {**healthy, "configured": False, "load_error": {"code": "decrypt_failed", "message": "原加密配置暂时无法解密，请重新填写连接。", "raw": HIDDEN_KEY}}
        page = create_page(broken)
        expect(form(page).locator('[data-testid="connection-recovery-warning"]')).to_contain_text("已保存的 AI 连接暂时无法读取")
        expect(form(page)).to_contain_text("资料、研究和证据整理仍可使用")
        expect(form(page).locator("[name=api_key]")).to_have_value("")
        assert HIDDEN_KEY not in page.locator("body").inner_text()
        page.get_by_role("button", name="打开数据目录", exact=True).click()
        page.get_by_role("button", name="打开运行日志", exact=True).click()
        assert page.evaluate("window.__connectionState.dataCalls") == 1
        assert page.evaluate("window.__connectionState.logsCalls") == 1
        with page.expect_download() as download_info:
            page.get_by_role("button", name="备份研究数据", exact=True).click()
        assert download_info.value.suggested_filename == "synthetic-research.sqlite3"
        assert state["downloads"] == 1
        record(page, "unreadable AI configuration leaves warning, key empty, local folders and research backup usable", "configuration-load-warning.png")
        page.close()

        page = create_page({**healthy, "secure_storage": False})
        expect(save(page)).to_be_disabled()
        expect(form(page)).to_contain_text("系统加密存储当前不可用")
        expect(form(page).locator("[name=api_key]")).to_have_value("")
        assert page.evaluate("window.__connectionState.saves") == []
        record(page, "unavailable secure storage disables native submit and never exposes the old key", "configuration-secure-storage-disabled.png")
        page.close()

        page = create_page(broken)
        form(page).locator("[name=base_url]").fill("https://synthetic-a.example/api")
        form(page).locator("[name=model]").fill("submitted model A")
        form(page).locator("[name=api_key]").fill("SYNTHETIC-NEW-KEY-A")
        save(page).click()
        expect(save(page)).to_be_disabled()
        before_reads = page.evaluate("window.__connectionState.getCalls")
        expect(form(page).locator("[name=model]")).to_be_enabled()
        form(page).locator("[name=base_url]").fill("https://synthetic-b.example/api")
        form(page).locator("[name=model]").fill("unsaved model B")
        form(page).locator("[name=api_key]").fill("SYNTHETIC-NEW-KEY-B")
        finish(page, {"saved": True, "recovery_archive": ARCHIVE_PATH})
        expect(form(page).locator(".connection-save-status")).to_contain_text("之后修改的输入仍在这里，尚未保存")
        expect(form(page).locator("[name=model]")).to_have_value("unsaved model B")
        expect(form(page).locator("[name=api_key]")).to_have_value("SYNTHETIC-NEW-KEY-B")
        expect(form(page).locator(".backup-archive-path")).to_contain_text(ARCHIVE_PATH)
        expect(form(page).locator('[data-testid="connection-recovery-warning"]')).to_have_count(0)
        expect(save(page)).to_be_enabled()
        assert page.evaluate("window.__connectionState.getCalls") == before_reads
        assert page.evaluate("window.__connectionState.saves[0].model") == "submitted model A"
        assert page.evaluate("window.__connectionState.saves[0].api_key") == "SYNTHETIC-NEW-KEY-A"
        record(page, "accepted save A retains later editable B, its key and the readable recovery archive without refreshing", "configuration-new-input-retained.png")

        page.set_viewport_size({"width": 390, "height": 1000})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        assert form(page).evaluate("node=>node.scrollWidth<=node.clientWidth+1")
        assert form(page).locator(".backup-archive-path").evaluate("node=>node.scrollWidth<=node.clientWidth+1")
        expect(form(page).locator(".backup-archive-path")).to_contain_text(ARCHIVE_PATH)
        record(page, "390px settings and long archive path remain inside their viewport", "configuration-mobile-archive.png")
        page.close()

        page = create_page(healthy)
        form(page).locator("[name=model]").fill("retry model")
        form(page).locator("[name=api_key]").fill("SYNTHETIC-RETRY-KEY")
        save(page).click()
        finish(page, "配置未保存：合成存储暂时不可写，原配置保留。", failure=True)
        expect(form(page).locator(".form-error")).to_contain_text("配置未保存")
        expect(form(page).locator("[name=model]")).to_have_value("retry model")
        expect(form(page).locator("[name=api_key]")).to_have_value("SYNTHETIC-RETRY-KEY")
        expect(save(page)).to_be_enabled()
        save(page).click()
        expect(form(page).locator(".form-error")).to_be_empty()
        assert len(page.evaluate("window.__connectionState.saves")) == 2
        finish(page, "配置仍未保存：请重试。", failure=True)
        expect(form(page).locator(".form-error")).to_contain_text("配置仍未保存")
        record(page, "rejected save retains all input and key, reenables retry and clears only the obsolete error", "configuration-save-failure-retry.png")
        page.close()

        page = create_page(healthy)
        form(page).locator("[name=model]").fill("leave route model")
        save(page).click()
        page.locator("#navigation a[data-route=library]").click()
        expect(page.get_by_role("heading", name="资料库", exact=True)).to_be_visible()
        before_reads = page.evaluate("window.__connectionState.getCalls")
        finish(page, {"saved": True})
        expect(page).to_have_url(ORIGIN + "/#library")
        expect(page.locator(".connection-settings")).to_have_count(0)
        assert page.evaluate("window.__connectionState.getCalls") == before_reads
        assert page.locator("#notifications").inner_text() == ""
        results.append("late accepted save on a departed route cannot refresh settings or navigate back")
        bridge_evidence.append({"case": results[-1], "state": page.evaluate("window.__connectionState")})
        if artifacts:
            page.screenshot(path=str(artifacts / "configuration-late-ack-library.png"))
        print("PASS " + results[-1])
        page.close()

        page = create_page(healthy)
        expect(form(page).locator("[name=api_key]")).to_have_value("")
        form(page).locator("[name=model]").fill("clean saved model")
        save(page).click()
        submitted = page.evaluate("window.__connectionState.saves[0]")
        assert submitted == {"base_url": healthy["base_url"], "model": "clean saved model", "api_key": "", "clear_key": False}
        before_reads = page.evaluate("window.__connectionState.getCalls")
        finish(page, {"saved": True})
        page.wait_for_function("count=>window.__connectionState.getCalls===count+1", arg=before_reads)
        expect(form(page).locator("[name=model]")).to_have_value("clean saved model")
        expect(form(page).locator("[name=api_key]")).to_have_value("")
        expect(form(page).locator("[name=clear_key]")).to_be_visible()
        form(page).locator("[name=clear_key]").check()
        save(page).click()
        assert page.evaluate("window.__connectionState.saves[1].clear_key") is True
        assert page.evaluate("window.__connectionState.saves[1].api_key") == ""
        finish(page, {"saved": True})
        expect(form(page).locator("[name=clear_key]")).to_have_count(0)
        save(page).click()
        assert page.evaluate("window.__connectionState.saves[2].clear_key") is False
        assert page.evaluate("window.__connectionState.saves[2].api_key") == ""
        finish(page, {"saved": True})
        expect(save(page)).to_be_enabled()
        record(page, "clean acceptance refreshes confirmed settings; empty key, clear checkbox and absent checkbox send explicit safe payloads", "configuration-clean-save-clear-key.png")
        page.close()

        page = create_page({**healthy, "runtime_error": {"code": "runtime_response_lost", "message": "连接保存后的运行状态未能确认，请退出后重新打开应用。", "cause": HIDDEN_CAUSE, "raw": HIDDEN_KEY}})
        expect(form(page).locator('[data-testid="connection-runtime-warning"]')).to_contain_text("AI 连接的运行状态需要恢复")
        expect(form(page)).to_contain_text("原磁盘配置仍保留")
        assert HIDDEN_KEY not in page.locator("body").inner_text()
        assert HIDDEN_CAUSE not in page.locator("body").inner_text()
        expect(form(page).locator("[name=api_key]")).to_have_value("")
        record(page, "runtime failure has readable recovery instructions while raw cause and original synthetic key stay hidden", "configuration-runtime-warning.png")
        page.close()
        assert not state["unexpected"], state["unexpected"]
        assert not errors, errors
        browser.close()
    if artifacts:
        (artifacts / "configuration-browser-results.json").write_text(json.dumps({"passed": results, "http_reads": state["reads"], "bridge": bridge_evidence, "unexpected": state["unexpected"], "pageerrors": errors, "fixture_notice": "All key strings, paths and configuration values are synthetic."}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(results)} configuration frontend integration groups passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path)
    main(parser.parse_args().artifacts)
