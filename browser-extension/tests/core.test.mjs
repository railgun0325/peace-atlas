import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {BridgeError, PLATFORMS, normalizeCookies, pairBridge, parsePairingURL, publicError,
  readPlatformCookies, relevantCookieChange, requiredPermissions, syncBridge, validateOrigin} from '../bridge-core.js';

const now = Date.UTC(2026, 9, 6), origin = 'http://127.0.0.1:18761';
const code = 'a'.repeat(64), token = 'b'.repeat(64);
const pairingURL = platform => `${origin}/api/v2/social/bridge/connect?code=${code}&platform=${platform}`;
const binding = platform => ({origin, token, platform, expiresAt: now + 86400000});
const cookie = (platform, name, extra = {}) => ({name, value: 'synthetic-' + name, domain: platform === 'twitter' ? '.x.com' : '.instagram.com',
  path: '/', secure: true, httpOnly: name === PLATFORMS[platform].httpOnly, sameSite: 'lax', session: false,
  expirationDate: now / 1000 + 3600, storeId: '0', ...extra});
const cookies = platform => PLATFORMS[platform].names.map(name => cookie(platform, name));
const wire = platform => normalizeCookies(platform, cookies(platform), {now});
const response = (body, status = 200) => new Response(JSON.stringify(body), {status, headers: {'Content-Type': 'application/json'}});

for (const platform of Object.keys(PLATFORMS)) {
  test(`pairing URL and permissions are scoped to ${platform}`, () => {
    assert.deepEqual(parsePairingURL(pairingURL(platform)), {origin, code, platform});
    assert.deepEqual(requiredPermissions(platform), {permissions: ['cookies'], origins: [PLATFORMS[platform].permission, 'http://127.0.0.1/*']});
  });
  test(`${platform} reads only the three named cookies for the fixed URL`, async () => {
    const calls = [];
    const result = await readPlatformCookies({async getAll(details) { calls.push(details); return [cookie(platform, details.name)]; }}, platform, {now});
    assert.deepEqual(calls, PLATFORMS[platform].names.map(name => ({url: PLATFORMS[platform].url, name, path: '/'})));
    assert.deepEqual(result, wire(platform));
    assert.ok(result.every(row => !Object.hasOwn(row, 'storeId') && !Object.hasOwn(row, 'expirationDate')));
  });
  test(`${platform} pair code is exchanged once and not persisted in the returned binding`, async () => {
    const calls = [];
    const result = await pairBridge(pairingURL(platform), {now, fetchImpl: async (url, options) => {
      calls.push([url, options]);
      return response({platform, bridge_token: token, expires_at: (now + 86400000) / 1000, secret_echo: 'never-forward'});
    }});
    assert.deepEqual(result, binding(platform));
    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], origin + '/api/v2/social/bridge/pair');
    assert.deepEqual(JSON.parse(calls[0][1].body), {platform, code});
    assert.deepEqual(calls[0][1].headers, {'Content-Type': 'application/json'});
    assert.equal(calls[0][1].redirect, 'error');
    assert.ok(!JSON.stringify(result).includes(code) && !JSON.stringify(result).includes('never-forward'));
  });
  test(`${platform} sync uses only the paired local endpoint and requires verification`, async () => {
    let call;
    const result = await syncBridge(binding(platform), wire(platform), {now, fetchImpl: async (url, options) => {
      call = {url, options};
      return response({platform, verified: true, saved: true, source: 'browser_extension', cookie_dump: 'never-forward'});
    }});
    assert.equal(call.url, origin + '/api/v2/social/bridge/sync');
    assert.deepEqual(call.options.headers, {'Content-Type': 'application/json', 'X-Workbench-Bridge': token});
    assert.deepEqual(JSON.parse(call.options.body), {platform, cookies: wire(platform)});
    assert.equal(call.options.credentials, 'omit');
    assert.equal(call.options.referrerPolicy, 'no-referrer');
    assert.equal(call.options.redirect, 'error');
    assert.deepEqual(result, {platform, verified: true, saved: true, source: 'browser_extension'});
  });
}

