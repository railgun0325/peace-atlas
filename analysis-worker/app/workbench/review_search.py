"""Bounded public account discovery. Search snippets never verify an identity.

Only the existing DNS-vetted public transport performs I/O. The caller owns
account-scoped caching, network permission and final-page/backlink validation.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from html import unescape
from urllib.parse import parse_qs, unquote, urldefrag, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from . import pipeline
from .account_verification import _independent_source_url, account_link_key, source_candidates
from .social.common import normalize_handle

MAX_RESULTS = 10
PROVIDER_TIMEOUT = 25
SEARCH_BUDGET = 85
_PROVIDERS = (
    ('bing', 'https://www.bing.com/search'),
    ('ddg', 'https://html.duckduckgo.com/html/'),
    ('google', 'https://www.google.com/search'),
)
_GOOGLE_HOSTS = {'www.google.com', 'www.google.com.hk', 'www.google.co.jp'}
_SEARCH_HOSTS = {'bing.com', 'duckduckgo.com', 'google.com', 'google.com.hk', 'google.co.jp'}
_URL_TEXT = re.compile(r'https?://[^\s<>"\[\]{}]+', re.I)
_HANDLE_CHARACTER = r'[\w.]'


class _Limited(ValueError):
    """A safe public-provider diagnostic, without arbitrary exception text."""


class _Rejected(ValueError):
    """A fixed reason category, never the rejected result's content."""


def _text(value, limit=2000):
    value = str(value or '')[:limit * 4]
    if not re.search(r'<[!/?A-Za-z]', value):
        return unescape(value)[:limit]
    return BeautifulSoup(value, 'html.parser').get_text(' ', strip=True)[:limit]


def _fold(value):
    return unicodedata.normalize('NFKC', value).casefold()


def _target(account):
    if not isinstance(account, dict):
        raise ValueError('调查对象必须是账号记录。')
    platform, handle = account.get('platform'), account.get('handle')
    if not isinstance(platform, str) or platform not in {'twitter', 'instagram'} or not isinstance(handle, str) or len(handle) > 30:
        raise ValueError('调查对象的平台或账号名不符合公开账号范围。')
    # Do not accept an arbitrary URL/free-text alias in the handle field.
    if not re.fullmatch(r'@?[A-Za-z0-9_.]+', handle):
        raise ValueError('调查对象的账号名格式不正确。')
    normalized = normalize_handle(platform, handle)
    url = account.get('url')
    key = platform + ':' + normalized
    if not isinstance(url, str) or len(url) > 2048 or account_link_key(url) != key:
        raise ValueError('调查对象主页必须与平台及完整账号名一致。')
    name = account.get('name') if account.get('name') is not None else normalized
    if not isinstance(name, str) or len(name) > 300 or any(ord(c) < 32 and not c.isspace() for c in name):
        raise ValueError('调查对象显示名长度或格式不正确。')
    name = ' '.join(name.split()) or normalized
    metadata = account.get('metadata')
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError('调查对象的公开元数据格式不正确。')
    canonical = ('https://x.com/' if platform == 'twitter' else 'https://www.instagram.com/') + normalized
    return platform, normalized, name.strip(), key, canonical


def _source_url(value):
    if not isinstance(value, str) or len(value) > 4096:
        raise _Rejected('invalid_source_url')
    try:
        url = urldefrag(_independent_source_url(value))[0]
    except (ValueError, TypeError):
        raise _Rejected('unsafe_source_url') from None
    host = (urlsplit(url).hostname or '').lower()
    if any(host == domain or host.endswith('.' + domain) for domain in _SEARCH_HOSTS):
        raise _Rejected('search_provider_page')
    if host == 'localhost' or host.endswith(('.localhost', '.local')) or '.' not in host:
        raise _Rejected('nonpublic_source_host')
    try:
        address = ipaddress.ip_address(host.strip('[]'))
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise _Rejected('nonpublic_source_host')
    # DNS is deliberately deferred to read_evidence's same vetted transport.
    return url


