"""Verify exact account links on approved public sources, without AI or login."""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlsplit

from . import pipeline
from .social.common import normalize_handle

# Organization-controlled anchors. A profile's bio, badge or self-declared
# website does not add a trusted source to this registry.
OFFICIAL_SOURCES = {
    "twitter:peace_boat": "https://peaceboat.org/",
    "twitter:amnestyjapan": "https://www.amnesty.or.jp/",
    "twitter:jcp_cc": "https://www.jcp.or.jp/",
    "twitter:cdp2017": "https://cdp-japan.jp/",
    "twitter:zengakuren": "https://zengakuren.jp/shinkan-2026/",
    "twitter:zengakuren_west": "https://zengakuren.jp/shinkan-2026/",
}


class PublicLinks(HTMLParser):
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    COMMENT_CLASSES = {"comment-list", "comment-body", "comment-content", "comments-area", "user-comments"}

    def __init__(self):
        super().__init__()
        self.links = []
        self.owner_links = []
        self._sections = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = dict(attrs)
        identifier = (attrs.get("id") or "").lower()
        classes = set((attrs.get("class") or "").lower().split())
        # Explicit visitor comments and inert templates are not a publisher's
        # identity assertion. This excludes known markup, not arbitrary UGC.
        ignored = (any(section[1] for section in self._sections)
                   or tag in {"template", "script", "style", "textarea"}
                   or identifier in {"comments", "respond"} or identifier.startswith("comment-")
                   or bool(classes & self.COMMENT_CLASSES))
        if tag == "a" and not ignored and 'ugc' not in (attrs.get('rel') or '').lower().split():
            href = attrs.get("href")
            if href:
                self.links.append(href)
                if 'me' in (attrs.get('rel') or '').lower().split() or any(section[0] in {'footer', 'header', 'nav'} for section in self._sections):
                    self.owner_links.append(href)
        if tag not in self.VOID_TAGS:
            self._sections.append((tag, ignored))

    def handle_endtag(self, tag):
        for index in range(len(self._sections) - 1, -1, -1):
            if self._sections[index][0] == tag.lower():
                del self._sections[index:]
                break

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)


def account_link_key(url):
    if not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        platform = "twitter" if host in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"} else "instagram" if host in {"instagram.com", "www.instagram.com"} else None
        if (not platform or parsed.username is not None or parsed.password is not None
                or parsed.scheme != "https" or parsed.port not in {None, 443}
                or any(character.isspace() for character in url)):
            return None
        # Only a homepage links to an identity; a status or /intent URL does not.
        parts = parsed.path.split("/")
        if len(parts) not in {2, 3} or parts[0] or (len(parts) == 3 and parts[2]):
            return None
        handle = parts[1]
        normalized = normalize_handle(platform, handle)
        # Free-text handle input may strip @; URL identity must not do so.
        if handle.lower() != normalized:
            return None
        return platform + ":" + normalized
    except ValueError:
        # A malformed unrelated href must not discard the whole source page.
        return None


def _independent_source_url(source):
    source = pipeline._url(source)
    parsed = urlsplit(source)
    host = (parsed.hostname or "").lower()
    social_hosts = {"x.com", "twitter.com", "instagram.com"}
    if parsed.scheme != "https" or parsed.port not in {None, 443} or any(host == social or host.endswith("." + social) for social in social_hosts):
        raise ValueError("核验依据需为独立于社媒平台的公开 HTTPS 页面。")
    return source


def _site(url):
    parsed = urlsplit(url)
    return ((parsed.hostname or '').lower().removeprefix('www.'), parsed.port or 443)


def _rows(value):
    return value if isinstance(value, list) else []


def _type(value):
    return value if isinstance(value, str) and value in {'organization', 'individual', 'unknown'} else 'unknown'


