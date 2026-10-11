"""Explain account types without inferring identity from names, badges or edges."""
from __future__ import annotations
from contextlib import nullcontext

TYPES = {'organization', 'individual', 'unknown'}
RESERVED_METADATA = {'account_identity', 'verification_sources', 'verification_schedule',
                     'public_verification', 'public_verification_history', 'research_relevance_override'}


def describe_identity(account):
    """A pure read projection; old metadata remains an unknown identity."""
    raw = (account.get('metadata') or {}).get('account_identity') or {}
    if (not isinstance(raw, dict) or not isinstance(raw.get('type'), str) or raw['type'] not in TYPES
            or type(raw.get('version')) is not int or raw['version'] != 1 or not isinstance(raw.get('basis'), str)
            or raw['basis'] not in {'manual_review', 'registered_source_backlink', 'evidence_review', 'public_profile_statement'}):
        raw = {}
    if not raw:
        # Additive projection for earlier, accepted curated-anchor checks. It
        # never grants status, fetches a site, or assigns a follower's type.
        from .account_verification import OFFICIAL_SOURCES, account_link_key, _independent_source_url, _site
        check = (account.get('metadata') or {}).get('public_verification')
        key = f"{account.get('platform')}:{str(account.get('handle', '')).lstrip('@').lower()}"
        anchor = OFFICIAL_SOURCES.get(key)
        if anchor and isinstance(check, dict) and account.get('status') == 'verified':
            try:
                accepted = (check.get('verified') is True and check.get('outcome') == 'public_backlink'
                    and check.get('source_basis') == 'official_registry' and check.get('source_url') == anchor
                    and check.get('account_id') == account.get('id') and check.get('account_key') == key
                    and check.get('account_url') == account.get('url') and account_link_key(account.get('url')) == key
                    and account_link_key(check.get('matched_account_url')) == key
                    and check.get('evidence_url') == account.get('evidence_url')
                    and _site(_independent_source_url(check.get('evidence_url'))) == _site(anchor))
            except (ValueError, TypeError):
                accepted = False
            if accepted:
                raw = {'type': 'organization', 'basis': 'curated_registry_backlink',
                    'confidence_scope': '已登记组织官网的既有准确主页回链记录；不推广到关注者，不判断立场或隶属。',
                    'evidence_urls': [check['evidence_url']], 'reviewed_at': check.get('checked_at', ''),
                    'manual_locked': False, 'note': '兼容既有核验记录的只读类型投影。'}
    identity = raw.get('type', 'unknown')
    fallback_scope = '尚无可核对的账号类型依据；名称、自称、蓝标和关注关系不能决定类型。'
    details = {'basis': raw.get('basis', 'insufficient_source'),
               'classification_basis': 'public_profile_description' if raw.get('basis') == 'public_profile_statement' else 'agent_review',
               'confidence_scope': raw.get('confidence_scope') if isinstance(raw.get('confidence_scope'), str) else fallback_scope,
               'evidence_urls': [url for url in raw.get('evidence_urls', []) if isinstance(url, str)] if isinstance(raw.get('evidence_urls'), list) else [],
               'manual_locked': raw.get('manual_locked') is True,
               'reviewed_at': raw.get('reviewed_at') if isinstance(raw.get('reviewed_at'), str) else '',
               'reviewed_by': raw.get('reviewed_by', 'human' if raw.get('basis') == 'manual_review' else ''),
               'note': raw.get('note') if isinstance(raw.get('note'), str) else ''}
    return {'account_type': identity, 'account_type_details': details}


def project_account(account):
    from .account_relevance import describe_relevance
    return {**account, **describe_identity(account), **describe_relevance(account)}


