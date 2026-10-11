"""Public deferred work, stored independently of admitted accounts and evidence.

Each material is a separate JSON row: a large backlog does not share a single
two-megabyte field. Replacing a checkpoint remains one database transaction.
"""
from __future__ import annotations

import json

from .limits import RESEARCH_DEPTH_MAX

DEFERRED_TABLE_SQL = """CREATE TABLE IF NOT EXISTS wb_research_deferred (
    run_id TEXT NOT NULL REFERENCES wb_research_runs(id), account_key TEXT NOT NULL,
    component TEXT NOT NULL CHECK(component IN ('header','items','relations','discovery_paths','evidence_urls')),
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0), record TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY(run_id,account_key,component,ordinal))"""
COMPONENTS = ('items', 'relations', 'discovery_paths', 'evidence_urls')


def infer_missing_research_work(summary, params=None):
    """Explain old incomplete checkpoints without inventing missing identities."""
    current = dict(summary or {})
    attempts = current.get('account_budget_skipped', 0)
    if type(attempts) in (int, float) and attempts > 0 and current.get('deferred_frontier_version') != 1:
        current.update(frontier_recovery_required=True, legacy_deferred_unknown=True)
    reason = current.get('collection_stop_reason') or current.get('stop_reason')
    params = params or {}
    stats = current.get('seed_query_stats')
    sources = params.get('seed_sources') or [params.get('source')]
    known_news_prefix = (params.get('source') == 'google_news' and isinstance(sources, (list, tuple))
        and bool(sources) and all(source == 'google_news' for source in sources)
        and type(current.get('account_count')) is int and current['account_count'] == 0
        and isinstance(stats, dict) and bool(stats) and all(isinstance(row, dict)
            and type(row.get('failed')) is bool and type(row.get('exhausted')) is bool
            and all(type(row.get(field)) is int and row[field] >= 0 for field in ('requested_limit', 'returned_count'))
            for row in stats.values()))
    if reason in {'record_budget', 'edge_budget'} and current.get('pending_work_version') != 1 and not known_news_prefix:
        current.update(collection_recovery_required=True, legacy_collection_work_unknown=True)
    return current


def _encode_component(value):
    from .research_repository import _public_json
    try:
        return _public_json(value, max_bytes=2097152)
    except ValueError as exc:
        if str(exc) == '保存内容超过大小限制':
            raise ValueError('单条待处理观察超过存储大小限制；原有断点保持，不会截断内容') from exc
        raise


