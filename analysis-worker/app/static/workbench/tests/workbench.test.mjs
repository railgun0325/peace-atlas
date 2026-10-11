// Run from repository root:
// node --test analysis-worker/app/static/workbench/tests/*.test.mjs
// No server, network, external packages, user data, or file writes are needed.
import test from 'node:test';
import assert from 'node:assert/strict';
import { safeURL, localDate, recentDates, quarterDates, dateText, validateDates, markdown, el, setContent, appendContent } from '../ui.js';
import { api, queryString, pathId } from '../api.js';
import { accountPage, itemRow } from '../library.js';
import { defaultFirefoxProfile, firefoxLoginParams } from '../firefox.js';
import { bridgePairingValue } from '../session_bridge.js';
import { collectParams, socialModes, jobTitle, evidenceDescription, evidenceMetadata, diagnosticsView, relationObservationLabels, openSystemBrowserLogin, detectSocialSession, sessionCheckMessage } from '../social.js';

test('calendar dates do not shift through UTC serialization', () => {
  assert.equal(localDate(new Date(2026, 9, 1)), '2026-10-01');
  assert.equal(localDate(new Date(2026, 11, 31)), '2026-12-31');
});

test('current quarter is October 1 through December 31', () => {
  assert.deepEqual(quarterDates('current', new Date('2026-10-06T06:00:00Z')), { start: '2026-10-01', end: '2026-12-31' });
});

test('previous quarter handles the year boundary', () => {
  assert.deepEqual(quarterDates('last', new Date('2026-01-12T06:00:00Z')), { start: '2025-10-01', end: '2025-12-31' });
});

test('Tokyo calendar has crossed midnight before Shanghai does', () => {
  assert.deepEqual(recentDates(7, new Date('2026-10-05T15:30:00Z')), { start: '2026-09-30', end: '2026-10-06' });
  assert.deepEqual(quarterDates('current', new Date('2026-09-30T15:30:00Z')), { start: '2026-10-01', end: '2026-12-31' });
});

test('recent ranges are inclusive and work across leap day', () => {
  assert.deepEqual(recentDates(1, new Date('2024-02-29T06:00:00Z')), { start: '2024-02-29', end: '2024-02-29' });
  assert.deepEqual(recentDates(2, new Date('2024-03-01T06:00:00Z')), { start: '2024-02-29', end: '2024-03-01' });
});

test('display dates use Tokyo and preserve unknown dates', () => {
  assert.equal(dateText('2026-10-05T15:30:00Z'), '2026/10/06');
  assert.equal(dateText(null), '日期未知');
  assert.equal(dateText('not-a-date'), 'not-a-date');
});

test('reversed date ranges are rejected, same-day ranges accepted', () => {
  assert.throws(() => validateDates({ start_date: '2026-10-02', end_date: '2026-10-01' }), /开始日期不能晚于结束日期/);
  assert.doesNotThrow(() => validateDates({ start_date: '2026-10-01', end_date: '2026-10-01' }));
});

for (const value of ['javascript:alert(1)', 'data:text/html,<script>bad</script>', 'file:///secret', '/relative', '']) {
  test(`unsafe or non-public link rejected: ${value || '(empty)'}`, () => assert.equal(safeURL(value), null));
}

test('public URLs and encoded UUID paths are preserved', () => {
  assert.equal(safeURL('https://example.org/a?q=1'), 'https://example.org/a?q=1');
  assert.equal(safeURL('http://example.org/a'), 'http://example.org/a');
  assert.equal(pathId('value/with ?#'), 'value%2Fwith%20%3F%23');
});

test('query encoding preserves Japanese and skips empty filters', () => {
  assert.equal(queryString({ query: '反戦', page: 1, empty: '', missing: null }), 'query=%E5%8F%8D%E6%88%A6&page=1');
});

test('structured API schema errors stay visible', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({ error: { code: 'validation_error', message: '结束日期不能早于开始日期。' } }), { status: 422 }));
  await assert.rejects(api('mock'), error => error.code === 'validation_error' && error.message === '结束日期不能早于开始日期。');
});

test('FastAPI detail-array schema errors are readable', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({ detail: [{ loc: ['body', 'name'], msg: '名称不能为空' }, { msg: '链接格式无效' }] }), { status: 422 }));
  await assert.rejects(api('mock'), /名称不能为空；链接格式无效/);
});

