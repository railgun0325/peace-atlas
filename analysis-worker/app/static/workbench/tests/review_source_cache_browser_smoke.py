"""Isolated real-browser acceptance of public-source cache controls and receipts.

Only the actual frontend runs; API fixtures never call a model or a public site.
"""
import argparse
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
HTML = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<link rel="stylesheet" href="/assets/styles.css"><link rel="stylesheet" href="/assets/features.css">
<link rel="stylesheet" href="/assets/typography.css"></head><body><main id="main"></main>
<div id="notifications"></div><script type="module">
import {openAutoReview,reviewResults} from '/assets/review.js';
window.navigation=[];
const ctx={navigate:(...args)=>navigation.push(args)};
const open=document.createElement('button');open.textContent='自动审核与核验';
open.onclick=()=>openAutoReview(ctx);document.body.prepend(open);
window.showResults=result=>document.getElementById('main').replaceChildren(reviewResults(result));
window.ready=true;
</script></body></html>'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    requests, errors, cases, held = [], [], [], []
    state = {'hold': False}
    estimate = {'input_digest': 'isolated-proof', 'total': 2, 'batches': 1,
                'counts': {'claims': 2}, 'lower_seconds': 30, 'upper_seconds': 120,
                'model': 'isolated-model-no-call', 'basis': '隔离界面测试',
                'ai_configured': True, 'harness_available': True}

    def json_response(route, body):
        route.fulfill(content_type='application/json', body=json.dumps(body, ensure_ascii=False))

    def route_request(route):
        parsed = urlsplit(route.request.url)
        assert parsed.hostname == 'cache-acceptance.test', 'No external network is allowed'
        if parsed.path == '/':
            return route.fulfill(content_type='text/html', body=HTML)
        if parsed.path.startswith('/assets/'):
            file = (ROOT / unquote(parsed.path.removeprefix('/assets/'))).resolve()
            assert file.is_relative_to(ROOT) and file.is_file()
            mime = {'.css': 'text/css', '.woff2': 'font/woff2'}.get(file.suffix, 'text/javascript')
            return route.fulfill(content_type=mime, body=file.read_bytes())
        body = route.request.post_data_json
        requests.append({'path': parsed.path, 'body': body})
        if parsed.path == '/api/v2/reviews/estimate':
            if state['hold']:
                held.append(route)
                return
            return json_response(route, estimate)
        if parsed.path == '/api/v2/reviews/start':
            return json_response(route, {'id': 'isolated-cache-job'})
        raise AssertionError(f'Unexpected request: {parsed.path}')

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.route('**/*', route_request)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://cache-acceptance.test/')
        page.get_by_role('button', name='自动审核与核验', exact=True).click()
        modal = page.get_by_role('dialog')
        mode = modal.locator('[name=source_cache_mode]')
        preview = modal.get_by_role('button', name='预估范围与用时', exact=True)
        start = modal.get_by_role('button', name='同意预估并开始自动审核', exact=True)
        expect(mode).to_have_value('reuse')
        assert mode.locator('option').evaluate_all('(rows)=>rows.map(row=>row.value)') == ['reuse', 'refresh', 'off']
        expect(start).to_be_disabled()
        for value in ['reuse', 'refresh', 'off']:
            mode.select_option(value)
            expect(start).to_be_disabled()
            preview.click()
            expect(start).to_be_enabled()
            assert requests[-1]['body']['source_cache_mode'] == value
            assert requests[-1]['body']['source_cache_ttl_seconds'] == 3600
        cases.append('Three real select options serialize their mode and 3600-second TTL')
        mode.select_option('reuse')
        state['hold'] = True
        preview.click()
        page.wait_for_timeout(100)
        assert len(held) == 1
        mode.select_option('refresh')
        json_response(held.pop(), estimate)
        expect(preview).to_be_enabled()
        expect(start).to_be_disabled()
        cases.append('Changing mode invalidates the previously approved and in-flight estimate')
        state['hold'] = False
        preview.click()
        expect(start).to_be_enabled()
        modal.screenshot(path=str(args.output / 'cache-controls.png'))
        start.click()
        expect(modal).to_have_count(0)
        assert requests[-1]['body']['source_cache_mode'] == 'refresh'
        assert requests[-1]['body']['input_digest'] == 'isolated-proof'
        assert page.evaluate('navigation') == [['jobs', {'selected': 'isolated-cache-job'}]]
        cases.append('Confirmed start submits the current mode and its estimate proof')
        receipt = {'completed': 2, 'total': 2, 'phase': 'complete', 'results': [],
                   'source_cache': {'mode': 'reuse', 'ttl_seconds': 3600, 'persistent_hits': 2,
                                    'run_hits': 3, 'successful_fetches': 1, 'fetch_calls': 2,
                                    'capture_receipts_truncated': 1,
                                    'captures': [{'source_url': 'https://example.org/public',
                                                  'first_read_via': 'persistent',
                                                  'captured_at': '2026-10-11T00:00:00Z',
                                                  'expires_at': '2026-10-11T01:00:00Z'}]}}
        page.evaluate('(result)=>showResults(result)', receipt)
        section = page.get_by_role('region', name='来源读取与复用')
        expect(section).to_contain_text('跨任务复用 2 次')
        expect(section).to_contain_text('本任务内复用 3 次')
        expect(section).to_contain_text('成功新读取 1 次')
        expect(section).to_contain_text('新读取请求 2 次')
        section.locator('summary').click()
        expect(section).to_contain_text('09:00:00')
        expect(section).to_contain_text('10:00:00')
        expect(section).to_contain_text('另有 1 条捕获记录未展示')
        expect(section.get_by_role('link')).to_have_attribute('href', 'https://example.org/public')
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
        page.screenshot(path=str(args.output / 'cache-receipts.png'), full_page=True)
        cases.append('Counts, original Japanese-time capture/expiry, source links and truncation render correctly')
        page.evaluate('(result)=>showResults(result)', {'completed': 0, 'total': 0, 'results': []})
        expect(page.get_by_role('region', name='来源读取与复用')).to_have_count(0)
        cases.append('Legacy jobs do not receive fabricated source-cache statistics')
        assert not errors, errors
        browser.close()
    result = {'ok': True, 'cases': cases, 'requests': requests, 'page_errors': errors,
              'scope': 'Actual frontend, isolated API fixtures, no model/public-site/formal-profile access'}
    (args.output / 'acceptance.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'ok': True, 'cases': len(cases), 'requests': len(requests), 'page_errors': errors}))


if __name__ == '__main__':
    main()
