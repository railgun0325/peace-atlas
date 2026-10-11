"""Real browser and HTTP acceptance; only the session check is a fixture.

Use an isolated server with run_worker=False. Research is queued through the
real validated API and inspected without opening an external platform.
"""
import argparse
import json
import re
from pathlib import Path
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright, expect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--artifacts', type=Path, required=True)
    args = parser.parse_args()
    base = args.base_url.rstrip('/')
    assert base.startswith('http://127.0.0.1:')
    args.artifacts.mkdir(parents=True, exist_ok=True)

    def api(path, body=None, method=None):
        request = Request(base + '/api/v2/' + path, data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
            method=method, headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=15) as response:
            return json.load(response)

    root = api('accounts', {'name': '组织关系回归 Root', 'handle': 'orgroot', 'platform': 'twitter', 'url': 'https://x.com/orgroot'})
    west = api('accounts', {'name': '组织关系回归 West', 'handle': 'orgwest', 'platform': 'twitter', 'url': 'https://x.com/orgwest'})
    api('items/import', {'items': [{'type': 'social', 'account_id': root['id'], 'title': '公开联系回归', 'content': '公開活動 @orgwest',
        'url': 'https://x.com/orgroot/status/301', 'published_at': '2026-10-05T00:00:00Z'}]})
    checks, errors = [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=pw.chromium.executable_path, headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base + '/#network?registry=1&query=组织关系回归&account=twitter:orgroot')
        expect(page.get_by_role('heading', name='账号关系图', exact=True)).to_be_visible()
        expect(page.locator('.nav-link.active')).to_have_attribute('data-route', 'connections')
        assert page.url.split('#')[1].startswith('connections?')
        expect(page.locator('.network-node')).to_have_count(2)
        checks.append('legacy_link_resolves_to_independent_sidebar_route')

        node = page.locator('.network-node[data-key="twitter:orgroot"]')
        node.focus(); page.keyboard.press('ArrowRight')
        pinned = node.get_attribute('transform')
        page.get_by_role('button', name='放大', exact=True).click()
        viewport = page.locator('.interactive-svg > g').get_attribute('transform')
        # A new account and observed post appear while this graph stays open.
        extra = api('accounts', {'name': '组织关系回归 Third', 'handle': 'orgthird', 'platform': 'twitter', 'url': 'https://x.com/orgthird'})
        api('items/import', {'items': [{'type': 'social', 'account_id': root['id'], 'title': '新增关系', 'content': '公開活動 @orgthird',
            'url': 'https://x.com/orgroot/status/302', 'published_at': '2026-10-05T01:00:00Z'}]})
        expect(page.locator('.network-node')).to_have_count(3, timeout=12000)
        expect(page.locator('.network-edge')).to_have_count(2)
        assert node.get_attribute('transform') == pinned
        assert page.locator('.interactive-svg > g').get_attribute('transform') == viewport
        # Native capture loss must clear both the held constraint and the outer
        # refresh guard; otherwise newly saved evidence never refreshes again.
        node.evaluate('n => n.addEventListener("pointerdown",e=>n.dataset.testPointerId=e.pointerId,{once:true})')
        dot = node.locator('.network-node-dot')
        box = dot.bounding_box()
        page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2)
        page.mouse.down()
        # A real pointer event commits capture without moving the pinned node;
        # an intentional +1px drag would correctly change its pinned position.
        page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2)
        page.wait_for_timeout(100)
        assert node.evaluate('n => n.hasPointerCapture(Number(n.dataset.testPointerId))')
        node.evaluate('n => n.releasePointerCapture(Number(n.dataset.testPointerId))')
        page.wait_for_timeout(100)
        assert page.locator('.interactive-network').get_attribute('data-layout-state') not in ['dragging','held-stable']
        page.mouse.up()
        api('accounts', {'name':'组织关系回归 Capture','handle':'orgcapture','platform':'twitter','url':'https://x.com/orgcapture'})
        expect(page.locator('.network-node')).to_have_count(4,timeout=12000)
        assert node.get_attribute('transform') == pinned
        assert page.locator('.interactive-svg > g').get_attribute('transform') == viewport
        checks.append('automatic_new_evidence_refresh_preserves_pinned_node_and_zoom')

        node.focus(); page.keyboard.press('Enter')
        page.get_by_role('button', name='编辑账号', exact=True).click()
        modal = page.locator('dialog')
        modal.get_by_label(re.compile('^显示名称')).fill('组织关系回归 Root 修改')
        modal.get_by_label('所属组织', exact=True).fill('人工资料记录')
        modal.get_by_label('标签', exact=True).fill('人工记录, 回归')
        modal.get_by_role('button', name='保存修改', exact=True).click()
        expect(modal).to_have_count(0)
        expect(page.locator('.network-inspector h2')).to_have_text('组织关系回归 Root 修改')
        assert node.get_attribute('transform') == pinned
        assert page.locator('.interactive-svg > g').get_attribute('transform') == viewport
        saved = next(row for row in api('accounts?page_size=200')['items'] if row['id'] == root['id'])
        assert saved['organization'] == '人工资料记录' and saved['tags'] == ['人工记录', '回归']
        expect(page.locator('.network-edge')).to_have_count(2)
        checks.append('edit_keeps_identity_evidence_edges_and_layout')
        page.screenshot(path=str(args.artifacts / 'connections-edited.png'), full_page=True)

        # The fixture makes only session readiness deterministic; job POST and
        # stored parameters continue through the actual backend validators.
        page.route('**/api/v2/social', lambda route: route.fulfill(json={'runtime': {'ready': True}, 'platforms': [
            {'id': 'twitter', 'modes': ['keyword', 'account'], 'session_available': True, 'state': 'ready'}]}))
        def job_route(route):
            if route.request.method == 'POST' and route.request.post_data_json.get('kind') == 'login':
                route.fulfill(status=202, json={'id': 'fixture-session-check'})
            else:
                route.continue_()
        page.route('**/api/v2/jobs', job_route)
        page.route('**/api/v2/jobs/fixture-session-check', lambda route: route.fulfill(json={'id': 'fixture-session-check',
            'status': 'succeeded', 'result': {'verified': True, 'requires_login': False}}))
        page.get_by_role('link', name='探索此账号', exact=True).click()
        modal = page.locator('dialog.collection-compose')
        expect(modal.get_by_label('公开账号用户名', exact=True)).to_have_value('orgroot')
        expect(modal.get_by_label('研究起点', exact=True)).to_have_value('account')
        modal.get_by_label('限定主题的关键词（可选）', exact=True).fill('')
        modal.get_by_label('其他研究起点（可选）', exact=True).fill('https://x.com/orgwest/')
        create = modal.get_by_role('button', name='开始深度研究', exact=True)
        expect(create).to_be_enabled()
        create.click()
        expect(modal).to_have_count(0)
        records = api('jobs')['items']
        job = next(row for row in records if row['kind'] == 'research' and row['params'].get('handle') == 'orgroot')
        assert job['status'] == 'queued' and job['params']['mode'] == 'account' and job['params']['keyword'] == ''
        assert job['params']['seed_accounts'] == ['https://x.com/orgwest/']
        assert 'twitter' in job['params']['seed_sources']
        checks.append('selected_account_and_extra_roots_reach_real_validated_queue_without_hidden_topic')
        assert not errors, errors
        checks.append('no_page_errors')
        context.close(); browser.close()
    (args.artifacts / 'results.json').write_text(json.dumps({'ok': True, 'checks': checks, 'page_errors': errors,
        'boundary': 'real browser and API; isolated profile; fixture session check; queued job not executed'}, ensure_ascii=False, indent=2), 'utf-8')
    print(f'{len(checks)} account relationship browser groups passed')


if __name__ == '__main__':
    main()
