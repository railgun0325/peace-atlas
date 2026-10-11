"""Offline DOM acceptance of the actual discovery tree, graph and claim review.

Fixtures use the store's public run/nodes/edges/items/claims shape. All API calls
are intercepted; no platform, model, account or real database writes. Optional
output writes only explicitly requested QA artifacts.
"""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    args = parser.parse_args()
    artifacts = Path(args.output).resolve() if args.output else None
    if artifacts:
        artifacts.mkdir(parents=True, exist_ok=True)
    unsafe = '<img src=x onerror="window.__unsafe=1">'
    root = {"account_key": "twitter:public_root", "account_id": "root-id", "platform": "twitter", "handle": "public_root", "name": "公开研究账号", "depth": 0, "status": "visited", "parent_key": None, "discovery_reason": "关键词材料中的作者", "account_status": "candidate", "evidence_urls": ["https://x.com/public_root/status/123"], "url": "https://x.com/public_root"}
    child = {"account_key": "twitter:public_child", "account_id": "child-id", "platform": "twitter", "handle": "public_child", "name": "公开关联账号", "depth": 1, "status": "visited", "parent_key": root["account_key"], "discovery_reason": "通过公开关注关系发现", "account_status": "candidate", "evidence_urls": ["https://x.com/public_root/following"], "url": "https://x.com/public_child"}
    instagram = {"account_key": "instagram:public_ig", "account_id": "ig-id", "platform": "instagram", "handle": "public_ig", "name": unsafe, "depth": 2, "status": "skipped", "parent_key": child["account_key"], "discovery_reason": unsafe, "account_status": "candidate", "evidence_urls": ["javascript:alert(1)"], "url": "data:text/html,bad"}
    root["account_status"] = "verified"
    child["account_status"] = "excluded"
    graph = {"run": {"id": "run-public", "job_id": "research-job", "status": "partial", "params": {"keyword": "反戦, 九条", "research_goal": "追踪公开账号之间的活动联系", "start_date": "2026-09-01", "end_date": "2026-10-06", "depth": 2}, "analysis": {"ai": {"requested": True, "used": False}, "decisions": [{"action": "展开相关作者", "reason": "正文与当前主题相关", "method": "rules", "outcome": "visited"}, {"action": "停止无关分支", "reason": unsafe, "method": "rules", "outcome": "skipped"}]}, "diagnostics": [{"message": "部分来源无法访问"}], "summary": {"accounts": 3}}, "nodes": [root, child, instagram], "edges": [{"id": "edge-follow", "source_key": root["account_key"], "target_key": child["account_key"], "relationship": "follows", "evidence_urls": ["https://x.com/public_root/following"]}, {"id": "edge-mention", "source_key": child["account_key"], "target_key": instagram["account_key"], "relationship": "mentions", "evidence_urls": ["https://x.com/public_child/status/456"]}], "items": [{"id": "post-1", "account_id": "root-id", "title": "原始公开材料", "content": "这是一条反战活动的公开原帖。", "url": "https://x.com/public_root/status/123", "published_at": "2026-09-30T05:00:00Z"}, {"id": "post-2", "account_id": "ig-id", "title": unsafe, "content": unsafe, "url": "javascript:alert(1)", "published_at": None}], "claims": [{"id": "claim-explicit", "subject_key": root["account_key"], "object_label": "公开团体", "statement": "主页中的自述提供组织关系线索，尚需复核。", "basis": "reported", "status": "candidate", "evidence_urls": []}, {"id": "claim-inferred", "subject_key": child["account_key"], "object_label": unsafe, "statement": unsafe, "basis": "inferred", "status": "candidate", "evidence_urls": []}], "coverage": "仅包含本次实际访问的公开资料。"}
    graph["run"]["params"].update(max_accounts=12, max_edges=60, max_records=50, time_budget_seconds=1800, max_scrolls=10)
    graph["run"]["frontier"] = [{"account_key": "twitter:public_pending"}]
    graph["run"]["summary"].update(deferred_frontier_version=1, pending_work_version=1, deferred_count=1)
    state = {"reads": 0, "reviews": [], "review_error": False, "resume_error": True, "resumes": 0, "cancels": 0}
    errors = []
    html = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><link rel="stylesheet" href="/assets/styles.css"><link rel="stylesheet" href="/assets/typography.css"><link rel="stylesheet" href="/assets/features.css"></head><body><main id="main"></main><div id="notifications"></div>
      <script type="module">import {renderNetwork} from '/assets/network.js';window.__polls=[];window.__pollDelays=[];window.__navigation=[];
      const ctx={params:new URLSearchParams('run=run-public'),signal:new AbortController().signal,navigate:(...args)=>window.__navigation.push(args),refresh(){},poll:(task,delay)=>{window.__polls.push(task);window.__pollDelays.push(delay);}};
      document.getElementById('main').replaceChildren(await renderNetwork(ctx));</script></body></html>"""

    def route(request):
        path = urlsplit(request.request.url).path
        method = request.request.method
        if path == "/":
            return request.fulfill(content_type="text/html", body=html)
        if path.startswith("/assets/"):
            file = ROOT / path.removeprefix("/assets/")
            assert file.resolve().is_relative_to(ROOT)
            return request.fulfill(content_type={".css": "text/css", ".woff2": "font/woff2", ".json": "application/json", ".txt": "text/plain"}.get(file.suffix, "text/javascript"), body=file.read_bytes())
        if path == "/api/v2/research/run-public":
            state["reads"] += 1
            data = graph
        elif path.endswith("/review") and method == "POST":
            payload = request.request.post_data_json
            state["reviews"].append(payload)
            if state["review_error"] or payload["status"] == "confirmed" and not payload["evidence_url"].startswith("https://"):
                return request.fulfill(status=422, content_type="application/json", body=json.dumps({"error": {"message": "公开证据暂不能确认，请保留输入后复查。"}}))
            claim = next(value for value in graph["claims"] if value["id"] == path.split("/")[6])
            claim.update(status=payload["status"], review_note=payload["note"], review_evidence_url=payload["evidence_url"])
            data = {"saved": True}
        elif path == "/api/v2/capabilities":
            data = {"research": {"available": True, "max_accounts": 40, "max_edges": 200, "max_records": 10000, "time_budget_seconds": 86400}}
        elif path == "/api/v2/research/run-public/continue" and method == "POST":
            assert set(request.request.post_data_json) == {"max_accounts", "max_edges", "max_records", "time_budget_seconds"}
            state["resumes"] += 1
            if state["resume_error"]:
                return request.fulfill(status=409, content_type="application/json", body=json.dumps({"error": {"message": "当前研究暂时无法继续"}}))
            data = {"id": "resumed-job"}
        elif path == "/api/v2/jobs/research-job/cancel" and method == "POST":
            state["cancels"] += 1
            graph["run"]["status"] = "partial"
            data = {"cancel_requested": True}
        elif path == "/api/v2/drafts/collection":
            data = {"draft": request.request.post_data_json if method == "PUT" else None}
        elif path == "/api/v2/social":
            data = {"runtime": {"ready": False}, "platforms": []}
        else:
            return request.fulfill(status=404, content_type="application/json", body=json.dumps({"error": {"message": "Unexpected test request: " + path}}))
        request.fulfill(content_type="application/json", body=json.dumps(data, ensure_ascii=False))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=playwright.chromium.executable_path, headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.route("**/*", route)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto("http://network.test/")
        expect(page.get_by_role("heading", name="研究结果", exact=True)).to_be_visible()
        expect(page.locator(".research-stats strong").nth(0)).to_have_text("3")
        expect(page.locator(".research-stats strong").nth(1)).to_have_text("2")
        expect(page.locator(".research-stats strong").nth(2)).to_have_text("2")
        expect(page.locator(".tree-account")).to_have_count(3)
        page.locator(".tree-account").filter(has_text="公开关联账号").click()
        expect(page.get_by_role("complementary", name="账号与证据详情")).to_contain_text("上一层：twitter:public_root")
        expect(page.get_by_role("complementary", name="账号与证据详情").get_by_role("link", name="访问公开主页", exact=True)).to_have_attribute("href", "https://x.com/public_child")
        page.get_by_role("button", name="关系网络", exact=True).click()
        expect(page.locator(".network-svg")).to_be_visible()
        # A manual exclusion now hides its node and incident edges in the graph;
        # the saved discovery history and underlying public fixture are retained.
        expect(page.locator(".network-node")).to_have_count(2)
        expect(page.locator(".network-edge")).to_have_count(0)
        expect(page.locator('[data-key="twitter:public_child"]')).to_have_count(0)
        expect(page.locator('.graph-relevance-note')).to_contain_text('人工排除 1')
        expect(page.locator('.network-inspector')).to_contain_text('已排除')
        child['account_status'] = 'candidate'
        page.reload()
        page.get_by_role("button", name="关系网络", exact=True).click()
        expect(page.locator(".network-node")).to_have_count(3)
        expect(page.locator(".network-edge")).to_have_count(2)
        if artifacts:
            (artifacts / "graph-account-statuses.json").write_text(json.dumps({"nodeLabels": page.locator(".network-node").evaluate_all("nodes => nodes.map(node => ({key: node.dataset.key, label: node.getAttribute('aria-label')}))"), "pageErrors": errors}, ensure_ascii=False, indent=2), "utf-8")
            page.screenshot(path=str(artifacts / "graph-account-statuses.png"), full_page=True)
        expect(page.locator('[data-key="twitter:public_root"]')).to_have_attribute("aria-label", "公开研究账号，未判定，已核验，外围结构。回车查看，方向键移动并固定，双击固定或释放。")
        expect(page.locator('[data-key="twitter:public_child"]')).to_have_attribute("aria-label", "公开关联账号，未判定，候选账号，桥接结构。回车查看，方向键移动并固定，双击固定或释放。")
        expect(page.locator('[data-key="instagram:public_ig"]')).to_have_attribute("aria-label", unsafe + "，未判定，候选账号，外围结构。回车查看，方向键移动并固定，双击固定或释放。")
        page.locator(".network-edge").first.focus()
        page.keyboard.press("Enter")
        expect(page.get_by_role("complementary", name="账号与证据详情")).to_contain_text("关注和共同材料本身不能确认组织隶属")
        expect(page.get_by_role("complementary", name="账号与证据详情").get_by_role("link", name="公开证据 1", exact=True)).to_have_attribute("href", "https://x.com/public_root/following")
        page.get_by_role("combobox", name="筛选平台", exact=True).select_option("instagram")
        expect(page.locator(".network-node")).to_have_count(1)
        expect(page.locator(".network-edge")).to_have_count(0)
        page.get_by_role("button", name="发现树", exact=True).click()
        expect(page.locator(".discovery-tree")).to_contain_text("在当前筛选范围外")
        page.get_by_role("combobox", name="筛选平台", exact=True).select_option("")
        page.get_by_role("combobox", name="筛选关系类型", exact=True).select_option("follows")
        expect(page.locator(".tree-account")).to_have_count(2)
        page.get_by_role("combobox", name="筛选关系类型", exact=True).select_option("")
        page.get_by_role("combobox", name="显示层级", exact=True).select_option("0")
        expect(page.locator(".tree-account")).to_have_count(1)
        page.get_by_role("combobox", name="显示层级", exact=True).select_option("3")
        page.get_by_role("searchbox", name="查找网络中的账号", exact=True).fill("public_child")
        expect(page.locator(".tree-account")).to_have_count(1)
        page.get_by_role("searchbox", name="查找网络中的账号", exact=True).fill("")
        assert page.locator("a[href^='javascript:'],a[href^='data:']").count() == 0
        assert page.locator("img").count() == 0 and page.evaluate("window.__unsafe === undefined")
        print("PASS store-shaped discovery tree/network, platform/relationship/depth/search filters, keyboard edges and public evidence anchors")

        page.get_by_role("button", name="确认证据", exact=True).first.click()
        modal = page.get_by_role("dialog")
        expect(modal.locator("[name=evidence_url]")).to_have_attribute("required", "true")
        expect(modal.locator("[name=note]")).to_have_attribute("maxlength", "1600")
        before_invalid_review = len(state["reviews"])
        modal.get_by_role("button", name="保存人工确认", exact=True).click()
        assert modal.locator("[name=evidence_url]").evaluate("node => node.validity.valueMissing")
        assert len(state["reviews"]) == before_invalid_review, "Missing required evidence must not reach the review API"
        modal.locator("[name=evidence_url]").fill("not a valid URL")
        modal.get_by_role("button", name="保存人工确认", exact=True).click()
        assert modal.locator("[name=evidence_url]").evaluate("node => node.validity.typeMismatch")
        assert len(state["reviews"]) == before_invalid_review, "Malformed evidence URLs must be blocked before the API"
        modal.locator("[name=evidence_url]").fill("https://example.org/public-affiliation")
        modal.locator("[name=note]").fill("公开来源中的具体自述与账号主体一致。")
        state["review_error"] = True
        modal.get_by_role("button", name="保存人工确认", exact=True).click()
        expect(modal.get_by_role("alert")).to_contain_text("公开证据暂不能确认")
        expect(modal.locator("[name=evidence_url]")).to_have_value("https://example.org/public-affiliation")
        expect(modal.locator("[name=note]")).to_have_value("公开来源中的具体自述与账号主体一致。")
        expect(modal.get_by_role("button", name="保存人工确认", exact=True)).to_be_enabled()
        state["review_error"] = False
        modal.get_by_role("button", name="保存人工确认", exact=True).click()
        expect(modal).to_have_count(0)
        expect(page.locator(".research-claim").first).to_contain_text("人工确认")
        expect(page.get_by_role("link", name="人工复核证据", exact=True)).to_have_attribute("href", "https://example.org/public-affiliation")
        page.get_by_role("button", name="排除此判断", exact=True).last.click()
        modal.locator("[name=note]").fill("没有足够的公开证据支持这个归属判断。")
        state["review_error"] = True
        modal.get_by_role("button", name="保存排除结论", exact=True).click()
        expect(modal.get_by_role("alert")).to_contain_text("公开证据暂不能确认")
        expect(modal.locator("[name=note]")).to_have_value("没有足够的公开证据支持这个归属判断。")
        state["review_error"] = False
        modal.get_by_role("button", name="保存排除结论", exact=True).click()
        expect(modal).to_have_count(0)
        expect(page.locator(".research-claim").last).to_contain_text("已排除")
        assert state["reviews"][-1]["status"] == "rejected"
        print("PASS explicit evidence and note fields, confirmation/rejection errors preserve inputs, saved review evidence and honest candidate states")

        resume = page.get_by_role("button", name="继续研究", exact=True)
        resume.click()
        continuation = page.get_by_role("dialog", name="继续研究", exact=True)
        expect(continuation.locator('[name="max_accounts"]')).to_have_attribute("max", "40")
        continuation.get_by_role("button", name="开始继续研究", exact=True).click()
        expect(continuation.get_by_role("alert")).to_contain_text("当前研究暂时无法继续")
        expect(continuation.get_by_role("button", name="开始继续研究", exact=True)).to_be_enabled()
        state["resume_error"] = False
        continuation.get_by_role("button", name="开始继续研究", exact=True).click()
        page.wait_for_function("window.__navigation.length === 1")
        assert page.evaluate("window.__navigation.at(-1)") == ["jobs", {"selected": "resumed-job"}]
        assert state["resumes"] == 2
        graph["run"]["status"] = "running"
        page.reload()
        expect(page.get_by_role("button", name="停止研究", exact=True)).to_be_visible()
        before = state["reads"]
        page.evaluate("async () => {await Promise.all(window.__polls.map(task=>task()));}")
        assert state["reads"] > before and page.evaluate("window.__pollDelays") == [5000]
        page.get_by_role("button", name="停止研究", exact=True).click()
        expect(page.get_by_role("button", name="继续研究", exact=True)).to_be_visible()
        assert state["cancels"] == 1
        before = state["reads"]
        page.evaluate("async () => {await Promise.all(window.__polls.map(task=>task()));}")
        assert state["reads"] == before, "Terminal research should not continue polling the graph endpoint"
        for stop_reason in ["record_budget", "account_budget", "edge_budget"]:
            graph["run"]["summary"]["stop_reason"] = stop_reason
            page.reload()
            expect(page.get_by_role("button", name="继续研究", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="从断点继续", exact=True)).to_have_count(0)
            expect(page.locator(".frontier-recovery-status")).to_contain_text("待准入账号 1")
            before_budget_resume = state["resumes"]
            page.get_by_role("button", name="继续研究", exact=True).click()
            continuation = page.get_by_role("dialog", name="继续研究", exact=True)
            expect(continuation.locator('[name="max_accounts"]')).to_have_attribute("max", "40")
            expect(continuation.get_by_role("button", name="开始继续研究", exact=True)).to_be_disabled()
            continuation.get_by_role("button", name="取消", exact=True).click()
            expect(page.get_by_role("dialog")).to_have_count(0)
            assert state["resumes"] == before_budget_resume
        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_role("button", name="关系网络", exact=True).click()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Research graph view overflows narrow viewport"
        assert page.locator("img").count() == 0 and page.evaluate("window.__unsafe === undefined")
        print("PASS scope-preserving continuation/error recovery, active-only polling, saved frontier budget expansion, 390px containment and inert untrusted text")

        graph.update(total_nodes=30, total_edges=50, total_items=3000, items_truncated=True)
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.reload()
        expect(page.locator(".research-stats strong").nth(0)).to_have_text("30")
        expect(page.locator(".research-stats strong").nth(1)).to_have_text("50")
        expect(page.locator(".research-stats strong").nth(2)).to_have_text("3,000")
        materials = page.locator(".research-sources")
        expect(materials.locator("summary")).to_have_text("本次保存的材料 · 共 3,000 条 / 当前显示 2 条")
        expect(materials.locator("summary")).not_to_contain_text("全部")
        materials.locator("summary").click()
        expect(materials.locator(".material-coverage")).to_be_visible()
        expect(materials.locator(".material-coverage")).to_contain_text("显示部分材料")
        expect(materials.locator(".material-coverage")).to_contain_text("实际保存记录")
        expect(materials.get_by_role("link", name="到资料库查看材料", exact=True)).to_have_attribute("href", "#library")
        expect(materials.locator("ol > li")).to_have_count(2)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Large-total material coverage overflows narrow viewport"
        assert not errors, errors
        browser.close()
        print("PASS full saved counts override displayed graph slices; 3,000 saved / 2 shown is explicit and remaining material links to the paginated library")
        print("4 network integration groups passed")


if __name__ == "__main__":
    main()
