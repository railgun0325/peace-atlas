// Offline API contracts. No local service, database or credential is accessed.
import test from 'node:test';
import assert from 'node:assert/strict';
import { api, apiPage, downloadFilename, pageNumber } from '../api.js';

test('download filenames decode UTF-8 and remove filesystem separators', () => {
  assert.equal(downloadFilename('attachment; filename="fallback.md"; filename*=UTF-8\'\'%E7%A0%94%E7%A9%B6%2F%E6%91%98%E8%A6%81.md'), '研究_摘要.md');
  assert.equal(downloadFilename('attachment; filename="..\\folder:file?.sqlite3"'), '_folder_file_.sqlite3');
});

test('malformed download names retain their safe fallback', () => {
  assert.equal(downloadFilename('attachment; filename="report.md"; filename*=UTF-8\'\'%E7%ZZ'), 'report.md');
  assert.equal(downloadFilename('attachment; filename="..."'), 'peace-atlas-export');
  assert.equal(downloadFilename('attachment'), 'peace-atlas-export');
});

test('binary downloads require an attachment and preserve exact bytes', async t => {
  const bytes = new Uint8Array([0, 255, 17, 128, 10]);
  t.mock.method(globalThis, 'fetch', async () => new Response(bytes, { headers: { 'Content-Disposition': 'attachment; filename="snapshot.sqlite3"' } }));
  const result = await api('backup', { responseType: 'download' });
  assert.equal(result.filename, 'snapshot.sqlite3');
  assert.deepEqual(new Uint8Array(await result.blob.arrayBuffer()), bytes);
});

test('a successful HTML fallback cannot masquerade as an export', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response('<html>App shell</html>', { headers: { 'Content-Type': 'text/html' } }));
  await assert.rejects(api('reports/test/download', { responseType: 'download' }), error => error.code === 'invalid_download');
});

test('download HTTP errors surface server messages without producing files', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({ error: { code: 'backup_unavailable', message: '当前数据库不支持备份。' } }), { status: 409 }));
  await assert.rejects(api('backup', { responseType: 'download' }), error => error.code === 'backup_unavailable' && error.message === '当前数据库不支持备份。');
});

test('download transport errors remain readable', async t => {
  t.mock.method(globalThis, 'fetch', async () => { throw new TypeError('Failed to fetch'); });
  await assert.rejects(api('backup', { responseType: 'download' }), error => error.code === 'network');
});

test('saved pagination accepts only positive safe integers', () => {
  for (const value of [null, '', 0, -1, 'NaN', 'Infinity', 1.5, Number.MAX_SAFE_INTEGER + 1]) assert.equal(pageNumber(value), 1);
  assert.equal(pageNumber('7'), 7);
});

test('obsolete result pages recover directly to the last valid page', async t => {
  const calls = [];
  const signal = new AbortController().signal;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, signal: options.signal });
    return new Response(JSON.stringify({ total: 26, items: url.includes('page=2&') ? [{ id: 'last' }] : [] }));
  });
  const result = await apiPage('items', { query: '反戦', page: 999, page_size: 25 }, { signal });
  assert.equal(calls.length, 2);
  assert.match(calls[0].url, /page=999/);
  assert.match(calls[1].url, /page=2/);
  assert.match(calls[1].url, /query=%E5%8F%8D%E6%88%A6/);
  assert.ok(calls.every(call => !call.signal.aborted));
  assert.deepEqual(result, { total: 26, items: [{ id: 'last' }], page: 2, page_size: 25 });
});

test('genuinely empty results recover page one without inventing records', async t => {
  const urls = [];
  t.mock.method(globalThis, 'fetch', async url => { urls.push(url); return new Response(JSON.stringify({ total: 0, items: [] })); });
  const result = await apiPage('reports', { page: 50, page_size: 20 });
  assert.equal(result.page, 1);
  assert.deepEqual(result.items, []);
  assert.equal(urls.length, 2);
});

test('failure while recovering a page stays a failure', async t => {
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async () => ++calls === 1 ? new Response(JSON.stringify({ total: 1, items: [] })) : new Response(JSON.stringify({ detail: '数据库暂时不可用' }), { status: 503 }));
  await assert.rejects(apiPage('jobs', { page: 100, page_size: 20 }), /数据库暂时不可用/);
  assert.equal(calls, 2);
});

test('valid pages make one request and preserve response metadata', async t => {
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async () => { calls++; return new Response(JSON.stringify({ total: 41, items: [{ id: 'record' }], coverage: 'partial' })); });
  const result = await apiPage('research', { page: 2, page_size: 25 });
  assert.equal(calls, 1);
  assert.equal(result.coverage, 'partial');
  assert.equal(result.page, 2);
});