def validate_deferred_entries(entries, *, admitted_keys=()):
    from .research_repository import _array, _key
    from .store import Store, _url
    from .social.common import account as public_account
    from .social.common import timestamp
    def canonical_key(value):
        canonical = _key(value)
        if canonical != value:
            raise ValueError('待处理观察的账号标识必须规范化')
        return canonical
    _array(entries, 'deferred_frontier', limit=10000)
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('待继续的候选必须为对象')
        key = canonical_key(entry.get('account_key'))
        if key != entry['account_key'] or key in seen:
            raise ValueError('待继续的候选账号标识重复或不规范')
        seen.add(key)
        account = entry.get('account')
        if not isinstance(account, dict) or Store.social_account_key(account.get('platform'), account.get('handle')) != key:
            raise ValueError('待继续的候选账号与标识不匹配')
        if public_account(account['platform'], account.get('url'))['account_key'] != key or account.get('account_key') != key:
            raise ValueError('待继续的候选公开账号链接与标识不匹配')
        if entry.get('admission_state', 'deferred') not in {'deferred', 'admitted'}:
            raise ValueError('待继续的候选准入状态无效')
        if entry.get('admission_state') == 'admitted' and key not in admitted_keys:
            raise ValueError('待处理观察自称已准入，但本研究未保存该账号节点')
        depth = entry.get('depth', 0)
        if isinstance(depth, bool) or not isinstance(depth, int) or not 0 <= depth <= RESEARCH_DEPTH_MAX:
            raise ValueError('待继续的候选深度无效')
        score = entry.get('score')
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 100:
            raise ValueError('待继续的候选优先级无效')
        paths = entry.get('discovery_paths')
        independent = key in admitted_keys and (entry.get('items') or entry.get('relations'))
        if not isinstance(paths, list) or (not paths or not entry.get('evidence_urls')) and not independent:
            raise ValueError('待继续的候选缺少公开来源路径')
        if entry.get('parent_key'):
            if canonical_key(entry['parent_key']) == key:
                raise ValueError('待继续的候选不能以自己作为发现来源')
        header = {name: value for name, value in entry.items() if name not in COMPONENTS}
        _encode_component(header)
        for component in COMPONENTS:
            values = entry.get(component, [])
            _array(values, component, limit=100000)
            for value in values:
                if component == 'evidence_urls':
                    _url(value)
                elif not isinstance(value, dict):
                    raise ValueError('待继续的候选材料、关系及路径必须为对象')
                elif component == 'items':
                    _url(value.get('url'))
                    if value.get('account_key') != key:
                        raise ValueError('待继续的账号材料与候选不匹配')
                    from .store import _published
                    _published(value.get('published_at'))
                    if any(name in value and not isinstance(value[name], str) for name in ('content', 'title')):
                        raise ValueError('待继续的公开材料标题和正文必须为文本')
                elif component == 'relations':
                    canonical_key(value.get('source_key')); canonical_key(value.get('target_key'))
                    if value.get('relation') not in {'follows', 'mentions'} or key not in {value['source_key'], value['target_key']}:
                        raise ValueError('待继续的观察关系类型无效')
                    urls = value.get('evidence_urls') or ([value['evidence_url']] if value.get('evidence_url') else [])
                    if not urls:
                        raise ValueError('待继续的观察关系需要公开证据链接')
                    for url in urls:
                        _url(url)
                else:
                    if not value.get('evidence_urls'):
                        raise ValueError('待继续的每条发现路径需要公开来源链接')
                    if value.get('parent_key'):
                        canonical_key(value['parent_key'])
                    if not isinstance(value.get('reason'), str) or not value['reason'] or type(value.get('depth')) is not int or not 0 <= value['depth'] <= RESEARCH_DEPTH_MAX:
                        raise ValueError('待继续的发现路径缺少有效理由或深度')
                    for url in value.get('evidence_urls', []):
                        _url(url)
                if isinstance(value, dict):
                    for field in ('observed_at', 'first_observed_at', 'last_observed_at'):
                        if value.get(field) is not None and value.get(field) != '' and timestamp(value[field]) is None:
                            raise ValueError('待处理公开观察的时间格式无效')
                _encode_component(value)
    return entries


def project_deferred(entries, blocked, excluded=()):
    """Remove discarded support per discovery path, without reviving sources."""
    from .research_repository import source_url_key
    excluded = set(excluded)
    if not blocked and not excluded:
        return entries
    # The same public source can support thousands of independent paths.
    # Memoize only this projection; later reads see fresh cleanup decisions.
    source_keys = {}
    def live(url):
        if not blocked:
            return True
        if not isinstance(url, str):
            return source_url_key(url) not in blocked
        if url not in source_keys:
            source_keys[url] = source_url_key(url)
        return source_keys[url] not in blocked
    result = []
    for entry in entries:
        if entry['account_key'] in excluded:
            continue
        paths = []
        for path in entry.get('discovery_paths', []):
            urls = [url for url in path.get('evidence_urls', []) if live(url)]
            if path.get('parent_key') not in excluded and urls:
                paths.append({**path, 'evidence_urls': urls})
        items = [row for row in entry.get('items', []) if live(row.get('url'))]
        relations = []
        for row in entry.get('relations', []):
            urls = row.get('evidence_urls') or ([row['evidence_url']] if row.get('evidence_url') else [])
            retained = [url for url in urls if live(url)]
            if retained and not {row.get('source_key'), row.get('target_key')} & excluded:
                from .research_repository import ResearchRepository
                current = ResearchRepository._live_research_evidence(row, blocked, source_keys=source_keys)
                if current is None:
                    continue
                if 'evidence_urls' in row:
                    current['evidence_urls'] = retained
                if not current.get('evidence_url'):
                    current['evidence_url'] = retained[0]
                relations.append(current)
        if blocked or excluded:
            if not paths and entry.get('admission_state') != 'admitted':
                continue
            if not paths and not items and not relations:
                continue
            urls = list(dict.fromkeys(url for path in paths for url in path['evidence_urls']))
            current = {**entry, 'discovery_paths': paths, 'evidence_urls': urls, 'items': items, 'relations': relations}
            if paths and not any(path.get('parent_key') == current.get('parent_key') for path in paths):
                path = min(paths, key=lambda row: row.get('depth', 0))
                current.update(parent_key=path.get('parent_key'), depth=path.get('depth', current.get('depth', 0)),
                               discovery_reason=path.get('reason', current.get('discovery_reason', '')))
            result.append(current)
        else:
            result.append(entry)
    return result


