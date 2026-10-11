import test from 'node:test';
import assert from 'node:assert/strict';
import { keywordGroups, keywordQueries, relatedKeywords, appendKeyword, normalizeSuggestions } from '../keyword_tools.js';
import { normalizeDraft, readDraft, writeDraft, removeDraft, removeDraftIf, sameDraft, captureDraft, restoreDraft, preferredDraft, draftSynchronizer, COLLECTION_DRAFT_KEY } from '../drafts.js';
import { normalizeBatchPlan, batchIntentKey, batchSummary } from '../batch.js';
import { collectParams } from '../social.js';

const sample = (keyword = '憲法九条', updated_at = '2026-10-06T00:00:00Z') => ({ schema: 1, values: { keyword, source: 'twitter', include_following: false }, modeBySource: { twitter: 'account' }, updated_at });

test('keyword groups accept comma, Chinese comma, semicolon and newline while preserving within-group AND spaces', () => {
  assert.deepEqual(keywordGroups('憲法九条，沖縄  平和運動;核兵器廃絶；反戦\n憲法九条'), ['憲法九条', '沖縄 平和運動', '核兵器廃絶', '反戦']);
  assert.deepEqual(keywordQueries('沖縄 平和運動, 憲法九条', 'any'), ['沖縄 平和運動', '憲法九条']);
  assert.deepEqual(keywordQueries('沖縄 平和運動, 憲法九条', 'all'), ['沖縄 平和運動 憲法九条']);
  assert.deepEqual(keywordQueries('', 'any'), []);
  assert.throws(() => keywordQueries('平和', 'wrong'), /匹配/);
  assert.throws(() => keywordQueries(Array.from({ length: 13 }, (_, i) => `term${i}`).join(',')), /12 组/);
});

test('hashtag syntax disallows spaces, empty input and grouped hashtags', () => {
  assert.deepEqual(keywordQueries(' #反戦 ', 'any', 'hashtag'), ['反戦']);
  for (const value of ['', '#平和 運動', '#反戦,#平和', '#反戦\n#平和']) assert.throws(() => keywordQueries(value, 'any', 'hashtag'), /一个.*空格/);
});

test('frontend keyword grammar agrees with source plans for fullwidth normalization, synonyms, duplicates and forbidden operators', () => {
  assert.deepEqual(keywordGroups('憲法９条，憲法九条；PEACE, peace'), ['憲法9条', 'PEACE']);
  assert.deepEqual(keywordQueries('沖縄 平和, 沖縄 九条', 'all'), ['沖縄 平和 九条']);
  for (const value of ['from:user', '平和 OR 九条', '"平和"', '(反戦)', '+must']) assert.throws(() => keywordQueries(value), /普通关键词/);
  assert.throws(() => keywordQueries('a'.repeat(1201)), /1200/);
  assert.throws(() => keywordQueries('a'.repeat(301)), /300/);
});

test('simple jobs explicitly carry the multi-keyword mode and reject platform-invalid usernames and hashtags', () => {
  assert.equal(collectParams({ source: 'google_news', keyword: '反戦, 九条', keyword_mode: 'any' }).keyword_mode, 'any');
  assert.equal(collectParams({ source: 'twitter', keyword: '反戦, 九条', keyword_mode: 'all' }).keyword_mode, 'all');
  assert.throws(() => collectParams({ source: 'twitter', mode: 'account', handle: 'https://x.com/somebody' }), /用户名/);
  assert.throws(() => collectParams({ source: 'instagram', mode: 'hashtag', keyword: '平和-運動' }), /文字/);
});

test('related keyword generation preserves user terms and additive suggestions do not overwrite input', () => {
  const suggestions = relatedKeywords('憲法九条');
  assert.ok(suggestions.some(item => item.term === '九条の会'));
  assert.ok(suggestions.every(item => item.source === 'built_in'));
  assert.deepEqual(relatedKeywords(''), []);
  assert.equal(appendKeyword('用户自己的词,沖縄 平和運動', '憲法九条'), '用户自己的词\n沖縄 平和運動\n憲法九条');
  assert.equal(appendKeyword('憲法九条', '憲法九条'), '憲法九条');
  assert.throws(() => appendKeyword('憲法九条', 'a,b'), /完整/);
  assert.ok(relatedKeywords('#反戦', 'hashtag').every(item => !/\s/.test(item.term)));
});

test('planner suggestions are bounded, de-duplicated text and reject multi-group output', () => {
  const data = { keywords: ['反戦', { term: '反戦', reason: '重复' }, { term: '沖縄 平和運動', reason: '<script>untrusted</script>' }, { term: 'a,b' }, { term: '' }] };
  assert.deepEqual(normalizeSuggestions(data).map(item => item.term), ['反戦', '沖縄 平和運動']);
  assert.equal(normalizeSuggestions(data)[1].reason, '<script>untrusted</script>');
  assert.deepEqual(normalizeSuggestions({}), []);
});

