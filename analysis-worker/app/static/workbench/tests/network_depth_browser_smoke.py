"""Actual graph controls in a disposable browser with a local fixture server.

No production backend/profile, remote platform, model, credentials or build.
"""
import argparse
import copy
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
HTML = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/assets/styles.css"><link rel="stylesheet" href="/assets/features.css"><link rel="stylesheet" href="/assets/typography.css"></head><body><main id="main"></main><div id="notifications"></div><script type="module">
import {renderNetwork} from '/assets/network.js';window.__navigation=[];window.__polls=[];
const params=new URLSearchParams(location.search),controller=new AbortController();window.__controller=controller;
const ctx={params,signal:controller.signal,navigate:(...args)=>window.__navigation.push(args),refresh(){},poll:(fn)=>window.__polls.push(fn)};
document.getElementById('main').replaceChildren(await renderNetwork(ctx));
</script></body></html>'''


def fixture():
    nodes = [{'account_key': 'twitter:' + key, 'account_id': key, 'platform': 'twitter', 'handle': key,
              'name': '公开账号 ' + key, 'depth': 0, 'account_status': 'candidate', 'url': 'https://x.com/' + key}
             for key in ['a', 'b', 'c', 'd', 'peer', 'y', 'isolated']]
    nodes[0].update(account_type='organization', account_type_details={'basis': 'manual', 'manual_locked': True,
        'evidence_urls': ['https://example.org/official'], 'note': '已核对的公开组织主页'}, account_status='candidate')
    nodes[1].update(account_type='individual', account_type_details={'basis': 'public_profile_statement',
        'evidence_urls': ['https://x.com/b'], 'confidence_scope': '公开主页自述'}, account_status='verified')
    nodes[2].update(account_type='unknown', name='<img src=x onerror="window.__unsafe=1">')
    pairs = [('a', 'b', 'follows'), ('b', 'c', 'follows'), ('c', 'd', 'follows'), ('peer', 'a', 'follows'),
             ('a', 'd', 'mentions'), ('peer', 'y', 'mentions'), ('y', 'a', 'mentions')]
    edges = [{'id': 'edge-' + a + b, 'source_key': 'twitter:' + a, 'target_key': 'twitter:' + b,
              'relationship': relation, 'evidence_urls': ['https://example.org/evidence/' + a + b]}
             for a, b, relation in pairs]
    return {'nodes': nodes, 'edges': edges, 'claims': [], 'items': [], 'total_nodes': 9, 'total_edges': 7,
        'coverage': '合成公开关系夹具；只验前端行为。', 'limited': True, 'coverage_metadata': {}, 'diagnostics': [],
        'run': {'id': 'depth-run', 'job_id': 'fixture-job', 'status': 'succeeded', 'params': {'research_goal': '公开关系路径核对',
            'start_date': '2026-10-01', 'end_date': '2026-10-09', 'depth': 3}, 'summary': {}, 'analysis': {}}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    original = fixture()
    current = copy.deepcopy(original)
    failure_state = {'identity': True, 'sources': True, 'relevance': True}
    original_hash = hashlib.sha256(json.dumps(original, sort_keys=True).encode()).hexdigest()
    requests, errors, external, cases = [], [], [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/':
                body, content_type = HTML.encode(), 'text/html; charset=utf-8'
            elif path.startswith('/assets/'):
                file = (ROOT / unquote(path.removeprefix('/assets/'))).resolve()
                if not file.is_relative_to(ROOT) or not file.is_file():
                    return self.send_error(404)
                body = file.read_bytes()
                content_type = {'.css': 'text/css', '.woff2': 'font/woff2'}.get(file.suffix, 'text/javascript')
            elif path in ['/api/v2/accounts/network', '/api/v2/research/depth-run']:
                requests.append({'method': 'GET', 'path': path})
                body, content_type = json.dumps(copy.deepcopy(current)).encode(), 'application/json'
            else:
                return self.send_error(404)
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def mutation(self, mode):
            path = urlsplit(self.path).path
            payload = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            requests.append({'method': self.command, 'path': path, 'body': payload})
            if path != f'/api/v2/accounts/a/{"verification-sources" if mode == "sources" else mode}':
                return self.send_error(404)
            if failure_state[mode]:
                failure_state[mode] = False
                status, data = 422, {'error': {'message': '模拟保存失败；请保留输入后重试。'}}
            else:
                status, data = 200, {'saved': True}
                node = current['nodes'][0]
                if mode == 'identity':
                    node.update(account_type=payload['type'], account_type_details={'basis': 'manual_review',
                        'manual_locked': payload['manual_locked'], 'note': payload['note'], 'evidence_urls': payload['evidence_urls']})
                elif mode == 'sources':
                    node.setdefault('metadata', {})['verification_sources'] = payload['sources']
                    node['metadata']['verification_schedule'] = {'state': 'queued', 'job_id': 'verification-fixture-job'}
                elif payload['state'] == 'unknown':
                    node.pop('research_relevance', None)
                    node.get('metadata', {}).pop('research_relevance_override', None)
                else:
                    excluded = payload['state'] == 'irrelevant'
                    node.setdefault('metadata', {})['research_relevance_override'] = {'version': 1, 'state': payload['state'],
                        'basis': 'manual_review', 'reason': payload['reason']}
                    node['research_relevance'] = {'state': 'excluded' if excluded else 'included', 'default_hidden': excluded,
                        'skip_expansion': excluded, 'basis': 'manual_review', 'reason': '人工标记研究相关性。 ' + payload['reason'] + ' 原始来源仍保留。'}
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers(); self.wfile.write(body)

        def do_PATCH(self):
            self.mutation('relevance' if urlsplit(self.path).path.endswith('/relevance') else 'identity')

        def do_PUT(self):
            self.mutation('sources')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    page = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
            def guard(route):
                if not route.request.url.startswith(base + '/'):
                    external.append(route.request.url)
                    return route.abort()
                route.continue_()
            context.route('**/*', guard)
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(error.stack or str(error)))
            diagnostics = context.new_cdp_session(page)
            diagnostics.send('Runtime.enable')
            diagnostics.on('Runtime.exceptionThrown', lambda event: errors.append(json.dumps(event['exceptionDetails'])))
            for route_name, query in [('registry', 'registry=1&account=twitter%3Aa'), ('research', 'run=depth-run&account=twitter%3Aa')]:
                page.goto(base + '/?' + query)
                if route_name == 'research':
                    page.get_by_role('button', name='关系网络', exact=True).click()
                expect(page.locator('.network-node')).to_have_count(7)
                expect(page.get_by_label('关系起点', exact=True)).to_be_visible(timeout=2000)
                expect(page.locator('.network-node[data-key="twitter:a"]')).to_have_attribute('data-account-type', 'organization')
                expect(page.locator('.network-node[data-key="twitter:b"]')).to_have_attribute('data-account-type', 'individual')
                expect(page.locator('.network-node[data-key="twitter:c"]')).to_have_attribute('data-account-type', 'unknown')
                expect(page.locator('.network-node[data-key="twitter:a"] .network-node-dot')).not_to_have_class('verified')
                expect(page.locator('.network-node[data-key="twitter:b"] .network-node-dot')).to_have_class('network-node-dot verified')
                assert page.locator('img').count() == 0 and page.evaluate('window.__unsafe===undefined')
                before = page.locator('.interactive-svg > g').get_attribute('transform')
                coordinates = page.locator('.network-node').evaluate_all('nodes=>nodes.map(n=>n.getAttribute("transform"))')
                page.get_by_label('关注方向', exact=True).select_option('out')
                page.get_by_label('关系跳数', exact=True).select_option('2')
                expect(page.locator('.network-node')).to_have_count(7)
                assert page.locator('.network-node:not(.dimmed)').evaluate_all('nodes=>nodes.map(n=>n.dataset.key)') == ['twitter:a', 'twitter:b', 'twitter:c']
                assert page.locator('.interactive-svg > g').get_attribute('transform') == before
                assert page.locator('.network-node').evaluate_all('nodes=>nodes.map(n=>n.getAttribute("transform"))') == coordinates
                page.get_by_label('路径终点', exact=True).select_option('twitter:d')
                expect(page.locator('.graph-path-result')).to_contain_text('未找到')
                page.get_by_label('关系跳数', exact=True).select_option('3')
                expect(page.locator('.graph-path-steps > li')).to_have_count(3)
                expect(page.locator('.graph-path-steps')).to_contain_text('twitter:a → twitter:b')
                expect(page.locator('.graph-path-steps').get_by_role('link', name='公开证据 1').first).to_have_attribute('href', 'https://example.org/evidence/ab')
                page.locator('.graph-path-steps').get_by_role('button').first.click()
                expect(page.locator('.network-inspector')).to_contain_text('twitter:a → twitter:b')
                assert page.locator('.network-edge.path-highlighted').count() == 3
                page.get_by_label('路径终点', exact=True).select_option('')
                page.get_by_label('关注方向', exact=True).select_option('in')
                page.get_by_label('关系跳数', exact=True).select_option('1')
                assert page.locator('.network-node:not(.dimmed)').evaluate_all('nodes=>nodes.map(n=>n.dataset.key)') == ['twitter:a', 'twitter:peer']
                page.get_by_label('关注方向', exact=True).select_option('all')
                page.get_by_label('关系跳数', exact=True).select_option('')
                page.get_by_label('结构层级', exact=True).select_option('bridge')
                assert page.locator('.network-node[data-tier="bridge"]').count() > 0
                expect(page.locator('.graph-structure-note')).to_contain_text('当前显示')
                expect(page.locator('.graph-structure-note')).to_contain_text('不足以')
                page.get_by_label('结构层级', exact=True).select_option('all')
                page.get_by_role('button', name='以起点账号继续研究', exact=True).click()
                assert page.evaluate('window.__navigation.at(-1)') == ['network', {'compose': 'research', 'source': 'twitter', 'handle': 'a'}]
                assert not [request for request in requests if request['method'] != 'GET']
                page.screenshot(path=str(args.output / f'{route_name}-depth-desktop.png'), full_page=True)
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), 'Graph controls overflow 390px viewport'
                page.screenshot(path=str(args.output / f'{route_name}-depth-mobile.png'), full_page=True)
                page.set_viewport_size({'width': 1440, 'height': 1000})
                cases.append({'route': route_name, 'ok': True, 'full_graph_retained': 7, 'positions_and_viewport_preserved': True})
            current['nodes'].extend([
                {'account_key': 'twitter:' + key, 'account_id': key, 'platform': 'twitter', 'handle': key, 'depth': 0,
                 'name': '公开账号 ' + key, 'account_status': 'excluded' if key == 'manually_excluded' else 'candidate', 'url': 'https://x.com/' + key}
                for key in ['youtube', 'youtube_japan', 'manually_excluded']])
            current['edges'].extend([{'id': 'general-' + a + b, 'source_key': 'twitter:' + a, 'target_key': 'twitter:' + b,
                'relationship': 'follows', 'evidence_urls': ['https://example.org/evidence/' + a + b]}
                for a, b in [('a', 'youtube'), ('youtube', 'd'), ('a', 'manually_excluded')]])
            current['total_nodes'] = 10
            current['total_edges'] = 10
            raw_general_hash = hashlib.sha256(json.dumps(current, sort_keys=True).encode()).hexdigest()
            for route_name, query in [('registry', 'registry=1&account=twitter%3Aa'), ('research', 'run=depth-run&account=twitter%3Aa')]:
                page.goto(base + '/?' + query)
                if route_name == 'research':
                    page.get_by_role('button', name='关系网络', exact=True).click()
                expect(page.locator('.network-node')).to_have_count(8)
                expect(page.locator('.network-node[data-key="twitter:youtube_japan"]')).to_have_count(1)
                expect(page.locator('.graph-relevance-note')).to_contain_text('通用账号 1')
                expect(page.locator('.graph-relevance-note')).to_contain_text('人工排除 1')
                page.get_by_label('关注方向', exact=True).select_option('out')
                page.get_by_label('关系跳数', exact=True).select_option('2')
                page.get_by_label('路径终点', exact=True).select_option('twitter:d')
                expect(page.locator('.graph-path-result')).to_contain_text('未找到')
                page.get_by_role('button', name='放大', exact=True).click()
                viewport = page.locator('.interactive-svg > g').get_attribute('transform')
                coordinates = page.locator('.network-node').evaluate_all('nodes=>Object.fromEntries(nodes.map(n=>[n.dataset.key,n.getAttribute("transform")]))')
                page.get_by_label('显示通用账号', exact=True).check()
                expect(page.locator('.network-node')).to_have_count(9)
                expect(page.locator('.network-node[data-key="twitter:manually_excluded"]')).to_have_count(0)
                expect(page.locator('.network-node[data-key="twitter:youtube"]')).to_have_attribute('data-tier', 'peripheral')
                expect(page.locator('.graph-path-steps > li')).to_have_count(2)
                expect(page.locator('.graph-path-steps')).to_contain_text('twitter:a → twitter:youtube')
                expect(page.locator('.graph-path-steps')).to_contain_text('twitter:youtube → twitter:d')
                assert page.locator('.interactive-svg > g').get_attribute('transform') == viewport
                after = page.locator('.network-node').evaluate_all('nodes=>Object.fromEntries(nodes.map(n=>[n.dataset.key,n.getAttribute("transform")]))')
                assert all(after[key] == value for key, value in coordinates.items()), 'Showing general accounts moved retained nodes'
                page.get_by_label('显示通用账号', exact=True).uncheck()
                expect(page.locator('.network-node')).to_have_count(8)
                expect(page.locator('.graph-path-result')).to_contain_text('未找到')
                assert page.locator('.interactive-svg > g').get_attribute('transform') == viewport
                page.screenshot(path=str(args.output / f'{route_name}-general-default.png'), full_page=True)
                cases.append({'route': route_name + '_general_filter', 'ok': True, 'default_hidden': 2, 'reversible_common_only': True,
                    'exact_handle_only': True, 'path_restored_with_evidence': True, 'positions_and_viewport_preserved': True})
            assert hashlib.sha256(json.dumps(current, sort_keys=True).encode()).hexdigest() == raw_general_hash
            current.clear(); current.update(copy.deepcopy(original))
            page.goto(base + '/?registry=1&account=twitter%3Aa')
            page.get_by_role('button', name='更正账号类型', exact=True).click()
            modal = page.get_by_role('dialog')
            modal.get_by_label('账号类型', exact=True).select_option('individual')
            modal.get_by_label('人工核对说明', exact=True).fill('已核对公开主页类型；不更改身份核验。')
            modal.get_by_label('类型依据链接（每行一条）', exact=True).fill('javascript:alert(1)')
            modal.get_by_role('button', name='保存人工类型', exact=True).click()
            expect(modal.get_by_role('alert')).to_be_visible()
            assert not [request for request in requests if request['method'] == 'PATCH']
            modal.get_by_label('类型依据链接（每行一条）', exact=True).fill('https://example.org/person')
            modal.get_by_role('button', name='保存人工类型', exact=True).click()
            expect(modal.get_by_role('alert')).to_contain_text('模拟保存失败')
            expect(modal.get_by_label('人工核对说明', exact=True)).to_have_value('已核对公开主页类型；不更改身份核验。')
            modal.get_by_role('button', name='保存人工类型', exact=True).click()
            expect(modal).to_have_count(0)
            expect(page.locator('.network-inspector .account-type-label')).to_have_text('个人')
            expect(page.locator('.network-inspector .badge')).to_have_text('待核验')
            patch = [request for request in requests if request['method'] == 'PATCH']
            assert len(patch) == 2 and patch[-1]['body'] == {'type': 'individual', 'manual_locked': True,
                'note': '已核对公开主页类型；不更改身份核验。', 'evidence_urls': ['https://example.org/person']}
            page.get_by_role('button', name='登记核验官网来源', exact=True).click()
            modal = page.get_by_role('dialog')
            modal.get_by_label('核验官网链接 1', exact=True).fill('https://example.org/about')
            modal.get_by_label('来源说明 1', exact=True).fill('人工核对的独立官网')
            modal.get_by_role('button', name='保存核验来源', exact=True).click()
            expect(modal.get_by_role('alert')).to_contain_text('模拟保存失败')
            expect(modal.get_by_label('核验官网链接 1', exact=True)).to_have_value('https://example.org/about')
            modal.get_by_role('button', name='保存核验来源', exact=True).click()
            expect(modal).to_have_count(0)
            expect(page.locator('.verification-schedule')).to_contain_text('已排队')
            expect(page.locator('.verification-schedule').get_by_role('link')).to_have_attribute('href', '#jobs?selected=verification-fixture-job')
            put = [request for request in requests if request['method'] == 'PUT']
            assert len(put) == 2 and put[-1]['body'] == {'sources': [{'url': 'https://example.org/about', 'label': '人工核对的独立官网', 'account_type': 'unknown'}]}
            assert current['nodes'][0]['account_status'] == 'candidate'
            cases.append({'route': 'registry_identity_and_sources', 'ok': True, 'patch_and_put_contract_exact': True,
                'inputs_preserved_after_failure': True, 'manual_type_does_not_grant_verified': True})
            page.screenshot(path=str(args.output / 'registry-identity-sources.png'), full_page=True)
            page.get_by_role('button', name='更正研究相关性', exact=True).click()
            modal = page.get_by_role('dialog')
            modal.get_by_label('研究相关性', exact=True).select_option('irrelevant')
            modal.get_by_label('相关性更正说明', exact=True).fill('本次研究不相关，仍保留原始账号和材料。')
            modal.get_by_role('button', name='保存研究相关性', exact=True).click()
            expect(modal.get_by_role('alert')).to_contain_text('模拟保存失败')
            expect(modal.get_by_label('相关性更正说明', exact=True)).to_have_value('本次研究不相关，仍保留原始账号和材料。')
            modal.get_by_role('button', name='保存研究相关性', exact=True).click()
            expect(modal).to_have_count(0)
            expect(page.locator('.network-node')).to_have_count(6)
            expect(page.locator('.network-inspector')).to_contain_text('人工排除研究')
            expect(page.locator('.network-inspector .badge')).to_have_text('待核验')
            page.get_by_role('button', name='更正研究相关性', exact=True).click()
            modal = page.get_by_role('dialog')
            expect(modal.get_by_label('相关性更正说明', exact=True)).to_have_value('本次研究不相关，仍保留原始账号和材料。')
            modal.get_by_label('研究相关性', exact=True).select_option('included')
            modal.get_by_role('button', name='保存研究相关性', exact=True).click()
            expect(modal).to_have_count(0)
            expect(page.locator('.network-node')).to_have_count(7)
            page.get_by_role('button', name='更正研究相关性', exact=True).click()
            modal = page.get_by_role('dialog')
            modal.get_by_label('研究相关性', exact=True).select_option('unknown')
            modal.get_by_role('button', name='保存研究相关性', exact=True).click()
            expect(modal).to_have_count(0)
            relevance_requests = [request for request in requests if request['path'].endswith('/relevance')]
            assert [request['body']['state'] for request in relevance_requests] == ['irrelevant', 'irrelevant', 'included', 'unknown']
            assert all(set(request['body']) == {'state', 'reason'} for request in relevance_requests)
            assert current['nodes'][0]['account_status'] == 'candidate' and current['nodes'][0]['account_type'] == 'individual'
            cases.append({'route': 'registry_relevance_review', 'ok': True, 'failure_retains_inputs': True,
                'irrelevant_hides_without_deleting': True, 'included_restores': True, 'unknown_reverts_rule': True,
                'type_and_verification_unchanged': True})
            current.clear(); current.update(copy.deepcopy(original))
            current.update(nodes=[{'account_key': 'twitter:youtube', 'account_id': 'youtube', 'platform': 'twitter', 'handle': 'youtube',
                'name': 'YouTube', 'depth': 0, 'account_status': 'candidate', 'url': 'https://x.com/youtube'}], edges=[], total_nodes=1, total_edges=0)
            page.goto(base + '/?registry=1&account=twitter%3Ayoutube')
            expect(page.locator('.network-node')).to_have_count(0)
            expect(page.get_by_label('显示通用账号', exact=True)).to_be_visible()
            expect(page.get_by_role('button', name='以起点账号继续研究', exact=True)).to_be_disabled()
            expect(page.locator('.network-inspector')).to_contain_text('通用平台账号')
            page.get_by_label('显示通用账号', exact=True).check()
            expect(page.locator('.network-node')).to_have_count(1)
            expect(page.locator('.network-node')).to_have_attribute('data-tier', 'peripheral')
            current['run']['params'].update(source='twitter', handle='youtube', mode='keyword')
            page.goto(base + '/?run=depth-run&account=twitter%3Ayoutube')
            page.get_by_role('button', name='关系网络', exact=True).click()
            expect(page.locator('.network-node')).to_have_count(0)
            current['run']['params']['mode'] = 'account'
            page.goto(base + '/?run=depth-run&account=twitter%3Ayoutube')
            page.get_by_role('button', name='关系网络', exact=True).click()
            expect(page.locator('.network-node')).to_have_count(1)
            expect(page.get_by_label('显示通用账号', exact=True)).not_to_be_checked()
            expect(page.locator('.graph-relevance-note')).to_contain_text('通用账号 0')
            cases.append({'route': 'empty_filter_and_explicit_root', 'ok': True, 'toggle_remains_when_all_hidden': True,
                'selected_common_is_not_a_root': True, 'run_seed_restores_common': True})
            current.clear(); current.update(copy.deepcopy(original))
            page.goto(base + '/?registry=1')
            svg = page.locator('.interactive-svg')
            svg.scroll_into_view_if_needed()
            def positions():
                return page.locator('.network-node').evaluate_all('''nodes=>Object.fromEntries(nodes.map(node=>{
                    const p=node.ownerSVGElement.createSVGPoint(),screen=p.matrixTransform(node.getScreenCTM()),m=node.transform.baseVal.getItem(0).matrix;
                    return [node.dataset.key,{x:m.e,y:m.f,sx:screen.x,sy:screen.y,pinned:node.dataset.pinned}];}))''')
            before = positions()
            held = before['twitter:a']
            assert page.evaluate('p=>document.elementFromPoint(p.sx,p.sy)?.closest(".network-node")?.dataset.key', held) == 'twitter:a'
            page.mouse.move(held['sx'], held['sy']); page.mouse.down()
            page.mouse.move(held['sx'] + 65, held['sy'] + 25, steps=8)
            steps = int(page.locator('.interactive-network').get_attribute('data-layout-steps'))
            during = positions()
            page.wait_for_timeout(350)
            stationary = positions()
            assert int(page.locator('.interactive-network').get_attribute('data-layout-steps')) > steps
            assert any(abs(stationary[key]['x'] - during[key]['x']) + abs(stationary[key]['y'] - during[key]['y']) > .1 for key in during if key != 'twitter:a')
            assert abs(stationary['twitter:a']['sx'] - held['sx'] - 65) < 3 and abs(stationary['twitter:a']['sy'] - held['sy'] - 25) < 3
            page.mouse.up()
            page.wait_for_function("()=>document.querySelector('.interactive-network')?.dataset.layoutState==='stable'", timeout=45000)
            stable = positions()
            steps = page.locator('.interactive-network').get_attribute('data-layout-steps')
            page.wait_for_timeout(250)
            assert positions() == stable and page.locator('.interactive-network').get_attribute('data-layout-steps') == steps
            expect(page.locator('.network-inspector')).to_be_hidden()
            account = page.locator('.network-node[data-key="twitter:a"]')
            account.focus(); page.keyboard.press('ArrowRight')
            expect(account).to_have_attribute('data-pinned', 'true')
            page.wait_for_function("()=>document.querySelector('.interactive-network')?.dataset.layoutState==='stable'", timeout=45000)
            pinned = positions()['twitter:a']
            page.get_by_label('关注方向', exact=True).select_option('out')
            assert positions()['twitter:a'] == pinned
            transform = page.locator('.interactive-svg > g').get_attribute('transform')
            box = svg.bounding_box()
            page.mouse.move(box['x'] + 12, box['y'] + 12); page.mouse.down()
            page.mouse.move(box['x'] + 42, box['y'] + 32, steps=5); page.mouse.up()
            assert page.locator('.interactive-svg > g').get_attribute('transform') != transform
            transform = page.locator('.interactive-svg > g').get_attribute('transform')
            page.mouse.wheel(0, -180)
            expect(page.locator('.interactive-svg > g')).not_to_have_attribute('transform', transform)
            assert positions()['twitter:a']['pinned'] == 'true' and positions()['twitter:a']['x'] == pinned['x']
            page.screenshot(path=str(args.output / 'graph-gesture-preserved.png'), full_page=True)
            cases.append({'route': 'shared_graph_gestures', 'ok': True, 'held_pointer_and_neighbours_respond': True,
                'release_settles_and_solver_stops': True, 'keyboard_pin_retained_across_emphasis': True, 'pan_and_wheel_zoom': True})
            assert not errors, errors
            assert not external, external
            browser.close()
        result = {'ok': True, 'cases': cases, 'pageErrors': errors, 'blocked_external_requests': external, 'requests': requests,
            'fixture_unchanged': hashlib.sha256(json.dumps(original, sort_keys=True).encode()).hexdigest() == original_hash,
            'source_hashes': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                for name in ['network_model.js', 'interactive_graph.js', 'network.js', 'registry.js', 'features.css', 'graph_layout.js']},
            'boundaries': ['Actual source UI, DOM and isolated Chromium against a local fixture HTTP server.',
                'No production service/database/profile/credentials or real platform/model calls; all nonlocal requests blocked.',
                'Changing graph emphasis does not rerender or change physics, positions, viewport or saved identities.',
                'Paths use actual saved relation arrows and public per-step evidence; following does not grant affiliation.']}
        (args.output / 'results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'ok': result['ok'], 'cases': len(cases), 'pageErrors': errors, 'blocked_external_requests': external}))
    except Exception as error:
        try:
            if page is not None and not page.is_closed():
                page.screenshot(path=str(args.output / 'failure.png'), full_page=True)
        except Exception:
            pass  # Preserve the original assertion if Playwright already closed.
        (args.output / 'failure.json').write_text(json.dumps({'error': str(error), 'pageErrors': errors, 'requests': requests},
            ensure_ascii=False, indent=2), encoding='utf-8')
        raise
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


if __name__ == '__main__':
    main()
