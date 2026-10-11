import test from 'node:test';
import assert from 'node:assert/strict';
import { reviewProgress } from '../review.js';

test('newly queued review has readable progress before the first result exists', () => {
  assert.match(reviewProgress(null), /已保存 0 \/ 0 项/);
  assert.match(reviewProgress(undefined), /准备自动审核/);
});
test('completed review displays durable counts without promising all facts are true', () => {
  const saved = { completed: 271, total: 271, phase: 'complete', model_calls: 46, tool_calls: 119 };
  const text = reviewProgress(saved);
  assert.match(text, /271 \/ 271/);
  assert.match(text, /审核流程已完成/);
  assert.match(text, /模型 46 次 \/ 工具 119 次/);
  assert.doesNotMatch(text, /全部属实|全部核验/);
  assert.equal(saved.completed, 271);
});