def _distinct_name(name):
    han = re.findall(r'[\u3400-\u9fff]', name)
    if len(han) >= 3:
        return True
    words = re.findall(r'[a-z]+', _fold(name))
    return len(words) >= 2 and sum(map(len, words)) >= 8


def _relevance(row, platform, handle, name, key):
    title, description = _text(row.get('title')), _text(row.get('description'))
    source = _source_url(row.get('url'))
    public_text = title + ' ' + description
    links = [match.rstrip('.,;:!?。、「」()（）') for match in _URL_TEXT.findall(public_text)]
    if any(account_link_key(link) == key for link in links):
        return source, title, description, ['exact_account_homepage']
    # URL query parameters (including search echoes) and another platform's
    # homepage cannot manufacture a bare-handle match.
    plain = _fold(_URL_TEXT.sub(' ', public_text))
    handle_match = re.search(r'(?<!' + _HANDLE_CHARACTER + r')@?' + re.escape(handle)
                             + r'(?!' + _HANDLE_CHARACTER + r')', plain)
    platform_context = (bool(re.search(r'\btwitter\b|\bx\s+(?:account|アカウント)', plain))
                        if platform == 'twitter' else bool(re.search(r'\binstagram\b|インスタグラム', plain)))
    parts = urlsplit(source)
    locator_text = plain + ' ' + _fold(unquote((parts.hostname or '') + parts.path))
    full_name = _fold(name)
    name_match = _distinct_name(name) and (
        full_name in locator_text if re.search(r'[\u3400-\u9fff]', full_name)
        else bool(re.search(r'(?<![a-z0-9_])' + re.escape(full_name) + r'(?![a-z0-9_])', locator_text)))
    if handle_match and (platform_context or name_match):
        return source, title, description, ['exact_handle_with_context']
    if name_match:
        return source, title, description, ['distinct_complete_display_name']
    raise _Rejected('no_exact_target_context')


def _query_aliases(handle, name, canonical):
    # Quotes prevent partial handle terms; each fallback has one fixed query.
    clean_name = name.replace('"', ' ').strip()
    return (
        ('exact_handle_and_name', f'"{handle}" "{clean_name}"'),
        ('exact_homepage_and_name', f'"{canonical}" "{clean_name}"'),
        ('complete_display_name' if _distinct_name(name) else 'exact_homepage_and_handle',
         f'"{clean_name}"' if _distinct_name(name) else f'"{canonical}" "{handle}"'),
    )


def _provider_destination(provider, request_url, final_url):
    parsed = urlsplit(final_url)
    expected = urlsplit(request_url)
    allowed_hosts = _GOOGLE_HOSTS if provider == 'google' else {expected.hostname}
    if (parsed.scheme != 'https' or parsed.port not in {None, 443}
            or parsed.username is not None or parsed.password is not None
            or (parsed.hostname or '').lower() not in allowed_hosts):
        raise _Limited('unexpected_provider_authority')
    if parsed.path.rstrip('/') != expected.path.rstrip('/'):
        raise _Limited('provider_redirect_or_access_limit')
    if provider in {'bing', 'google'}:
        final_query = parse_qs(parsed.query).get('q', [])
        requested_query = parse_qs(expected.query).get('q', [])
        if final_query != requested_query:
            raise _Limited('provider_query_changed')


def _restricted(soup):
    if soup.select_one('input[type="password"], #challenge-form, #captcha, .g-recaptcha, form[action*="captcha"], form[action*="anomaly"]'):
        return True
    heading = _fold(' '.join(_text(tag.get_text(' ', strip=True), 300) for tag in soup.select('title, h1')))
    if any(marker in heading for marker in ('verify you are', 'captcha', 'unusual traffic', 'access denied', 'before you continue', 'sign in', 'log in')):
        return True
    return bool(re.search(r'captcha|verify (?:you are |that you are )?(?:a )?human|unusual traffic',
                          _fold(soup.get_text(' ', strip=True)[:1200])))


