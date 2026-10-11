"""Host-only, bounded support for an activity described in its original post.

This checks what the saved source says. It does not independently verify an event.
Publication context is limited to the existing primary-post DOM capture contract.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
import unicodedata
from urllib.parse import urlsplit

from ..japan_geo import MAPPING, prefecture_only


DATE_GAP = '实际活动年份、月日和活动描述必须出现在已核对摘录中；不能由发布日期补齐。'
LOCATION_GAP = '确认的都道府县必须在实际举办描述的已核对摘录中；不明确时留空。'
OCCURRENCE_GAP = '实际活动举行状态缺少已核对支持；预告、号召或泛泛描述不能计入已举行活动。'
JST = timezone(timedelta(hours=9), 'Asia/Tokyo')
PROVENANCE_FIELDS = ('collection_method', 'author_verified_in_dom', 'post_id', 'post_kind',
                     'content_kind', 'text_representation')
_ACTIVITY = re.compile(r'集会|デモ(?:行進)?|シンポジウム|講演会|抗議行動|街頭行動|平和行進|座り込み|討論会|上映会|スタンディング|抗议活动|示威|和平活动')
_FULL = re.compile(r'(?<!\d)(\d{4})[年/.-]\s*(\d{1,2})[月/.-]\s*(\d{1,2})(?:日|(?!\d))')
_PARTIAL = re.compile(r'(?<![\d年])(\d{1,2})(?:月\s*|[・./-])(\d{1,2})(?:日|(?!\d))')
_RELATIVE = re.compile(r'本日|今日|昨日')
_HELD = re.compile(r'開催(?:された|されました|した|しました)|行(?:われた|われました|いました|った)|'
                   r'参加(?:しています|している|しました|した)|実施(?:した|しました|された)|'
                   r'集まりました|座り込(?:んだ|みました|んでいます)|やっています|やってます')
_FUTURE = re.compile(r'予定|予告|開催します|行います|実施します|参加しよう|結集しよう|集まろう|'
                     r'参加を|ご参加|ぜひ(?:合流|参加)|(?:集会|デモ|座り込み)[^。\n]{0,32}へ[!！]|'
                     r'中止|延期|開催されなかった|行われなかった|参加していません')


class ActivitySupportError(ValueError):
    """A checked original source cannot support this proposed confirmation."""


class ActivitySupportSchemaError(ValueError):
    """Malformed host input or forged persistence metadata; never a downgrade."""


def _normal(text):
    return unicodedata.normalize('NFKC', text)


def _compact(text):
    return re.sub(r'\s+', '', _normal(text))


def _hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def activity_source_projection(item):
    """Bind only source evidence/provenance, not sibling activity reviews."""
    metadata = item.get('metadata') or {}
    if not isinstance(metadata, dict):
        metadata = {}
    return {**{key: item.get(key) for key in ('id', 'title', 'url', 'content', 'published_at',
                                            'type', 'publication_date_unknown')},
            'metadata': {key: metadata.get(key) for key in PROVENANCE_FIELDS}}


def original_publication(item):
    """Return an aware timestamp only for a matching, original-body DOM capture."""
    metadata = item.get('metadata') or {}
    stamp = item.get('published_at')
    if (item.get('type') != 'social' or item.get('publication_date_unknown') is not False
            or not isinstance(metadata, dict) or not isinstance(stamp, str)
            or metadata.get('collection_method') not in {'x_browser_profile_timeline', 'x_browser_live_search'}
            or metadata.get('author_verified_in_dom') is not True
            or metadata.get('post_kind') not in {'post', 'quote'}
            or metadata.get('content_kind') != 'body'
            or metadata.get('text_representation') not in {'public_post_dom', 'original_after_platform_toggle'}):
        return None
    try:
        url = urlsplit(item.get('url') or '')
        post = re.fullmatch(r'/(?:i|[A-Za-z0-9_]{1,15})/status/(\d+)/?', url.path)
        if (url.scheme != 'https' or url.hostname not in {'x.com', 'twitter.com', 'www.twitter.com'}
                or url.username or url.password or url.port or url.query or url.fragment or not post
                or not isinstance(metadata.get('post_id'), str) or post[1] != metadata['post_id']):
            return None
        parsed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(JST)
    except (ValueError, TypeError, OverflowError):
        return None


def _scopes(quote, candidate):
    """A relative expression cannot cross a separate Japanese event sentence."""
    parts = [part.strip() for part in re.split(r'[。!?！？]', quote) if part.strip()]
    selected = []
    for part in parts:
        if _compact(candidate).strip('。!?') not in _compact(part):
            continue
        text = _normal(part)
        if len(_ACTIVITY.findall(text)) > len(_ACTIVITY.findall(_normal(candidate))):
            part = candidate
        selected.append(part)
    return selected


def _month_days(text):
    # A historical abbreviated year (71年11・14) is not today's month/day.
    return [(int(match[1]), int(match[2])) for match in _PARTIAL.finditer(text)
            if not text[match.end():].startswith('時')]


def _derive_date(scopes, supports, proposed, publication):
    values = []
    for raw in scopes:
        text = _normal(raw)
        full = list(_FULL.finditer(text))
        if full:
            try:
                dates = {date(int(m[1]), int(m[2]), int(m[3])).isoformat() for m in full}
            except ValueError:
                raise ActivitySupportError(DATE_GAP) from None
            if dates != {proposed}:
                raise ActivitySupportError(DATE_GAP)
            if '今年' in text and publication is not None and int(proposed[:4]) != publication.year:
                raise ActivitySupportError(DATE_GAP)
            values.append({'value': proposed, 'rule': 'explicit_full_date',
                           'expression': full[0][0], 'quote': raw})
        relative = _RELATIVE.findall(text)
        if relative:
            if publication is None or len(set(relative)) != 1:
                raise ActivitySupportError(DATE_GAP)
            resolved = publication.date() - timedelta(days=1 if relative[0] == '昨日' else 0)
            partials = _month_days(text)
            if resolved.isoformat() != proposed or any(value != (resolved.month, resolved.day) for value in partials):
                raise ActivitySupportError(DATE_GAP)
            values.append({'value': proposed,
                           'rule': 'previous_publication_day' if relative[0] == '昨日' else 'publication_day',
                           'expression': relative[0], 'quote': raw})
        partials = _month_days(text)
        year_quote = raw if '今年' in text else ''
        if not year_quote and len(set(partials)) == 1:
            month, day = partials[0]
            # Only a checked continuation explicitly repeating this event's date.
            continuation = re.compile(rf'今年(?:の)?\s*0?{month}(?:月\s*|[・./-])0?{day}(?:日|(?!\d))')
            for support in supports:
                parts = [part.strip() for part in re.split(r'[。!?！？]', support['quote']) if part.strip()]
                for index, part in enumerate(parts[1:], 1):
                    if (_compact(parts[index - 1]) == _compact(raw)
                            and continuation.search(_normal(part)) and not _ACTIVITY.search(_normal(part))
                            and re.search(r'行動となりました|この(?:デモ|集会)|同(?:デモ|集会)', _normal(part))):
                        year_quote = part.strip()
                        break
        if year_quote and not full and not relative:
            if publication is None or len(set(partials)) != 1:
                raise ActivitySupportError(DATE_GAP)
            try:
                resolved = date(publication.year, *partials[0]).isoformat()
            except ValueError:
                raise ActivitySupportError(DATE_GAP) from None
            if resolved != proposed:
                raise ActivitySupportError(DATE_GAP)
            values.append({'value': proposed, 'rule': 'publication_year',
                           'expression': year_quote, 'quote': raw})
    if not values:
        raise ActivitySupportError(DATE_GAP)
    # Explicit dates and relative expressions must agree, not silently override.
    return values[0]


def _occurrence(scopes, day, publication):
    for raw in scopes:
        text = _normal(raw)
        if (_FUTURE.search(text) or re.search(r'(?:今日も|長年|以来|これまでも).*(?:続けて|闘い)', text)
                or not _ACTIVITY.search(text)):
            raise ActivitySupportError(OCCURRENCE_GAP)
        if publication is not None:
            if day > publication.date().isoformat():
                raise ActivitySupportError(OCCURRENCE_GAP)
            clock = re.search(r'(?<!\d)(\d{1,2})(?:[:：](\d{2})|時|[-–〜~]\d{1,2}時)', text)
            if (day == publication.date().isoformat() and clock
                    and int(clock[1]) * 60 + int(clock[2] or 0) > publication.hour * 60 + publication.minute):
                raise ActivitySupportError(OCCURRENCE_GAP)
        if _HELD.search(text):
            return {'status': 'reported_held', 'rule': 'past_or_present_statement', 'quote': raw}
        if re.search(r'(?:本日|今日).*(?:デモ|集会)です', text):
            return {'status': 'reported_held', 'rule': 'same_day_description', 'quote': raw}
    raise ActivitySupportError(OCCURRENCE_GAP)


def _location(scopes, proposed):
    candidates, spans = [], []
    for raw in scopes:
        text = _normal(raw)
        text = re.sub(r'https?://[^\s<>]+', ' ', text, flags=re.I)
        text = re.sub(r'(?<![\w-])(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s<>]*)?', ' ', text, flags=re.I)
        matches = []
        for alias, canonical in MAPPING.items():
            prefecture = prefecture_only(canonical)
            if not prefecture:
                continue
            pattern = re.escape(alias)
            if alias.isascii():
                pattern = r'(?<![A-Za-z])' + pattern + r'(?![A-Za-z])'
            matches.extend((m.start(), m.end(), alias, prefecture) for m in re.finditer(pattern, text, re.I))
        spans.clear()
        for start, end, alias, prefecture in sorted(matches, key=lambda m: (m[0] - m[1], m[0])):
            if any(start < b and end > a for a, b in spans):
                continue
            spans.append((start, end))
            before, after = text[:start], text[end:]
            venue = (re.match(r'(?:[都道府県]|市内|県内|市|区|町|村|高裁|県庁|庁|大学|公園|文化センター|駅前|基地|[一-龯]{1,8}(?:区|市|公園|ホール))*(?:で|にて|において|を会場)', after)
                     or re.match(r'(?:[都道府県])?(?:高裁|県庁|庁)(?:包囲)?(?:反戦)?(?:デモ|集会)', after)
                     or re.search(r'(?:\bin\s*|会場\s*[:：]\s*)$', before, re.I))
            if venue and not re.search(r'(?:動労|労組|組合|支部)$', before):
                candidates.append({'prefecture': prefecture, 'alias': alias, 'quote': raw})
                for a, b, other_alias, other_prefecture in matches:
                    if b <= start and re.fullmatch(r'\s*(?:と|・|、)\s*', text[b:start]):
                        candidates.append({'prefecture': other_prefecture, 'alias': other_alias, 'quote': raw})
    prefectures = {row['prefecture'] for row in candidates}
    if len(prefectures) > 1:
        raise ActivitySupportError(LOCATION_GAP)
    if not proposed:
        return {'prefecture': '', 'alias': '', 'quote': ''}
    if prefectures != {proposed}:
        raise ActivitySupportError(LOCATION_GAP)
    return candidates[0]


def build_activity_review_basis(expected_row, resolved_proofs, decision):
    """Validate a confirmation and return reproducible original-source metadata.