test('HTTP 200 ok:false never masquerades as an empty dataset', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({ ok: false, message: '数据库不可用' }), { status: 200 }));
  await assert.rejects(api('mock'), /数据库不可用/);
});

test('invalid JSON is a visible service error', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response('not JSON', { status: 200 }));
  await assert.rejects(api('mock'), /无法读取/);
});

test('valid empty datasets remain valid data', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 }));
  assert.deepEqual(await api('mock'), { items: [], total: 0 });
});

test('default-browser login rejects an unconfirmed open without creating or navigating to a job', async t => {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, method: options.method, body: JSON.parse(options.body) });
    return new Response(JSON.stringify({ opened: false, login_verified: false, session_imported: false }), { status: 200 });
  });
  await assert.rejects(openSystemBrowserLogin({ signal: new AbortController().signal, navigate: () => assert.fail('Opening a browser must not navigate to a login job') }, 'instagram'), /未确认打开/);
  assert.deepEqual(calls, [{ url: '/api/v2/social/browser/open', method: 'POST', body: { platform: 'instagram' } }]);
});

test('default-browser login rejects unsupported platforms before any request', async t => {
  t.mock.method(globalThis, 'fetch', async () => assert.fail('No request expected'));
  await assert.rejects(openSystemBrowserLogin({ signal: new AbortController().signal }, 'other'), /请选择 X 或 Instagram/);
});

test('default-browser launch errors remain readable and never fall back to an automated login', async t => {
  const urls = [];
  t.mock.method(globalThis, 'fetch', async url => {
    urls.push(url);
    return new Response(JSON.stringify({ error: { code: 'browser_open_failed', message: '无法打开系统默认浏览器。' } }), { status: 503 });
  });
  await assert.rejects(openSystemBrowserLogin({ signal: new AbortController().signal }, 'twitter'), error => error.code === 'browser_open_failed' && error.message === '无法打开系统默认浏览器。');
  assert.deepEqual(urls, ['/api/v2/social/browser/open']);
});

test('existing-session verification uses an auto job and never opens a browser for a valid session', async t => {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, body: options.body && JSON.parse(options.body) });
    return new Response(JSON.stringify(url.endsWith('/jobs') ? { id: 'verification-job' } : { status: 'succeeded', result: { method: 'auto', verified: true, requires_login: false, source: 'workbench' } }), { status: 200 });
  });
  const result = await detectSocialSession({ signal: new AbortController().signal }, 'twitter');
  assert.equal(result.verified, true);
  assert.deepEqual(calls, [{ url: '/api/v2/jobs', body: { kind: 'login', params: { platform: 'twitter', method: 'auto' } } }, { url: '/api/v2/jobs/verification-job', body: undefined }]);
});

test('Instagram auto verification preserves Firefox source metadata without an explicit profile or another login', async t => {
  const calls = [];
  const metadata = { method: 'auto', verified: true, requires_login: false, source: 'firefox', saved: true, cookies_count: 3, state: 'ready' };
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, body: options.body && JSON.parse(options.body) });
    return new Response(JSON.stringify(url.endsWith('/jobs') ? { id: 'instagram-check' } : { status: 'succeeded', result: metadata }), { status: 200 });
  });
  const result = await detectSocialSession({ signal: new AbortController().signal }, 'instagram');
  assert.deepEqual(result, { ...metadata, job_id: 'instagram-check' });
  assert.deepEqual(calls, [{ url: '/api/v2/jobs', body: { kind: 'login', params: { platform: 'instagram', method: 'auto' } } }, { url: '/api/v2/jobs/instagram-check', body: undefined }]);
  assert.match(sessionCheckMessage(result), /已自动复用 Firefox 登录/);
});

test('rate-limited or challenged session verification never opens another login window', async t => {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async url => {
    calls.push(url);
    return new Response(JSON.stringify(url.endsWith('/jobs') ? { id: 'verification-job' } : { status: 'succeeded', result: { method: 'auto', verified: false, requires_login: false, state: 'rate_limited', message: '平台限制请求' } }), { status: 200 });
  });
  const result = await detectSocialSession({ signal: new AbortController().signal }, 'twitter');
  assert.equal(result.verified, false);
  assert.deepEqual(calls, ['/api/v2/jobs', '/api/v2/jobs/verification-job']);
  assert.equal(sessionCheckMessage(result), '平台限制请求');
});