test('draft schema preserves all editable values but never stores arbitrary session or password fields', () => {
  const draft = normalizeDraft({ ...sample(), values: { ...sample().values, password: 'never', token: 'never', max_records: '50' }, modeBySource: { twitter: 'account', instagram: 'hashtag', other: 'account' } });
  assert.deepEqual(draft.values, { keyword: '憲法九条', source: 'twitter', include_following: false, max_records: '50' });
  assert.deepEqual(draft.modeBySource, { twitter: 'account', instagram: 'hashtag' });
  assert.equal(normalizeDraft({ ...sample(), updated_at: 'invalid' }), null);
  assert.equal(normalizeDraft({ ...sample(), schema: 9 }), null);
  assert.equal(normalizeDraft({ ...sample(), values: { keyword: 'x'.repeat(2401) } }), null);
  assert.equal(normalizeDraft({ ...sample(), values: { total_records: '1000', batch_title: '研究任务串', batch_kind: 'research' } }).values.total_records, '1000');
});

test('local draft survives reopen and corrupted/unavailable storage falls back safely', () => {
  const values = new Map(); const storage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
  assert.equal(writeDraft(sample(), storage), true);
  assert.equal(readDraft(storage).values.keyword, '憲法九条');
  removeDraft(storage); assert.equal(readDraft(storage), null);
  values.set(COLLECTION_DRAFT_KEY, '{broken'); assert.equal(readDraft(storage), null);
  assert.equal(writeDraft(sample(), { setItem() { throw new Error('quota'); } }), false);
});

test('draft capture includes disabled fields and unchecked booleans, then restores supported selections', () => {
  const inputs = [{ name: 'keyword', value: '原始输入', tagName: 'TEXTAREA', disabled: true }, { name: 'include_following', type: 'checkbox', checked: false }, { name: 'source', value: 'twitter', tagName: 'SELECT', options: [{ value: 'twitter' }, { value: 'rss' }] }, { name: 'password', value: 'never' }];
  const form = { querySelectorAll: () => inputs };
  const draft = captureDraft(form, { twitter: 'account' }, new Date('2026-10-06T00:00:00Z'));
  inputs[0].value = ''; inputs[1].checked = true; inputs[2].value = 'rss';
  restoreDraft(form, draft);
  assert.equal(inputs[0].value, '原始输入'); assert.equal(inputs[1].checked, false); assert.equal(inputs[2].value, 'twitter');
  assert.equal(Object.hasOwn(draft.values, 'password'), false);
});

test('latest persisted draft wins across ports; local timestamp tie never replaces unsynced input', () => {
  assert.equal(preferredDraft(sample('local', '2026-10-06T01:00:00Z'), sample('server')).values.keyword, 'local');
  assert.equal(preferredDraft(sample('local'), sample('server', '2026-10-06T02:00:00Z')).values.keyword, 'server');
  assert.equal(preferredDraft(sample('local'), sample('server')).values.keyword, 'local');
});

test('server draft writes are serialized and a successful submit clear always happens after pending writes', async () => {
  const calls = []; let release;
  const first = new Promise(resolve => { release = resolve; });
  const sync = draftSynchronizer({ save: async draft => { calls.push(draft.values.keyword); if (draft.values.keyword === 'first') await first; }, clear: async () => calls.push('clear'), delay: 10000 });
  sync.changed(sample('first')); const one = sync.flush(); await Promise.resolve(); await Promise.resolve();
  sync.changed(sample('second')); sync.flush();
  const cleared = sync.clear(); release(); await one; await cleared;
  assert.deepEqual(calls, ['first', 'second', 'clear']);
  sync.changed(sample('late')); await sync.flush(); assert.deepEqual(calls, ['first', 'second', 'clear']);
});

test('failed sync preserves the local draft and closing flushes the last edit', async () => {
  const statuses = [], saved = [];
  const sync = draftSynchronizer({ save: async draft => { saved.push(draft.values.keyword); throw new Error('offline'); }, clear: async () => {}, onStatus: state => statuses.push(state), delay: 10000 });
  sync.changed(sample('last edit')); await sync.close();
  assert.deepEqual(saved, ['last edit']); assert.deepEqual(statuses, ['local', 'local']);
});

test('accepted tasks clear only their exact local snapshot, regardless of added save revision', () => {
  const values = new Map(); const storage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
  const submitted = sample('submitted');
  writeDraft({ ...submitted, revision: 3 }, storage);
  assert.equal(sameDraft(readDraft(storage), submitted), true);
  assert.equal(removeDraftIf(submitted, storage), true);
  const newer = sample('newer', '2026-10-06T00:01:00Z');
  writeDraft(newer, storage);
  assert.equal(removeDraftIf(submitted, storage), false);
  assert.equal(readDraft(storage).values.keyword, 'newer');
  assert.equal(sameDraft(sample('submitted', newer.updated_at), submitted), false);
});