The caller must first resolve proofs from successful check_citation trace entries.
Only this candidate's saved-source proof can authorize publication context.
"""
    source, record = expected_row.get('source'), expected_row.get('record')
    if (not isinstance(source, dict) or not isinstance(record, dict)
            or not isinstance(record.get('quote'), str) or not isinstance(expected_row.get('id'), str)
            or not isinstance(resolved_proofs, list) or not 1 <= len(resolved_proofs) <= 3):
        raise ActivitySupportSchemaError('活动宿主审核输入无效。')
    text = str(source.get('title') or '') + '\n' + str(source.get('content') or '')
    proof_hash = _hash(json.dumps(text, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False))
    supports, scopes = [], []
    for proof in resolved_proofs:
        if not isinstance(proof, dict) or proof.get('valid') is not True:
            raise ActivitySupportSchemaError('活动支持摘录未经过有效核对。')
        if proof.get('citation_id') != 'activities:' + expected_row['id']:
            continue
        quote = proof.get('quote')
        if (not isinstance(quote, str) or not 1 <= len(quote) <= 2000 or quote not in text
                or proof.get('url') != source.get('url') or proof.get('source_capture_sha256') != proof_hash):
            raise ActivitySupportSchemaError('活动支持摘录与当前原帖捕获不一致。')
        supports.append({key: proof[key] for key in ('citation_id', 'quote', 'url', 'source_capture_sha256')})
        if re.search(r'[「『][^」』]*(?:本日|今日|昨日|今年)[^」』]*[」』]', _normal(quote)):
            raise ActivitySupportError(DATE_GAP)
        scopes.extend(_scopes(quote, record['quote']))
    if not scopes:
        raise ActivitySupportError(DATE_GAP)
    proposed = decision.get('date')
    try:
        if not isinstance(proposed, str) or len(proposed) != 10 or date.fromisoformat(proposed).isoformat() != proposed:
            raise ValueError
    except ValueError:
        raise ActivitySupportError('自动活动日期无效。') from None
    publication = original_publication(source)
    date_basis = _derive_date(scopes, supports, proposed, publication)
    occurrence = _occurrence(scopes, proposed, publication)
    location = _location(scopes, decision.get('prefecture') or '')
    metadata = source.get('metadata') or {}
    return {'version': 1, 'scope': 'source_claim_only', 'source_capture_sha256': _hash(text),
            'supports': supports, 'date': date_basis, 'location': location, 'occurrence': occurrence,
            'publication': ({'published_at': source['published_at'], 'time_zone': 'Asia/Tokyo',
                             **{key: metadata.get(key) for key in PROVENANCE_FIELDS}} if publication is not None else None)}


def validate_saved_review_basis(item, basis, decision):
    """Recompute optional metadata under preview/apply's current-source binding."""
    if basis is None:
        return None
    if (not isinstance(basis, dict) or type(basis.get('version')) is not int
            or basis.get('scope') != 'source_claim_only' or not isinstance(basis.get('supports'), list)):
        raise ActivitySupportSchemaError('活动审核依据结构无效。')
    proofs = [{**support, 'valid': True} for support in basis['supports'] if isinstance(support, dict)]
    if len(proofs) != len(basis['supports']):
        raise ActivitySupportSchemaError('活动审核支持摘录结构无效。')
    row = {'id': decision['id'], 'record': {'quote': decision['quote']}, 'source': item}
    rebuilt = build_activity_review_basis(row, proofs, decision)
    if basis != rebuilt:
        raise ActivitySupportSchemaError('活动审核依据与当前原帖、日期或举办地不一致。')
    return rebuilt


def review_basis_stale(item, basis):
    """A derived date also depends on publication time and its provenance."""
    if not isinstance(basis, dict) or basis.get('publication') is None:
        return False
    publication = basis.get('publication')
    if not isinstance(publication, dict) or original_publication(item) is None:
        return True
    metadata = item.get('metadata') or {}
    return (publication.get('published_at') != item.get('published_at')
            or any(publication.get(key) != metadata.get(key) for key in PROVENANCE_FIELDS))