test('existing-session result descriptions distinguish verification from merely opening a browser', () => {
  assert.match(sessionCheckMessage({ verified: true, source: 'firefox' }), /自动复用 Firefox 登录/);
  assert.match(sessionCheckMessage({ verified: true, source: 'workbench' }), /已验证工作台保存/);
  assert.match(sessionCheckMessage({ verified: false, requires_login: true, browser_opened: true }), /尚未同步采集会话/);
  assert.equal(jobTitle({ kind: 'login', params: { platform: 'twitter', method: 'auto' } }), 'Twitter / X · 已有登录检测与复用');
});

test('local browser pairing accepts only a bounded loopback address for the selected platform', () => {
  const code = 'a'.repeat(64);
  const pairing_url = `http://127.0.0.1:18769/api/v2/social/bridge/connect?code=${code}&platform=twitter`;
  assert.deepEqual(bridgePairingValue({ pairing_url, expires_in: 300 }, 'twitter'), { url: pairing_url, expiresIn: 300 });
  for (const invalid of [pairing_url.replace('127.0.0.1', 'example.org'), pairing_url.replace('http:', 'https:'), pairing_url.replace('18769', '65536'), pairing_url.replace('/connect', '/other'), pairing_url.replace('platform=twitter', 'platform=instagram'), `${pairing_url}&extra=1`, `${pairing_url}#secret`, pairing_url.replace(code, 'short')]) {
    assert.throws(() => bridgePairingValue({ pairing_url: invalid, expires_in: 300 }, 'twitter'), /本地会话连接地址无法确认/);
  }
  for (const expires_in of [0, 301, '300', null]) assert.throws(() => bridgePairingValue({ pairing_url, expires_in }, 'twitter'), /无法确认/);
});

test('network failures have a user-readable message', async t => {
  t.mock.method(globalThis, 'fetch', async () => { throw new TypeError('Failed to fetch'); });
  await assert.rejects(api('mock'), /无法连接本地服务/);
});

test('navigation abort propagates as cancellation, not timeout', async t => {
  t.mock.method(globalThis, 'fetch', async (_url, options) => new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(options.signal.reason), { once: true })));
  const controller = new AbortController();
  const pending = api('mock', { signal: controller.signal });
  controller.abort();
  await assert.rejects(pending, error => error.name === 'AbortError');
});

test('request timeout has a distinct visible message', async t => {
  t.mock.method(globalThis, 'fetch', async (_url, options) => new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(options.signal.reason), { once: true })));
  await assert.rejects(api('mock', { timeout: 5 }), error => error.code === 'timeout' && /请求超时/.test(error.message));
});

// A minimal inert DOM adapter executes the real Markdown renderer. It has no
// HTML parser: calling innerHTML fails the test, and anchors are inspected as
// actual emitted nodes. Browser interaction/visual tests run separately.
function installInertDOM(t) {
  class InertNode {
    constructor(tag = '', text = '') { this.tagName = tag.toUpperCase(); this.attributes = {}; this.children = []; this.namespaceURI = ''; this.text = text; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name] ?? null; }
    addEventListener() {}
    append(...nodes) { for (const node of nodes) { if (node instanceof InertNode && node.tagName === '#FRAGMENT') this.children.push(...node.children); else this.children.push(node instanceof InertNode ? node : new InertNode('#TEXT', String(node))); } }
    replaceChildren(...nodes) { this.text = ''; this.children = []; this.append(...nodes); }
    get textContent() { return this.text + this.children.map(child => child.textContent).join(''); }
    set textContent(value) { this.text = String(value); this.children = []; }
    set innerHTML(_value) { throw new Error('Renderer attempted unsafe HTML parsing'); }
    querySelectorAll(tag) { const found = []; for (const child of this.children) { if (child.tagName.toLowerCase() === tag.toLowerCase()) found.push(child); found.push(...child.querySelectorAll(tag)); } return found; }
  }
  const previous = { Node: globalThis.Node, document: globalThis.document };
  globalThis.Node = InertNode;
  globalThis.document = {
    createElement: tag => new InertNode(tag),
    createTextNode: text => new InertNode('#TEXT', text),
    createDocumentFragment: () => new InertNode('#FRAGMENT'),
    createElementNS: (namespace, tag) => { const node = new InertNode(tag); node.namespaceURI = namespace; return node; },
  };
  t.after(() => { for (const [key, value] of Object.entries(previous)) { if (value === undefined) delete globalThis[key]; else globalThis[key] = value; } });
}

