"""Bounded, disposable public-read copies; business evidence remains independent.

No import-time I/O, identity decisions, target registry authority or credentials.
Freshness applies between runs; a run's in-memory pages remain a fixed capture.
"""
from __future__ import annotations

import asyncio
import base64
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
from threading import Event

EXTRACTOR_VERSION = 'review-public-html-v1'
DEFAULT_TTL_SECONDS = 3600
MAX_CACHE_ROWS = 128
MAX_CACHE_BYTES = 32 * 1024 * 1024
MAX_RAW_BYTES = 2 * 1024 * 1024
MAX_ENTRY_BYTES = 4 * 1024 * 1024
_CACHE_WRITE_LOCK = 1278032980
PAGE_FIELDS = {'citation_id', 'url', 'text', 'independent', 'owner_account_keys',
               'linked_account_keys', 'source_capture_sha256'}
CACHE_TABLE_SQL = """CREATE TABLE IF NOT EXISTS wb_public_source_cache (
    source_url TEXT NOT NULL PRIMARY KEY, final_url TEXT NOT NULL,
    source_key TEXT NOT NULL, final_source_key TEXT NOT NULL,
    page TEXT NOT NULL, raw_body TEXT NOT NULL, raw_body_sha256 TEXT NOT NULL,
    extractor_version TEXT NOT NULL, captured_at TEXT NOT NULL, expires_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL, size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0))"""
CACHE_INDEX_SQL = 'CREATE INDEX IF NOT EXISTS wb_public_source_cache_used ON wb_public_source_cache(last_used_at,captured_at,source_url)'


def utc_now():
    return datetime.now(timezone.utc)


def timestamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def parse_timestamp(value):
    if not isinstance(value, str):
        raise ValueError('invalid_cache_time')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('invalid_cache_time')
    return result.astimezone(timezone.utc)


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def content_hash(value):
    return hashlib.sha256(encode(value).encode('utf-8')).hexdigest()


def extract_page(requested, body, final):
    # The real parser is shared by live reads and cache validation. Hashing the
    # raw bytes additionally distinguishes link/markup changes with equal text.
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin
    from .account_verification import PublicLinks, account_link_key, _independent_source_url
    requested, final = _independent_source_url(requested), _independent_source_url(final)
    if not isinstance(body, bytes) or len(body) > MAX_RAW_BYTES or body.startswith(b'%PDF'):
        raise ValueError('invalid_public_capture')
    links = PublicLinks()
    links.feed(body.decode('utf-8', errors='replace'))
    keys = sorted({key for raw in links.owner_links if (key := account_link_key(urljoin(final, raw)))})
    linked = sorted({key for raw in links.links if (key := account_link_key(urljoin(final, raw)))})
    soup = BeautifulSoup(body, 'html.parser')
    for element in soup.select('script,style,template,textarea,#comments,.comments-area'):
        element.decompose()
    text = soup.get_text('\n', strip=True)[:80000]
    return {'citation_id': requested, 'url': final, 'text': text, 'independent': True,
            'owner_account_keys': keys, 'linked_account_keys': linked,
            'source_capture_sha256': content_hash({'url': final, 'text': text})}


def receipt(page, body_hash, captured_at, expires_at, via):
    return {'source_url': page['citation_id'], 'source_capture_sha256': page['source_capture_sha256'],
            'raw_body_sha256': body_hash, 'extractor_version': EXTRACTOR_VERSION,
            'captured_at': captured_at, 'expires_at': expires_at, 'first_read_via': via}