def research_registry_context(accounts):
    """Runtime public curation only; never mutate a registry or a job's params."""
    from .account_relevance import describe_relevance
    from .store import Store
    result = {}
    for account in accounts:
        try:
            key = Store.social_account_key(account.get('platform'), account.get('handle'))
        except (ValueError, TypeError):
            continue
        descriptor = describe_identity(account)
        details = descriptor['account_type_details']
        metadata = {}
        if details['basis'] in {'manual_review', 'registered_source_backlink', 'curated_registry_backlink', 'evidence_review', 'public_profile_statement'}:
            metadata['account_identity'] = {'version': 1, 'type': descriptor['account_type'],
                'basis': 'registered_source_backlink' if details['basis'] == 'curated_registry_backlink' else details['basis'],
                **{field: details[field] for field in ('confidence_scope', 'evidence_urls', 'manual_locked', 'reviewed_at', 'note')}}
            if details['basis'] == 'public_profile_statement':
                metadata['account_identity']['classification_basis'] = 'public_profile_description'
        relevance = describe_relevance(account)['research_relevance']
        if relevance['basis'] == 'manual_review':
            raw = account['metadata']['research_relevance_override']
            metadata['research_relevance_override'] = {'version': 1, 'state': raw['state'], 'basis': 'manual_review',
                'reason': raw.get('reason', '') if isinstance(raw.get('reason'), str) else ''}
        result[key] = {'account_key': key, 'platform': account['platform'], 'handle': account['handle'],
                       'status': account['status'], 'metadata': metadata}
    return result


def _locked_account(store, connection, account_id):
    suffix = ' FOR UPDATE' if store._postgres else ''
    value = store._account(store._execute(connection, 'SELECT * FROM wb_accounts WHERE id=?' + suffix, (account_id,)).fetchone())
    if not value:
        raise LookupError('账号不存在。')
    return value


def type_evidence_urls(account, urls, basis='agent_review'):
    """Type evidence may cite its own profile; verification anchors remain independent."""
    from .account_verification import _independent_source_url, account_link_key
    if not isinstance(basis, str) or basis not in {'agent_review', 'independent_source', 'public_profile_description'}:
        raise ValueError('账号类型依据类别无效。')
    if basis != 'public_profile_description':
        return list(dict.fromkeys(_independent_source_url(url) for url in urls))
    actor = f"{account['platform']}:{account['handle'].lstrip('@').lower()}"
    profile = (account.get('metadata') or {}).get('last_public_profile') or {}
    if (not isinstance(profile, dict) or not isinstance(profile.get('bio'), str) or not profile['bio'].strip()
            or account_link_key(account.get('url')) != actor or account_link_key(profile.get('url')) != actor
            or not urls or any(account_link_key(url) != actor for url in urls)):
        raise ValueError('公开主页类型依据必须是该账号已保存非空简介及自己的准确 HTTPS 主页。')
    return [account['url']]


def correct_identity(store, account_id, *, type, manual_locked=True, note='', evidence_urls=(), evidence_basis='agent_review', _connection=None, _schedule=True, _reviewer='human'):
    from .store import _now
    from .research_repository import _public_json
    from .account_verification import schedule_account_checks
    if not isinstance(type, str) or type not in TYPES or not isinstance(manual_locked, bool):
        raise ValueError('账号类型或人工锁定值无效。')
    if not isinstance(note, str) or len(note) > 1000 or len(evidence_urls) > 5:
        raise ValueError('人工类型说明或依据超过上限。')
    with (nullcontext(_connection) if _connection is not None else store._transaction(write=True)) as connection:
        account = _locked_account(store, connection, account_id)
        urls = type_evidence_urls(account, evidence_urls, evidence_basis)
        metadata = dict(account.get('metadata') or {})
        previous = metadata.get('account_identity') or {}
        if _reviewer == 'deepseek_auto_review' and previous.get('manual_locked') is True:
            raise ValueError('自动审核不覆盖已锁定的人工类型。')
        revision = previous.get('revision', 0) if isinstance(previous, dict) else 0
        revision = revision if isinstance(revision, int) and not isinstance(revision, bool) else 0
        profile_basis = evidence_basis == 'public_profile_description'
        scope = ('公开主页自述的发布主体类型；不确认官方账号、实名、组织隶属、政治身份或观点真实性。' if profile_basis else
                 '人工登记的账号类型；不是模型推断，不等于组织隶属或观点真实性。' if _reviewer == 'human' else
                 '经授权的 Agent 公开证据审核结果；账号核验、组织隶属和观点真实性分别判断。')
        metadata['account_identity'] = {'version': 1, 'type': type,
            'basis': 'public_profile_statement' if profile_basis else 'manual_review' if _reviewer == 'human' else 'evidence_review',
            'classification_basis': evidence_basis, 'confidence_scope': scope,
            'reviewed_by': _reviewer,
            'manual_locked': manual_locked, 'evidence_urls': urls, 'reviewed_at': _now(),
            'note': note.strip(), 'revision': revision + 1}
        store._execute(connection, 'UPDATE wb_accounts SET metadata=?,updated_at=? WHERE id=?',
                       (_public_json(metadata), _now(), account_id))
        if _schedule:
            schedule_account_checks(store, [account_id], _connection=connection)
        account = _locked_account(store, connection, account_id)
    return project_account(account)