test('conditional clear preserves a newer pending edit and the synchronizer remains writable', async () => {
  const calls = [];
  const sync = draftSynchronizer({ save: async draft => calls.push(draft.values.keyword), clear: async () => calls.push('clear'), delay: 10000 });
  sync.changed(sample('newer'));
  assert.equal(await sync.clearIf(() => false), false);
  sync.changed(sample('later')); await sync.close();
  assert.deepEqual(calls, ['newer', 'later']);
});

test('edits during a conditional clear queue after it and remain synchronizable', async () => {
  const calls = []; let release;
  const held = new Promise(resolve => { release = resolve; });
  const sync = draftSynchronizer({ save: async draft => calls.push(draft.values.keyword), clear: async () => { calls.push('clear'); await held; }, delay: 10000 });
  sync.changed(sample('submitted'));
  const clearing = sync.clearIf(() => true);
  while (!calls.includes('clear')) await Promise.resolve();
  sync.changed(sample('newer'));
  const saved = sync.flush(); release();
  assert.equal(await clearing, true); await saved; await sync.close();
  assert.deepEqual(calls, ['submitted', 'clear', 'newer']);
});

test('batch previews preserve task budgets and declared targets without implying achieved counts', () => {
  const plan = normalizeBatchPlan({ tasks: [{ kind: 'research', params: { max_records: 200, keyword: '反戦' }, label: '公开材料' }, { kind: 'collect', params: { max_records: 100, keyword: '九条' } }], total_records: 1000, max_possible_records: 300, warnings: ['本次日期窗口最多保存300条'], title: '任务串' });
  assert.equal(plan.tasks.length, 2); assert.equal(plan.total_records, 1000); assert.equal(plan.max_possible_records, 300);
  assert.equal(plan.warnings[0], '本次日期窗口最多保存300条');
  assert.equal(Object.hasOwn(plan, 'inserted'), false);
});

test('batch preview rejects empty tasks, more than 200 jobs and per-job budget violations', () => {
  const task = { kind: 'research', params: { max_records: 200 }, label: 'test' };
  const base = { tasks: [task], total_records: 1000, max_possible_records: 1000 };
  for (const tasks of [[], Array.from({ length: 201 }, () => task), [{ kind: 'collect', params: { max_records: 101 } }], [{ kind: 'research', params: { max_records: 1000001 } }], [{ kind: 'unknown', params: { max_records: 1 } }]]) assert.throws(() => normalizeBatchPlan({ ...base, tasks }), /预算|有效任务/);
  assert.equal(normalizeBatchPlan({ ...base, tasks: [{ kind: 'research', params: { max_records: 1000000 } }] }).tasks[0].params.max_records, 1000000);
  for (const total_records of [0, 1000001, 1.5]) assert.throws(() => normalizeBatchPlan({ ...base, total_records }), /总体预算/);
});

test('batch preview identity changes when target, query, date, relation direction or goal changes', () => {
  const intent = { kind: 'research', title: '', total_records: 1000, params: { keyword: '反戦', start_date: '2026-09-01', end_date: '2026-10-06', research_goal: '联动', expand_relations: ['following'] } };
  for (const change of [{ total_records: 2000 }, { params: { ...intent.params, keyword: '九条' } }, { params: { ...intent.params, start_date: '2026-10-01' } }, { params: { ...intent.params, expand_relations: ['mentions'] } }, { params: { ...intent.params, research_goal: '组织隶属' } }]) assert.notEqual(batchIntentKey(intent), batchIntentKey({ ...intent, ...change }));
});

test('a 100 percent ended batch with failures or partial work is never reported as all succeeded', () => {
  const summary = batchSummary({ status: 'partial', progress: 100, task_count: 5, counts: { succeeded: 2, failed: 1, partial: 1, cancelled: 1 }, records_saved: 17, total_records: 1000 });
  assert.equal(summary.complete, false); assert.equal(summary.ended, 5); assert.equal(summary.succeeded, 2); assert.equal(summary.retryable, 3); assert.equal(summary.saved, 17);
  assert.equal(batchSummary({ status: 'succeeded', task_count: 2, counts: { succeeded: 2 } }).complete, true);
  const hardStopped = reason => ({ kind: 'research', status: 'partial', result: { summary: { stop_reason: reason } } });
  const jobs = ['record_budget', 'account_budget', 'edge_budget', 'time_budget', 'branch_errors'].map(hardStopped);
  assert.equal(batchSummary({ status: 'partial', jobs }).retryable, 2);
  assert.equal(batchSummary({ status: 'partial', jobs, retryable_count: 0 }).retryable, 0);
  assert.equal(batchSummary({ status: 'partial', jobs, retryable_count: 1 }).retryable, 1);
});