class SourceCacheRepository:
    """Store mixin using only short ordinary SQLite/PostgreSQL transactions."""
    def _initialize_source_cache(self):
        with self._transaction(write=True) as connection:
            self._execute(connection, CACHE_TABLE_SQL)
            self._execute(connection, CACHE_INDEX_SQL)

    def _lock_source_cache(self, connection):
        # SQLite BEGIN IMMEDIATE already serializes the capacity/cleanup write.
        # PostgreSQL needs one shared transaction lock for different source URLs.
        if self._postgres:
            self._execute(connection, 'SELECT pg_advisory_xact_lock(?)', (_CACHE_WRITE_LOCK,))

    def get_public_source_cache(self, source_url, *, ttl_seconds=DEFAULT_TTL_SECONDS):
        from .research_repository import source_url_key
        with self._transaction() as connection:
            blocked = self._cleanup_source_keys(connection)
            value = self._execute(connection, 'SELECT * FROM wb_public_source_cache WHERE source_url=?', (source_url,)).fetchone()
        if source_url_key(source_url) in blocked:
            return {'status': 'blocked'}
        if value is None:
            return {'status': 'miss'}
        value = dict(value)
        now = utc_now()
        try:
            if (any(not isinstance(value[key], str) for key in value if key != 'size_bytes')
                    or value['extractor_version'] != EXTRACTOR_VERSION or type(value['size_bytes']) is not int
                    or not 0 < value['size_bytes'] <= MAX_ENTRY_BYTES
                    or len(value['raw_body']) > (MAX_RAW_BYTES + 2) // 3 * 4
                    or len(value['page'].encode('utf-8')) > MAX_ENTRY_BYTES):
                raise ValueError('invalid_cache_shape')
            captured, expires = parse_timestamp(value['captured_at']), parse_timestamp(value['expires_at'])
            if captured > now or expires <= captured or expires - captured > timedelta(days=1):
                raise ValueError('invalid_cache_time')
            if now >= min(expires, captured + timedelta(seconds=ttl_seconds)):
                return {'status': 'expired'}
            body = base64.b64decode(value['raw_body'], validate=True)
            page = json.loads(value['page'])
            if (not isinstance(page, dict) or set(page) != PAGE_FIELDS
                    or hashlib.sha256(body).hexdigest() != value['raw_body_sha256']
                    or value['size_bytes'] != len(value['raw_body'].encode('ascii')) + len(value['page'].encode('utf-8'))
                    or page != extract_page(source_url, body, value['final_url']) or not page['text'].strip()
                    or value['source_key'] != source_url_key(source_url) or value['final_source_key'] != source_url_key(page['url'])):
                raise ValueError('invalid_cache_capture')
            if value['final_source_key'] in blocked:
                return {'status': 'blocked'}
        except (ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError):
            # Preserve the suspect copy for diagnostics/backup until a valid
            # replacement or cache-only capacity/cleanup operation removes it.
            return {'status': 'invalid'}
        with self._transaction(write=True) as connection:
            self._lock_source_cache(connection)
            now = utc_now()
            if now >= min(expires, captured + timedelta(seconds=ttl_seconds)):
                return {'status': 'expired'}
            if {value['source_key'], value['final_source_key']} & self._cleanup_source_keys(connection):
                return {'status': 'blocked'}
            # Do not mark a concurrent replacement as used by the older capture.
            self._execute(connection, 'UPDATE wb_public_source_cache SET last_used_at=? WHERE source_url=? AND raw_body_sha256=? AND captured_at=?',
                (timestamp(now), source_url, value['raw_body_sha256'], value['captured_at']))
        return {'status': 'hit', 'page': page, 'receipt': receipt(page, value['raw_body_sha256'],
                value['captured_at'], timestamp(min(expires, captured + timedelta(seconds=ttl_seconds))), 'persistent')}

    def put_public_source_cache(self, page, body, captured_at, expires_at, *, cancelled=lambda: False):
        from .research_repository import source_url_key
        if cancelled():
            raise asyncio.CancelledError()
        if page != extract_page(page['citation_id'], body, page['url']) or not page['text'].strip():
            return {'stored': False, 'evicted': 0}
        raw, payload = base64.b64encode(body).decode('ascii'), encode(page)
        size = len(raw.encode('ascii')) + len(payload.encode('utf-8'))
        if size > MAX_ENTRY_BYTES:
            return {'stored': False, 'evicted': 0}
        source_key, final_key = source_url_key(page['citation_id']), source_url_key(page['url'])
        with self._transaction(write=True) as connection:
            self._lock_source_cache(connection)
            if cancelled():
                raise asyncio.CancelledError()
            if {source_key, final_key} & self._cleanup_source_keys(connection):
                return {'stored': False, 'evicted': 0}
            self._execute(connection, """INSERT INTO wb_public_source_cache
                (source_url,final_url,source_key,final_source_key,page,raw_body,raw_body_sha256,
                 extractor_version,captured_at,expires_at,last_used_at,size_bytes)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_url) DO UPDATE SET
                final_url=excluded.final_url,source_key=excluded.source_key,final_source_key=excluded.final_source_key,
                page=excluded.page,raw_body=excluded.raw_body,raw_body_sha256=excluded.raw_body_sha256,
                extractor_version=excluded.extractor_version,captured_at=excluded.captured_at,
                expires_at=excluded.expires_at,last_used_at=excluded.last_used_at,size_bytes=excluded.size_bytes""",
                (page['citation_id'], page['url'], source_key, final_key, payload, raw, hashlib.sha256(body).hexdigest(),
                 EXTRACTOR_VERSION, captured_at, expires_at, captured_at, size))
            rows = self._execute(connection, 'SELECT source_url,size_bytes,page,raw_body FROM wb_public_source_cache ORDER BY last_used_at DESC,captured_at DESC,source_url').fetchall()
            total, retained, evicted = 0, 0, 0
            for value in rows:
                try:
                    actual = len(value['page'].encode('utf-8')) + len(value['raw_body'].encode('ascii'))
                    valid = type(value['size_bytes']) is int and value['size_bytes'] == actual and 0 < actual <= MAX_ENTRY_BYTES
                except (AttributeError, UnicodeError):
                    actual, valid = 0, False
                if not valid or retained >= MAX_CACHE_ROWS or total + actual > MAX_CACHE_BYTES:
                    self._execute(connection, 'DELETE FROM wb_public_source_cache WHERE source_url=?', (value['source_url'],))
                    evicted += 1
                else:
                    total += actual
                    retained += 1
            stored = self._execute(connection, 'SELECT 1 FROM wb_public_source_cache WHERE source_url=? AND raw_body_sha256=? AND captured_at=?',
                (page['citation_id'], hashlib.sha256(body).hexdigest(), captured_at)).fetchone() is not None
            if cancelled():
                raise asyncio.CancelledError()
        return {'stored': stored, 'evicted': evicted}

    def clear_public_source_cache(self, urls=None, *, _connection=None):
        from .research_repository import source_url_key
        def clear(connection):
            self._lock_source_cache(connection)
            if urls is None:
                return self._execute(connection, 'DELETE FROM wb_public_source_cache').rowcount
            keys = sorted({source_url_key(url) for url in urls} - {''})
            if not keys:
                return 0
            placeholders = ','.join('?' for _ in keys)
            return self._execute(connection, 'DELETE FROM wb_public_source_cache WHERE source_key IN (' + placeholders + ') OR final_source_key IN (' + placeholders + ')', [*keys, *keys]).rowcount
        if _connection is not None:
            return clear(_connection)
        with self._transaction(write=True) as connection:
            return clear(connection)

    def public_source_cache_stats(self):
        with self._transaction() as connection:
            value = self._execute(connection, 'SELECT COUNT(*) AS entries,COALESCE(SUM(size_bytes),0) AS size_bytes FROM wb_public_source_cache').fetchone()
            return {**dict(value), 'max_entries': MAX_CACHE_ROWS, 'max_size_bytes': MAX_CACHE_BYTES}


