"""Real HTTP/browser product acceptance in an EMPTY, disposable profile.

This is the project's Python Playwright smoke workflow, not @playwright/test.
Never point this fixture writer at an existing user library.
"""
import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright, expect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    assert base.startswith("http://127.0.0.1:")
    args.artifacts.mkdir(parents=True, exist_ok=True)

    def api(path, body=None):
        request = Request(base + "/api/v2/" + path, data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None, headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=30) as response:
            return json.load(response)

    assert api("overview")["stats"]["items"] == 0 and not api("accounts")["items"], "Fixture requires empty disposable user data"
    alpha = api("accounts", {"name": "浏览器测试账号 Alpha", "handle": "alpha", "platform": "twitter", "url": "https://x.com/alpha"})
    beta = api("accounts", {"name": "浏览器测试账号 Beta", "handle": "beta", "platform": "twitter", "url": "https://x.com/beta"})
    tokyo = {"type": "news", "title": "测试活动：东京平和集会", "content": "2026年10月5日、東京都で平和集会が開催された。", "url": "https://example.org/tokyo-event", "published_at": "2026-10-06T00:00:00Z"}
    kyoto = {"type": "news", "title": "测试活动：京都平和集会", "content": "2026年10月6日、京都府で平和集会が開催された。", "url": "https://example.org/kyoto-event", "published_at": "2026-10-07T00:00:00Z"}
    api("items/import", {"items": [tokyo, kyoto, {"type": "social", "account_id": alpha["id"], "title": "公开提及测试", "content": "公開原帖で @beta を提及。", "url": "https://x.com/alpha/status/123", "published_at": "2026-10-05T00:00:00Z"}]})
    errors, checks = [], []
    with sync_playwright() as pw:
        # The portable desktop bundles full Chromium, as does browser_smoke.py.
        browser = pw.chromium.launch(executable_path=pw.chromium.executable_path, headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base + "/#network?registry=1")
        expect(page.get_by_role("heading", name="账号关系图", exact=True)).to_be_visible()
        expect(page.locator(".network-node")).to_have_count(2)
        expect(page.locator(".network-edge")).to_have_count(1)
        expect(page.get_by_role("button", name="开启布局动效", exact=True)).to_be_visible()
        node = page.locator('.network-node[data-key="twitter:alpha"]')
        before, edge_before = node.get_attribute("transform"), page.locator(".network-edge").get_attribute("d")
        node.locator(".network-node-dot").hover()
        box = node.locator(".network-node-dot").bounding_box()
        center_x, center_y = box['x'] + box['width']/2, box['y'] + box['height']/2
        page.mouse.move(center_x, center_y)
        page.mouse.down(); page.mouse.move(center_x + 70, center_y + 45, steps=10); page.mouse.up()
        assert node.get_attribute("transform") != before and page.locator(".network-edge").get_attribute("d") != edge_before
        node.focus(); before = node.get_attribute("transform"); page.keyboard.press("ArrowRight")
        assert node.get_attribute("transform") != before
        viewport = page.locator(".interactive-svg > g")
        before = viewport.get_attribute("transform")
        svg_box = page.locator(".interactive-svg").bounding_box()
        page.mouse.move(svg_box["x"] + 10, svg_box["y"] + 10)
        page.mouse.down(); page.mouse.move(svg_box["x"] + 60, svg_box["y"] + 30, steps=5); page.mouse.up()
        assert viewport.get_attribute("transform") != before
        page.get_by_role("button", name="放大", exact=True).click()
        expect(page.locator(".network-zoom .mono")).not_to_have_text("100%")
        page.locator(".network-edge").focus(); page.keyboard.press("Enter")
        expect(page.get_by_role("link", name="关系依据", exact=True)).to_have_attribute("href", "https://x.com/i/status/123")
        page.screenshot(path=str(args.artifacts / "registry-network.png"), full_page=True)
        checks.extend(["drag_moves_node_and_edge", "keyboard_moves_node", "canvas_pan_and_zoom", "source_edge_inspector", "reduced_motion_graph"])

        page.goto(base + "/#overview")
        expect(page.locator(".map-prefecture")).to_have_count(47)
        expect(page.locator(".activity-metrics")).to_contain_text("已核对活动")
        assert api("activities")["denominator"] == 0
        page.locator('.activity-record-toolbar [name="status"]').select_option("candidate")
        expect(page.locator(".activity-card")).to_have_count(2)
        card = page.locator(".activity-card").filter(has_text="东京平和集会")
        card.get_by_role("button", name="核对这场活动", exact=True).click()
        modal = page.get_by_role("dialog")
        expect(modal.locator('[name="date"]')).to_have_value("2026-10-05")
        expect(modal.locator('[name="prefecture"]')).to_have_value("東京都")
        modal.get_by_role("button", name="保存已核对活动", exact=True).click()
        expect(modal).to_have_count(0)
        expect(page.locator(".activity-metrics strong").nth(1)).to_have_text("1")
        expect(page.locator(".activity-list")).not_to_have_attribute('aria-busy', 'true')
        tokyo_path = page.locator('.map-prefecture[data-prefecture="東京都"]')
        def hover_tokyo():
            point = tokyo_path.evaluate("node => {const p=new DOMPoint(Number(node.dataset.labelX),Number(node.dataset.labelY)).matrixTransform(node.getScreenCTM()); return {x:p.x,y:p.y};}")
            page.mouse.move(point["x"], point["y"])
        page.locator('.japan-activity-map').scroll_into_view_if_needed()
        hover_tokyo()
        expect(page.locator(".activity-selection")).to_contain_text("100.0%")
        kyoto_card = page.locator(".activity-card").filter(has_text="京都平和集会")
        kyoto_card.get_by_role("button", name="核对这场活动", exact=True).click()
        modal = page.get_by_role("dialog")
        modal.get_by_role("button", name="保存已核对活动", exact=True).click()
        expect(modal).to_have_count(0)
        expect(page.locator(".activity-metrics strong").nth(1)).to_have_text("2")
        page.locator('.activity-record-toolbar [name="status"]').select_option("confirmed")
        expect(page.locator(".activity-list")).not_to_have_attribute('aria-busy', 'true')
        page.locator('.japan-activity-map').scroll_into_view_if_needed()
        hover_tokyo(); expect(page.locator(".activity-selection")).to_contain_text("50.0%")
        tokyo_path.focus(); page.keyboard.press("Enter")
        expect(page.locator(".activity-card")).to_have_count(1)
        expect(page.locator(".activity-card")).to_contain_text("东京平和集会")
        page.screenshot(path=str(args.artifacts / "japan-activity-map.png"), full_page=True)
        page.locator('.activity-date-filter [name="start_date"]').fill("2026-10-06")
        page.locator('.activity-date-filter [name="end_date"]').fill("2026-10-06")
        page.get_by_role("button", name="更新活动范围", exact=True).click()
        expect(page.locator(".activity-metrics strong").nth(1)).to_have_text("1")
        page.get_by_role("button", name="查看全国", exact=True).click()
        expect(page.locator(".activity-card")).to_have_count(1)
        expect(page.locator(".activity-card")).to_contain_text("京都平和集会")
        assert api("activities")["denominator"] == 2
        checks.extend(["offline_47_prefectures", "candidate_does_not_count", "source_date_and_location_review", "percent_100_then_50", "keyboard_prefecture_filter", "activity_date_denominator"])

        page.goto(base + "/#accounts")
        page.get_by_role("button", name="自动核验候选账号", exact=True).click()
        page.get_by_role("dialog").get_by_role("button", name="开始公开核验", exact=True).click()
        expect(page.get_by_role("heading", name="任务中心", exact=True)).to_be_visible()
        expect(page.locator(".job-row")).to_contain_text("公开账号自动核验")
        page.wait_for_function("async () => { const d=await (await fetch('/api/v2/jobs')).json(); return d.items[0].status === 'succeeded'; }")
        assert all(row["status"] == "candidate" for row in api("accounts")["items"])
        page.goto(base + "/#accounts")
        page.get_by_role("button", name="关系", exact=True).first.click()
        page.get_by_role("button", name="打开可拖拽关系图", exact=True).click()
        expect(page.locator(".network-node")).to_have_count(2)
        checks.extend(["automatic_check_queued_and_visible", "missing_source_remains_candidate", "account_relation_graph_entry"])

        page.goto(base + "/#library")
        page.get_by_role("button", name="资料清理", exact=True).click()
        expect(page.get_by_role("heading", name="资料清理", exact=True)).to_be_visible()
        target = page.get_by_role("checkbox", name="选择资料：测试活动：东京平和集会", exact=True)
        target.check()
        page.get_by_role("button", name="移入回收站", exact=True).click()
        modal = page.get_by_role("dialog")
        expect(modal.locator(".cleanup-preview-list")).to_contain_text("东京平和集会")
        expect(modal.locator(".cleanup-preview-list li")).to_have_count(1)
        modal.get_by_role("button", name="移入回收站", exact=True).click()
        expect(modal).to_have_count(0)
        expect(page.locator(".cleanup-selection-row")).to_have_count(2)
        assert api("activities")["denominator"] == 1
        page.get_by_role("link", name="查看回收站", exact=True).click()
        expect(page.locator(".cleanup-selection-row")).to_have_count(1)
        target.check()
        page.get_by_role("button", name="恢复资料", exact=True).click()
        modal = page.get_by_role("dialog")
        modal.get_by_role("button", name="恢复资料", exact=True).click()
        expect(modal).to_have_count(0)
        expect(page.get_by_role("heading", name="回收站为空", exact=True)).to_be_visible()
        assert api("activities")["denominator"] == 2
        page.goto(base + "/#library?cleanup=1")
        target.check(); page.get_by_role("button", name="移入回收站", exact=True).click()
        modal = page.get_by_role("dialog"); modal.get_by_role("button", name="移入回收站", exact=True).click(); expect(modal).to_have_count(0)
        page.get_by_role("link", name="查看回收站", exact=True).click(); target.check()
        page.get_by_role("button", name="彻底删除", exact=True).click()
        modal = page.get_by_role("dialog")
        modal.get_by_role("button", name="彻底删除", exact=True).click()
        expect(modal.get_by_role("alert")).to_contain_text("明确勾选")
        modal.locator('[name="confirm_purge"]').check()
        modal.get_by_role("button", name="彻底删除", exact=True).click()
        expect(modal).to_have_count(0)
        expect(page.get_by_role("heading", name="回收站为空", exact=True)).to_be_visible()
        assert api("overview")["stats"]["items"] == 2 and api("activities")["denominator"] == 1
        checks.extend(["cleanup_selected_preview", "trash_removes_activity", "restore_preserves_activity", "purge_requires_checkbox", "permanent_purge_scope"])

        revision = {"type": "news", "title": "捕获版本验收", "content": "公开第一版材料。", "url": "https://example.org/revision", "published_at": "2026-10-08T00:00:00Z", "metadata": {"collection_method": "public_rss", "content_kind": "rss_summary", "source_url": "https://example.org/feed", "discovery_query": "反戦"}}
        assert api("items/import", {"items": [revision]})["inserted"] == 1
        assert api("items/import", {"items": [{**revision, "content": "公开第二版材料。"}]})["updated"] == 1
        page.goto(base + "/#library")
        page.get_by_role("button", name="查看资料：捕获版本验收", exact=True).click()
        modal = page.get_by_role("dialog")
        expect(modal).to_contain_text("公开第二版材料。")
        expect(modal).to_contain_text("捕获版本")
        modal.get_by_text("查看保留的历史捕获", exact=True).click()
        expect(modal.locator(".capture-revision")).to_contain_text("公开第一版材料。")
        original = modal.get_by_role("textbox", name="出版方原文地址（聚合器资料需填写）", exact=True)
        original.fill("file:///private")
        modal.get_by_role("button", name="读取公开原文", exact=True).click()
        expect(modal.get_by_role("alert")).to_be_visible()
        original.fill("https://news.google.com/rss/articles/fixture")
        modal.get_by_role("button", name="读取公开原文", exact=True).click()
        expect(page.get_by_role("heading", name="任务中心", exact=True)).to_be_visible()
        page.wait_for_function("async () => {const d=await (await fetch('/api/v2/jobs')).json(); return d.items.some(j=>j.params._news_capture && j.status==='partial');}")
        capture = next(row for row in api("jobs")["items"] if row["params"].get("_news_capture"))
        assert capture["result"]["diagnostics"][0]["outcome"] == "aggregator_only"
        checks.extend(["original_url_validation", "article_capture_partial_diagnostics"])
        page.goto(base + "/#library")
        page.keyboard.press("Escape"); expect(modal).to_have_count(0)
        page.get_by_role("button", name="查看资料：公开提及测试", exact=True).click()
        modal = page.get_by_role("dialog")
        expect(modal.locator(".source-verification")).to_have_attribute("aria-label", "来源账号核验状态")
        expect(modal.locator(".source-verification")).to_contain_text("待核验")
        page.keyboard.press("Escape"); expect(modal).to_have_count(0)
        before_url = page.url
        page.get_by_role("link", name="跳转到内容", exact=True).focus(); page.keyboard.press("Enter")
        assert page.url == before_url and page.evaluate("document.activeElement.id") == "main"
        checks.extend(["news_revision_drawer", "saved_account_trust_on_material", "skip_link_preserves_route"])

        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(base + "/#overview")
        expect(page.locator(".map-prefecture")).to_have_count(47)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "Mobile page must not overflow horizontally"
        page.screenshot(path=str(args.artifacts / "japan-map-mobile.png"), full_page=True)
        checks.append("mobile_no_horizontal_overflow")
        assert not errors, errors
        (args.artifacts / "results.json").write_text(json.dumps({"passed": checks, "page_errors": errors, "note": "Only isolated synthetic fixtures; no live social collection or provider calls."}, ensure_ascii=False, indent=2), encoding="utf-8")
        browser.close()
    print(f"PASS {len(checks)} browser product interaction checks")


if __name__ == "__main__":
    main()