const invalidURLs = [
  'https://127.0.0.1:18761', 'http://localhost:18761', 'http://127.0.0.2:18761', 'http://[::1]:18761',
  'http://2130706433:18761', 'http://0x7f000001:18761', 'http://user:pass@127.0.0.1:18761',
  'http://127.0.0.1.example.org:18761', 'http://127.0.0.1:0', 'http://127.0.0.1:65536',
  'http://127.0.0.1:018761', 'http://127.0.0.1', 'http://127.0.0.1:80',
].map(base => base + '/api/v2/social/bridge/connect?code=' + code + '&platform=twitter');
invalidURLs.push(pairingURL('twitter') + '#fragment', pairingURL('twitter') + '&target=https://example.org',
  pairingURL('twitter') + '&code=' + code, pairingURL('twitter').replace('/connect?', '/sync?'),
  pairingURL('twitter').replace('/connect?', '/other/../connect?'), pairingURL('twitter').replace('/connect?', '/%63onnect?'),
  pairingURL('twitter').replace(code, 'tiny'), pairingURL('youtube'), pairingURL('twitter').replace('platform=twitter', 'platform=twitter&platform=instagram'));
for (const [index, url] of invalidURLs.entries()) test(`invalid destination or pairing shape ${index} never fetches`, async () => {
  let fetched = false;
  await assert.rejects(() => pairBridge(url, {now, fetchImpl: async () => { fetched = true; }}), BridgeError);
  assert.equal(fetched, false);
});

test('wire export discards additional properties without exporting passwords or profile state', () => {
  const rows = cookies('twitter').map(row => ({...row, password: 'extra-not-exported', localStorage: {secret: 'extra-not-exported'}}));
  assert.deepEqual(normalizeCookies('twitter', rows, {now}), wire('twitter'));
});
test('optional identity fields may be absent, but required platform fields may not', () => {
  assert.equal(normalizeCookies('twitter', cookies('twitter').slice(0, 2), {now}).length, 2);
  assert.throws(() => normalizeCookies('twitter', [cookie('twitter', 'auth_token')], {now}), {code: 'missing_session'});
  assert.equal(normalizeCookies('instagram', cookies('instagram').slice(0, 2), {now}).length, 2);
  assert.throws(() => normalizeCookies('instagram', [cookie('instagram', 'sessionid')], {now}), {code: 'missing_session'});
});
for (const [name, patch, expected] of [
  ['wrong domain', {domain: '.evil.example'}, 'unsafe_cookie'],
  ['related subdomain', {domain: '.api.x.com'}, 'unsafe_cookie'],
  ['different path', {path: '/settings'}, 'unsafe_cookie'],
  ['insecure', {secure: false}, 'unsafe_cookie'],
  ['auth without HttpOnly', {httpOnly: false}, 'unsafe_cookie'],
  ['partitioned', {partitionKey: {topLevelSite: 'https://x.com'}}, 'unsafe_cookie'],
  ['empty', {value: ''}, 'unsafe_cookie'],
  ['control characters', {value: 'bad\nvalue'}, 'unsafe_cookie'],
  ['expired', {expirationDate: now / 1000 - 1}, 'expired_session'],
  ['milliseconds expiry', {expirationDate: now + 3600000}, 'unsafe_cookie'],
  ['invalid session expiry', {expirationDate: -1, session: false}, 'unsafe_cookie'],
  ['missing store', {storeId: undefined}, 'ambiguous_session'],
]) test(`reject ${name} before export`, () => {
  assert.throws(() => normalizeCookies('twitter', [cookie('twitter', 'auth_token', patch), ...cookies('twitter').slice(1)], {now}), {code: expected});
});
test('reject mixed stores and same-name ambiguity even on allowed domains', () => {
  assert.throws(() => normalizeCookies('twitter', [cookie('twitter', 'auth_token'), cookie('twitter', 'ct0', {storeId: '1'})], {now}), {code: 'ambiguous_session'});
  assert.throws(() => normalizeCookies('twitter', [cookie('twitter', 'auth_token'), cookie('twitter', 'auth_token', {domain: 'x.com'})], {now}), {code: 'ambiguous_session'});
});
test('session cookies and Chrome SameSite values become valid wire attributes', () => {
  const rows = cookies('twitter');
  rows[0] = cookie('twitter', 'auth_token', {session: true, expirationDate: undefined, sameSite: 'no_restriction'});
  rows[1].sameSite = 'strict'; rows[2].sameSite = 'unspecified';
  const result = normalizeCookies('twitter', rows, {now});
  assert.equal(result[0].expires, -1); assert.equal(result[0].sameSite, 'None');
  assert.equal(result[1].sameSite, 'Strict'); assert.equal(result[2].sameSite, 'Lax');
});
test('only allowlisted platform cookie changes are considered', () => {
  assert.equal(relevantCookieChange({cookie: cookie('twitter', 'ct0')}, 'twitter'), true);
  assert.equal(relevantCookieChange({cookie: cookie('twitter', 'tracking')}, 'twitter'), false);
  assert.equal(relevantCookieChange({cookie: cookie('twitter', 'ct0', {domain: '.other.example'})}, 'twitter'), false);
  assert.equal(relevantCookieChange({cookie: cookie('instagram', 'sessionid')}, 'twitter'), false);
  assert.equal(relevantCookieChange({cookie: cookie('twitter', 'ct0', {partitionKey: {}})}, 'twitter'), false);
});

