"""Snapshot-bound review queue and atomic batches shared by UI and Agent clients."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class QueueArgs(Strict):
    kind: Literal['accounts', 'claims', 'activities'] = 'claims'
    pending_only: bool = True
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=100, ge=1, le=200)


class IdentityDecision(Strict):
    type: Literal['organization', 'individual', 'unknown']
    note: str = Field(min_length=1, max_length=1000)
    evidence_urls: list[str] = Field(default_factory=list, max_length=5)
    manual_locked: bool = False


class SourceDecision(Strict):
    url: str = Field(min_length=1, max_length=2048)
    label: str = Field(default='', max_length=200)
    account_type: Literal['organization', 'individual', 'unknown'] = 'unknown'


class BoundDecision(Strict):
    id: str = Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')
    expected_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class AccountDecision(BoundDecision):
    identity: IdentityDecision | None = None
    sources: list[SourceDecision] | None = Field(default=None, max_length=5)

    @model_validator(mode='after')
    def effect(self):
        if self.identity is None and self.sources is None:
            raise ValueError('账号审核需要类型决定或明确登记的公开来源。')
        return self


class ClaimDecision(BoundDecision):
    run_id: str = Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')
    status: Literal['confirmed', 'rejected', 'inconclusive']
    note: str = Field(min_length=1, max_length=1600)
    evidence_url: str = Field(default='', max_length=2048)


class ActivityDecision(BoundDecision):
    item_id: str = Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')
    status: Literal['confirmed', 'rejected', 'inconclusive']
    title: str = Field(min_length=1, max_length=260)
    date: str = ''
    prefecture: str = ''
    quote: str = Field(min_length=1, max_length=2000)
    merge_with: str = ''
    note: str = Field(min_length=1, max_length=1600)


class BatchArgs(Strict):
    accounts: list[AccountDecision] = Field(default_factory=list, max_length=500)
    claims: list[ClaimDecision] = Field(default_factory=list, max_length=500)
    activities: list[ActivityDecision] = Field(default_factory=list, max_length=500)

    @model_validator(mode='after')
    def unique(self):
        if not 1 <= len(self.accounts) + len(self.claims) + len(self.activities) <= 500:
            raise ValueError('一次原子提交需要 1 至 500 项；更多记录可分批提交，无总体数量停止。')
        for rows in (self.accounts, self.claims, self.activities):
            if len({row.id for row in rows}) != len(rows):
                raise ValueError('同一批不能重复审核同一记录。')
        return self


class AutoReviewArgs(Strict):
    kinds: list[Literal['accounts', 'claims', 'activities']] = Field(default_factory=lambda: ['accounts', 'claims', 'activities'], min_length=1, max_length=3)
    batch_size: int = Field(default=16, ge=1, le=40, strict=True)
    recheck: bool = False
    use_public_network: bool = True

    @model_validator(mode='after')
    def unique(self):
        self.kinds = list(dict.fromkeys(self.kinds))
        return self


class BatchApply(BatchArgs):
    preview_digest: str = Field(pattern=r'^[a-f0-9]{64}$')


class AutoReviewStart(AutoReviewArgs):
    input_digest: str = Field(pattern=r'^[a-f0-9]{64}$')


class ReviewConflict(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def activity_digest(item, activity):
    # Sibling reviews share item metadata. Only this activity and its original
    # evidence bind its decision, so another reviewed paragraph is not a conflict.
    return digest({'source': {key: item.get(key) for key in ('id', 'title', 'url', 'content', 'published_at')}, 'activity': activity})


def _queue(store, connection, kind, pending_only):
    from .account_identity import project_account
    from .activity_events import item_activities
    if kind == 'accounts':
        rows = []
        for raw in store._execute(connection, 'SELECT * FROM wb_accounts ORDER BY id').fetchall():
            account = project_account(store._account(raw))
            if account['status'] == 'excluded':
                continue
            if pending_only and account['account_type_details']['reviewed_at']:
                continue
            rows.append({'kind': kind, 'id': account['id'], 'expected_sha256': digest(dict(raw)), 'record': account})
        return rows
    if kind == 'claims':
        sql = 'SELECT * FROM wb_research_claims'
        if pending_only:
            sql += " WHERE status='candidate' AND reviewed_at IS NULL"
        rows = store._execute(connection, sql + ' ORDER BY id').fetchall()
        accounts = {f"{value['platform']}:{value['handle'].lstrip('@').lower()}": project_account(value)
                    for raw in store._execute(connection, 'SELECT * FROM wb_accounts').fetchall() if (value := store._account(raw))}
        return [{'kind': kind, 'id': row['id'], 'expected_sha256': digest(dict(row)), 'record': store._research_claim(row),
                 'account': accounts.get(row['subject_key'])} for row in rows]
    result = []
    rows = store._execute(connection, 'SELECT * FROM wb_items WHERE ' + store.item_visibility() + ' ORDER BY id').fetchall()
    for raw in rows:
        item = store._item(raw)
        for activity in item_activities(item):
            if pending_only and (activity['status'] != 'candidate' or activity.get('review_outcome') == 'inconclusive' and not activity.get('stale')):
                continue
            result.append({'kind': kind, 'id': activity['id'], 'expected_sha256': activity_digest(item, activity), 'record': activity,
                           'source': {key: item.get(key) for key in ('id', 'title', 'url', 'content', 'published_at')}})
    return result


def review_queue(store, *, kind='claims', pending_only=True, page=1, page_size=100):
    args = QueueArgs(kind=kind, pending_only=pending_only, page=page, page_size=page_size)
    with store._transaction() as connection:
        if store._postgres:
            store._execute(connection, 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        rows = _queue(store, connection, args.kind, args.pending_only)
    return {'items': rows[(page-1)*page_size:page*page_size], 'total': len(rows), **args.model_dump()}


def snapshot(store, kinds, *, pending_only=True, _connection=None):
    from contextlib import nullcontext
    with (nullcontext(_connection) if _connection is not None else store._transaction()) as connection:
        if store._postgres and _connection is None:
            store._execute(connection, 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        return [row for kind in kinds for row in _queue(store, connection, kind, pending_only)
                if kind != 'accounts' or not row['record']['account_type_details']['manual_locked']]


def _bound_rows(store, connection, payload):
    from .account_verification import _independent_source_url
    from .store import _url, _iso_date
    from .activity_events import PREFECTURES, item_activities
    bound = []
    suffix = ' FOR UPDATE' if store._postgres else ''
    for kind, table, records in [('accounts', 'wb_accounts', payload.accounts), ('claims', 'wb_research_claims', payload.claims), ('activities', 'wb_items', payload.activities)]:
        for decision in records:
            identity = decision.item_id if kind == 'activities' else decision.id
            raw = store._execute(connection, f'SELECT * FROM {table} WHERE id=?' + suffix, (identity,)).fetchone()
            if not raw:
                raise LookupError('所选审核记录不存在。')
            current_digest = digest(dict(raw))
            if kind == 'activities':
                item = store._item(raw)
                activity = next((row for row in item_activities(item) if row['id'] == decision.id), None)
                if activity is None:
                    raise LookupError('候选活动不存在。')
                current_digest = activity_digest(item, activity)
            if current_digest != decision.expected_sha256:
                raise ReviewConflict('审核材料或此前结论已改变；本批未写入，请重新读取与预览。')
            if kind == 'accounts':
                account = store._account(raw)
                if account['status'] == 'excluded':
                    raise ReviewConflict('已排除账号不参加自动类型审核。')
                identity_value = decision.identity
                if identity_value:
                    if not identity_value.note.strip():
                        raise ValueError('请提供账号类型核对说明。')
                    urls = [_independent_source_url(url) for url in identity_value.evidence_urls]
                    if identity_value.type != 'unknown' and not urls:
                        raise ValueError('批量判定组织或个人需要独立公开来源；无依据请记为未判定。')
                if decision.sources is not None:
                    for source in decision.sources:
                        _independent_source_url(source.url)
            elif kind == 'claims':
                if raw['run_id'] != decision.run_id:
                    raise ValueError('主张不属于所选研究。')
                if not decision.note.strip():
                    raise ValueError('请提供关系审核说明。')
                url = _url(decision.evidence_url)
                if decision.status == 'confirmed' and not url:
                    raise ValueError('确认关系需要公开证据链接。')
            else:
                item = store._item(raw)
                if (item.get('metadata', {}).get('atlas_cleanup') or {}).get('status') == 'trashed':
                    raise ReviewConflict('资料已回收，未审核活动。')
                if decision.id not in {row['id'] for row in item_activities(item)}:
                    raise LookupError('候选活动不存在。')
                if decision.quote.strip() not in item['title'] + '\n' + item['content']:
                    raise ValueError('活动摘录必须逐字存在于当前正文。')
                _iso_date(decision.date, '活动日期')
                if decision.prefecture and decision.prefecture not in PREFECTURES:
                    raise ValueError('活动举办地不是有效都道府县。')
                if decision.status == 'confirmed' and (not decision.date or not _url(item.get('url'))):
                    raise ValueError('确认活动需要活动日期与公开原文来源。')
                if decision.merge_with:
                    targets = [event for row in store._execute(connection, 'SELECT * FROM wb_items WHERE ' + store.item_visibility()).fetchall()
                               for event in item_activities(store._item(row)) if event.get('event_key') == decision.merge_with and event['status'] == 'confirmed']
                    if not any(event['date'] == decision.date and event.get('prefecture', '') == decision.prefecture for event in targets):
                        raise ValueError('活动合并目标不存在或实际日期、举办地不同。')
            bound.append({'kind': kind, 'id': decision.id, 'decision': decision.model_dump()})
    return bound


def preview_batch(store, arguments):
    payload = BatchArgs.model_validate(arguments)
    with store._transaction() as connection:
        selected = _bound_rows(store, connection, payload)
    return {'selected': selected, 'count': len(selected), 'digest': digest(selected), 'created_jobs': 0,
            'may_use_model': False, 'may_read_public_network': False, 'atomic': True}


def apply_batch(store, arguments, *, expected_digest, reviewer='external_agent', _after_apply=None):
    from .account_identity import correct_identity, register_sources
    payload = BatchArgs.model_validate(arguments)
    with store._transaction(write=True) as connection:
        selected = _bound_rows(store, connection, payload)
        if digest(selected) != expected_digest:
            raise ReviewConflict('审核预览与当前材料不一致，本批未写入。')
        saved = []
        for account in payload.accounts:
            if account.sources is not None:
                register_sources(store, account.id, [row.model_dump() for row in account.sources], _connection=connection, _schedule=False, _reviewer=reviewer)
            if account.identity is not None:
                value = correct_identity(store, account.id, **account.identity.model_dump(), _connection=connection, _schedule=False, _reviewer=reviewer)
                saved.append({'kind': 'accounts', 'id': account.id, 'type': value['account_type']})
            else:
                saved.append({'kind': 'accounts', 'id': account.id, 'sources_registered': len(account.sources)})
        for claim in payload.claims:
            store.review_research_claim(claim.run_id, claim.id, claim.status, claim.note, claim.evidence_url, _connection=connection)
            saved.append({'kind': 'claims', 'id': claim.id, 'outcome': claim.status})
        for activity in payload.activities:
            value = activity.model_dump(exclude={'item_id', 'expected_sha256'})
            store.review_activity(activity.item_id, value, _connection=connection, _reviewer=reviewer)
            saved.append({'kind': 'activities', 'id': activity.id, 'outcome': activity.status})
        result = {'applied': len(saved), 'items': saved, 'atomic': True, 'created_jobs': 0, 'original_material_retained': True}
        if _after_apply is not None:
            _after_apply(connection, result)
    return result


def review_estimate(store, arguments):
    from .pipeline import capabilities
    payload = AutoReviewArgs.model_validate(arguments)
    rows = snapshot(store, payload.kinds, pending_only=not payload.recheck)
    counts = dict(Counter(row['kind'] for row in rows))
    batches = (len(rows) + payload.batch_size - 1) // payload.batch_size
    caps = capabilities()
    return {'params': payload.model_dump(), 'counts': counts, 'total': len(rows), 'input_digest': digest(rows),
            'model': caps['llm']['model'], 'ai_configured': caps['llm']['configured'],
            'harness_available': caps['analysis_harness']['available'], 'batches': batches,
            'lower_seconds': batches * 25, 'upper_seconds': batches * 180 + (counts.get('accounts', 0) * 25 if payload.use_public_network else 0),
            'confidence': 'low', 'basis': '按小批工具调查估算；网页和模型响应影响实际耗时，未按本机校准。',
            'may_use_model': bool(rows), 'may_read_public_network': payload.use_public_network,
            'global_record_limit': None, 'global_time_limit': None, 'created_jobs': 0,
            'note': '逐批保存。证据不足是已完成的审核结论；账号核验、账号类型和关系事实分别记录。'}


def start_review(store, arguments, *, expected_digest):
    from .store import _now
    payload = AutoReviewArgs.model_validate(arguments)
    with store._transaction(write=True) as connection:
        if store._postgres:
            store._execute(connection, 'SELECT pg_advisory_xact_lock(1278032975)')
        for raw in store._execute(connection, "SELECT * FROM wb_jobs WHERE kind='collect' AND status IN ('queued','running')").fetchall():
            if store._job(raw)['params'].get('_evidence_review') is True:
                raise ReviewConflict('已有自动审核任务正在执行，请查看任务中心。')
        rows = snapshot(store, payload.kinds, pending_only=not payload.recheck, _connection=connection)
        if digest(rows) != expected_digest:
            raise ReviewConflict('待审范围发生变化，请重新预估。')
        if not rows:
            raise ValueError('当前没有需要审核的内容。')
        job = store.create_job('collect', {'_evidence_review': True, 'source': 'evidence_review',
            'review_options': payload.model_dump(), 'review_snapshot': rows, 'snapshot_digest': digest(rows), 'planned_at': _now()}, _connection=connection)
    return job
