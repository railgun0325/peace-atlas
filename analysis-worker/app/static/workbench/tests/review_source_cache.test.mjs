import test from 'node:test';
import assert from 'node:assert/strict';
import { openAutoReview, reviewResults } from '../review.js';
import { openAccountTypeCorrection, openVerificationSources } from '../registry.js';

// Exercise the actual dialog callbacks and API serialization without a browser,
// local service, model connection, or formal profile.
function installDOM(t) {
  class TestNode {
    constructor(tag) { this.tagName = tag; this.attrs = {}; this.children = []; this.listeners = new Map(); this.text = ''; this.disabled = false; this.checked = false; this.selected = false; }
    setAttribute(name, value) { this.attrs[name] = String(value); }
    getAttribute(name) { return this.attrs[name]; }
    addEventListener(name, callback, options = {}) { const rows = this.listeners.get(name) || []; rows.push({ callback, once: options.once }); this.listeners.set(name, rows); }
    append(...nodes) { for (const node of nodes) { node.parent = this; this.children.push(node); } }
    replaceChildren(...nodes) { this.children.forEach(node => { node.parent = null; }); this.children = []; this.text = ''; this.append(...nodes); }
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(node => node !== this); this.parent = null; }
    get textContent() { return this.text + this.children.map(node => node.textContent).join(''); }
    set textContent(value) { this.text = String(value); this.children = []; }
    set innerHTML(_) { throw new Error('Executable HTML must not be used'); }
    get value() { return this._value ?? (this.tagName === 'select' ? this.children.find(node => node.selected)?.value || this.children[0]?.value || '' : this.attrs.value || ''); }
    set value(value) { this._value = String(value); }
    querySelectorAll(selector) {
      const match = selector.match(/^(\w+)(?:\[name=["']?([^"'\]]+)["']?\])?$/);
      assert.ok(match, `Unsupported test selector: ${selector}`);
      return this.children.flatMap(node => [...(node.tagName === match[1] && (!match[2] || node.attrs.name === match[2]) ? [node] : []), ...node.querySelectorAll(selector)]);
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    reportValidity() { return true; }
    showModal() { this.open = true; }
    close() { this.open = false; for (const row of this.listeners.get('close') || []) row.callback({ target: this, currentTarget: this }); }
    async fire(name) {
      if (name === 'click' && this.disabled) return;
      for (const row of [...(this.listeners.get(name) || [])]) {
        if (row.once) this.listeners.set(name, this.listeners.get(name).filter(item => item !== row));
        await row.callback({ target: this, currentTarget: this, preventDefault() {} });
      }
    }
  }
  const previousNode = globalThis.Node, previousDocument = globalThis.document;
  const body = new TestNode('body'), notifications = new TestNode('div');
  globalThis.Node = TestNode;
  globalThis.document = {
    body, activeElement: null,
    createElement: tag => new TestNode(tag),
    createElementNS: (namespaceURI, tag) => Object.assign(new TestNode(tag), { namespaceURI }),
    createTextNode: text => Object.assign(new TestNode('#text'), { text: String(text) }),
    getElementById: id => id === 'notifications' ? notifications : null,
  };
  const nativeTimeout = globalThis.setTimeout;
  t.mock.method(globalThis, 'setTimeout', (callback, delay, ...args) => {
    const timer = nativeTimeout(callback, delay, ...args);
    if (delay === 6500) timer.unref();
    return timer;
  });
  t.after(() => { globalThis.Node = previousNode; globalThis.document = previousDocument; });
  return body;
}

