"""Opt-in live acceptance against a separately launched, disposable workbench.

This writes real public news and reports into the selected workbench. Never run
against a user's production database without intending to keep those records.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--token-env', default='')
    parser.add_argument('--start-date', required=True)
    parser.add_argument('--end-date', required=True)
    parser.add_argument('--keyword', default='憲法九条')
    parser.add_argument('--ai', action='store_true', help='Use the already configured AI connection once')
    args = parser.parse_args()
    results = {'started_at': datetime.now(timezone.utc).isoformat(), 'checks': [], 'jobs': []}
    base = args.base_url.rstrip('/')
    headers = {'Content-Type': 'application/json'}
    if args.token_env:
        headers['X-Workbench-Token'] = os.environ[args.token_env]

    def request(route, data=None, raw=False):
        payload = None if data is None else json.dumps(data, ensure_ascii=False).encode('utf8')
        req = Request(base + route, data=payload, headers=headers)
        try:
            with urlopen(req, timeout=10) as response:
                content = response.read()
                return content if raw else json.loads(content)
        except HTTPError as exc:
            raise AssertionError(f'{route}: HTTP {exc.code}') from None

    def check(name, condition):
        assert condition, name
        results['checks'].append(name)
        print('PASS ' + name, flush=True)

    def job(kind, params):
        submitted = request('/api/v2/jobs', {'kind': kind, 'params': params})
        deadline = time.monotonic() + 270
        while time.monotonic() < deadline:
            state = request('/api/v2/jobs/' + submitted['id'])
            if state['status'] not in {'queued', 'running'}:
                results['jobs'].append(state)
                return state
            time.sleep(0.4)
        raise AssertionError('Job did not reach a terminal state in 270 seconds')

    try:
        check('backend health', request('/api/v2/health')['ok'])
        params = {'source': 'google_news', 'keyword': args.keyword, 'start_date': args.start_date,
                  'end_date': args.end_date, 'max_records': 5}
        first = job('collect', params)
        check('live public collection completed', first['status'] == 'succeeded')
        check('live public records were available', first['result']['fetched'] > 0)
        before = request('/api/v2/items')['total']
        second = job('collect', params)
        check('repeat collection completed', second['status'] == 'succeeded')
        check('repeat collection deduplicated', second['result']['duplicates'] > 0 and request('/api/v2/items')['total'] == before)
        records = request('/api/v2/items')['items']
        check('record detail and source URL persisted', all(request('/api/v2/items/' + item['id'])['url'].startswith('https://') for item in records))
        report_params = {'title': '真实公开资料 · 发布验收', 'start_date': args.start_date, 'end_date': args.end_date,
                         'mode': 'evidence', 'include_news': True, 'include_social': False}
        generated = job('report', report_params)
        check('evidence report generated', generated['status'] == 'succeeded')
        rid = generated['result']['report_id']
        report = request('/api/v2/reports/' + rid)
        check('report preserves evidence snapshot', report['source_count'] > 0 and bool(report['snapshot']))
        exported = request('/api/v2/reports/' + rid + '/download', raw=True)
        check('export matches stored report', exported.decode('utf8') == report['markdown'])
        results['export_sha256'] = hashlib.sha256(exported).hexdigest()
        rerun = job('report', {**report_params, 'title': '真实公开资料 · 第二次生成'})
        check('rerun creates a new report', rerun['status'] == 'succeeded' and rerun['result']['report_id'] != rid)
        check('previous report stays immutable', request('/api/v2/reports/' + rid)['markdown'] == report['markdown'])
        empty = job('report', {**report_params, 'include_news': False, 'include_social': True})
        check('empty material fails explicitly', empty['status'] == 'failed' and bool(empty['error']))
        if args.ai:
            check('existing AI connection is configured', request('/api/v2/capabilities')['llm']['configured'])
            ai = job('report', {**report_params, 'title': '真实公开资料 · AI 连接验收', 'mode': 'ai'})
            check('real AI report completed', ai['status'] == 'succeeded')
            ai_report = request('/api/v2/reports/' + ai['result']['report_id'])
            check('AI report preserves sources and mode', ai_report['source_count'] > 0 and ai_report['mode'] == 'ai' and bool(ai_report['snapshot']))
        results['ok'] = True
    except Exception as exc:
        results['ok'] = False
        results['failure'] = str(exc)
        raise
    finally:
        results['finished_at'] = datetime.now(timezone.utc).isoformat()
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf8')


if __name__ == '__main__':
    main()