def _rss(body, query, diagnostic):
    if b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper():
        raise _Limited('unsupported_xml_declaration')
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        if _restricted(BeautifulSoup(body, 'html.parser')):
            raise _Limited('provider_captcha_or_login') from None
        raise _Limited('invalid_rss_response') from None
    if root.tag != 'rss' or root.find('channel') is None:
        raise _Limited('unrecognized_rss_schema')
    channel = root.find('channel')
    echo = channel.findtext('link') or ''
    echoed_queries = parse_qs(urlsplit(echo).query).get('q', [])
    diagnostic['query_echo'] = echo[:2048]
    diagnostic['query_echo_matches'] = echoed_queries == [query]
    if not diagnostic['query_echo_matches']:
        raise _Limited('rss_query_echo_missing_or_mismatch')
    return [dict(url=item.findtext('link') or '', title=item.findtext('title') or '',
                 description=item.findtext('description') or '') for item in channel.findall('item')[:MAX_RESULTS]]


def _unwrap(provider, href, final_url):
    full = urljoin(final_url, href)
    parsed = urlsplit(full)
    host = (parsed.hostname or '').lower()
    if provider == 'ddg' and (host == 'duckduckgo.com' or host.endswith('.duckduckgo.com')) and parsed.path.startswith('/l/'):
        values = parse_qs(parsed.query).get('uddg', [])
        return values[0] if len(values) == 1 else ''
    if provider == 'google' and host in _GOOGLE_HOSTS and parsed.path == '/url':
        values = parse_qs(parsed.query).get('q', []) or parse_qs(parsed.query).get('url', [])
        return values[0] if len(values) == 1 else ''
    return full


def _html(provider, body, final_url):
    soup = BeautifulSoup(body, 'html.parser')
    if _restricted(soup):
        raise _Limited('provider_captcha_or_login')
    if provider == 'google' and soup.select_one('a[href*="/httpservice/retry/enablejs"]'):
        raise _Limited('provider_requires_javascript')
    rows = []
    if provider == 'ddg':
        containers = soup.select('.result')
        if not containers and not soup.select_one('#links.results, .results, .no-results'):
            raise _Limited('unrecognized_html_search_schema')
        for container in containers:
            anchor = container.select_one('a.result__a[href]')
            if anchor is None:
                continue
            snippet = container.select_one('.result__snippet')
            rows.append(dict(url=_unwrap(provider, anchor['href'], final_url),
                             title=anchor.get_text(' ', strip=True),
                             description=snippet.get_text(' ', strip=True) if snippet else ''))
            if len(rows) == MAX_RESULTS:
                break
    else:
        headings = soup.select('a[href] h3')
        if not headings and not soup.select_one('#search'):
            raise _Limited('unrecognized_html_search_schema')
        for heading in headings[:MAX_RESULTS]:
            anchor = heading.find_parent('a', href=True)
            container = anchor.parent
            rows.append(dict(url=_unwrap(provider, anchor['href'], final_url),
                             title=heading.get_text(' ', strip=True),
                             description=container.get_text(' ', strip=True)))
    return rows