test('material cards preserve canonical body priority and text-only legacy fallback', t => {
  installInertDOM(t);
  const base = { id: 'fixture', type: 'news', title: '隔离资料', topics: [], content: '保存的原始正文', clean_text: '旧格式正文' };
  const output = itemRow(base, {});
  assert.match(output.textContent, /保存的原始正文/);
  assert.doesNotMatch(output.textContent, /旧格式正文/);
});

test('invalid imported fallback metadata cannot crash recent-material and library cards', t => {
  installInertDOM(t);
  const output = itemRow({ id: 'fixture', type: 'news', title: '隔离资料', topics: [], content: '', clean_text: 123, content_text: '可读取的旧正文' }, {});
  assert.match(output.textContent, /可读取的旧正文/);
  assert.doesNotMatch(output.textContent, /123/);
});

test('absent material text remains absent rather than rendering metadata objects as body', t => {
  installInertDOM(t);
  const output = itemRow({ id: 'fixture', type: 'news', title: '隔离资料', topics: [], content: '', clean_text: {}, content_text: ['无效正文'] }, {});
  assert.doesNotMatch(output.textContent, /object Object|无效正文/);
  assert.equal(output.querySelectorAll('div').find(node => node.className === 'item-excerpt').textContent, '');
});

test('pipeline angle-bracket evidence links render as clickable anchors', t => {
  installInertDOM(t);
  const output = markdown('来源：[S001 · 日本和平资料](<https://example.org/article(1)?q=peace&lang=ja>)');
  const anchors = output.querySelectorAll('a');
  assert.equal(anchors.length, 1);
  assert.equal(anchors[0].getAttribute('href'), 'https://example.org/article(1)?q=peace&lang=ja');
  assert.equal(anchors[0].textContent, 'S001 · 日本和平资料');
  assert.equal(anchors[0].getAttribute('rel'), 'noopener noreferrer');
  assert.equal(anchors[0].getAttribute('target'), '_blank');
});

test('regular Markdown links, emphasis, and code still render correctly', t => {
  installInertDOM(t);
  const output = markdown('**结论**：[`S001`](https://example.org/source) 与 `原始材料`。');
  assert.equal(output.querySelectorAll('a')[0].getAttribute('href'), 'https://example.org/source');
  assert.equal(output.querySelectorAll('strong')[0].textContent, '结论');
  assert.equal(output.querySelectorAll('code')[0].textContent, '原始材料');
});

test('untrusted HTML and unsafe Markdown links never become executable nodes', t => {
  installInertDOM(t);
  const output = markdown('<img src=x onerror=alert(1)>\n<script>alert(1)</script>\n[attack](javascript:alert(1))\n[attack](<data:text/html,bad>)');
  assert.equal(output.querySelectorAll('img').length, 0);
  assert.equal(output.querySelectorAll('script').length, 0);
  assert.equal(output.querySelectorAll('a').length, 0);
  assert.match(output.textContent, /<script>/);
});

test('evidence report headings, quotations and citation lists remain semantic', t => {
  installInertDOM(t);
  const output = markdown('# 研究报告\n\n> 证据汇编 · 未调用 AI\n\n## 来源\n- [S001](<https://example.org/one>)\n- [S002](https://example.org/two)');
  assert.equal(output.querySelectorAll('h2')[0].textContent, '研究报告');
  assert.equal(output.querySelectorAll('blockquote').length, 1);
  assert.equal(output.querySelectorAll('li').length, 2);
  assert.equal(output.querySelectorAll('a').length, 2);
});

test('numbered report lists preserve ordered structure and starting number', t => {
  installInertDOM(t);
  const output = markdown('3. **核对来源**\n4. 保存依据\n- 未完成的线索\n1) 继续研究');
  const ordered = output.querySelectorAll('ol');
  assert.equal(ordered.length, 2);
  assert.equal(ordered[0].getAttribute('start'), '3');
  assert.equal(ordered[0].querySelectorAll('li').length, 2);
  assert.equal(output.querySelectorAll('ul').length, 1);
  assert.match(ordered[0].textContent, /核对来源/);
});