for (const body of [
  {platform: 'twitter', opened: true}, {platform: 'twitter', verified: true, saved: false, source: 'browser_extension'},
  {platform: 'instagram', verified: true, saved: true, source: 'browser_extension'},
  {platform: 'twitter', verified: true, saved: true, source: 'other'},
]) test('opened, partial, or mismatched sync response cannot claim login success ' + JSON.stringify(body), async () => {
  await assert.rejects(() => syncBridge(binding('twitter'), wire('twitter'), {now, fetchImpl: async () => response(body)}), {code: 'verification_failed'});
});
test('expired local bridge, remote origins and invalid payloads never transmit', async () => {
  let calls = 0; const fetchImpl = async () => { calls++; };
  await assert.rejects(() => syncBridge({...binding('twitter'), expiresAt: now - 1}, wire('twitter'), {now, fetchImpl}), {code: 'pairing_expired'});
  await assert.rejects(() => syncBridge({...binding('twitter'), origin: 'https://evil.example'}, wire('twitter'), {now, fetchImpl}), {code: 'invalid_pairing_url'});
  await assert.rejects(() => syncBridge(binding('twitter'), [...wire('twitter'), {name: 'password'}], {now, fetchImpl}), {code: 'ambiguous_session'});
  assert.equal(calls, 0);
});
test('redirect and reflected server errors never expose credentials', async () => {
  await assert.rejects(() => pairBridge(pairingURL('twitter'), {now, fetchImpl: async () => ({redirected: true})}), {code: 'network_error'});
  let caught;
  try { await syncBridge(binding('twitter'), wire('twitter'), {now, fetchImpl: async () => response({detail: 'reflected-' + token}, 500)}); }
  catch (error) { caught = error; }
  assert.equal(caught.code, 'verification_failed');
  assert.ok(!JSON.stringify(publicError(caught)).includes(token));
});
for (const [serverCode, expected] of [['challenge', 'challenge'], ['rate_limited', 'rate_limited'], ['busy', 'busy'], ['bridge_busy', 'busy'], ['bridge_denied', 'pairing_expired'], ['pair_denied', 'pairing_failed']]) {
  test(`the server error envelope preserves ${serverCode} as ${expected}`, async () => {
    await assert.rejects(() => syncBridge(binding('twitter'), wire('twitter'), {now,
      fetchImpl: async () => response({error: {code: serverCode, message: 'untrusted-' + token}}, 422)}), {code: expected});
  });
}
test('fetch timeouts are bounded and do not retry', async () => {
  let calls = 0;
  await assert.rejects(() => pairBridge(pairingURL('twitter'), {now, timeoutMs: 5, fetchImpl: async (_url, {signal}) => {
    calls++; return new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new Error('synthetic timeout'))));
  }}), {code: 'timeout'});
  assert.equal(calls, 1);
});
test('pair response token, platform and expiry are validated', async () => {
  for (const updates of [{platform: 'instagram'}, {bridge_token: 'short'}, {expires_at: 0}, {expires_at: (now + 172800000) / 1000}]) {
    await assert.rejects(() => pairBridge(pairingURL('twitter'), {now, fetchImpl: async () => response({platform: 'twitter', bridge_token: token, expires_at: (now + 3600000) / 1000, ...updates})}), {code: 'invalid_response'});
  }
});
test('manifest has no content scripts, all-site access, external messages or remote code', async () => {
  const manifest = JSON.parse(await readFile(new URL('../manifest.json', import.meta.url), 'utf8'));
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(manifest.permissions, ['storage', 'alarms']);
  assert.deepEqual(manifest.optional_permissions, ['cookies']);
  assert.equal(manifest.incognito, 'not_allowed');
  assert.ok(!manifest.content_scripts && !manifest.externally_connectable && !manifest.host_permissions);
  assert.deepEqual(manifest.optional_host_permissions, ['https://x.com/*', 'https://www.instagram.com/*', 'http://127.0.0.1/*']);
  assert.match(manifest.content_security_policy.extension_pages, /connect-src http:\/\/127\.0\.0\.1:\*/);
  assert.equal(validateOrigin(origin), origin);
});