async def search_account_public(account):
    """Discover independent public locators with at most three GET requests.

    Registered/profile sources remain available even when every provider is
    limited. Supplementary search always runs when the caller invokes this
    function, so a cached result can still supply fallbacks after reading fails.
    """
    platform, handle, name, key, canonical = _target(account)
    saved = []
    for row in source_candidates(account):
        try:
            safe_url = _source_url(row['url'])
        except (ValueError, TypeError):
            continue
        saved.append({**row, 'url': safe_url})
    result = dict(saved_sources=saved, search_results=[], search_is_not_verification=True,
                  diagnostics=[], discovery_is_limited=True,
                  limits={'providers': 3, 'requests_per_provider': 1, 'results_per_provider': MAX_RESULTS,
                          'provider_timeout_seconds': PROVIDER_TIMEOUT, 'total_budget_seconds': SEARCH_BUDGET})
    deadline = time.monotonic() + SEARCH_BUDGET
    seen = set()
    aliases = _query_aliases(handle, name, canonical)
    for (provider, endpoint), (alias, query) in zip(_PROVIDERS, aliases):
        params = {'q': query}
        if provider == 'bing':
            params['format'] = 'rss'
        elif provider == 'google':
            params.update(hl='ja', num=str(MAX_RESULTS))
        request_url = endpoint + '?' + urlencode(params)
        diagnostic = dict(provider=provider, query=query, query_alias=alias, status='limited',
                          raw_count=0, relevant_count=0, rejected_count=0, rejected=[],
                          rejected_reason_counts={},
                          fallback_reason='', query_echo=None, query_echo_matches=None,
                          result_association='unexamined', request_url=request_url,
                          provider_limit={'requests': 1, 'timeout_seconds': PROVIDER_TIMEOUT,
                                          'max_results': MAX_RESULTS})
        result['diagnostics'].append(diagnostic)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            diagnostic['fallback_reason'] = 'overall_time_budget_exhausted'
            break
        try:
            body, final_url, status = await asyncio.wait_for(
                pipeline._fetch_public_feed(request_url, accept=('application/rss+xml, application/xml, text/xml'
                                                                if provider == 'bing' else 'text/html')),
                timeout=min(PROVIDER_TIMEOUT, remaining))
            diagnostic.update(final_url=str(final_url)[:4096], status_code=status)
            _provider_destination(provider, request_url, final_url)
            if not isinstance(body, bytes) or len(body) > pipeline.MAX_FEED_BYTES:
                raise _Limited('invalid_or_oversized_provider_response')
            if status != 200:
                if _restricted(BeautifulSoup(body, 'html.parser')):
                    raise _Limited('provider_captcha_or_login')
                raise _Limited('provider_http_status_' + str(status))
            raw = _rss(body, query, diagnostic) if provider == 'bing' else _html(provider, body, final_url)
            diagnostic['raw_count'] = len(raw)
            for row in raw:
                try:
                    url, title, description, basis = _relevance(row, platform, handle, name, key)
                except (ValueError, TypeError) as exc:
                    reason = str(exc) if isinstance(exc, _Rejected) else 'invalid_source_result'
                    diagnostic['rejected'].append(dict(reason=reason))
                    counts = diagnostic['rejected_reason_counts']
                    counts[reason] = counts.get(reason, 0) + 1
                    continue
                diagnostic['relevant_count'] += 1
                if url not in seen:
                    seen.add(url)
                    result['search_results'].append(dict(url=url, title=title, description=description,
                                                         match_basis=basis, requires_owner_backlink=True))
            diagnostic['rejected_count'] = len(diagnostic['rejected'])
            diagnostic['result_association'] = 'exact_target_locator' if diagnostic['relevant_count'] else 'no_exact_target_locator'
            diagnostic['status'] = 'related_locators' if diagnostic['relevant_count'] else 'no_related_locators'
            if diagnostic['relevant_count']:
                result['discovery_is_limited'] = False
                break
            diagnostic['fallback_reason'] = 'no_related_locators' if raw else 'empty_public_results'
        except _Limited as exc:
            diagnostic['fallback_reason'] = str(exc)
        except asyncio.TimeoutError:
            diagnostic['fallback_reason'] = 'public_transport_timeout'
        except Exception:
            # Transport exception text can contain proxy URLs or other private
            # context. Preserve the failure category, never arbitrary messages.
            diagnostic['fallback_reason'] = 'public_transport_or_parser_failed'
    if not result['search_results']:
        result['search_unavailable'] = True
    return result
