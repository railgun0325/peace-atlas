import test from 'node:test';
import assert from 'node:assert/strict';
import { budgetLimits, durationText } from '../budget_limits.js';
import { normalizeBatchPlan } from '../batch.js';

test('unavailable capabilities preserve practical research defaults and inclusive expanded limits', () => {
  const limits = budgetLimits();
  assert.deepEqual(limits.research.max_records, { min: 0, max: 1000000, default: 0 });
  assert.deepEqual(limits.research.time_budget_seconds, { min: 0, max: 2592000, default: 0 });
  assert.equal(limits.research.depth.max, 8);
  assert.equal(limits.research.max_accounts.default, 0);
  assert.equal(limits.research.max_edges.default, 0);
  assert.equal(limits.research.max_scrolls.max, 5000);
  assert.equal(limits.collect.max_records.max, 100);
  assert.equal(limits.collect.max_scrolls.max, 20);
  assert.equal(limits.batch.max_tasks, 200);
  limits.research.max_records.max = 1;
  assert.equal(budgetLimits().research.max_records.max, 1000000, 'A caller must not mutate future fallback budgets');
});

test('service capabilities remain authoritative and default values fit their advertised range', () => {
  const capabilities = { budget_limits: {
    research: { max_records: { min: 2, max: 750, default: 1000 }, max_scrolls: { min: 2, max: 50, default: 1 } },
    batch: { total_records: { min: 1, max: 2000, default: 1500 }, max_tasks: 12 },
  } };
  const before = JSON.stringify(capabilities);
  const limits = budgetLimits(capabilities);
  assert.deepEqual(limits.research.max_records, { min: 2, max: 750, default: 750 });
  assert.deepEqual(limits.research.max_scrolls, { min: 2, max: 50, default: 2 });
  assert.equal(limits.batch.max_tasks, 12);
  assert.equal(limits.batch.total_records.default, 1500);
  assert.equal(JSON.stringify(capabilities), before);
});

test('malformed metadata cannot produce zero or inverted ranges and legacy research ceilings still apply', () => {
  const limits = budgetLimits({ research: { max_scrolls: 40 }, budget_limits: {
    research: { max_records: { min: -1, max: '10000', default: '50' }, max_scrolls: { min: 100, max: 0 } },
    batch: { max_tasks: -1 },
  } });
  assert.deepEqual(limits.research.max_records, { min: 0, max: 1000000, default: 0 });
  assert.deepEqual(limits.research.max_scrolls, { min: 1, max: 40, default: 10 });
  assert.equal(limits.batch.max_tasks, 200);
});

test('time budgets explain hours and minutes without losing a nonzero remainder', () => {
  for (const [seconds, expected] of [[0, '0 秒'], [30, '30 秒'], [1800, '30 分钟'], [3600, '1 小时'], [3661, '1 小时 1 分钟 1 秒'], [86400, '24 小时']]) {
    assert.equal(durationText(seconds), expected);
  }
  assert.equal(durationText('3600'), '1 小时');
  assert.equal(durationText(-1), '待填写');
  assert.equal(durationText(NaN), '待填写');
});

test('batch preview supports research records above the former 200 ceiling', () => {
  const plan = normalizeBatchPlan({
    tasks: [{ kind: 'research', params: { max_records: 500, max_scrolls: 25, time_budget_seconds: 3600 }, label: '公开研究' }],
    total_records: 1000, max_possible_records: 500,
  });
  assert.deepEqual(plan.tasks[0].params, { max_records: 500, max_scrolls: 25, time_budget_seconds: 3600 });
});

test('batch preview validates against service-provided per-task, series and task-count budgets', () => {
  const limits = budgetLimits({ budget_limits: {
    research: { max_records: { min: 1, max: 500, default: 50 } },
    batch: { total_records: { min: 1, max: 1000, default: 1000 }, max_tasks: 2 },
  } });
  const task = { kind: 'research', params: { max_records: 500 } };
  const plan = { tasks: [task], total_records: 1000, max_possible_records: 500 };
  assert.equal(normalizeBatchPlan(plan, limits).tasks[0].params.max_records, 500);
  assert.throws(() => normalizeBatchPlan({ ...plan, tasks: [{ ...task, params: { max_records: 501 } }] }, limits), /任务预算/);
  assert.throws(() => normalizeBatchPlan({ ...plan, tasks: [task, task, task] }, limits), /有效任务/);
  assert.throws(() => normalizeBatchPlan({ ...plan, total_records: 1001 }, limits), /总体预算/);
});