def deferred_summary(entries):
    return {'deferred_count': sum(row.get('admission_state', 'deferred') != 'admitted' for row in entries),
            'deferred_record_count': sum(len(row.get('items', [])) for row in entries),
            'deferred_relation_count': sum(len(row.get('relations', [])) for row in entries)}


class ResearchFrontier:
    def _read_deferred(self, connection, run_id, *, include_materials=True):
        record = 'record'
        if not include_materials:
            light = "json_build_object('url', record::jsonb->>'url')::text" if self._postgres else "json_object('url',json_extract(record,'$.url'))"
            record = "CASE WHEN component='items' THEN " + light + " ELSE record END AS record"
        rows = self._execute(connection, 'SELECT account_key,component,ordinal,' + record + ' FROM wb_research_deferred WHERE run_id=? ORDER BY ordinal,account_key,component', (run_id,)).fetchall()
        headers, components = [], {}
        for row in rows:
            value = json.loads(row['record'])
            if row['component'] == 'header':
                headers.append((row['ordinal'], row['account_key'], value))
            else:
                components.setdefault((row['account_key'], row['component']), []).append((row['ordinal'], value))
        return [{**header, **{component: [value for _, value in sorted(components.get((key, component), []))]
                                for component in COMPONENTS}} for _, key, header in sorted(headers)]

    def _checkpoint_deferred(self, connection, run_id, entries, stamp):
        from .research_repository import _public_json
        admitted = {row['account_key'] for row in self._execute(connection,
            'SELECT account_key FROM wb_research_nodes WHERE run_id=?', (run_id,)).fetchall()}
        validate_deferred_entries(entries, admitted_keys=admitted)
        self._execute(connection, 'DELETE FROM wb_research_deferred WHERE run_id=?', (run_id,))
        prefix = 'INSERT INTO wb_research_deferred (run_id,account_key,component,ordinal,record,updated_at) VALUES '
        chunk, byte_count = [], 0

        def flush():
            if chunk:
                self._execute(connection, prefix + ','.join('(?,?,?,?,?,?)' for _ in chunk),
                              tuple(value for row in chunk for value in row))

        def append(key, component, ordinal, value):
            nonlocal chunk, byte_count
            record = _public_json(value, max_bytes=2097152)
            row = (run_id, key, component, ordinal, record, stamp)
            row_bytes = sum(len(str(value).encode('utf-8')) + 8 for value in row) + 16
            if chunk and (len(chunk) >= 100 or (len(chunk) + 1) * 6 > 900
                          or byte_count + row_bytes > 1048576):
                flush()
                chunk, byte_count = [], 0
            # Single legal <=2MiB records may exceed this soft chunk budget.
            chunk.append(row)
            byte_count += row_bytes

        for position, entry in enumerate(entries):
            header = {name: value for name, value in entry.items() if name not in COMPONENTS}
            append(entry['account_key'], 'header', position, header)
            for component in COMPONENTS:
                for ordinal, value in enumerate(entry.get(component, [])):
                    append(entry['account_key'], component, ordinal, value)
        flush()

    def _excluded_account_keys(self, connection):
        return [row['account_key'] for row in self._execute(connection,
            "SELECT k.account_key FROM wb_account_keys k JOIN wb_accounts a ON a.id=k.account_id WHERE a.status='excluded' ORDER BY k.account_key").fetchall()]
