"""Real Chromium node contours and gestures against disposable public fixtures.

Serves current product assets in intercepted synthetic pages only. No production
backend, profile, browser session, source acquisition or model request is used.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://contours.test'
HTML = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/assets/styles.css"><link rel="stylesheet" href="/assets/features.css"><link rel="stylesheet" href="/assets/typography.css"></head><body><main id="main"></main><div id="notifications"></div><script type="module">
import {renderNetwork} from '/assets/network.js';window.__navigation=[];
const ctx={params:new URLSearchParams(location.search),signal:new AbortController().signal,navigate:(...args)=>window.__navigation.push(args),refresh(){},poll(){}};
document.getElementById('main').replaceChildren(await renderNetwork(ctx));
</script></body></html>'''


def fixture():
    types = [('organization', '组织'), ('individual', '个人'), ('unknown', '未判定')]
    nodes = [{'account_key': f'twitter:{kind}_{status}', 'account_id': f'{kind}_{status}',
              'platform': 'twitter', 'handle': f'{kind}_{status}', 'name': label + (' · 已核验' if status == 'verified' else ' · 候选'),
              'account_type': kind, 'account_status': status, 'depth': 0, 'url': f'https://x.com/{kind}_{status}'}
             for kind, label in types for status in ['candidate', 'verified']]
    nodes.extend([{'account_key': 'twitter:' + key, 'account_id': key, 'platform': 'twitter', 'handle': key,
                   'name': key, 'account_type': 'organization', 'account_status': 'excluded' if key == 'manual_excluded' else 'candidate',
                   'depth': 0, 'url': 'https://x.com/' + key} for key in ['youtube', 'youtube_japan', 'manual_excluded']])
    keys = [node['account_key'] for node in nodes]
    pairs = [(keys[0], key) for key in keys[1:4]] + [(keys[2], keys[4]), (keys[4], keys[5]),
        (keys[0], keys[6]), (keys[6], keys[7]), (keys[0], keys[8])]
    edges = [{'id': 'edge-' + str(i), 'source_key': a, 'target_key': b, 'relationship': 'follows',
              'evidence_urls': ['https://example.org/contours/' + str(i)]} for i, (a, b) in enumerate(pairs)]
    return {'nodes': nodes, 'edges': edges, 'claims': [], 'items': [], 'diagnostics': [], 'coverage_metadata': {}, 'total_nodes': len(nodes), 'total_edges': len(edges),
            'coverage': '合成夹具，仅验类型轮廓、颜色和交互；不代表真实账号结论。',
            'run': {'id': 'contour-run', 'job_id': 'fixture-job', 'status': 'succeeded',
                    'params': {'research_goal': '公开关系核对', 'depth': 3}, 'summary': {}, 'analysis': {}}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--baseline', action='store_true', help='Collect the pre-fix counterexample before failing.')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    graph = fixture()
    original = hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest()
    errors, external, requests, cases, violations, appearances = [], [], [], [], [], []
    hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
              for name in ['interactive_graph.js', 'features.css', 'network_model.js', 'graph_layout.js', 'network.js', 'registry.js']}

    def route(r):
        parsed = urlsplit(r.request.url)
        if parsed.netloc != 'contours.test':
            external.append(r.request.url)
            return r.abort()
        path = parsed.path
        if path == '/':
            return r.fulfill(content_type='text/html', body=HTML)
        if path.startswith('/assets/'):
            file = (ROOT / path.removeprefix('/assets/')).resolve()
            assert file.is_relative_to(ROOT)
            return r.fulfill(content_type={'.css': 'text/css', '.woff2': 'font/woff2'}.get(file.suffix, 'text/javascript'), body=file.read_bytes())
        requests.append({'method': r.request.method, 'path': path})
        if r.request.method == 'GET' and path in ['/api/v2/accounts/network', '/api/v2/research/contour-run']:
            return r.fulfill(content_type='application/json', body=json.dumps(copy.deepcopy(graph), ensure_ascii=False))
        return r.fulfill(status=404, content_type='application/json', body=json.dumps({'error': {'message': 'Unexpected fixture request: ' + path}}))

    def positions(page):
        return page.locator('.network-node').evaluate_all('''nodes => Object.fromEntries(nodes.map(n => {
          const m=n.transform.baseVal.getItem(0).matrix,p=n.ownerSVGElement.createSVGPoint(),s=p.matrixTransform(n.getScreenCTM());
          return [n.dataset.key,{x:m.e,y:m.f,sx:s.x,sy:s.y,pinned:n.dataset.pinned}];
        }))''')

    def appearance(page):
        return page.locator('.network-node').evaluate_all('''nodes => nodes.map(n => {
          const dot=n.querySelector('.network-node-dot'),halo=n.querySelector('.network-node-halo');
          return {key:n.dataset.key,type:n.dataset.accountType,shape:dot.tagName,halo:halo.tagName,fill:getComputedStyle(dot).fill,
            verified:dot.classList.contains('verified'),check:n.querySelectorAll('.network-node-verification-mark').length,aria:n.getAttribute('aria-label')};
        })''')

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
        context.route('**/*', route)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(error.stack or str(error)))
        try:
            for entry, query in [('registry', 'registry=1'), ('research', 'run=contour-run')]:
                page.goto(BASE + '/?' + query)
                if entry == 'research':
                    page.get_by_role('button', name='关系网络', exact=True).click()
                expect(page.locator('.network-node')).to_have_count(7)
                page.locator('.interactive-svg').scroll_into_view_if_needed()
                observed = appearance(page)
                appearances.append({'entry': entry, 'nodes': observed})
                expected = {'organization': ('rect', 'rgb(73, 109, 93)'), 'individual': ('circle', 'rgb(155, 102, 52)'), 'unknown': ('path', 'rgb(108, 118, 134)')}
                for node in observed:
                    shape, fill = expected[node['type']]
                    if node['shape'] != shape or node['halo'] != shape or node['fill'] != fill:
                        violations.append({'entry': entry, 'node': node, 'expected': {'shape': shape, 'fill': fill}})
                    if node['check'] != int(node['verified']):
                        violations.append({'entry': entry, 'verification_mark': node})
                for view, size in [('desktop', {'width': 1440, 'height': 1000}), ('small', {'width': 390, 'height': 844})]:
                    page.set_viewport_size(size)
                    page.locator('.interactive-svg').scroll_into_view_if_needed()
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    page.screenshot(path=str(args.output / f'{entry}-{view}.png'), full_page=True)
                    page.locator('.interactive-network').screenshot(path=str(args.output / f'{entry}-{view}-graph.png'))
                    cases.append({'name': entry + '_' + view + '_type_contours_and_colours', 'ok': not violations})
                if args.baseline:
                    page.set_viewport_size({'width': 1440, 'height': 1000})
                    continue
                assert not violations, violations
                # The existing 940-unit canvas fits the whole graph on small
                # screens. Its ordinary zoom control must still make every
                # full contour inspectable without horizontal page overflow.
                for _ in range(3):
                    page.get_by_role('button', name='放大', exact=True).click()
                for kind in expected:
                    node = page.locator(f'.network-node[data-key="twitter:{kind}_candidate"]')
                    node.locator('.network-node-dot').hover()
                    assert node.locator('.network-node-dot').evaluate('n=>getComputedStyle(n).fill') == expected[kind][1]
                page.locator('.interactive-network').screenshot(path=str(args.output / f'{entry}-small-zoomed-graph.png'))
                page.get_by_label('关注方向', exact=True).select_option('out')
                page.get_by_label('关系跳数', exact=True).select_option('2')
                page.get_by_label('显示通用账号', exact=True).check()
                expect(page.locator('.network-node[data-key="twitter:youtube"]')).to_have_count(1)
                page.get_by_label('显示通用账号', exact=True).uncheck()
                page.get_by_label('关注方向', exact=True).select_option('all')
                page.get_by_label('关系跳数', exact=True).select_option('')
                page.locator('.interactive-svg').scroll_into_view_if_needed()
                held = positions(page)['twitter:unknown_candidate']
                page.mouse.move(held['sx'], held['sy']); page.mouse.down()
                page.mouse.move(held['sx'] + 18, held['sy'] + 12, steps=6)
                after = positions(page)['twitter:unknown_candidate']
                assert abs(after['sx'] - held['sx'] - 18) < 3 and abs(after['sy'] - held['sy'] - 12) < 3
                page.mouse.up()
                page.wait_for_function("()=>document.querySelector('.interactive-network')?.dataset.layoutState==='stable'", timeout=45000)
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                cases.append({'name': entry + '_small_zoom_hover_drag_and_filter', 'ok': True})
                page.set_viewport_size({'width': 1440, 'height': 1000})
                page.get_by_role('button', name='适应画布', exact=True).click()
                for kind in expected:
                    node = page.locator(f'.network-node[data-key="twitter:{kind}_candidate"]')
                    node.locator('.network-node-dot').hover()
                    expect(node).to_have_class(__import__('re').compile(r'.*highlighted.*'))
                    assert node.locator('.network-node-dot').evaluate('n=>getComputedStyle(n).fill') == expected[kind][1]
                    assert page.locator('.network-edge.highlighted').count() > 0
                    node.focus(); page.keyboard.press('Enter')
                    expect(node).to_have_attribute('aria-pressed', 'true')
                    assert node.locator('.network-node-dot').evaluate('n=>getComputedStyle(n).fill') == expected[kind][1]
                    expect(page.locator('.network-inspector')).to_contain_text(node.get_attribute('data-key'))
                    if entry == 'registry':
                        page.get_by_role('button', name='收起详情', exact=True).click()
                    page.locator('.interactive-svg').scroll_into_view_if_needed()
                cases.append({'name': entry + '_hover_and_keyboard_selection_retain_type_colours', 'ok': True})
                before = positions(page)
                viewport = page.locator('.interactive-svg > g').get_attribute('transform')
                page.get_by_label('关注方向', exact=True).select_option('out')
                page.get_by_label('关系跳数', exact=True).select_option('2')
                assert positions(page) == before
                assert page.locator('.interactive-svg > g').get_attribute('transform') == viewport
                page.get_by_label('显示通用账号', exact=True).check()
                expect(page.locator('.network-node[data-key="twitter:youtube"]')).to_have_count(1)
                expect(page.locator('.network-node[data-key="twitter:manual_excluded"]')).to_have_count(0)
                assert all(positions(page)[key] == value for key, value in before.items())
                assert page.locator('.network-node[data-key="twitter:youtube"] .network-node-dot').evaluate('n=>n.tagName') == 'rect'
                page.get_by_label('显示通用账号', exact=True).uncheck()
                expect(page.locator('.network-node')).to_have_count(7)
                page.get_by_label('关注方向', exact=True).select_option('all')
                page.get_by_label('关系跳数', exact=True).select_option('')
                cases.append({'name': entry + '_direction_depth_and_reversible_filter_preserve_positions', 'ok': True})
                # Move each outer shape: hit handling, shared springs, release and idle stop remain real.
                for kind in expected:
                    page.locator('.interactive-svg').scroll_into_view_if_needed()
                    key = f'twitter:{kind}_candidate'
                    held = positions(page)[key]
                    inspector_before = page.locator('.network-inspector').inner_text()
                    page.mouse.move(held['sx'], held['sy']); page.mouse.down()
                    steps = int(page.locator('.interactive-network').get_attribute('data-layout-steps'))
                    page.mouse.move(held['sx'] + 42, held['sy'] + 25, steps=8)
                    after = positions(page)
                    assert abs(after[key]['sx'] - held['sx'] - 42) < 3
                    assert abs(after[key]['sy'] - held['sy'] - 25) < 3
                    assert any(abs(after[k]['x'] - held_position['x']) > .1 for k, held_position in before.items() if k != key)
                    assert int(page.locator('.interactive-network').get_attribute('data-layout-steps')) > steps
                    page.mouse.up()
                    page.wait_for_function("()=>document.querySelector('.interactive-network')?.dataset.layoutState==='stable'", timeout=45000)
                    stable = positions(page)
                    stable_steps = page.locator('.interactive-network').get_attribute('data-layout-steps')
                    page.wait_for_timeout(200)
                    assert positions(page) == stable and page.locator('.interactive-network').get_attribute('data-layout-steps') == stable_steps
                    if entry == 'registry':
                        expect(page.locator('.network-inspector')).to_be_hidden()
                    else:
                        assert page.locator('.network-inspector').inner_text() == inspector_before
                    cases.append({'name': entry + '_' + kind + '_drag_springs_release_and_stable_idle', 'ok': True})
                    before = stable
                page.get_by_role('button', name='开启布局动效', exact=True).click()
                expect(page.get_by_role('button', name='暂停布局动效', exact=True)).to_be_visible()
                page.wait_for_function("()=>document.querySelector('.interactive-network')?.dataset.layoutState==='stable'", timeout=45000)
                cases.append({'name': entry + '_existing_motion_control', 'ok': True})
                page.screenshot(path=str(args.output / f'{entry}-interacted.png'), full_page=True)
            assert not errors, errors
            assert not external, external
            assert all(request['method'] == 'GET' for request in requests)
            assert hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest() == original
            result = {'ok': not violations, 'baseline': args.baseline, 'cases': cases, 'violations': violations,
                      'appearances': appearances, 'pageErrors': errors, 'external_requests': external,
                      'requests': requests, 'fixture_unchanged': True, 'source_hashes': hashes}
            (args.output / 'results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({'ok': result['ok'], 'cases': len(cases), 'violations': len(violations), 'pageErrors': errors}))
            assert not violations, 'Saved old-circle counterexample: ' + str(len(violations)) + ' violations'
        except Exception as error:
            page.screenshot(path=str(args.output / 'failure.png'), full_page=True)
            (args.output / 'failure.json').write_text(json.dumps({'error': str(error), 'pageErrors': errors, 'violations': violations}, ensure_ascii=False, indent=2), encoding='utf-8')
            raise
        finally:
            context.close(); browser.close()


if __name__ == '__main__':
    main()
