import test from 'node:test';
import assert from 'node:assert/strict';
import {createBridgeController} from '../bridge-controller.js';
import {ALARM_PREFIX, BridgeError, PLATFORMS} from '../bridge-core.js';

const now = Date.UTC(2026, 9, 6), token = 'test-local-bridge-token-'.repeat(3), code = 'a'.repeat(64);
const origin = 'http://127.0.0.1:18761';
const pairingURL = platform => `${origin}/api/v2/social/bridge/connect?code=${code}&platform=${platform}`;
const binding = platform => ({platform, origin, token, expiresAt: now + 86400000});
const rawCookie = (platform, name) => ({name, value: 'test-cookie-value-' + name,
  domain: platform === 'twitter' ? '.x.com' : '.instagram.com', path: '/', secure: true,
  httpOnly: name === PLATFORMS[platform].httpOnly, sameSite: 'lax', session: false, expirationDate: now / 1000 + 7200, storeId: '0'});

function mockAPI() {
  const data = {}, calls = {cookieReads: [], pairs: [], syncs: [], permissionRemovals: [], alarmCreates: []};
  const alarms = new Map(); let permitted = true, absentCookie = false;
  const api = {
    storage: {local: {
      async get(keys) { return Object.fromEntries((Array.isArray(keys) ? keys : [keys]).filter(key => Object.hasOwn(data, key)).map(key => [key, structuredClone(data[key])])); },
      async set(values) { Object.assign(data, structuredClone(values)); },
      async remove(keys) { for (const key of Array.isArray(keys) ? keys : [keys]) delete data[key]; },
    }},
    permissions: {async contains() { return permitted; }, async remove(value) { calls.permissionRemovals.push(value); return true; }},
    cookies: {async getAll(details) {
      calls.cookieReads.push(details); if (absentCookie) return [];
      const platform = details.url === 'https://x.com/' ? 'twitter' : 'instagram';
      return [rawCookie(platform, details.name)];
    }},
    alarms: {
      async get(name) { return alarms.get(name); },
      async create(name, details) { alarms.set(name, {name, ...details}); calls.alarmCreates.push({name, ...details}); },
      async clear(name) { return alarms.delete(name); },
    },
  };
  const transport = {
    now: () => now,
    async pair(url) { calls.pairs.push(url); return binding(new URL(url).searchParams.get('platform')); },
    async sync(bound, cookies) { calls.syncs.push({bound, cookies}); return {verified: true, saved: true, platform: bound.platform, source: 'browser_extension'}; },
  };
  return {api, data, calls, alarms, transport, deny() { permitted = false; }, noCookies() { absentCookie = true; }};
}