def _profile_urls(account):
    metadata = account.get('metadata') or {}
    profile = metadata.get('last_public_profile') or {}
    raw = []
    if isinstance(profile, dict):
        for field in ('website', 'external_url'):
            if isinstance(profile.get(field), str):
                raw.append(profile[field])
        for row in _rows(profile.get('links'))[:10]:
            raw.append(row.get('url') if isinstance(row, dict) else row)
        import re
        raw.extend(re.findall(r'https://[^\s<>"\[\]]+', str(profile.get('bio') or ''))[:10])
    for row in _rows(metadata.get('site_links'))[:10]:
        raw.append(row.get('url') if isinstance(row, dict) else row)
    result = []
    for url in raw:
        try:
            url = _independent_source_url(url)
        except (ValueError, TypeError):
            continue
        url = urldefrag(url)[0]
        if url not in result:
            result.append(url)
    return result[:5]


def trusted_sites(accounts=()):
    sites = {_site(url): {'basis': 'official_registry', 'account_type': 'unknown'} for url in OFFICIAL_SOURCES.values()}
    for account in accounts:
        for row in _rows((account.get('metadata') or {}).get('verification_sources'))[:5]:
            if not isinstance(row, dict) or row.get('trust_basis') != 'manual_registry':
                continue
            try:
                url = _independent_source_url(row.get('url'))
            except (ValueError, TypeError):
                continue
            # Site authority can be shared; its registrant's identity cannot.
            # Target-specific types remain on that target's own source rows.
            sites[_site(url)] = {'basis': 'registered_site', 'account_type': 'unknown'}
    return sites


def source_candidates(account, supplied='', sites=None):
    key, source, basis = verification_source(account, supplied)
    values = []
    if source:
        values.append({'url': source, 'basis': basis, 'trusted': True,
                       'account_type': 'organization' if basis == 'official_registry' else 'unknown', 'owner_link_required': False})
    for row in _rows((account.get('metadata') or {}).get('verification_sources'))[:5]:
        if not isinstance(row, dict) or row.get('trust_basis') not in {'manual_registry', 'evidence_review_registry'}:
            continue
        try:
            url = _independent_source_url(row.get('url'))
        except (ValueError, TypeError):
            continue
        automatic = row.get('trust_basis') == 'evidence_review_registry'
        values.append({'url': url, 'basis': 'evidence_review_registry' if automatic else 'registered_source', 'trusted': True,
                        'account_type': _type(row.get('account_type')), 'owner_link_required': automatic})
    sites = trusted_sites() if sites is None else sites
    for url in _profile_urls(account):
        known = sites.get(_site(url))
        values.append({'url': url, 'basis': known['basis'] if known else 'public_profile_link',
                        'trusted': bool(known) and not known.get('conflict', False),
                        'registry_conflict': bool(known and known.get('conflict')),
                        'account_type': 'unknown',
                        'owner_link_required': bool(known)})
    unique = {}
    for row in values:
        unique.setdefault(row['url'], row)
    return sorted(unique.values(), key=lambda row: not row['trusted'])[:5]


def registered_sites(store, connection):
    """All explicit registry anchors, without an arbitrary account window."""
    rows = store._execute(connection, 'SELECT metadata FROM wb_accounts WHERE metadata LIKE ? ORDER BY id',
                          ('%verification_sources%',)).fetchall()
    return trusted_sites({'metadata': json.loads(row['metadata'])} for row in rows)


