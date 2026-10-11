import test from 'node:test';
import assert from 'node:assert/strict';
import { continuationFields, continuationLimits, continuationRequest, continuationState } from '../research_resume_model.js';

const caps = { research: { available: true, max_accounts: 24, max_edges: 120 }, budget_limits: { research: {
  max_records: { min: 1, max: 700, default: 50 }, time_budget_seconds: { min: 30, max: 7200, default: 1800 },
} } };
const run = () => ({ id: 'saved-run', status: 'partial', frontier: [], params: {
  max_accounts: 12, max_edges: 60, max_records: 50, time_budget_seconds: 1800,
  source: 'twitter', handle: 'public_root', depth: 2, keyword: '反戦', start_date: '2026-09-01', end_date: '2026-10-09',
}, summary: { collection_stop_reason: 'account_budget', deferred_frontier_version: 1, pending_work_version: 1,
  deferred_count: 3, deferred_record_count: 2, deferred_relation_count: 1 } });
const values = () => ({ max_accounts: 24, max_edges: 60, max_records: 50, time_budget_seconds: 1800 });

test('continuation sends four budget fields and preserves immutable saved research scope', () => {
  const saved = run(), before = JSON.stringify(saved);
  assert.deepEqual(continuationRequest(saved, { ...values(), source: 'instagram', depth: 3, resume_run_id: 'wrong' }, continuationLimits(caps)), values());
  assert.equal(JSON.stringify(saved), before);
  assert.deepEqual(Object.keys(continuationFields), ['max_accounts', 'max_edges', 'max_records', 'time_budget_seconds']);
});

test('actual capabilities govern all adjustable limits and missing service metadata blocks continuation', () => {
  const limits = continuationLimits(caps);
  assert.equal(limits.max_accounts.max, 24);
  assert.equal(limits.max_edges.max, 120);
  assert.equal(limits.max_records.max, 700);
  assert.equal(limits.time_budget_seconds.max, 7200);
  assert.throws(() => continuationLimits(), /继续研究能力/);
  assert.throws(() => continuationLimits({ research: { available: true, max_accounts: true, max_edges: 120 } }), /完整研究上限/);
  assert.throws(() => continuationRequest(run(), { ...values(), max_accounts: 25 }, limits), /1–24/);
  assert.throws(() => continuationRequest(run(), { ...values(), max_records: '' }, limits), /材料上限/);
});

test('hard stops require the corresponding capacity increase and reject shrinking cumulative limits', () => {
  for (const [reason, name] of [['account_budget', 'max_accounts'], ['edge_budget', 'max_edges'], ['record_budget', 'max_records']]) {
    const saved = run(); saved.summary.collection_stop_reason = reason;
    const unchanged = { ...saved.params, time_budget_seconds: 3600 };
    assert.throws(() => continuationRequest(saved, unchanged, continuationLimits(caps)), /提高该上限/);
    unchanged[name]++;
    assert.equal(continuationRequest(saved, unchanged, continuationLimits(caps))[name], unchanged[name]);
  }
  assert.throws(() => continuationRequest(run(), { ...values(), max_edges: 59 }, continuationLimits(caps)), /不能低于/);
  const ceiling = run(); ceiling.params.max_accounts = 24;
  assert.throws(() => continuationRequest(ceiling, values(), continuationLimits(caps)), /当前能力无法继续扩容/);
  assert.throws(() => continuationRequest(run(), { ...values(), time_budget_seconds: 60 }, continuationLimits(caps)), /不能低于当前研究上限 1800/);
});

test('all recorded hard-cap hits must advance and a queued continuation uses the recorded stopped limit', () => {
  const saved = run(); saved.summary.limit_hits = ['account_budget', 'edge_budget'];
  assert.throws(() => continuationRequest(saved, values(), continuationLimits(caps)), /关系上限/);
  assert.equal(continuationRequest(saved, { ...values(), max_edges: 120 }, continuationLimits(caps)).max_edges, 120);
  saved.params.max_accounts = 24; saved.summary.max_accounts = 12;
  assert.equal(continuationRequest(saved, { ...values(), max_edges: 120 }, continuationLimits(caps)).max_accounts, 24);
});

test('cancelled queued work can use unchanged bounds while active or completed branches cannot continue', () => {
  const saved = run(); saved.status = 'cancelled'; saved.summary.collection_stop_reason = 'cancelled';
  saved.frontier = [{ account_key: 'twitter:pending' }];
  assert.equal(continuationRequest(saved, saved.params, continuationLimits(caps)).max_accounts, 12);
  saved.status = 'running';
  assert.throws(() => continuationRequest(saved, values(), continuationLimits(caps)), /已有待执行/);
  saved.status = 'succeeded'; saved.frontier = []; saved.summary = { collection_complete: true };
  assert.equal(continuationState(saved).available, false);
});

test('legacy rejection attempts never become candidate counts and saved pending work remains distinct', () => {
  const saved = run(); saved.summary = { account_budget_skipped: 49, frontier_recovery_required: true };
  let state = continuationState(saved);
  assert.equal(state.deferred, null); assert.equal(state.legacyGap, true); assert.equal(state.available, false);
  assert.match(state.reason, /缺口未知/);
  saved.frontier = [{ account_key: 'twitter:pending' }];
  state = continuationState(saved);
  assert.equal(state.available, true); assert.equal(state.legacyGap, true); assert.equal(state.queued, 1);
  saved.frontier = []; saved.summary = { pending_work_version: 1, pending_account_work_count: 1 };
  assert.equal(continuationState(saved).available, true);
});

test('server availability overrides local backlog inference without enabling an active branch', () => {
  const saved = run(); saved.summary.continuation_available = false; saved.summary.continuation_reason = '剩余线索均已清理';
  assert.equal(continuationState(saved).available, false);
  assert.equal(continuationState(saved).reason, '剩余线索均已清理');
  saved.frontier = []; saved.summary = { continuation_available: true };
  assert.equal(continuationState(saved).available, true);
  saved.status = 'queued';
  assert.equal(continuationState(saved).available, false);
});

test('unfinished news query entrances count as saved work without inventing account candidates', () => {
  const saved = run(); saved.params = { ...saved.params, source: 'google_news', mode: 'keyword' };
  saved.summary = { pending_seed_queries_version: 1, pending_seed_query_count: 2,
    continuation_pending_queries: 3, deferred_count: 0, deferred_record_count: 0, deferred_relation_count: 0 };
  let state = continuationState(saved);
  assert.equal(state.pendingQueries, 2); assert.equal(state.available, true);
  assert.equal(state.deferred, 0); assert.equal(state.queued, 0);
  saved.summary.pending_seed_query_count = 0;
  assert.equal(continuationState(saved).pendingQueries, 0);
  delete saved.summary.pending_seed_query_count;
  assert.equal(continuationState(saved).pendingQueries, 3);
  saved.summary.pending_seed_query_count = true;
  assert.equal(continuationState(saved).pendingQueries, 3);
  saved.summary = { pending_seed_queries: [{ source: 'google_news', query_id: 'opaque query' }] };
  state = continuationState(saved);
  assert.equal(state.pendingQueries, null); assert.equal(state.available, false);
});