test('connect verifies automatically and stores no website cookies or one-use code', async () => {
  const fixture = mockAPI(), controller = createBridgeController(fixture.api, fixture.transport);
  const result = await controller.message({type: 'connect', pairingURL: pairingURL('twitter')});
  assert.deepEqual(result, {ok: true, platform: 'twitter', verified: true, saved: true});
  assert.equal(fixture.calls.cookieReads.length, 3);
  assert.equal(fixture.calls.syncs.length, 1);
  const stored = JSON.stringify(fixture.data);
  assert.ok(stored.includes(token));
  assert.ok(!stored.includes('test-cookie-value') && !stored.includes(code));
  const publicState = await controller.message({type: 'status'});
  assert.equal(publicState.items[0].state, 'verified');
  assert.equal(publicState.items[1].state, 'disconnected');
  assert.ok(!JSON.stringify(publicState).includes(token) && !JSON.stringify(publicState).includes('test-cookie-value'));
});
test('denied optional permission prevents pairing and any cookie read', async () => {
  const fixture = mockAPI(); fixture.deny();
  const controller = createBridgeController(fixture.api, fixture.transport);
  const result = await controller.message({type: 'connect', pairingURL: pairingURL('twitter')});
  assert.equal(result.code, 'permission_denied');
  assert.equal(fixture.calls.pairs.length, 0); assert.equal(fixture.calls.cookieReads.length, 0);
  assert.deepEqual(fixture.data, {});
});
test('successful pairing without a platform session remains waiting, not ready', async () => {
  const fixture = mockAPI(); fixture.noCookies();
  const controller = createBridgeController(fixture.api, fixture.transport);
  const result = await controller.message({type: 'connect', pairingURL: pairingURL('instagram')});
  assert.equal(result.code, 'missing_session');
  assert.equal(fixture.calls.syncs.length, 0);
  const state = (await controller.message({type: 'status'})).items[1];
  assert.equal(state.connected, true); assert.equal(state.state, 'waiting'); assert.equal(state.checkedAt, null);
});
test('a merely opened backend response cannot set verified state', async () => {
  const fixture = mockAPI(); fixture.transport.sync = async () => ({platform: 'twitter', opened: true});
  const controller = createBridgeController(fixture.api, fixture.transport);
  const result = await controller.message({type: 'connect', pairingURL: pairingURL('twitter')});
  assert.equal(result.code, 'verification_failed');
  assert.equal((await controller.message({type: 'status'})).items[0].state, 'waiting');
});
test('cookie events are ignored before pairing and for unrelated platforms, fields or domains', async () => {
  const fixture = mockAPI(), controller = createBridgeController(fixture.api, fixture.transport);
  await controller.cookieChanged({cookie: rawCookie('twitter', 'ct0')});
  assert.equal(fixture.calls.cookieReads.length, 0); assert.equal(fixture.alarms.size, 0);
  fixture.data['bridge-binding-twitter'] = binding('twitter');
  await controller.cookieChanged({cookie: rawCookie('instagram', 'sessionid')});
  await controller.cookieChanged({cookie: {...rawCookie('twitter', 'ct0'), domain: '.other.example'}});
  await controller.cookieChanged({cookie: {...rawCookie('twitter', 'ct0'), name: 'analytics'}});
  assert.equal(fixture.alarms.size, 0); assert.equal(fixture.calls.cookieReads.length, 0);
});
test('matching events debounce once, throttle to one minute, then sync only the bound platform', async () => {
  const fixture = mockAPI(), controller = createBridgeController(fixture.api, fixture.transport);
  fixture.data['bridge-binding-twitter'] = binding('twitter');
  fixture.data['bridge-status-twitter'] = {state: 'verified', lastAttempt: now - 5000};
  await controller.cookieChanged({cookie: rawCookie('twitter', 'ct0')});
  await controller.cookieChanged({cookie: rawCookie('twitter', 'auth_token')});
  assert.equal(fixture.calls.alarmCreates.length, 1);
  const alarm = fixture.alarms.get(ALARM_PREFIX + 'twitter');
  assert.equal(alarm.when, now + 55000); assert.equal(fixture.calls.cookieReads.length, 0);
  assert.equal((await controller.message({type: 'status'})).items[0].state, 'pending');
  fixture.alarms.delete(alarm.name);
  await controller.alarmFired(alarm);
  assert.equal(fixture.calls.syncs.length, 1);
  assert.ok(fixture.calls.cookieReads.every(row => row.url === 'https://x.com/'));
  assert.equal(fixture.alarms.size, 0);
});
test('expired bridge and revoked permissions do not read website cookies', async () => {
  const fixture = mockAPI(), controller = createBridgeController(fixture.api, fixture.transport);
  fixture.data['bridge-binding-twitter'] = {...binding('twitter'), expiresAt: now - 1};
  let result = await controller.message({type: 'sync', platform: 'twitter'});
  assert.equal(result.code, 'pairing_expired'); assert.equal(fixture.calls.cookieReads.length, 0);
  fixture.data['bridge-binding-twitter'] = binding('twitter'); fixture.deny();
  result = await controller.message({type: 'sync', platform: 'twitter'});
  assert.equal(result.code, 'permission_denied'); assert.equal(fixture.calls.cookieReads.length, 0);
});
test('failed sync records only a static error and never schedules retry polling', async () => {
  const fixture = mockAPI();
  fixture.transport.sync = async () => { throw new Error('SECRET-cookie-response'); };
  const controller = createBridgeController(fixture.api, fixture.transport);
  const result = await controller.message({type: 'connect', pairingURL: pairingURL('twitter')});
  assert.equal(result.ok, false); assert.equal(fixture.alarms.size, 0);
  assert.ok(!JSON.stringify(result).includes('SECRET') && !JSON.stringify(fixture.data).includes('SECRET'));
});
test('disconnect forgets only that binding and cancels later synchronization', async () => {
  const fixture = mockAPI(), controller = createBridgeController(fixture.api, fixture.transport);
  fixture.data['bridge-binding-twitter'] = binding('twitter');
  fixture.data['bridge-binding-instagram'] = binding('instagram');
  await controller.cookieChanged({cookie: rawCookie('twitter', 'ct0')});
  const result = await controller.message({type: 'disconnect', platform: 'twitter'});
  assert.equal(result.disconnected, true);
  assert.ok(!fixture.data['bridge-binding-twitter'] && fixture.data['bridge-binding-instagram']);
  assert.equal(fixture.alarms.size, 0);
  assert.deepEqual(fixture.calls.permissionRemovals, [{origins: ['https://x.com/*']}]);
  await controller.alarmFired({name: ALARM_PREFIX + 'twitter'});
  assert.equal(fixture.calls.cookieReads.length, 0);
});
test('disconnect during an in-flight verification does not restore binding or show success', async () => {
  const fixture = mockAPI(); let release, entered;
  const started = new Promise(resolve => { entered = resolve; });
  fixture.transport.sync = async bound => { entered(); await new Promise(resolve => { release = resolve; }); return {platform: bound.platform, verified: true, saved: true}; };
  const controller = createBridgeController(fixture.api, fixture.transport);
  const pending = controller.message({type: 'connect', pairingURL: pairingURL('twitter')});
  await started;
  await controller.message({type: 'disconnect', platform: 'twitter'});
  release();
  assert.equal((await pending).code, 'not_connected');
  assert.equal((await controller.message({type: 'status'})).items[0].state, 'disconnected');
  assert.ok(!fixture.data['bridge-binding-twitter'] && !fixture.data['bridge-status-twitter']);
});
test('invalid commands cannot inject arbitrary cookies or destinations', async () => {
  const fixture = mockAPI(), controller = createBridgeController(fixture.api, fixture.transport);
  for (const value of [null, {type: 'upload', url: 'https://evil.example', cookies: []}, {type: 'sync', platform: 'evil'}]) {
    assert.equal((await controller.message(value)).ok, false);
  }
  assert.equal(fixture.calls.cookieReads.length, 0); assert.equal(fixture.calls.syncs.length, 0);
});
test('unexpected pairing origins fail validation before any cookie read', async () => {
  const fixture = mockAPI(); fixture.transport.pair = async () => ({...binding('twitter'), origin: 'http://evil.example:18761'});
  const controller = createBridgeController(fixture.api, fixture.transport);
  assert.equal((await controller.message({type: 'connect', pairingURL: pairingURL('twitter')})).ok, false);
  assert.equal(fixture.calls.cookieReads.length, 0); assert.deepEqual(fixture.data, {});
});

test('cold worker starts without optional cookies API and rejects non-popup senders', async () => {
  const fixture = mockAPI(); const listeners = {};
  fixture.api.storage.local.setAccessLevel = async () => {};
  fixture.api.runtime = {id: 'test-extension', getURL: name => 'chrome-extension://test-extension/' + name,
    onMessage: {addListener(fn) { listeners.message = fn; }}};
  fixture.api.permissions.onAdded = {addListener(fn) { listeners.added = fn; }};
  fixture.api.alarms.onAlarm = {addListener(fn) { listeners.alarm = fn; }};
  delete fixture.api.cookies;
  globalThis.chrome = fixture.api;
  try {
    await import('../background.js?offline-cold-start');
    let reply;
    assert.equal(listeners.message({type: 'connect', pairingURL: pairingURL('twitter')}, {id: 'foreign', url: 'https://x.com/'}, result => { reply = result; }), false);
    assert.equal(reply.code, 'invalid_message');
    let registrations = 0;
    fixture.api.cookies = {onChanged: {addListener() { registrations++; }}};
    listeners.added(); listeners.added();
    assert.equal(registrations, 1);
  } finally { delete globalThis.chrome; }
});
