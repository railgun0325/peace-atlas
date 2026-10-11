"""Isolated Chromium acceptance of report retry previews.

Every browser request is fulfilled from repository static assets or synthetic
API fixtures. No application server, database, model, CDP, or formal profile is
used. The fixtures validate the actual strict, flat HTTP retry body.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://report-retry.test"
ORIGINAL_SHA = "a" * 64
CURRENT_SHA = "b" * 64
NEW_CURRENT_SHA = "c" * 64
HTML = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/assets/styles.css"><link rel="stylesheet" href="/assets/features.css"><link rel="stylesheet" href="/assets/typography.css"></head><body><main id="main"></main><div id="notifications"></div><script type="module">
import {renderJobs} from '/assets/research.js';
window.navigation=[];const ctx={params:new URLSearchParams('selected=fixture-report-job'),signal:new AbortController().signal,navigate:(...args)=>navigation.push(args),refresh(){},poll(){}};
window.openJobs=async()=>document.getElementById('main').replaceChildren(await renderJobs(ctx));window.ready=true;
</script></body></html>'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--executable', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    state = {'case': 'legacy', 'revision': 0, 'children': 0}
    cases, requests, console, errors, unexpected, screenshots = [], [], [], [], [], []
    job = {'id': 'fixture-report-job', 'kind': 'report', 'status': 'failed', 'progress': 65,
           'created_at': '2026-10-11T01:00:00Z', 'params': {'mode': 'ai', 'analysis_harness': 'off',
           'title': '隔离报告续跑验收', 'start_date': '2026-10-01', 'end_date': '2026-10-11',
           'time_budget_seconds': 0}, 'result': {}, 'events': []}

    def preview():
        legacy = state['case'] == 'legacy'
        sha = NEW_CURRENT_SHA if state['revision'] else CURRENT_SHA
        original = {'available': not legacy, 'sha256': None if legacy else ORIGINAL_SHA,
                    'input_fingerprint': None if legacy else CURRENT_SHA,
                    'source_count': None if legacy else 4,
                    'reason': '旧任务没有完整输入快照，原输入不可恢复' if legacy else ''}
        changed = legacy or state['case'] == 'changed'
        return {'job_id': job['id'], 'latest_job_id': job['id'], 'kind': 'report', 'status': 'failed',
                'saved_params': copy.deepcopy(job['params']), 'original_input': original,
                'current_input': {'available': True, 'sha256': sha, 'source_count': 4, 'reason': ''},
                'changes': {'compared_to': 'unknown_original' if legacy else 'saved_original',
                            'source_count': None if legacy else 0,
                            'account_count': None if legacy else 1 if changed else 0,
                            'claim_count': None if legacy else 2 if changed else 0,
                            'context_changed': None if legacy else changed},
                'reading_reuse': {'matched': 0 if legacy else 6, 'pending': 8 if legacy else 2,
                                 'total': 8, 'unit': 'fragments',
                                 'validation': 'exact_fingerprint_and_exact_quote', 'paid_verified': False},
                'stage_reuse': {'original': {key: 'unavailable' if legacy else 'requires_runtime_validation'
                                           for key in ['reading', 'harness', 'draft', 'compression']},
                               'latest': {'reading': 'matching_fragments_only',
                                          **{key: 'not_saved' if legacy else 'invalidated' if changed
                                             else 'requires_runtime_validation'
                                             for key in ['harness', 'draft', 'compression']}}},
                'cost_level': 'new_analysis_with_matching_readings_only' if changed
                              else 'resume_candidate_requires_runtime_validation',
                'may_use_model': True, 'may_read_public_network': False}

    def route(handler):
        request = handler.request
        parsed = urlsplit(request.url)
        path = parsed.path
        if parsed.netloc != 'report-retry.test':
            unexpected.append(request.url)
            handler.abort()
            return
        if path == '/':
            handler.fulfill(content_type='text/html; charset=utf-8', body=HTML)
            return
        if path.startswith('/assets/'):
            asset = (ROOT / unquote(path.removeprefix('/assets/'))).resolve()
            if not asset.is_relative_to(ROOT) or not asset.is_file():
                unexpected.append('missing static asset ' + path)
                handler.fulfill(status=404, body='')
                return
            handler.fulfill(content_type={'.css': 'text/css', '.woff2': 'font/woff2'}.get(asset.suffix, 'text/javascript'), body=asset.read_bytes())
            return
        if path == '/favicon.ico':
            handler.fulfill(status=204, body='')
            return
        entry = {'case': state['case'], 'revision': state['revision'], 'method': request.method,
                 'path': path, 'body': request.post_data_json if request.post_data else None}
        requests.append(entry)
        status = 200
        if path == '/api/v2/jobs':
            data = {'items': [copy.deepcopy(job)], 'total': 1}
        elif path == '/api/v2/job-batches':
            data = {'items': [], 'total': 0}
        elif path == '/api/v2/jobs/fixture-report-job':
            data = copy.deepcopy(job)
        elif path == '/api/v2/capabilities':
            data = {'llm': {'configured': True, 'model': 'fixture-only'}, 'analysis_harness': {'available': True}}
        elif path == '/api/v2/jobs/fixture-report-job/retry-preview' and request.method == 'GET':
            if state['case'] == 'preview-failed':
                data, status = {'detail': '隔离续跑预览暂时不可用'}, 503
            else:
                data = preview()
        elif path == '/api/v2/jobs/fixture-report-job/retry' and request.method == 'POST':
            body = entry['body']
            allowed = {'input_mode', 'input_sha256', 'analysis_harness', 'time_budget_seconds',
                       'harness_max_model_calls', 'harness_max_tool_calls'}
            assert isinstance(body, dict) and set(body).issubset(allowed), body
            mode = body.get('input_mode')
            assert mode in {'original', 'latest'}, body
            selected = preview()['original_input' if mode == 'original' else 'current_input']
            if not selected['available']:
                data, status = {'error': {'code': 'validation_error', 'message': '原输入快照不可恢复；请明确选择最新输入'}}, 422
            elif body.get('input_sha256') != selected['sha256']:
                data, status = {'error': {'code': 'request_error', 'message': '输入已经变化，请重新预览后再确认'}}, 409
            else:
                state['children'] += 1
                data, status = {'id': 'fixture-created-' + str(state['children'])}, 202
        else:
            unexpected.append(request.method + ' ' + path)
            data, status = {'detail': 'unexpected fixture route'}, 404
        entry['status'] = status
        handler.fulfill(status=status, content_type='application/json', body=json.dumps(data, ensure_ascii=False))

    def open_retry(page, case):
        state.update(case=case, revision=0, children=0)
        page.goto(ORIGIN)
        page.wait_for_function('window.ready===true')
        page.evaluate('openJobs()')
        page.get_by_role('button', name='免费预览报告续跑', exact=True).click()
        page.locator('.report-retry-preview').wait_for()
        return page.get_by_role('dialog', name='免费预览报告续跑', exact=True)

    def screenshot(page, name):
        path = args.output / name
        page.screenshot(path=str(path), full_page=True)
        screenshots.append(name)

    def posts(case):
        return [row for row in requests if row['case'] == case and row['method'] == 'POST']

    passed = False
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=str(args.executable) if args.executable else None)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000}, service_workers='block')
            context.route('**/*', route)
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('console', lambda message: console.append({'type': message.type, 'text': message.text, 'case': state['case']}))

            modal = open_retry(page, 'legacy')
            expect(modal.locator('select[name=input_mode]')).to_have_value('original')
            expect(modal.locator('option[value=original]')).to_be_disabled()
            expect(modal.get_by_role('button', name='创建报告续跑任务', exact=True)).to_be_disabled()
            expect(modal.locator('.report-retry-preview')).to_contain_text('变更数量未知')
            assert not posts('legacy') and state['children'] == 0
            screenshot(page, '01-legacy-original-unavailable.png')
            modal.locator('select[name=input_mode]').select_option('latest')
            expect(modal.get_by_role('button', name='创建报告续跑任务', exact=True)).to_be_enabled()
            expect(modal.locator('.report-retry-preview')).to_contain_text('匹配 0 / 8 个片段')
            expect(modal.locator('.report-retry-preview')).to_contain_text('DSH 调查：本次未选择工具调查')
            screenshot(page, '02-legacy-explicit-latest.png')
            modal.get_by_role('button', name='创建报告续跑任务', exact=True).click()
            page.wait_for_function('navigation.length===1')
            assert len(posts('legacy')) == 1 and posts('legacy')[0]['body']['input_mode'] == 'latest'
            assert posts('legacy')[0]['body']['input_sha256'] == CURRENT_SHA and state['children'] == 1
            cases.append({'case': 'legacy', 'passed': True, 'original_unavailable': True,
                          'latest_explicitly_selected': True, 'accepted_children': 1})

            modal = open_retry(page, 'original')
            expect(modal.locator('select[name=input_mode]')).to_have_value('original')
            expect(modal.locator('select[name=analysis_harness]')).to_have_value('off')
            expect(modal.get_by_role('button', name='创建报告续跑任务', exact=True)).to_be_enabled()
            expect(modal.locator('.report-retry-preview')).to_contain_text('运行时再验证')
            assert not posts('original')
            screenshot(page, '03-original-input-confirmation.png')
            modal.get_by_role('button', name='创建报告续跑任务', exact=True).click()
            page.wait_for_function('navigation.length===1')
            assert posts('original')[0]['body']['input_mode'] == 'original'
            assert posts('original')[0]['body']['input_sha256'] == ORIGINAL_SHA and state['children'] == 1
            cases.append({'case': 'original', 'passed': True, 'default_input_preserved': True,
                          'analysis_default_preserved': True, 'accepted_children': 1})

            modal = open_retry(page, 'preview-failed')
            expect(modal.locator('.report-retry-preview')).to_contain_text('隔离续跑预览暂时不可用')
            expect(modal.get_by_role('button', name='创建报告续跑任务', exact=True)).to_be_disabled()
            modal.locator('input[name=report_minutes]').press('Enter')
            expect(modal.get_by_role('button', name='创建报告续跑任务', exact=True)).to_be_disabled()
            assert not posts('preview-failed') and state['children'] == 0
            screenshot(page, '04-preview-failure-blocks-enqueue.png')
            cases.append({'case': 'preview-failed', 'passed': True, 'post_count': 0, 'accepted_children': 0})

            modal = open_retry(page, 'changed')
            expect(modal.get_by_role('button', name='创建报告续跑任务', exact=True)).to_be_enabled()
            modal.locator('select[name=input_mode]').select_option('latest')
            expect(modal.locator('.report-retry-preview')).to_contain_text('DSH 调查：本次未选择工具调查')
            modal.locator('select[name=analysis_harness]').select_option('dsh')
            expect(modal.locator('.report-retry-preview')).to_contain_text('DSH 调查：完整上下文已变化，重新执行')
            expect(modal.locator('.report-retry-preview')).to_contain_text('综合草稿：完整上下文已变化，重新执行')
            state['revision'] = 1
            modal.get_by_role('button', name='创建报告续跑任务', exact=True).click()
            expect(modal.locator('.form-error')).to_contain_text('输入已经变化，请重新预览后再确认')
            expect(modal.get_by_role('button', name='创建报告续跑任务', exact=True)).to_be_disabled()
            assert state['children'] == 0 and posts('changed')[0]['status'] == 409
            assert posts('changed')[0]['body']['input_sha256'] == CURRENT_SHA
            screenshot(page, '05-input-changed-rejected.png')
            modal.locator('select[name=input_mode]').select_option('original')
            expect(modal.get_by_role('button', name='创建报告续跑任务', exact=True)).to_be_disabled()
            modal.get_by_role('button', name='重新免费预览', exact=True).click()
            expect(modal.get_by_role('button', name='创建报告续跑任务', exact=True)).to_be_enabled()
            modal.locator('select[name=input_mode]').select_option('latest')
            screenshot(page, '06-refreshed-input-confirmation.png')
            modal.get_by_role('button', name='创建报告续跑任务', exact=True).click()
            page.wait_for_function('navigation.length===1')
            assert len(posts('changed')) == 2 and posts('changed')[-1]['body']['input_sha256'] == NEW_CURRENT_SHA
            assert posts('changed')[-1]['status'] == 202 and state['children'] == 1
            cases.append({'case': 'changed', 'passed': True, 'stale_rejected': True,
                          'fresh_preview_required': True, 'accepted_children_after_refresh': 1})
            context.close()
            browser.close()
            passed = not errors and not unexpected and len(cases) == 4
    finally:
        receipt = {'passed': passed, 'cases': cases, 'page_errors': errors, 'unexpected_requests': unexpected,
                   'screenshots': screenshots, 'actual_browser': 'isolated Chromium', 'api_is_fixture': True,
                   'application_server_used': False, 'cdp_used': False, 'actual_model_used': False,
                   'formal_profile_used': False, 'expected_error_http_statuses': [409, 503],
                   'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                     for name in ['actions.js', 'research.js', 'api.js', 'ui.js']}}
        (args.output / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
        (args.output / 'requests.json').write_text(json.dumps(requests, ensure_ascii=False, indent=2), encoding='utf-8')
        (args.output / 'console.json').write_text(json.dumps(console, ensure_ascii=False, indent=2), encoding='utf-8')
    assert passed, receipt
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == '__main__':
    main()
