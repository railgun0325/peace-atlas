import test from 'node:test';
import assert from 'node:assert/strict';
import { openReportRetry } from '../actions.js';
import { renderJobs } from '../research.js';

function installDOM(t) {
  class TestNode {
    constructor(tag) { this.tagName = tag; this.attrs = {}; this.children = []; this.listeners = new Map(); this.text = ''; this.className = ''; this.disabled = false; this.checked = false; this.selected = false; }
    setAttribute(name, value) { this.attrs[name] = String(value); }
    getAttribute(name) { return this.attrs[name]; }
    removeAttribute(name) { delete this.attrs[name]; }
    addEventListener(name, callback, options = {}) { const rows = this.listeners.get(name) || []; rows.push({ callback, once: options.once }); this.listeners.set(name, rows); }
    append(...nodes) { for (const node of nodes) { node.parent = this; this.children.push(node); } }
    before(...nodes) { const index = this.parent.children.indexOf(this); nodes.forEach(node => { node.parent = this.parent; }); this.parent.children.splice(index, 0, ...nodes); }
    replaceChildren(...nodes) { this.children.forEach(node => { node.parent = null; }); this.children = []; this.text = ''; this.append(...nodes); }
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(node => node !== this); this.parent = null; }
    get isConnected() { return this.tagName === 'body' || Boolean(this.parent?.isConnected); }
    get textContent() { return this.text + this.children.map(node => node.textContent).join(''); }
    set textContent(value) { this.text = String(value); this.children = []; }
    set innerHTML(_) { throw new Error('Executable HTML must not be used'); }
    get value() { return this._value ?? (this.tagName === 'select' ? this.children.find(node => node.selected)?.value || this.children[0]?.value || '' : this.attrs.value || ''); }
    set value(value) { this._value = String(value); }
    querySelectorAll(selector) {
      const match = selector.match(/^(\w+)?(?:\[([\w-]+)(?:=["']?([^"'\]]+)["']?)?\])?(?:\.([\w-]+))?$/);
      assert.ok(match && match[0], `Unsupported test selector: ${selector}`);
      const matches = node => (!match[1] || node.tagName === match[1]) && (!match[2] || (Object.hasOwn(node.attrs, match[2]) && (!match[3] || node.attrs[match[2]] === match[3]))) && (!match[4] || node.className.split(' ').includes(match[4]));
      return this.children.flatMap(node => [...(matches(node) ? [node] : []), ...node.querySelectorAll(selector)]);
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    get elements() { return Object.fromEntries(this.querySelectorAll('[name]').map(node => [node.attrs.name, node])); }
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
  const previousNode = globalThis.Node, previousDocument = globalThis.document, previousFormData = globalThis.FormData;
  const body = new TestNode('body'), notifications = new TestNode('div');
  globalThis.Node = TestNode;
  globalThis.document = { body, activeElement: null, createElement: tag => new TestNode(tag), createElementNS: (namespaceURI, tag) => Object.assign(new TestNode(tag), { namespaceURI }), createTextNode: text => Object.assign(new TestNode('#text'), { text: String(text) }), getElementById: id => id === 'notifications' ? notifications : null };
  globalThis.FormData = class { constructor(form) { this.entries = form.querySelectorAll('[name]').filter(node => !node.disabled && (node.attrs.type !== 'checkbox' || node.checked)).map(node => [node.attrs.name, node.value]); } *[Symbol.iterator]() { yield* this.entries; } };
  const nativeTimeout = globalThis.setTimeout;
  t.mock.method(globalThis, 'setTimeout', (callback, delay, ...args) => { const timer = nativeTimeout(callback, delay, ...args); if (delay === 6500) timer.unref(); return timer; });
  t.after(() => { globalThis.Node = previousNode; globalThis.document = previousDocument; globalThis.FormData = previousFormData; });
  return body;
}

const originalSHA = 'a'.repeat(64), currentSHA = 'b'.repeat(64);
const job = { id: 'report/failed', kind: 'report', status: 'failed', params: { mode: 'ai', analysis_harness: 'off', title: '保存的研究目标', start_date: '2026-10-01', end_date: '2026-10-11', time_budget_seconds: 0 } };
const caps = { llm: { configured: true }, analysis_harness: { available: true } };
function preview(overrides = {}) {
  return { job_id: job.id, latest_job_id: job.id, kind: 'report', status: 'failed', saved_params: job.params,
    original_input: { available: true, sha256: originalSHA, source_count: 5, reason: null },
    current_input: { available: true, sha256: currentSHA, source_count: 6, reason: null },
    changes: { compared_to: 'saved_original', source_count: 3, account_count: 1, claim_count: 2, source_counts: { added: 1, removed: 1, changed: 1 }, context_changed: true },
    reading_reuse: { matched: 7, pending: 4, total: 11, unit: 'fragments', validation: 'exact_fingerprint_and_exact_quote', paid_verified: false },
    stage_reuse: { original: { reading: 'requires_runtime_validation', harness: 'requires_runtime_validation', draft: 'requires_runtime_validation', compression: 'requires_runtime_validation' }, latest: { reading: 'matching_fragments_only', harness: 'invalidated', draft: 'invalidated', compression: 'invalidated' } },
    cost_level: 'new_analysis_with_matching_readings_only', may_use_model: true, may_read_public_network: false, ...overrides };
}
const response = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
async function setup(t, next = () => response(preview())) {
  const body = installDOM(t), calls = [], navigations = [], created = [];
  t.mock.method(globalThis, 'fetch', async (path, init) => { const call = { path, method: init.method, body: init.body ? JSON.parse(init.body) : undefined }; calls.push(call); return path === '/api/v2/capabilities' ? response(caps) : next(call); });
  const controller = new AbortController();
  await openReportRetry({ signal: controller.signal, navigate: (...args) => navigations.push(args) }, job, { onCreated: value => created.push(value) });
  const modal = body.querySelector('dialog'), form = modal.querySelector('form');
  return { body, modal, form, mode: form.querySelector('select[name=input_mode]'), submit: form.querySelector('button[type=submit]'), refresh: modal.querySelectorAll('button').find(node => node.textContent === '重新免费预览'), calls, navigations, created };
}

test('report retry opens a free read-only preview and preserves original input and analysis defaults', async t => {
  const { modal, mode, form, submit, calls } = await setup(t);
  assert.ok(calls.some(call => call.path === '/api/v2/jobs/report%2Ffailed/retry-preview' && call.method === 'GET'));
  assert.ok(calls.every(call => call.method === 'GET'));
  assert.ok(mode); assert.equal(mode.value, 'original');
  assert.deepEqual(mode.children.map(node => node.value), ['original', 'latest']);
  assert.equal(form.elements.analysis_harness.value, 'off', 'Do not silently upgrade to DSH');
  assert.match(modal.textContent, /免费预览/);
  assert.match(modal.textContent, /不调用模型/);
  assert.match(modal.textContent, /原输入续跑/);
  assert.match(modal.textContent, /最新审核上下文重新生成/);
  assert.equal(submit.disabled, false);
});

for (const modeValue of ['original', 'latest']) test(`${modeValue} retry submits the selected preview SHA and only enqueues after confirmation`, async t => {
  const state = await setup(t, call => response(call.method === 'POST' ? { id: 'new-report-job' } : preview()));
  assert.ok(state.mode);
  state.mode.value = modeValue; await state.mode.fire('change');
  assert.equal(state.calls.filter(call => call.method === 'POST').length, 0);
  await state.form.fire('submit');
  const posts = state.calls.filter(call => call.method === 'POST');
  assert.equal(posts.length, 1);
  assert.equal(posts[0].path, '/api/v2/jobs/report%2Ffailed/retry');
  assert.equal(posts[0].body.input_mode, modeValue);
  assert.equal(posts[0].body.input_sha256, modeValue === 'original' ? originalSHA : currentSHA);
  assert.equal(posts[0].body.analysis_harness, 'off');
  assert.equal(posts[0].body.time_budget_seconds, 0);
  assert.equal(Object.hasOwn(posts[0].body, 'report_options'), false, 'The HTTP contract is a strict flat ReportRetryOptions body');
  assert.deepEqual(state.navigations, [['jobs', { selected: 'new-report-job' }]]);
  assert.equal(state.created.length, 1);
});

test('latest preview explains changed context and candidate reading fragments without promising saved costs', async t => {
  const { modal, mode, form } = await setup(t);
  assert.ok(mode); mode.value = 'latest'; await mode.fire('change');
  form.elements.analysis_harness.value = 'dsh'; await form.elements.analysis_harness.fire('change');
  for (const pattern of [/原输入 5 条/, /当前输入 6 条/, /来源.*3/, /账号.*1/, /主张.*2/, /匹配 7 \/ 11/, /待阅读 4/, /片段/, /DSH.*重新/, /综合.*重新/, /不保证.*费用|费用.*实际/, /运行时.*验证/]) assert.match(modal.textContent, pattern);
  assert.doesNotMatch(modal.textContent, /已节省|免费续跑|预计.*[¥￥$]|无需.*模型/);
});

test('stage impact follows the selected analysis mode and never promises unselected DSH work', async t => {
  const state = await setup(t);
  state.mode.value = 'latest'; await state.mode.fire('change');
  assert.match(state.modal.querySelector('.report-retry-preview').textContent, /DSH 调查：本次未选择工具调查/);
  assert.doesNotMatch(state.modal.querySelector('.report-retry-preview').querySelectorAll('li').find(node => node.textContent.startsWith('DSH 调查：')).textContent, /重新执行/);
  state.form.elements.analysis_harness.value = 'dsh'; await state.form.elements.analysis_harness.fire('change');
  assert.match(state.modal.querySelector('.report-retry-preview').textContent, /DSH 调查：.*重新执行/);
});

test('missing original snapshot blocks original retry and requires explicit latest selection; unknown changes remain unknown', async t => {
  const unavailable = preview({ original_input: { available: false, sha256: null, source_count: null, reason: '旧任务没有完整输入快照' }, changes: { compared_to: 'unknown_original', source_count: null, account_count: null, claim_count: null, context_changed: null } });
  const state = await setup(t, call => response(call.method === 'POST' ? { id: 'latest-report' } : unavailable));
  assert.ok(state.mode); assert.equal(state.mode.value, 'original');
  assert.equal(state.submit.disabled, true);
  assert.match(state.modal.textContent, /旧任务没有完整输入快照/);
  assert.match(state.modal.textContent, /变更.*未知/);
  assert.doesNotMatch(state.modal.textContent, /来源.*变更 0|未发生变化/);
  await state.form.fire('submit');
  assert.equal(state.calls.filter(call => call.method === 'POST').length, 0);
  state.mode.value = 'latest'; await state.mode.fire('change'); assert.equal(state.submit.disabled, false);
  await state.form.fire('submit');
  assert.equal(state.calls.find(call => call.method === 'POST').body.input_mode, 'latest');
});

test('a missing current snapshot blocks latest generation without replacing it with original input', async t => {
  const state = await setup(t, () => response(preview({ current_input: { available: false, sha256: null, source_count: null, reason: '当前没有可形成报告的来源' } })));
  assert.ok(state.mode); state.mode.value = 'latest'; await state.mode.fire('change');
  assert.equal(state.submit.disabled, true); await state.form.fire('submit');
  assert.equal(state.calls.filter(call => call.method === 'POST').length, 0);
  assert.match(state.modal.textContent, /当前没有可形成报告的来源/);
});

test('TOCTOU conflict consumes the stale preview and requires a new free preview before retry', async t => {
  let gets = 0, posts = 0;
  const state = await setup(t, call => {
    if (call.method === 'GET') return response(preview({ current_input: { available: true, sha256: ++gets === 1 ? currentSHA : 'c'.repeat(64), source_count: 6 } }));
    return ++posts === 1 ? response({ error: { code: 'request_error', message: '输入已经变化，请重新预览' } }, 409) : response({ id: 'fresh-report' });
  });
  assert.ok(state.mode); state.mode.value = 'latest'; await state.mode.fire('change'); await state.form.fire('submit');
  assert.match(state.modal.textContent, /重新预览/); assert.equal(state.submit.disabled, true);
  state.mode.value = 'original'; await state.mode.fire('change'); assert.equal(state.submit.disabled, true);
  await state.form.fire('submit'); assert.equal(posts, 1);
  assert.ok(state.refresh); await state.refresh.fire('click');
  state.mode.value = 'latest'; await state.mode.fire('change'); assert.equal(state.submit.disabled, false);
  await state.form.fire('submit');
  assert.equal(posts, 2);
  assert.equal(state.calls.filter(call => call.method === 'POST').at(-1).body.input_sha256, 'c'.repeat(64));
});

test('the actual validation_error envelope for a lost original snapshot also consumes its stale preview', async t => {
  const state = await setup(t, call => call.method === 'POST'
    ? response({ error: { code: 'validation_error', message: '保存快照已损坏，请重新预览' } }, 422)
    : response(preview()));
  await state.form.fire('submit');
  assert.match(state.modal.textContent, /保存快照已损坏/);
  assert.equal(state.submit.disabled, true);
  state.mode.value = 'latest'; await state.mode.fire('change');
  assert.equal(state.submit.disabled, true);
  await state.form.fire('submit');
  assert.equal(state.calls.filter(call => call.method === 'POST').length, 1);
});

test('preview failure does not expose an unvalidated fallback or enqueue any report', async t => {
  const state = await setup(t, () => response({ detail: '无法读取续跑预览' }, 500));
  assert.equal(state.submit.disabled, true);
  assert.match(state.modal.textContent, /无法读取续跑预览/);
  await state.form.fire('submit');
  assert.equal(state.calls.filter(call => call.method === 'POST').length, 0);
});

test('malformed preview SHA is unavailable rather than sent as an unchecked approval', async t => {
  const state = await setup(t, () => response(preview({ original_input: { available: true, sha256: 'bad-sha', source_count: 5 } })));
  assert.equal(state.submit.disabled, true); await state.form.fire('submit');
  assert.equal(state.calls.filter(call => call.method === 'POST').length, 0);
});

test('unchanged complete context retains only runtime validation candidates, not a guaranteed DSH or synthesis reuse', async t => {
  const state = await setup(t, () => response(preview({ changes: { compared_to: 'saved_original', source_count: 0, account_count: 0, claim_count: 0, context_changed: false }, cost_level: 'resume_candidate_requires_runtime_validation', stage_reuse: { latest: { reading: 'matching_fragments_only', harness: 'requires_runtime_validation', draft: 'requires_runtime_validation', compression: 'not_saved' } } })));
  assert.ok(state.mode); state.mode.value = 'latest'; await state.mode.fire('change');
  assert.match(state.modal.textContent, /运行时.*验证/);
  assert.match(state.modal.textContent, /未保存/);
  assert.doesNotMatch(state.modal.textContent, /全部复用|不再调用模型/);
});

test('the actual failed report task button opens only the preview before any enqueue', async t => {
  const body = installDOM(t), calls = [];
  t.mock.method(globalThis, 'fetch', async (path, init) => {
    calls.push({ path, method: init.method });
    if (path.startsWith('/api/v2/jobs?')) return response({ items: [job], total: 1 });
    if (path.startsWith('/api/v2/job-batches?')) return response({ items: [], total: 0 });
    if (path.endsWith('/retry-preview')) return response(preview());
    if (path === '/api/v2/capabilities') return response(caps);
    if (path === '/api/v2/jobs/report%2Ffailed') return response(job);
    throw new Error(`Unexpected route ${init.method} ${path}`);
  });
  const ctx = { signal: new AbortController().signal, params: new URLSearchParams(), poll() {}, navigate() {} };
  const page = await renderJobs(ctx); body.append(page);
  await page.querySelector('.job-row').fire('click');
  const retry = body.querySelector('dialog').querySelectorAll('button').find(node => node.textContent === '免费预览报告续跑');
  assert.ok(retry); await retry.fire('click');
  assert.ok(calls.some(call => call.path.endsWith('/retry-preview')));
  assert.ok(calls.every(call => call.method === 'GET'));
  assert.equal(body.querySelectorAll('dialog').length, 2);
});

test('non-report task retry keeps the existing direct retry route without report input fields', async t => {
  const body = installDOM(t), calls = [], collect = { id: 'collect-failed', kind: 'collect', status: 'failed', params: { source: 'rss' } };
  t.mock.method(globalThis, 'fetch', async (path, init) => {
    calls.push({ path, method: init.method, body: init.body });
    if (path.startsWith('/api/v2/jobs?')) return response({ items: [collect], total: 1 });
    if (path.startsWith('/api/v2/job-batches?')) return response({ items: [], total: 0 });
    if (path.endsWith('/retry')) return response({ id: 'collect-retried' });
    return response({ ...collect, id: path.endsWith('collect-retried') ? 'collect-retried' : collect.id });
  });
  const ctx = { signal: new AbortController().signal, params: new URLSearchParams(), poll() {}, navigate() {} };
  const page = await renderJobs(ctx); body.append(page); await page.querySelector('.job-row').fire('click');
  await body.querySelector('dialog').querySelectorAll('button').find(node => node.textContent === '创建重试任务').fire('click');
  const posts = calls.filter(call => call.method === 'POST');
  assert.equal(posts.length, 1); assert.equal(posts[0].path, '/api/v2/jobs/collect-failed/retry'); assert.equal(posts[0].body, undefined);
  assert.equal(calls.some(call => call.path.endsWith('/retry-preview')), false);
});