def register_sources(store, account_id, sources, *, _connection=None, _schedule=True, _reviewer='human'):
    """Register explicitly authorized sources; auto review retains its provenance."""
    from .store import _now
    from .research_repository import _public_json
    from .account_verification import _independent_source_url, schedule_account_checks
    if not isinstance(sources, list) or len(sources) > 5:
        raise ValueError('可信公开来源最多登记五项。')
    clean = []
    for source in sources:
        if not isinstance(source, dict) or set(source) - {'url', 'label', 'account_type'}:
            raise ValueError('可信来源字段无效。')
        url = _independent_source_url(source.get('url'))
        identity = source.get('account_type', 'unknown')
        label = source.get('label', '')
        if not isinstance(identity, str) or identity not in TYPES or not isinstance(label, str) or len(label) > 200:
            raise ValueError('可信来源类型或说明无效。')
        if not any(row['url'] == url for row in clean):
            clean.append({'url': url, 'label': label.strip(), 'account_type': identity,
                          'trust_basis': 'manual_registry' if _reviewer != 'deepseek_auto_review' else 'evidence_review_registry', 'registered_by': _reviewer})
    with (nullcontext(_connection) if _connection is not None else store._transaction(write=True)) as connection:
        account = _locked_account(store, connection, account_id)
        metadata = dict(account.get('metadata') or {})
        if _reviewer == 'deepseek_auto_review':
            previous = {row['url']: row for row in metadata.get('verification_sources', []) if isinstance(row, dict) and row.get('url')}
            clean = [previous[row['url']] if row['url'] in previous and all(previous[row['url']].get(key) == row.get(key) for key in ('label', 'account_type')) else row for row in clean]
        metadata['verification_sources'] = clean
        store._execute(connection, 'UPDATE wb_accounts SET metadata=?,updated_at=? WHERE id=?',
                       (_public_json(metadata), _now(), account_id))
        if _schedule:
            schedule_account_checks(store, [account_id], _connection=connection)
        account = _locked_account(store, connection, account_id)
    return project_account(account)


def correct_relevance(store, account_id, *, state, reason=''):
    """A research-only manual decision; identity and raw evidence are retained."""
    from .store import _now
    from .research_repository import _public_json
    if not isinstance(state, str) or state not in {'included', 'irrelevant', 'unknown'} or not isinstance(reason, str) or len(reason) > 1000:
        raise ValueError('研究相关性或说明无效；说明最多 1000 字。')
    with store._transaction(write=True) as connection:
        account = _locked_account(store, connection, account_id)
        metadata = dict(account.get('metadata') or {})
        if state == 'unknown':
            metadata.pop('research_relevance_override', None)
        else:
            metadata['research_relevance_override'] = {'version': 1, 'state': state, 'basis': 'manual_review',
                'reason': reason.strip(), 'reviewed_at': _now()}
        store._execute(connection, 'UPDATE wb_accounts SET metadata=?,updated_at=? WHERE id=?',
                       (_public_json(metadata), _now(), account_id))
        account = _locked_account(store, connection, account_id)
    return project_account(account)