test('report tables preserve headers, alignment, evidence links and escaped pipes', t => {
  installInertDOM(t);
  const output = markdown('| 账号 | 关系 | 证据 |\n| :--- | :---: | ---: |\n| **公开组织** | 提及\\|共同活动 | [S001](<https://example.org/item(1)>) |\n\n表后说明');
  assert.equal(output.querySelectorAll('table').length, 1);
  assert.deepEqual(output.querySelectorAll('th').map(node => node.textContent), ['账号', '关系', '证据']);
  assert.ok(output.querySelectorAll('th').every(node => node.getAttribute('scope') === 'col'));
  const cells = output.querySelectorAll('td');
  assert.equal(cells.length, 3);
  assert.equal(cells[1].textContent, '提及|共同活动');
  assert.equal(cells[1].className, 'table-center');
  assert.equal(cells[2].className, 'table-right');
  assert.equal(output.querySelectorAll('strong')[0].textContent, '公开组织');
  assert.equal(output.querySelectorAll('a')[0].getAttribute('href'), 'https://example.org/item(1)');
  assert.equal(output.querySelectorAll('p')[0].textContent, '表后说明');
});

test('tables accept omitted outer pipes while malformed rows remain readable text', t => {
  installInertDOM(t);
  const output = markdown('账号 | 状态\n--- | ---\n甲 | 待核验\n不匹配 | 的 | 三列\n\n只有\\|文本\n| 表头 |\n| -- |');
  assert.equal(output.querySelectorAll('table').length, 1);
  assert.deepEqual(output.querySelectorAll('td').map(node => node.textContent), ['甲', '待核验']);
  assert.match(output.querySelectorAll('p')[0].textContent, /不匹配/);
  assert.match(output.textContent, /只有\\\|文本/);
  assert.match(output.textContent, /\| -- \|/);
});

test('untrusted table cells and fenced table examples remain inert', t => {
  installInertDOM(t);
  const output = markdown('| 内容 | 链接 |\n| --- | --- |\n| <img src=x onerror=bad> | [bad](javascript:bad) |\n\n```md\n| 示例 |\n| --- |\n| 内容 |\n```');
  assert.equal(output.querySelectorAll('table').length, 1);
  assert.equal(output.querySelectorAll('img').length, 0);
  assert.equal(output.querySelectorAll('a').length, 0);
  assert.match(output.querySelectorAll('td')[0].textContent, /onerror=bad/);
  assert.match(output.querySelectorAll('code')[0].textContent, /\| --- \|/);
});

test('queued task optional sections never print false null or undefined', t => {
  installInertDOM(t);
  const job = { cancel_requested: false, error: null, result: null };
  const result = job.result || {};
  const summary = el('div');
  setContent(summary, el('p', {}, '等待执行'), job.cancel_requested && el('p', {}, '正在取消'), job.error && el('p', {}, job.error), result.report_id && el('a', {}, '报告'), false, null, undefined);
  assert.equal(summary.textContent, '等待执行');
  assert.equal(summary.querySelectorAll('p').length, 1);
  assert.equal(summary.querySelectorAll('a').length, 0);
});

test('conditional desktop fields and nested arrays use the same safe children rules', t => {
  installInertDOM(t);
  const form = el('form');
  appendContent(form, el('p', {}, '连接设置'), false, [null, undefined, [true, el('p', {}, '保存')]], 0);
  assert.equal(form.textContent, '连接设置保存0');
  assert.equal(form.querySelectorAll('p').length, 2);
});

test('empty optional report sources disappear without dropping numeric zero', t => {
  installInertDOM(t);
  const report = el('article', {}, true, false, null, undefined, '来源数量：', 0);
  setContent(report, report.textContent, [].length > 0 && el('section', {}, '来源快照'));
  assert.equal(report.textContent, '来源数量：0');
  assert.equal(report.querySelectorAll('section').length, 0);
});

test('platform capabilities restrict modes while both platforms support hashtag search', () => {
  assert.deepEqual(socialModes('twitter'), ['keyword', 'hashtag', 'account']);
  assert.deepEqual(socialModes('instagram', { modes: ['account'] }), ['account']);
  assert.deepEqual(socialModes('instagram', { modes: [] }), []);
  assert.equal(collectParams({ source: 'twitter', mode: 'hashtag', keyword: '#平和' }).keyword, '平和');
  assert.throws(() => collectParams({ source: 'instagram', mode: 'keyword', keyword: '平和' }, { modes: ['account'] }), /不支持/);
});