def input_fingerprint(account, candidates=None):
    candidates = source_candidates(account) if candidates is None else candidates
    identity = (account.get('metadata') or {}).get('account_identity') or {}
    value = {'account_id': account.get('id'), 'platform': account.get('platform'), 'handle': account.get('handle'), 'url': account.get('url'),
             'status': account.get('status'), 'evidence_url': account.get('evidence_url', ''),
             'identity': identity, 'sources': candidates}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def schedule_account_checks(store, account_ids, *, _connection=None):
    """Queue once per changed input, in the writer's transaction; never on GET."""
    from contextlib import nullcontext
    from .store import _json, _now
    account_ids = list(dict.fromkeys(account_ids))
    if not account_ids:
        return []
    with (nullcontext(_connection) if _connection is not None else store._transaction(write=True)) as connection:
        if store._postgres:
            store._execute(connection, 'SELECT pg_advisory_xact_lock(1278032974)')
        sites = registered_sites(store, connection)
        active = set()
        for row in store._execute(connection, "SELECT params FROM wb_jobs WHERE kind='collect' AND status IN ('queued','running')").fetchall():
            params = json.loads(row['params'])
            if params.get('_account_verification') is True:
                active.update(params.get('account_ids') or [])
        eligible = []
        for identity in account_ids:
            suffix = ' FOR UPDATE' if store._postgres else ''
            account = store._account(store._execute(connection, 'SELECT * FROM wb_accounts WHERE id=?' + suffix, (identity,)).fetchone())
            if not account or account['status'] != 'candidate':
                continue
            candidates = source_candidates(account, sites=sites)
            if not candidates or account_link_key(account.get('url')) != f"{account['platform']}:{account['handle'].lstrip('@').lower()}":
                continue
            fingerprint = input_fingerprint(account, candidates)
            previous = (account.get('metadata') or {}).get('verification_schedule') or {}
            if identity in active:
                # A write during a running check is remembered without creating
                # a second active job; terminalization queues the changed input.
                if isinstance(previous, dict) and previous.get('input_sha256') != fingerprint:
                    metadata = dict(account.get('metadata') or {})
                    metadata['verification_schedule'] = {**previous, 'state': 'pending_change'}
                    store._execute(connection, 'UPDATE wb_accounts SET metadata=? WHERE id=?', (_json(metadata), identity))
                continue
            if isinstance(previous, dict) and previous.get('input_sha256') == fingerprint:
                continue
            eligible.append((account, fingerprint))
        jobs = []
        for start in range(0, len(eligible), 20):
            chunk = eligible[start:start + 20]
            job = store.create_job('collect', {'_account_verification': True, '_automatic_verification': True,
                'source': 'public_verification', 'account_ids': [row['id'] for row, _ in chunk]}, _connection=connection)
            jobs.append(job['id'])
            for account, fingerprint in chunk:
                metadata = dict(account.get('metadata') or {})
                metadata['verification_schedule'] = {'version': 1, 'input_sha256': fingerprint,
                    'job_id': job['id'], 'state': 'queued', 'queued_at': _now()}
                store._execute(connection, 'UPDATE wb_accounts SET metadata=? WHERE id=?', (_json(metadata), account['id']))
        return jobs


def begin_schedule(store, job):
    from .store import _json, _now
    with store._transaction(write=True) as connection:
        sites = registered_sites(store, connection)
        for identity in job['params']['account_ids']:
            suffix = ' FOR UPDATE' if store._postgres else ''
            account = store._account(store._execute(connection, 'SELECT * FROM wb_accounts WHERE id=?' + suffix, (identity,)).fetchone())
            if not account:
                continue
            candidates = source_candidates(account, job['params'].get('verification_source_url', ''), sites)
            metadata = dict(account.get('metadata') or {})
            metadata['verification_schedule'] = {'version': 1, 'job_id': job['id'], 'state': 'running',
                'input_sha256': input_fingerprint(account, candidates), 'started_at': _now()}
            store._execute(connection, 'UPDATE wb_accounts SET metadata=? WHERE id=?', (_json(metadata), identity))