class PublicSourceCache:
    def __init__(self, store, *, mode='reuse', ttl_seconds=DEFAULT_TTL_SECONDS, cancelled=lambda: False, stats=None):
        if mode not in {'reuse', 'refresh', 'off', 'shadow'} or type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 86400:
            raise ValueError('Invalid public source cache policy')
        self.store, self.mode, self.ttl_seconds, self.cancelled = store, mode, ttl_seconds, cancelled
        self._cancelled = Event()
        self.stats = stats if isinstance(stats, dict) else {}
        self.stats.update(version=1, mode=mode, ttl_seconds=ttl_seconds)
        for key in ('run_hits', 'persistent_hits', 'fetch_calls', 'successful_fetches', 'misses', 'expired', 'invalid',
                    'forced_refreshes', 'writes', 'write_errors', 'evicted', 'shadow_hits', 'capture_receipts_truncated'):
            if type(self.stats.get(key)) is not int or self.stats[key] < 0:
                self.stats[key] = 0
        captures = self.stats.get('captures')
        self.stats['captures'] = [copy.deepcopy(value) for value in captures if self.valid_receipt(value)][-32:] if isinstance(captures, list) else []
        self._seen = {(value['source_url'], value['raw_body_sha256'], value['captured_at']) for value in self.stats['captures']}

    @staticmethod
    def valid_receipt(value):
        fields = {'source_url', 'source_capture_sha256', 'raw_body_sha256', 'extractor_version', 'captured_at', 'expires_at', 'first_read_via'}
        if not isinstance(value, dict) or set(value) != fields or any(not isinstance(item, str) for item in value.values()):
            return False
        if not 1 <= len(value['source_url']) <= 2048 or value['first_read_via'] not in {'network', 'persistent'}:
            return False
        if any(len(value[key]) != 64 or any(char not in '0123456789abcdef' for char in value[key]) for key in ('source_capture_sha256', 'raw_body_sha256')):
            return False
        try:
            return parse_timestamp(value['expires_at']) > parse_timestamp(value['captured_at']) and bool(value['extractor_version'])
        except (ValueError, TypeError, OverflowError):
            return False

    def check_cancelled(self):
        if self._cancelled.is_set() or self.cancelled():
            raise asyncio.CancelledError()

    def remember(self, value):
        key = (value['source_url'], value['raw_body_sha256'], value['captured_at'])
        if key not in self._seen:
            self._seen.add(key)
            self.stats['captures'].append(copy.deepcopy(value))
            if len(self.stats['captures']) > 32:
                removed = self.stats['captures'].pop(0)
                self._seen.discard((removed['source_url'], removed['raw_body_sha256'], removed['captured_at']))
                self.stats['capture_receipts_truncated'] += 1

    @staticmethod
    def cache_error(error):
        return isinstance(error, (OSError, sqlite3.DatabaseError)) or type(error).__module__.startswith('psycopg')

    async def lookup(self, source_url):
        self.check_cancelled()
        if self.mode == 'off':
            return None
        if self.mode == 'refresh':
            self.stats['forced_refreshes'] += 1
            return None
        try:
            value = await asyncio.to_thread(self.store.get_public_source_cache, source_url, ttl_seconds=self.ttl_seconds)
        except Exception as error:
            if not self.cache_error(error):
                raise
            self.stats['invalid'] += 1
            return None
        self.check_cancelled()
        status = value['status']
        if status == 'hit':
            self.stats['shadow_hits' if self.mode == 'shadow' else 'persistent_hits'] += 1
            if self.mode == 'shadow':
                return None
            self.remember(value['receipt'])
            return {**value['page'], 'source_read_receipt': value['receipt']}
        self.stats['misses'] += 1
        if status in {'expired', 'invalid'}:
            self.stats[status] += 1
        return None

    async def acquired(self, page, body):
        self.check_cancelled()
        now = utc_now()
        value = receipt(page, hashlib.sha256(body).hexdigest(), timestamp(now), timestamp(now + timedelta(seconds=self.ttl_seconds)), 'network')
        if page['text'].strip():
            self.stats['successful_fetches'] += 1
            self.remember(value)
            if self.mode != 'off':
                task = asyncio.create_task(asyncio.to_thread(self.store.put_public_source_cache, page, body,
                    value['captured_at'], value['expires_at'], cancelled=lambda: self._cancelled.is_set() or self.cancelled()))
                try:
                    saved = await asyncio.shield(task)
                except asyncio.CancelledError:
                    self._cancelled.set()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    except Exception as error:
                        if not self.cache_error(error):
                            raise
                        self.stats['write_errors'] += 1
                    raise
                except Exception as error:
                    if not self.cache_error(error):
                        raise
                    self.stats['write_errors'] += 1
                else:
                    self.stats['writes'] += int(saved['stored'])
                    self.stats['evicted'] += saved['evicted']
        self.check_cancelled()
        return {**page, 'source_read_receipt': value}
