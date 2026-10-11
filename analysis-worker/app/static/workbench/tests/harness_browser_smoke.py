"""Real browser acceptance of Harness controls, using isolated API fixtures."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
HTML = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/assets/styles.css"><link rel="stylesheet" href="/assets/features.css"><link rel="stylesheet" href="/assets/typography.css"></head><body><main id="main"></main><div id="notifications"></div><script type="module">
import {openReportCreate} from '/assets/actions.js';import {renderJobs,openReport} from '/assets/research.js';
window.navigation=[];const ctx={params:new URLSearchParams('selected=fixture-job'),signal:new AbortController().signal,navigate:(...args)=>navigation.push(args),refresh(){},poll(){}};
window.openCreate=()=>openReportCreate(ctx,{run:{id:'fixture-run',params:{research_goal:'比较公开来源的证据与关系',start_date:'2026-09-01',end_date:'2026-10-09'}}});
window.openJobs=async()=>document.getElementById('main').replaceChildren(await renderJobs(ctx));window.openSaved=()=>openReport('fixture-report',ctx);window.ready=true;
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--executable', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Assets)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    errors, cases, requests, console = [], [], [], []
    state = {'configured': True, 'available': True, 'job_status': 'running'}
    reading = {'input_source_count': 2, 'reviewed_source_count': 2, 'pending_source_count': 0}
    trace = [{'seq': 1, 'name': 'read_evidence', 'args': {'citation_id': 'S001'}, 'ok': True,
        'result': {'text': '公開資料の原文です。🙂'}}, {'seq': 2, 'name': 'check_citation',
        'args': {'citation_id': 'S001', 'exact_quote': '原文です。'}, 'ok': True, 'result': {'valid': True}}]
    harness = {'model_calls': 3, 'tool_calls': 2, 'phase': 'complete', 'complete': True, 'trace': trace}
    job = {'id': 'fixture-job', 'kind': 'report', 'status': 'running', 'progress': 80,
        'params': {'mode': 'ai', 'analysis_harness': 'dsh', 'title': '隔离工具分析'}, 'result': {'report_checkpoint': {'coverage_progress': reading, 'harness_state': harness}}, 'events': []}
    original_sha, current_sha = 'a' * 64, 'b' * 64

    def retry_preview():
        return {'job_id': job['id'], 'latest_job_id': job['id'], 'kind': 'report',
            'status': state['job_status'], 'saved_params': copy.deepcopy(job['params']),
            'original_input': {'available': True, 'sha256': original_sha, 'input_fingerprint': current_sha,
                'source_count': 2, 'reason': ''},
            'current_input': {'available': True, 'sha256': current_sha, 'source_count': 2, 'reason': ''},
            'changes': {'compared_to': 'saved_original', 'source_count': 0, 'account_count': 0,
                'claim_count': 0, 'context_changed': False},
            'reading_reuse': {'matched': 2, 'pending': 0, 'total': 2, 'unit': 'fragments',
                'validation': 'exact_fingerprint_and_exact_quote', 'paid_verified': False},
            'stage_reuse': {mode: {key: 'matching_fragments_only' if mode == 'latest' and key == 'reading'
                else 'not_saved' if key in {'draft', 'compression'} else 'requires_runtime_validation'
                for key in ['reading', 'harness', 'draft', 'compression']} for mode in ['original', 'latest']},
            'cost_level': 'resume_candidate_requires_runtime_validation', 'may_use_model': True,
            'may_read_public_network': False}

    def route(route):
        request = route.request
        path = urlsplit(request.url).path
        requests.append({'method': request.method, 'path': path, 'body': request.post_data})
        if path == '/api/v2/capabilities':
            data = {'llm': {'configured': state['configured'], 'model': 'fixture-flash'}, 'analysis_harness': {'available': state['available']}}
        elif path == '/api/v2/jobs' and request.method == 'POST':
            data = {'id': 'fixture-created'}
        elif path == '/api/v2/jobs':
            data = {'items': [copy.deepcopy(job)], 'total': 1}
        elif path == '/api/v2/jobs/fixture-job':
            data = copy.deepcopy(job)
            data['status'] = state['job_status']
        elif path == '/api/v2/jobs/fixture-job/retry-preview' and request.method == 'GET':
            data = retry_preview()
        elif path == '/api/v2/jobs/fixture-job/retry' and request.method == 'POST':
            body = request.post_data_json
            assert set(body).issubset({'input_mode', 'input_sha256', 'analysis_harness',
                'time_budget_seconds', 'harness_max_model_calls', 'harness_max_tool_calls'}), body
            assert body.get('input_mode') in {'original', 'latest'}, body
            expected = original_sha if body['input_mode'] == 'original' else current_sha
            assert body.get('input_sha256') == expected, body
            data = {'id': 'fixture-retried'}
        elif path == '/api/v2/job-batches':
            data = {'items': [], 'total': 0}
        elif path == '/api/v2/reports/fixture-report':
            data = {'id': 'fixture-report', 'mode': 'ai', 'title': '隔离工具报告', 'source_count': 2,
                'markdown': '已保存公开材料。', 'snapshot': {'items': [], 'harness_state': harness}}
        else:
            errors.append('unexpected API ' + path)
            route.fulfill(status=404, content_type='application/json', body='{}')
            return
        route.fulfill(status=200, content_type='application/json', body=json.dumps(data, ensure_ascii=False))

    def load(page):
        page.goto(f'http://127.0.0.1:{server.server_port}/')
        page.wait_for_function('window.ready===true')

    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(str(args.output / 'browser-profile'),
                executable_path=str(args.executable) if args.executable else playwright.chromium.executable_path,
                headless=True, viewport={'width': 1440, 'height': 1000})
            page = context.pages[0]
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('console', lambda message: console.append({'type': message.type, 'text': message.text}))
            page.route('**/api/v2/**', route)
            load(page)
            page.evaluate('openCreate()')
            expect(page.locator('select[name=analysis_harness]')).to_have_value('dsh')
            expect(page.locator('input[name=report_minutes]')).to_have_value('0')
            expect(page.locator('input[name=harness_max_model_calls]')).to_be_enabled()
            page.screenshot(path=str(args.output / '01-harness-create.png'), full_page=True)
            cases.append('installed-runtime defaults research report to DSH with no total time cutoff')
            page.locator('input[name=harness_max_model_calls]').fill('32')
            page.locator('input[name=harness_max_tool_calls]').fill('96')
            page.get_by_role('button', name='创建报告任务', exact=True).click()
            page.wait_for_function('navigation.length===1')
            submitted = json.loads(next(row['body'] for row in requests if row['method'] == 'POST'))['params']
            assert submitted['analysis_harness'] == 'dsh' and submitted['harness_max_model_calls'] == 32
            assert submitted['harness_max_tool_calls'] == 96 and submitted['time_budget_seconds'] == 0
            assert submitted['research_run_id'] == 'fixture-run' and submitted['read_original'] is False
            cases.append('actual fetch submits host Harness parameters and canonical saved run')
            state['available'] = False
            load(page)
            page.evaluate('openCreate()')
            expect(page.locator('select[name=analysis_harness]')).to_have_value('off')
            assert page.locator('option[value=dsh]').is_disabled()
            expect(page.locator('input[name=harness_max_model_calls]')).to_be_disabled()
            cases.append('missing runtime stays standard and disables unavailable option')
            state['configured'] = False
            load(page)
            page.evaluate('openCreate()')
            expect(page.locator('select[name=mode]')).to_have_value('evidence')
            expect(page.locator('.harness-limits')).to_be_hidden()
            cases.append('no model config uses evidence report without Harness')
            load(page)
            page.evaluate('openJobs()')
            expect(page.locator('.report-reading-progress')).to_contain_text('2 / 2')
            expect(page.locator('.harness-progress')).to_contain_text('3 轮决策 / 2 次调用')
            page.screenshot(path=str(args.output / '02-harness-job.png'), full_page=True)
            cases.append('job shows saved reading and tool progress separately')
            load(page)
            page.evaluate('openSaved()')
            page.locator('.harness-evidence-trace summary').click()
            expect(page.locator('.harness-evidence-trace')).to_contain_text('公開資料の原文です。🙂')
            expect(page.locator('.harness-evidence-trace')).to_contain_text('逐字匹配')
            page.screenshot(path=str(args.output / '03-harness-trace.png'), full_page=True)
            cases.append('saved report exposes actual text and exact citation result')
            state.update(configured=True, available=True, job_status='failed')
            job['params'].update(start_date='2026-09-01', end_date='2026-10-09', time_budget_seconds=7200)
            load(page)
            page.evaluate('openJobs()')
            retry_count = sum(row['path'].endswith('/retry') for row in requests)
            page.get_by_role('button', name='免费预览报告续跑', exact=True).click()
            expect(page.locator('select[name=input_mode]')).to_have_value('original')
            expect(page.locator('.report-retry-preview')).to_contain_text('运行时再验证')
            assert any(row['path'].endswith('/retry-preview') and row['method'] == 'GET' for row in requests)
            assert sum(row['path'].endswith('/retry') for row in requests) == retry_count
            expect(page.locator('select[name=analysis_harness]')).to_have_value('dsh')
            expect(page.locator('input[name=report_minutes]')).to_have_value('120')
            expect(page.locator('.report-retry-readiness')).to_contain_text('已就绪')
            assert page.locator('input[name=start_date]').count() == 0
            page.locator('input[name=harness_max_model_calls]').fill('32')
            page.locator('input[name=harness_max_tool_calls]').fill('96')
            page.screenshot(path=str(args.output / '04-harness-retry.png'), full_page=True)
            page.get_by_role('button', name='创建报告续跑任务', exact=True).click()
            page.wait_for_function('navigation.length===1')
            retry = json.loads(next(row['body'] for row in requests if row['path'].endswith('/retry')))
            assert retry == {'input_mode': 'original', 'input_sha256': original_sha,
                'analysis_harness': 'dsh', 'time_budget_seconds': 7200,
                'harness_max_model_calls': 32, 'harness_max_tool_calls': 96}
            cases.append('failed report retry previews original input for free and submits flat SHA-bound DSH options without source edits')
            state['available'] = False
            load(page)
            page.evaluate('openJobs()')
            page.get_by_role('button', name='免费预览报告续跑', exact=True).click()
            expect(page.locator('select[name=analysis_harness]')).to_have_value('dsh')
            assert page.locator('option[value=dsh]').is_disabled()
            expect(page.get_by_role('button', name='创建报告续跑任务', exact=True)).to_be_disabled()
            page.locator('select[name=analysis_harness]').select_option('off')
            expect(page.locator('select[name=analysis_harness]')).to_have_value('off')
            expect(page.get_by_role('button', name='创建报告续跑任务', exact=True)).to_be_enabled()
            expect(page.locator('.report-retry-preview')).to_contain_text('DSH 调查：本次未选择工具调查')
            page.screenshot(path=str(args.output / '05-harness-retry-explicit-standard.png'), full_page=True)
            cases.append('runtime missing retry preserves the saved DSH choice until standard analysis is explicitly selected')
            state['configured'] = False
            load(page)
            page.evaluate('openJobs()')
            page.get_by_role('button', name='免费预览报告续跑', exact=True).click()
            expect(page.get_by_role('button', name='创建报告续跑任务', exact=True)).to_be_disabled()
            expect(page.locator('.report-retry-readiness')).to_contain_text('尚未就绪')
            page.screenshot(path=str(args.output / '06-harness-retry-model-unavailable.png'), full_page=True)
            cases.append('missing model connection disables report retry with reason')
            context.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        receipt = {'passed': not errors and len(cases) == 9, 'cases': cases, 'errors': errors,
            'actual_browser': True, 'api_is_fixture': True, 'actual_model_used': False,
            'formal_profile_used': False, 'submitted_parameters': submitted if 'submitted' in locals() else None,
            'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                for name in ['actions.js', 'research.js']}}
        (args.output / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
        (args.output / 'requests.json').write_text(json.dumps(requests, ensure_ascii=False, indent=2), encoding='utf-8')
        (args.output / 'console.json').write_text(json.dumps(console, ensure_ascii=False, indent=2), encoding='utf-8')
    assert not errors and len(cases) == 9, receipt
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == '__main__':
    main()