def finish_schedule(store, job, state):
    """End a specific lane attempt; only changed inputs can queue a successor."""
    from .store import _json, _now
    changed = []
    with store._transaction(write=True) as connection:
        for identity in job['params']['account_ids']:
            suffix = ' FOR UPDATE' if store._postgres else ''
            account = store._account(store._execute(connection, 'SELECT * FROM wb_accounts WHERE id=?' + suffix, (identity,)).fetchone())
            if not account:
                continue
            metadata = dict(account.get('metadata') or {})
            schedule = metadata.get('verification_schedule')
            if not isinstance(schedule, dict) or schedule.get('job_id') != job['id']:
                continue
            if schedule.get('state') == state:
                continue
            if schedule.get('state') == 'pending_change':
                changed.append(identity)
            metadata['verification_schedule'] = {**schedule, 'state': state, 'finished_at': _now()}
            store._execute(connection, 'UPDATE wb_accounts SET metadata=? WHERE id=?', (_json(metadata), identity))
        if changed and state not in {'cancelled', 'interrupted'}:
            schedule_account_checks(store, changed, _connection=connection)


def verification_source(account, supplied=""):
    key = f"{account.get('platform')}:{str(account.get('handle', '')).lstrip('@').lower()}"
    source = supplied or OFFICIAL_SOURCES.get(key) or ""
    if not source:
        return key, "", "none"
    source = _independent_source_url(source)
    return key, source, "user_supplied" if supplied else "official_registry"


def _source_failure(exc):
    """Useful failure categories without recording raw HTTP exceptions."""
    result = {"error_type": type(exc).__name__}
    if isinstance(exc, pipeline.httpx.HTTPStatusError):
        return {**result, "failure_reason": "http_error", "http_status": exc.response.status_code,
                "message": f"公开依据网页返回 HTTP {exc.response.status_code}，仍保留候选状态，可重试。"}
    if isinstance(exc, (asyncio.TimeoutError, pipeline.httpx.TimeoutException)):
        reason, detail = "timeout", "公开依据网页读取超时"
    elif isinstance(exc, pipeline.PipelineError):
        # These are fixed, local transport messages; only the category is saved.
        text = str(exc)
        if "DNS" in text or "Fake-IP" in text or "DoH" in text:
            reason, detail = "dns_error", "公开依据网页 DNS 解析失败或超时"
        elif "重定向次数" in text:
            reason, detail = "redirect_limit", "公开依据网页重定向次数过多"
        elif "内网" in text or "保留" in text or "非公网" in text:
            reason, detail = "address_rejected", "依据网页地址未通过公共互联网地址检查"
        elif "大小" in text or "MiB" in text:
            reason, detail = "response_limit", "公开依据网页响应超过大小限制"
        else:
            reason, detail = "invalid_source", "公开依据网页未通过来源校验"
    else:
        reason, detail = "network_error", "本次无法连接或读取公开依据网页"
    return {**result, "failure_reason": reason, "message": detail + "，仍保留候选状态，可重试。"}


class _PageCache:
    """One bounded job's request coalescing; no HTML goes into metadata."""
    def __init__(self):
        self.pages = {}
        self.locks = {}

    async def read(self, url):
        lock = self.locks.setdefault(url, asyncio.Lock())
        async with lock:
            if url in self.pages:
                return self.pages[url], True
            notes = []
            token = pipeline._DNS_DIAGNOSTICS.set(notes)
            try:
                body, final, _ = await asyncio.wait_for(pipeline._fetch_public_feed(
                    url, accept="text/html, application/xhtml+xml"), 25)
                page = {'body': body, 'final_url': final, 'dns_diagnostics': notes}
            except (ValueError, OSError, asyncio.TimeoutError, pipeline.httpx.HTTPError) as exc:
                page = {'failure': _source_failure(exc), 'dns_diagnostics': notes}
            finally:
                pipeline._DNS_DIAGNOSTICS.reset(token)
            if len(self.pages) < 60:
                self.pages[url] = page
            return page, False


