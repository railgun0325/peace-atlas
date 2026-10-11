import test from 'node:test';
import assert from 'node:assert/strict';
import { accountTypeSummary, accountTypeControls } from '../account_type.js';
import { accountTypeBasisName, accountTypeInfo } from '../network_model.js';
import { reviewProgress } from '../review.js';
import { jobTitle } from '../social.js';

function installDOM(t) {
  class InertNode {
    constructor(tag = '', text = '') { this.tagName = tag.toUpperCase(); this.attributes = {}; this.children = []; this.text = text; this.events = {}; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name] ?? null; }
    addEventListener(name, callback) { this.events[name] = callback; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; this.text = ''; }
    remove() {}
    get textContent() { return this.text + this.children.map(child => child.textContent).join(''); }
    querySelectorAll(tag) { return this.children.flatMap(child => [...(child.tagName.toLowerCase() === tag.toLowerCase() ? [child] : []), ...child.querySelectorAll(tag)]); }
  }
  const prior = { Node: globalThis.Node, document: globalThis.document };
  globalThis.Node = InertNode;
  const notifications = new InertNode('div');
  globalThis.document = { createElement: tag => new InertNode(tag), createTextNode: value => new InertNode('#text', value), getElementById: () => notifications };
  t.mock.method(globalThis, 'setTimeout', () => 1);
  t.after(() => { for (const [key, value] of Object.entries(prior)) { if (value === undefined) delete globalThis[key]; else globalThis[key] = value; } });
}

test('fast classification displays approximate source and confidence without an identity claim', t => {
  installDOM(t);
  const account = { account_type: 'organization', account_type_details: { basis: 'fast_model_classification', confidence: 'medium', note: '保存简介写明集体运营' } };
  const summary = accountTypeSummary(account);
  assert.match(summary.textContent, /组织.*DS 快速粗分.*可手动更改.*一般把握.*集体运营/);
  assert.doesNotMatch(summary.textContent, /已核验|官方账号/);
  assert.equal(accountTypeBasisName(accountTypeInfo(account)), 'DS 快速粗分 · 可手动更改');
  assert.match(reviewProgress({ classification_version: 1, phase: 'complete', completed: 100, total: 100, model_calls: 1, tool_calls: 0 }), /100 \/ 100.*类型粗分已完成.*模型 1 次/);
  assert.equal(jobTitle({ params: { _account_classification: true } }), 'DS 快速判定账号类型');
});

test('each quick type action makes one manual locked correction and refreshes saved result', async t => {
  installDOM(t);
  const requests = [], refreshed = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    const body = JSON.parse(options.body); requests.push({ url, method: options.method, body });
    return new Response(JSON.stringify({ id: 'fixture', account_type: body.type, status: 'verified' }), { status: 200 });
  });
  const account = { id: 'fixture', account_type: 'unknown' };
  const controls = accountTypeControls({ signal: new AbortController().signal }, account, value => refreshed.push(value));
  const actions = controls.querySelectorAll('button');
  assert.deepEqual(actions.map(row => row.textContent), ['设为组织', '设为个人', '设为未判定']);
  for (const control of actions) await control.events.click();
  assert.deepEqual(requests.map(row => row.body.type), ['organization', 'individual', 'unknown']);
  assert.ok(requests.every(row => row.url === '/api/v2/accounts/fixture/identity' && row.method === 'PATCH' && row.body.manual_locked === true && row.body.evidence_urls.length === 0));
  assert.equal(refreshed.length, 3);
  assert.ok(refreshed.every(row => row.status === 'verified'));
  assert.ok(actions.every(row => row.disabled === false));
});

test('explicit unlock only changes the lock endpoint and preserves type provenance', async t => {
  installDOM(t);
  const requests = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push({ url, body: JSON.parse(options.body) });
    return new Response(JSON.stringify({ id: 'fixture', account_type: 'individual' }), { status: 200 });
  });
  const controls = accountTypeControls({ signal: new AbortController().signal }, { account_id: 'fixture', account_type: 'individual', account_type_details: { manual_locked: true } }, () => {});
  const unlock = controls.querySelectorAll('button').find(row => row.textContent === '解除人工锁定');
  assert.ok(unlock); await unlock.events.click();
  assert.deepEqual(requests, [{ url: '/api/v2/accounts/fixture/type-lock', body: { manual_locked: false } }]);
});

test('failed correction shows readable error and does not refresh unsaved state', async t => {
  installDOM(t);
  t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({ error: { message: '保存失败，请重试' } }), { status: 409 }));
  const controls = accountTypeControls({ signal: new AbortController().signal }, { id: 'fixture' }, () => assert.fail('No unsaved refresh'));
  await controls.querySelectorAll('button')[0].events.click();
  assert.match(controls.textContent, /保存失败，请重试/);
  assert.ok(controls.querySelectorAll('button').every(row => row.disabled === false));
});