test('social account parameters strip @ and exclude stale keyword/feed fields', () => {
  assert.deepEqual(collectParams({ source: 'instagram', mode: 'account', handle: ' @public_org ', keyword: 'stale', feed_url: 'https://example.org/feed', max_records: '20', start_date: '2026-05-01', end_date: '2026-05-31' }), { source: 'instagram', mode: 'account', relevance: 'focused', handle: 'public_org', start_date: '2026-05-01', end_date: '2026-05-31', max_records: 20, max_accounts: 3, max_scrolls: 5, include_following: false, max_following: 0 });
});

test('hashtag and explicit broad collection remain separate from research filtering', () => {
  const params = collectParams({ source: 'instagram', mode: 'hashtag', keyword: ' #平和 ', relevance: 'broad', include_following: 'on', max_following: '50' });
  assert.equal(params.keyword, '平和');
  assert.equal(params.relevance, 'broad');
  assert.equal(params.include_following, true);
  assert.equal(params.max_following, 50);
  assert.throws(() => collectParams({ source: 'twitter', keyword: '平和', relevance: 'all' }), /相关性/);
});

test('hidden social parameters never leak into RSS and news jobs', () => {
  const rss = collectParams({ source: 'rss', feed_url: ' https://example.org/feed ', mode: 'account', handle: 'stale', include_following: 'on' });
  assert.equal(rss.feed_url, 'https://example.org/feed');
  for (const key of ['mode', 'handle', 'keyword', 'relevance', 'include_following', 'max_accounts']) assert.equal(Object.hasOwn(rss, key), false);
  const news = collectParams({ source: 'google_news', keyword: ' 平和 ', feed_url: 'stale' });
  assert.equal(news.keyword, '平和');
  assert.equal(Object.hasOwn(news, 'feed_url'), false);
});

test('social collection validates all upper bounds and requires its search input', () => {
  const base = { source: 'twitter', keyword: '平和' };
  for (const [key, value] of [['max_records', 101], ['max_accounts', 11], ['max_scrolls', 21], ['max_scrolls', 0], ['max_accounts', 1.5], ['max_records', 'oops']]) assert.throws(() => collectParams({ ...base, [key]: value }), /整数/);
  assert.throws(() => collectParams({ ...base, include_following: true, max_following: 51 }), /整数/);
  assert.throws(() => collectParams({ source: 'instagram', mode: 'account', handle: '@' }), /用户名/);
  assert.throws(() => collectParams({ source: 'twitter', keyword: ' ' }), /关键词/);
  assert.throws(() => collectParams({ ...base, start_date: '2026-10-02', end_date: '2026-10-01' }), /开始日期/);
});

test('login and social task titles identify the actual platform and account', () => {
  assert.equal(jobTitle({ kind: 'login', params: { platform: 'twitter' } }), 'Twitter / X · 手动登录');
  assert.equal(jobTitle({ kind: 'collect', params: { source: 'instagram', handle: 'public_org' } }), 'Instagram · @public_org');
  assert.equal(jobTitle({ kind: 'report', params: {} }), '研究报告生成');
});

test('image descriptions and legacy records are never labeled as verified body text', () => {
  assert.equal(evidenceDescription({ metadata: { content_kind: 'image_alt' } }).clue, true);
  assert.equal(evidenceDescription({ metadata: { content_kind: 'body' } }).kind, '页面正文');
  assert.match(evidenceDescription({ metadata: { content_kind: 'legacy_unverified' } }).kind, /待核对/);
  assert.match(evidenceDescription({ published_at: null, created_at: '2026-05-18' }).date, /发布时间未知/);
});

test('social evidence rendering keeps provenance safe and zero engagement visible', t => {
  installInertDOM(t);
  const output = evidenceMetadata({ type: 'social', published_at: null, metadata: { content_kind: 'image_alt', query: '<img src=x onerror=bad>', engagement: { likes: 0, replies: 2 }, relevance: { mode: 'broad', reasons: ['匹配指定账号'] } } });
  assert.match(output.textContent, /图片说明/);
  assert.match(output.textContent, /发布时间未知/);
  assert.match(output.textContent, /点赞 0/);
  assert.match(output.textContent, /评论 2/);
  assert.match(output.textContent, /候选材料/);
  assert.equal(output.querySelectorAll('img').length, 0);
});