const estimate = { total: 3, batches: 1, counts: { accounts: 3 }, input_digest: 'current-scope', lower_seconds: 30, upper_seconds: 90, model: 'fixture', basis: 'fixture estimate', ai_configured: true, harness_available: true };
const response = value => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
function controls(body) {
  const modal = body.querySelector('dialog');
  return { modal, mode: modal.querySelector('select[name=source_cache_mode]'), estimate: modal.querySelectorAll('button').find(node => node.textContent === '预估范围与用时'), start: modal.querySelectorAll('button').find(node => node.textContent === '同意预估并开始自动审核') };
}
function open(t) {
  const body = installDOM(t), navigations = [];
  openAutoReview({ navigate: (...args) => navigations.push(args) });
  return { ...controls(body), navigations };
}
function cacheResult(overrides = {}) {
  return { completed: 3, total: 3, phase: 'complete', source_cache: {
    version: 1, mode: 'reuse', ttl_seconds: 3600, persistent_hits: 7, run_hits: 2, fetch_calls: 5, successful_fetches: 3,
    capture_receipts_truncated: 0, captures: [], ...overrides,
  } };
}

test('source reuse is the visible default, limited to one hour, with no shadow control', t => {
  const { modal, mode, start } = open(t);
  assert.ok(mode, 'Automatic review must expose source reuse controls');
  assert.equal(mode.value, 'reuse');
  assert.deepEqual(mode.children.map(option => option.value), ['reuse', 'refresh', 'off']);
  assert.match(modal.textContent, /复用有效期内已保存来源（默认）/);
  assert.match(modal.textContent, /强制刷新/);
  assert.match(modal.textContent, /本次关闭/);
  assert.match(modal.textContent, /1 小时/);
  assert.match(modal.textContent, /不等于.*现场新读取/);
  assert.match(modal.textContent, /目标账号.*核验/);
  assert.doesNotMatch(modal.textContent, /shadow/i);
  assert.equal(start.disabled, true);
});

for (const modeValue of ['reuse', 'refresh', 'off']) test(`${modeValue} estimate and start serialize the same source policy and fresh preview proof`, async t => {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (path, init) => { calls.push({ path, body: JSON.parse(init.body) }); return response(path.endsWith('/estimate') ? estimate : { id: 'review-job' }); });
  const { mode, estimate: preview, start, navigations } = open(t);
  assert.ok(mode);
  mode.value = modeValue; await mode.fire('change');
  await preview.fire('click');
  assert.equal(start.disabled, false);
  await start.fire('click');
  assert.equal(calls.length, 2);
  assert.equal(calls[0].path, '/api/v2/reviews/estimate');
  assert.equal(calls[0].body.source_cache_mode, modeValue);
  assert.equal(calls[0].body.source_cache_ttl_seconds, 3600);
  assert.deepEqual(calls[1].body, { ...calls[0].body, input_digest: 'current-scope' });
  assert.deepEqual(navigations, [['jobs', { selected: 'review-job' }]]);
});

test('changing source policy invalidates approved and in-flight estimates before any start', async t => {
  const calls = []; let resolvePending;
  t.mock.method(globalThis, 'fetch', async (path, init) => {
    calls.push({ path, body: JSON.parse(init.body) });
    if (calls.length === 2) return new Promise(resolve => { resolvePending = resolve; });
    return response(estimate);
  });
  const { mode, estimate: preview, start } = open(t);
  assert.ok(mode);
  await preview.fire('click'); assert.equal(start.disabled, false);
  mode.value = 'refresh'; await mode.fire('change'); assert.equal(start.disabled, true);
  await start.fire('click'); assert.equal(calls.length, 1);
  const pending = preview.fire('click');
  mode.value = 'off'; await mode.fire('change');
  resolvePending(response(estimate)); await pending;
  assert.equal(start.disabled, true, 'The stale refresh estimate cannot approve off mode');
  await preview.fire('click'); assert.equal(start.disabled, false);
  assert.deepEqual(calls.map(call => call.body.source_cache_mode), ['reuse', 'refresh', 'off']);
});