def _cached_result(account, fingerprint):
    cached = (account.get('metadata') or {}).get('public_verification')
    if (not isinstance(cached, dict) or cached.get('input_sha256') != fingerprint
            or cached.get('version') != 1 or cached.get('account_id') != account.get('id')
            or not isinstance(cached.get('verified'), bool) or cached.get('account_url') != account.get('url')
            or not isinstance(cached.get('outcome'), str)
            or cached.get('outcome') not in {'public_backlink', 'untrusted_backlink', 'conflicting_sources',
                'no_backlink', 'source_unavailable', 'source_redirected'}):
        return None
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(cached['checked_at'].replace('Z', '+00:00'))).total_seconds()
    except (ValueError, KeyError, TypeError):
        return None
    ttl = 300 if cached.get('outcome') in {'source_unavailable', 'source_redirected'} else 3600
    if not 0 <= age <= ttl:
        return None
    return {**cached, 'cached': True, 'requests_made': 0, 'cache_hits': 1,
            'cached_pages_checked': cached.get('pages_checked', 0), 'pages_checked': 0}


async def check_account(account, source_url="", *, sites=None, force=False, retry=False, _cache=None):
    from .store import _now
    key, _, _ = verification_source(account, source_url)
    candidates = source_candidates(account, source_url, sites)
    fingerprint = input_fingerprint(account, candidates)
    first = candidates[0] if candidates else {}
    result = {"version": 1, "account_id": account["id"], "account_key": key, "account_url": account.get("url", ""),
              "source_url": first.get('url', ''), "source_basis": first.get('basis', 'none'),
              "requested_source_url": source_url, "input_sha256": fingerprint,
              "checked_at": _now(), "verified": False, "cached": False,
              "previous_status": account["status"], "previous_evidence_url": account.get("evidence_url", ""),
              "requests_made": 0, "cache_hits": 0, "pages_checked": 0, "page_limit": 3,
              "source_diagnostics": [], "dns_diagnostics": []}
    if account["status"] != "candidate":
        return {**result, "outcome": "retained", "message": "保留已有核验或排除决定。"}
    if key != account_link_key(account.get("url", "")):
        return {**result, "outcome": "identity_mismatch", "message": "账号主页与用户名不一致，未自动核验。"}
    if not candidates:
        return {**result, "outcome": "missing_source", "message": "没有可检查的公开来源；可登记可信独立来源，或等待公开主页外链更新。"}
    cached = None if force else _cached_result(account, fingerprint)
    if retry and cached and cached.get('outcome') in {'source_unavailable', 'source_redirected'}:
        cached = None
    if cached:
        return cached
    cache = _cache or _PageCache()
    queue, visited, matches = list(candidates), set(), []
    while queue and result['pages_checked'] < 3:
        candidate = queue.pop(0)
        source = urldefrag(candidate['url'])[0]
        if source in visited:
            continue
        visited.add(source)
        page, reused = await cache.read(source)
        result['pages_checked'] += 1
        result['cache_hits' if reused else 'requests_made'] += 1
        result['dns_diagnostics'].extend(page['dns_diagnostics'])
        diagnostic = {'url': source, 'basis': candidate['basis'], 'trusted': candidate['trusted'], 'cached': reused}
        if page.get('failure'):
            result['source_diagnostics'].append({**diagnostic, 'outcome': 'source_unavailable', **page['failure']})
            continue
        try:
            final = _independent_source_url(page['final_url'])
            if _site(final) != _site(source):
                raise ValueError('off_site_redirect')
        except ValueError:
            result['source_diagnostics'].append({**diagnostic, 'outcome': 'source_redirected',
                'failure_reason': 'invalid_redirect', 'message': '来源跳转到其他站点或不合格地址，未继承可信来源资格。'})
            continue
        parser = PublicLinks()
        parser.feed(page['body'].decode('utf-8', errors='replace'))
        links, owner_links = [], []
        for raw in parser.links:
            try:
                links.append(urljoin(final, raw))
            except ValueError:
                pass
        for raw in parser.owner_links:
            try:
                owner_links.append(urljoin(final, raw))
            except ValueError:
                pass
        all_matches = [link for link in links if account_link_key(link) == key]
        allowed_matches = [link for link in (owner_links if candidate['owner_link_required'] else links)
                           if account_link_key(link) == key]
        trusted_match = candidate['trusted'] and bool(allowed_matches)
        result['source_diagnostics'].append({**diagnostic, 'final_url': final,
            'outcome': 'public_backlink' if trusted_match else 'candidate_backlink' if all_matches else 'no_backlink',
            'exact_homepage_matches': len(all_matches), 'owner_scope_matches': len(allowed_matches)})
        if all_matches:
            matches.append({**candidate, 'trusted': bool(trusted_match), 'evidence_url': final,
                            'matched_account_url': all_matches[0]})
        # Explore a small publisher-owned navigation subset, never feeds,
        # searches, social pages, comment URLs, or an off-site destination.
        discovered = []
        for link in links:
            try:
                link = urldefrag(_independent_source_url(link))[0]
                parsed = urlsplit(link)
                if _site(link) != _site(source) or parsed.query or parsed.path.count('/') > 5:
                    continue
                if not any(word in parsed.path.lower() for word in ('about', 'contact', 'social', 'profile', 'official', 'organization', '概要', '連絡')):
                    continue
            except ValueError:
                continue
            if link not in visited and link not in discovered and all(row['url'] != link for row in queue):
                discovered.append(link)
                queue.append({**candidate, 'url': link})
            if len(discovered) >= 2:
                break
    result['coverage_limited'] = bool(queue)
    trusted = [row for row in matches if row['trusted']]
    types = {row['account_type'] for row in trusted if row['account_type'] in {'organization', 'individual'}}
    if len(types) > 1 or any(row.get('registry_conflict') for row in matches):
        return {**result, 'outcome': 'conflicting_sources', 'message': '已登记来源的身份类型依据存在冲突，保留候选状态，请人工核对。'}
    if trusted:
        match = trusted[0]
        return {**result, 'verified': True, 'outcome': 'public_backlink', 'evidence_url': match['evidence_url'],
                'source_basis': match['basis'], 'matched_account_url': match['matched_account_url'],
                'registered_account_type': next(iter(types), 'unknown'),
                'message': '已登记可信公开来源明确回链到准确账号主页；此核验不判断观点、活动立场或组织隶属。'}
    if matches:
        return {**result, 'outcome': 'untrusted_backlink', 'candidate_evidence_url': matches[0]['evidence_url'],
                'matched_account_url': matches[0]['matched_account_url'],
                'message': '公开候选页面存在准确回链，但来源尚未登记可信或未证明账号属于该站点；仍为候选，需人工核对。'}
    failures = [row for row in result['source_diagnostics'] if row['outcome'] in {'source_unavailable', 'source_redirected'}]
    if failures and len(failures) == result['pages_checked']:
        failure = failures[0]
        return {**result, 'outcome': failure['outcome'],
                **{name: failure[name] for name in ('failure_reason', 'error_type', 'http_status', 'message') if name in failure}}
    return {**result, 'outcome': 'no_backlink', 'message': '有限检查的公开页面未出现准确账号主页回链；仍为候选，未检查的页面不视为否定证据。'}


async def run_checks(accounts, source_url="", *, sites=None, force=False, retry=False, on_result=None):
    if len(accounts) > 20:
        raise ValueError('一次公开核验最多检查二十个账号。')
    gate, cache = asyncio.Semaphore(3), _PageCache()
    sites = trusted_sites(accounts) if sites is None else sites

    async def one(account):
        async with gate:
            result = await check_account(account, source_url if len(accounts) == 1 else '', sites=sites, force=force, retry=retry, _cache=cache)
            if on_result:
                await on_result(result)
            return result

    tasks = [asyncio.create_task(one(account)) for account in accounts]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        # A failed checkpoint or shutdown owns every in-flight request. Do not
        # allow siblings to keep fetching or writing after this job ends.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
