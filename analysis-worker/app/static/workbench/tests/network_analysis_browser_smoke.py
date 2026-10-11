"""Actual network.js UI and fetch acceptance, isolated HTTP assets/browser profile.

Research and retry API responses are synthetic and intercepted. No real backend,
database, source collection, model, saved session, or application profile is used.
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
import {renderNetwork} from '/assets/network.js';import {renderJobs} from '/assets/research.js';window.__navigation=[];
const params=new URLSearchParams(location.search),jobs=params.get('view')==='jobs';params.delete('view');if(!jobs)params.set('run','run-analysis');
const ctx={params,signal:new AbortController().signal,navigate:(...args)=>window.__navigation.push(args),refresh(){},poll(){}};
document.getElementById('main').replaceChildren(await (jobs?renderJobs(ctx):renderNetwork(ctx)));
</script></body></html>'''

class Assets(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/':
            body, content_type = HTML.encode(), 'text/html; charset=utf-8'
        elif path.startswith('/assets/'):
            file = (ROOT / unquote(path.removeprefix('/assets/'))).resolve()
            if not file.is_relative_to(ROOT) or not file.is_file():
                self.send_error(404)
                return
            body = file.read_bytes()
            content_type = {'.css': 'text/css', '.woff2': 'font/woff2'}.get(file.suffix, 'text/javascript')
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def fixture(status='partial', stop_reason='record_budget', pending=6, complete=False):
    return {'run': {'id': 'run-analysis', 'job_id': 'research-qa-job', 'status': 'partial',
                    'params': {'research_goal': '预算内已保存资料的 AI 分析', 'start_date': '2026-09-01', 'end_date': '2026-10-09', 'depth': 2},
                    'summary': {'stop_reason': stop_reason, 'collection_complete': False, 'analysis_status': status, 'analysis_pending_evidence': pending},
                    'analysis': {'ai': {'requested': True, 'used': True, 'complete': complete, 'input_evidence_count': 4, 'available_evidence_count': 10, 'pending_evidence_count': pending, 'completed_batches': 1, 'resumed_batches': 1}}},
            'nodes': [], 'edges': [], 'items': [{'id': 'saved-record', 'title': '已保存公开资料', 'content': '原始材料保持不变', 'url': 'https://example.org/source', 'published_at': '2026-10-01T00:00:00Z'}], 'claims': [], 'coverage': '模拟保存快照，仅验证 UI 状态和请求接线。'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Assets)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state = {'graph': fixture(), 'requests': [], 'fail_retry': False}
    errors, cases = [], []

    def route(route):
        request = route.request
        path = urlsplit(request.url).path
        state['requests'].append({'path': path, 'method': request.method, 'body': request.post_data})
        if path == '/api/v2/research/run-analysis' and request.method == 'GET':
            data, status = state['graph'], 200
        elif path == '/api/v2/jobs' and request.method == 'GET':
            data, status = {'items': [state['job']], 'total': 1}, 200
        elif path == '/api/v2/job-batches' and request.method == 'GET':
            data, status = {'items': [], 'total': 0}, 200
        elif path == '/api/v2/jobs/research-qa-job' and request.method == 'GET':
            data, status = state['job'], 200
        elif path == '/api/v2/jobs/analysis-resumed-qa-job' and request.method == 'GET':
            data, status = {**state['job'], 'id': 'analysis-resumed-qa-job', 'status': 'queued'}, 200
        elif path == '/api/v2/job-batches/analysis-qa-batch' and request.method == 'GET':
            data, status = state['batch'], 200
        elif path == '/api/v2/job-batches/analysis-qa-batch/retry' and request.method == 'POST':
            data, status = {'batch': {'id': 'analysis-retry-batch'}}, 200
        elif path in ['/api/v2/jobs/research-qa-job/retry', '/api/v2/jobs/run-analysis/retry'] and request.method == 'POST':
            if state['fail_retry']:
                data, status = {'error': {'message': '模拟 AI 补跑暂不可用'}}, 409
            else:
                data, status = {'id': 'analysis-resumed-qa-job'}, 200
        else:
            data, status = {'error': {'message': 'Unexpected fixture request: ' + path}}, 404
        route.fulfill(status=status, content_type='application/json', body=json.dumps(data, ensure_ascii=False))

    def load(page, data):
        state['graph'], state['requests'] = copy.deepcopy(data), []
        page.goto(f'http://127.0.0.1:{server.server_port}/')
        expect(page.get_by_role('heading', name='研究结果', exact=True)).to_be_visible()

    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(str(args.output / 'qa-profile'), executable_path=playwright.chromium.executable_path, headless=True, viewport={'width': 1440, 'height': 1000})
            page = context.pages[0]
            page.route('**/api/v2/**', route)
            page.on('pageerror', lambda error: errors.append(str(error)))
            for stop_reason in ['record_budget', 'account_budget', 'edge_budget']:
                for status in ['partial', 'pending', 'failed']:
                    data = fixture(status, stop_reason)
                    load(page, data)
                    label = '仅重试 AI 分析' if status == 'failed' else '继续 AI 分析'
                    control = page.get_by_role('button', name=label, exact=True)
                    expect(control).to_be_visible()
                    expect(page.locator('.analysis-resume-note')).to_contain_text('已保存采集快照')
                    expect(page.locator('.analysis-resume-note')).to_contain_text('不重新打开采集')
                    expect(page.locator('.analysis-coverage')).to_contain_text('4 / 10 条证据摘录，待审 6 条')
                    expect(page.locator('.analysis-coverage')).to_contain_text('成功批次 1，续用批次 1')
                    expect(page.locator('.analysis-excerpt-note')).to_contain_text('摘录不等于全部原文')
                    expect(page.get_by_role('button', name='从断点继续', exact=True)).to_have_count(0)
                    before = json.dumps(state['graph'], sort_keys=True)
                    control.click()
                    page.wait_for_function('window.__navigation.length===1')
                    assert page.evaluate('window.__navigation[0]') == ['jobs', {'selected': 'analysis-resumed-qa-job'}]
                    posts = [r for r in state['requests'] if r['method'] == 'POST']
                    assert posts == [{'path': '/api/v2/jobs/research-qa-job/retry', 'method': 'POST', 'body': None}]
                    assert json.dumps(state['graph'], sort_keys=True) == before
                    cases.append({'name': f'{stop_reason}_{status}_analysis_only_ui', 'ok': True, 'requests': copy.deepcopy(state['requests']), 'savedSnapshotUnchanged': True, 'collectionComplete': False})
            data = fixture()
            load(page, data)
            state['fail_retry'] = True
            page.get_by_role('button', name='继续 AI 分析', exact=True).click()
            expect(page.get_by_role('alert')).to_contain_text('模拟 AI 补跑暂不可用')
            expect(page.get_by_role('button', name='继续 AI 分析', exact=True)).to_be_enabled()
            assert page.evaluate('window.__navigation.length') == 0
            state['fail_retry'] = False
            page.get_by_role('button', name='继续 AI 分析', exact=True).click()
            page.wait_for_function('window.__navigation.length===1')
            cases.append({'name': 'retry_error_preserves_state_and_reenables', 'ok': True, 'requests': copy.deepcopy(state['requests'])})
            for reason in ['record_budget', 'account_budget', 'edge_budget']:
                data = fixture('completed', reason, 0, True)
                data['run']['analysis']['ai']['input_evidence_count'] = 10
                load(page, data)
                expect(page.get_by_role('button', name='继续 AI 分析', exact=True)).to_have_count(0)
                expect(page.get_by_role('button', name='从断点继续', exact=True)).to_have_count(0)
                expect(page.get_by_role('button', name='继续研究', exact=True)).to_be_disabled()
                expect(page.locator('.frontier-recovery-status')).to_contain_text('待准入账号 未记录')
                expect(page.locator('.analysis-excerpt-note')).to_contain_text('当前保存证据摘录的 AI 分析已完成')
                cases.append({'name': f'{reason}_completed_hides_retry', 'ok': True})
            data = fixture()
            data['run']['status'] = 'running'
            load(page, data)
            expect(page.get_by_role('button', name='继续 AI 分析', exact=True)).to_have_count(0)
            expect(page.get_by_role('button', name='停止研究', exact=True)).to_be_visible()
            cases.append({'name': 'active_job_has_no_duplicate_retry', 'ok': True})
            data = fixture()
            data['run']['summary'] = {'stop_reason': 'record_budget'}
            data['run']['analysis']['ai'] = {'requested': True, 'used': True}
            load(page, data)
            expect(page.locator('.analysis-coverage')).to_contain_text('旧结果未记录完整摘录覆盖状态')
            expect(page.locator('.analysis-coverage')).to_contain_text('不能据此认定全部材料已审')
            expect(page.get_by_role('button', name='继续 AI 分析', exact=True)).to_have_count(0)
            cases.append({'name': 'legacy_used_not_claimed_complete', 'ok': True})
            data = fixture()
            del data['run']['job_id']
            del data['run']['summary']['analysis_pending_evidence']
            data['run']['summary']['analysis_status'] = 'completed'
            load(page, data)
            expect(page.get_by_role('button', name='继续 AI 分析', exact=True)).to_be_visible()
            expect(page.locator('.analysis-coverage')).to_contain_text('待审 6 条')
            page.get_by_role('button', name='继续 AI 分析', exact=True).click()
            page.wait_for_function('window.__navigation.length===1')
            assert state['requests'][-1] == {'path': '/api/v2/jobs/run-analysis/retry', 'method': 'POST', 'body': None}
            cases.append({'name': 'ai_pending_fallback_and_run_id_fallback', 'ok': True, 'requests': copy.deepcopy(state['requests'])})
            data = fixture()
            data['run']['summary']['analysis_pending_evidence'] = 2
            load(page, data)
            expect(page.locator('.analysis-coverage')).to_contain_text('待审 2 条')
            cases.append({'name': 'summary_pending_precedes_ai_pending', 'ok': True})
            page.screenshot(path=str(args.output / 'partial-budget-desktop.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            expect(page.get_by_role('button', name='继续 AI 分析', exact=True)).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(args.output / 'partial-budget-mobile.png'), full_page=True)
            cases.append({'name': '390px_coverage_and_retry_containment', 'ok': True})
            page.set_viewport_size({'width': 1440, 'height': 1000})
            for reason in ['record_budget', 'account_budget', 'edge_budget']:
                for status in ['partial', 'pending', 'failed']:
                    graph = fixture(status, reason)
                    state['job'] = {'id': 'research-qa-job', 'kind': 'research', 'status': 'partial', 'params': graph['run']['params'], 'result': {'run_id': 'run-analysis', 'summary': graph['run']['summary']}, 'events': []}
                    state['requests'] = []
                    page.goto(f'http://127.0.0.1:{server.server_port}/?view=jobs&selected=research-qa-job')
                    modal = page.get_by_role('dialog')
                    expect(modal).to_be_visible()
                    label = '仅重试 AI 分析' if status == 'failed' else '继续 AI 分析'
                    expect(modal.get_by_role('button', name=label, exact=True)).to_be_visible()
                    expect(modal.get_by_role('button', name='规划后续任务串', exact=True)).to_have_count(0)
                    expect(modal.locator('.analysis-resume-note')).to_contain_text('不重新打开采集')
                    before = json.dumps(state['job'], sort_keys=True)
                    modal.get_by_role('button', name=label, exact=True).click()
                    expect(page.get_by_role('dialog')).to_contain_text('analysis-resumed-qa-job')
                    assert json.dumps(state['job'], sort_keys=True) == before
                    posts = [r for r in state['requests'] if r['method'] == 'POST']
                    assert posts == [{'path': '/api/v2/jobs/research-qa-job/retry', 'method': 'POST', 'body': None}]
                    cases.append({'name': f'jobs_{reason}_{status}_analysis_retry', 'ok': True, 'requests': copy.deepcopy(state['requests']), 'savedSnapshotUnchanged': True})
            for explicit_count in [None, 1]:
                for status in ['partial', 'failed']:
                    state['job']['result']['summary'].update(analysis_status=status, analysis_pending_evidence=6, stop_reason='record_budget')
                    state['batch'] = {'id': 'analysis-qa-batch', 'title': '仅补保存快照的 AI 任务串', 'status': 'partial', 'task_count': 1, 'completed_count': 1, 'active_count': 0, 'succeeded_count': 0, 'records_saved': 1, 'total_records': 10, 'jobs': [state['job']]}
                    if explicit_count is not None:
                        state['batch']['retryable_count'] = explicit_count
                    state['requests'] = []
                    page.goto(f'http://127.0.0.1:{server.server_port}/?view=jobs&batch=analysis-qa-batch')
                    label = '仅重试 AI 分析任务' if status == 'failed' else '继续 AI 分析任务'
                    control = page.locator('.batch-detail').get_by_role('button', name=label, exact=True)
                    expect(control).to_be_visible()
                    expect(page.locator('.batch-detail .analysis-resume-note')).to_contain_text('仅补待审 AI 分析')
                    expect(page.locator('.batch-detail').get_by_role('button', name='规划后续任务串', exact=True)).to_have_count(0)
                    control.click()
                    page.wait_for_function('window.__navigation.length===1')
                    assert page.evaluate('window.__navigation[0]') == ['jobs', {'page': 1, 'batch': 'analysis-retry-batch'}]
                    posts = [r for r in state['requests'] if r['method'] == 'POST']
                    assert posts == [{'path': '/api/v2/job-batches/analysis-qa-batch/retry', 'method': 'POST', 'body': None}]
                    cases.append({'name': f'batch_{explicit_count}_{status}_pending_priority', 'ok': True, 'requests': copy.deepcopy(state['requests'])})
            assert not errors, errors
            context.close()
        result = {'ok': True, 'cases': cases, 'pageErrors': errors, 'sourceSha256': hashlib.sha256((ROOT / 'network.js').read_bytes()).hexdigest(), 'sourceHashes': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in ['network.js', 'research.js', 'batch.js']}, 'boundaries': ['Actual network.js/research.js/batch.js, DOM, fetch, HTTP static asset server and isolated Chromium profile.', 'Research/job/batch GET and retry POST APIs intercepted with synthetic fixtures; no real backend analysis/collection job is started.', 'POST path/method/no body and jobs navigation verified. Backend analysis-only semantics remain separately owned by core/API tests.', 'Saved fixture snapshot remains unchanged; no formal database, sessions, secrets, or app config are used.']}
        (args.output / 'results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'{len(cases)} network analysis UI cases passed; page errors: {len(errors)}')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == '__main__':
    main()