test('source reuse never changes the user choice to prohibit public network reading', async t => {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (_, init) => { calls.push(JSON.parse(init.body)); return response(estimate); });
  const { modal, mode, estimate: preview } = open(t);
  assert.ok(mode);
  const network = modal.querySelectorAll('input').at(-1);
  network.checked = false; await network.fire('change');
  for (const value of ['reuse', 'refresh', 'off']) { mode.value = value; await mode.fire('change'); await preview.fire('click'); }
  assert.equal(calls.length, 3);
  assert.ok(calls.every(body => body.use_public_network === false));
});

test('review results distinguish old source reuse, same-run reuse, successful new captures, and attempts', t => {
  installDOM(t);
  const result = cacheResult(), before = structuredClone(result);
  const view = reviewResults(result);
  assert.match(view.textContent, /跨任务复用 7 次/);
  assert.match(view.textContent, /本任务内复用 2 次/);
  assert.match(view.textContent, /成功新读取 3 次/);
  assert.match(view.textContent, /新读取请求 5 次/);
  assert.match(view.textContent, /请求次数包含失败/);
  assert.match(view.textContent, /有效期 1 小时/);
  assert.match(view.textContent, /不等于.*现场新读取/);
  assert.deepEqual(result, before);
});

test('capture receipts display original saved time and expiry, safe links, and a truthful partial count', t => {
  installDOM(t);
  const view = reviewResults(cacheResult({ capture_receipts_truncated: 4, captures: [
    { source_url: 'https://example.test/saved', captured_at: '2026-10-01T09:00:00Z', expires_at: '2026-10-01T10:00:00Z', first_read_via: 'persistent' },
    { source_url: 'javascript:alert(1)', captured_at: '2026-10-01T09:20:00Z', expires_at: '2026-10-01T10:20:00Z', first_read_via: 'network' },
  ] }));
  assert.match(view.textContent, /复用保存内容/);
  assert.match(view.textContent, /本次新读取/);
  assert.match(view.textContent, /捕获 2026\/10\/01 18:00/);
  assert.match(view.textContent, /有效期至 2026\/10\/01 19:00/);
  assert.match(view.textContent, /日本时间/);
  assert.match(view.textContent, /另有 4 条捕获记录未展示/);
  assert.doesNotMatch(view.textContent, /当前有效|刚刚读取/);
  assert.deepEqual(view.querySelectorAll('a').map(node => node.getAttribute('href')), ['https://example.test/saved']);
  assert.match(view.textContent, /未提供有效来源链接/);
});

test('off mode explains persistent reading and writing are disabled while per-task reuse remains visible', t => {
  installDOM(t);
  const view = reviewResults(cacheResult({ mode: 'off', persistent_hits: 0, run_hits: 2 }));
  assert.match(view.textContent, /本次关闭/);
  assert.match(view.textContent, /不读取或保存跨任务缓存/);
  assert.match(view.textContent, /本任务内复用 2 次/);
});

test('old reviews without cache receipts do not fabricate cache counts or capture times', t => {
  installDOM(t);
  for (const source_cache of [undefined, null]) {
    const view = reviewResults({ completed: 3, total: 3, source_cache });
    assert.doesNotMatch(view.textContent, /跨任务复用|成功新读取|捕获记录/);
    assert.match(view.textContent, /已保存 3 \/ 3/);
  }
});

test('missing counters and invalid capture times stay unknown rather than claiming zero or fresh evidence', t => {
  installDOM(t);
  const view = reviewResults({ source_cache: { version: 1, mode: 'reuse', captures: [{ source_url: 'https://example.test/saved', captured_at: 'invalid', expires_at: null }] } });
  assert.match(view.textContent, /跨任务复用 未记录/);
  assert.match(view.textContent, /成功新读取 未记录/);
  assert.match(view.textContent, /捕获 时间未记录/);
  assert.match(view.textContent, /有效期至 时间未记录/);
  assert.match(view.textContent, /读取方式未记录/);
  assert.doesNotMatch(view.textContent, /跨任务复用 0|本次新读取|刚刚读取/);
});

