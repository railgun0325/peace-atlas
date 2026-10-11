import test from 'node:test';
import assert from 'node:assert/strict';
import { workflowValues, primaryResearchSource } from '../workflow_model.js';

test('legacy research and simple drafts migrate without losing query, dates or budget', () => {
  const shared = { keyword: '革命, 労働者', start_date: '2026-09-01', end_date: '2026-10-06', max_records: '83', total_records: '1300' };
  for (const [legacy, kind] of [['research', 'research'], ['simple', 'collect']]) {
    const migrated = workflowValues({ ...shared, workflow: legacy, batch_kind: 'research' });
    assert.equal(migrated.workflow, 'single'); assert.equal(migrated.batch_kind, kind);
    for (const [key, value] of Object.entries(shared)) assert.equal(migrated[key], value);
  }
});
test('legacy batches keep both their content type and unchanged budgets', () => {
  for (const kind of ['collect', 'research']) {
    const migrated = workflowValues({ workflow: 'batch', batch_kind: kind, max_records: '50' });
    assert.equal(migrated.workflow, 'batch'); assert.equal(migrated.batch_kind, kind); assert.equal(migrated.max_records, '50');
  }
});
test('explicit account collection overrides a research draft while retaining unrelated user input', () => {
  const migrated = workflowValues({ workflow: 'research', source: 'instagram', handle: 'old_account', keyword: '平和', max_records: '91' }, { kind: 'collect', workflow: 'single', source: 'twitter', handle: 'selected_account' });
  assert.deepEqual([migrated.batch_kind, migrated.workflow, migrated.source, migrated.handle, migrated.keyword, migrated.max_records], ['collect', 'single', 'twitter', 'selected_account', '平和', '91']);
});
test('planning a follow-up series changes execution without changing content or per-task limit', () => {
  const migrated = workflowValues({ workflow: 'simple', batch_kind: 'research', max_records: '71' }, { workflow: 'batch' });
  assert.equal(migrated.workflow, 'batch'); assert.equal(migrated.batch_kind, 'collect'); assert.equal(migrated.max_records, '71');
});
test('fresh news research does not silently select a social platform', () => {
  const values = workflowValues();
  assert.equal(values.seed_news, true); assert.equal(values.seed_twitter, false); assert.equal(values.seed_instagram, false);
  assert.equal(primaryResearchSource(values), 'google_news');
});
test('existing seed selections are preserved and an explicit source can be added', () => {
  const saved = { seed_news: false, seed_twitter: true, seed_instagram: false };
  assert.equal(primaryResearchSource(workflowValues(saved)), 'twitter');
  const explicit = workflowValues(saved, { source: 'instagram' });
  assert.equal(explicit.seed_news, false); assert.equal(explicit.seed_twitter, true); assert.equal(explicit.seed_instagram, true);
});
test('the first checked research seed is primary; no seeds cannot become an unselected source', () => {
  assert.equal(primaryResearchSource({ seed_news: false, seed_twitter: false, seed_instagram: false }), '');
  assert.equal(primaryResearchSource({ seed_news: true, seed_twitter: true }), 'google_news');
  assert.equal(primaryResearchSource({ seed_instagram: true }), 'instagram');
});
test('an explicit account platform stays primary when news is also selected', () => {
  const values = workflowValues({ seed_news: true, seed_twitter: false }, { source: 'twitter', handle: 'zengakuren', kind: 'research' });
  assert.equal(values.handle, 'zengakuren'); assert.equal(values.batch_kind, 'research');
  assert.equal(primaryResearchSource(values, values.source), 'twitter');
  assert.equal(primaryResearchSource({ ...values, seed_twitter: false }, 'twitter'), 'google_news');
});
test('unsupported old content choices normalize safely and RSS remains a collection source', () => {
  const migrated = workflowValues({ workflow: 'simple', source: 'rss', feed_url: 'https://example.org/rss', batch_kind: 'unknown' });
  assert.equal(migrated.batch_kind, 'collect'); assert.equal(migrated.source, 'rss'); assert.equal(migrated.feed_url, 'https://example.org/rss');
});