test('source diagnostics preserve exclusion counts without executable links or null text', t => {
  installInertDOM(t);
  assert.equal(diagnosticsView([]), null);
  const output = diagnosticsView([{ outcome: 'unknown_date', code: 'ignored_code', message: '<script>bad</script>', count: 0, unknown_dates: 3, scanned: 12, irrelevant: 5, duplicates: 0, url: 'javascript:alert(1)' }, '已到达停止边界']);
  assert.match(output.textContent, /发布时间未知/);
  assert.doesNotMatch(output.textContent, /ignored_code/);
  assert.match(output.textContent, /检查帖子 12/);
  assert.match(output.textContent, /相关性排除 5/);
  assert.match(output.textContent, /重复 0/);
  assert.match(output.textContent, /日期未知 3/);
  assert.match(output.textContent, /已到达停止边界/);
  assert.equal(output.querySelectorAll('a').length, 0);
  assert.equal(output.querySelectorAll('script').length, 0);
  assert.doesNotMatch(output.textContent, /undefined|null|false/);
});


test('relations keep first and latest observation separate without inventing missing dates', () => {
  assert.deepEqual(relationObservationLabels({ observed_at: '2026-05-18T00:00:00Z', last_observed_at: '2026-10-06T00:00:00Z' }), ['首次观察 2026/05/18 09:00', '最近观察 2026/10/06 09:00']);
  assert.deepEqual(relationObservationLabels({ observed_at: '2026-05-18T00:00:00Z', last_observed_at: '2026-05-18T00:00:00Z' }), ['首次观察 2026/05/18 09:00']);
  assert.deepEqual(relationObservationLabels({ observed_at: null, last_observed_at: null }), ['首次观察时间未保存']);
  assert.deepEqual(relationObservationLabels({ observed_at: null, last_observed_at: '2026-10-06T00:00:00Z' }), ['首次观察时间未保存', '最近观察 2026/10/06 09:00']);
});


test('account pagination searches the full dataset and clamps out-of-range pages', () => {
  const accounts = Array.from({ length: 61 }, (_, index) => ({ id: index, name: `Organization ${index}`, handle: `source_${index}`, status: index === 60 ? 'verified' : 'candidate' }));
  assert.equal(accountPage(accounts).items.length, 30);
  assert.equal(accountPage(accounts).total, 61);
  assert.equal(accountPage(accounts, { page: 2 }).items[0].id, 30);
  assert.equal(accountPage(accounts, { page: 999 }).page, 3);
  assert.equal(accountPage(accounts, { page: 999 }).items[0].id, 60);
  const search = accountPage(accounts, { query: 'SOURCE_60', page: 3 });
  assert.equal(search.total, 1);
  assert.equal(search.page, 1);
  assert.equal(search.items[0].id, 60);
  assert.equal(accountPage(accounts, { status: 'verified', page: 2 }).page, 1);
  assert.deepEqual(accountPage(accounts, { query: 'no match', page: -1 }), { items: [], total: 0, page: 1, page_size: 30 });
});

test('unreliable legacy engagement shows an explicit note instead of default zeros', t => {
  installInertDOM(t);
  const output = evidenceMetadata({ type: 'social', metadata: { content_kind: 'legacy_unverified', engagement_verified: false, legacy_engagement: { likes: 0 }, engagement: { likes: 0, comments: 0 } } });
  assert.match(output.textContent, /历史库未保留可靠互动指标/);
  assert.doesNotMatch(output.textContent, /点赞 0|评论 0/);
});


test('Firefox profile default selection excludes unavailable profiles and does not create a login', () => {
  const profiles = [{ id: 'missing-default', default: true, available: false }, { id: 'available-one', default: false, available: true }, { id: 'available-default', default: true, available: true }];
  assert.equal(defaultFirefoxProfile(profiles), 'available-default');
  assert.equal(defaultFirefoxProfile(profiles.slice(0, 2)), 'available-one');
  assert.equal(defaultFirefoxProfile([profiles[0]]), '');
  assert.equal(defaultFirefoxProfile([]), '');
});

test('Firefox import job sends only selected opaque profile identity and rejects unavailable selection', () => {
  const profiles = [{ id: 'opaque-123', name: 'Local profile', available: true }, { id: 'missing', available: false }];
  assert.deepEqual(firefoxLoginParams('opaque-123', profiles), { platform: 'twitter', method: 'firefox', profile_id: 'opaque-123' });
  for (const id of ['', 'unknown', 'missing']) assert.throws(() => firefoxLoginParams(id, profiles), /可用的 Firefox 配置/);
  assert.equal(jobTitle({ kind: 'login', params: { platform: 'twitter', method: 'firefox' } }), 'Twitter / X · Firefox 会话导入');
});