test('internal shadow comparisons are not exposed as ordinary reuse hits', t => {
  installDOM(t);
  const view = reviewResults(cacheResult({ mode: 'shadow', persistent_hits: 0, shadow_hits: 17 }));
  assert.doesNotMatch(view.textContent, /shadow|17/i);
  assert.match(view.textContent, /跨任务复用 0 次/);
});

test('manual correction offers two types for legacy unknown and persists personal as a manual lock without identity changes', async t => {
  const body = installDOM(t), requests = [];
  let refreshed = 0;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push({ url, method: options.method, body: JSON.parse(options.body) });
    return response({ account_type: 'individual', account_status: 'candidate' });
  });
  openAccountTypeCorrection({}, { account_id: 'legacy', account_type: 'unknown' }, () => { refreshed++; });
  const modal = body.querySelector('dialog'), select = modal.querySelector('select[name=account_type]');
  assert.deepEqual(select.children.map(option => [option.value, option.textContent]), [['organization', '组织'], ['individual', '个人']]);
  assert.equal(select.value, 'individual');
  await modal.querySelectorAll('button').find(control => control.textContent === '保存人工类型').fire('click');
  assert.equal(requests.length, 1);
  assert.deepEqual(requests[0], { url: '/api/v2/accounts/legacy/identity', method: 'PATCH', body: {
    type: 'individual', manual_locked: true, note: '', evidence_urls: [] } });
  // The callback uses the saved API response; no public verification request occurs.
  assert.equal(refreshed, 1);
});

test('verification source type selector has organization and personal only and its exact-link registration stays separate', async t => {
  const body = installDOM(t), requests = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push({ url, method: options.method, body: JSON.parse(options.body) });
    return response({});
  });
  openVerificationSources({}, { account_id: 'legacy', metadata: { verification_sources: [
    { url: 'https://example.org/about', label: '来源', account_type: 'unknown' }] } }, () => {});
  const modal = body.querySelector('dialog'), select = modal.querySelector('select[name=verification_type_1]');
  assert.deepEqual(select.children.map(option => [option.value, option.textContent]), [['organization', '组织'], ['individual', '个人']]);
  assert.equal(select.value, 'individual');
  await modal.querySelectorAll('button').find(control => control.textContent === '保存核验来源').fire('click');
  assert.deepEqual(requests, [{ url: '/api/v2/accounts/legacy/verification-sources', method: 'PUT', body: {
    sources: [{ url: 'https://example.org/about', label: '来源', account_type: 'individual' }] } }]);
});

test('fast result rendering folds legacy unknown into a single personal count without granting verification', t => {
  installDOM(t);
  const view = reviewResults({ classification_version: 1, outcomes: { organization: 2, individual: 3, unknown: 4 }, results: [] });
  assert.match(view.textContent, /组织\/个人粗分（可手动更改）/);
  assert.match(view.textContent, /组织 2 · 个人 7/);
  assert.doesNotMatch(view.textContent, /个人 3|未判定|类型证据不足|已核验/);
});

test('historical strict review maps account unknown to personal while claim and activity evidence outcomes remain separate', t => {
  installDOM(t);
  const view = reviewResults({ review_version: 1, outcomes: { individual: 1, unknown: 1, inconclusive: 2 }, results: [
    { id: 'account', kind: 'accounts', outcome: 'unknown', label: 'legacy', note: '账号类型依据不足' },
    { id: 'claim', kind: 'claims', outcome: 'inconclusive', label: 'claim', note: '关系证据不足' },
    { id: 'activity', kind: 'activities', outcome: 'inconclusive', label: 'activity', note: '活动证据不足' }] });
  assert.match(view.textContent, /个人 2 · 证据不足 2/);
  assert.match(view.textContent, /账号类型 · 个人 · legacy/);
  assert.match(view.textContent, /关系主张 · 证据不足 · claim/);
  assert.match(view.textContent, /活动与举办地 · 证据不足 · activity/);
  assert.doesNotMatch(view.textContent, /类型证据不足/);
});
