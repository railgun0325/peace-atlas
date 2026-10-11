import {ALARM_PREFIX, PLATFORMS, BridgeError, pairBridge, parsePairingURL, platformInfo, publicError,
  readPlatformCookies, relevantCookieChange, requiredPermissions, syncBridge, validateBinding} from './bridge-core.js';

const bindingKey = platform => 'bridge-binding-' + platform;
const statusKey = platform => 'bridge-status-' + platform;

// All platform data access lives in the worker, never the popup. Dependencies
// are injected for offline tests; production uses the browser's cookies API.
export function createBridgeController(api, {pair = pairBridge, sync = syncBridge, now = Date.now} = {}) {
  const flights = new Set(), generations = new Map();
  const generation = platform => generations.get(platform) || 0;
  const nextGeneration = platform => { const value = generation(platform) + 1; generations.set(platform, value); return value; };

  async function bindingFor(platform) {
    platformInfo(platform);
    return (await api.storage.local.get(bindingKey(platform)))[bindingKey(platform)];
  }
  async function setStatus(platform, status, expectedGeneration) {
    if (expectedGeneration !== undefined && generation(platform) !== expectedGeneration) return;
    await api.storage.local.set({[statusKey(platform)]: status});
  }
  async function withFlight(platform, action) {
    platformInfo(platform);
    if (flights.has(platform)) throw new BridgeError('busy');
    flights.add(platform);
    try { return await action(); } finally { flights.delete(platform); }
  }
  async function permitted(platform) {
    if (!await api.permissions.contains(requiredPermissions(platform))) throw new BridgeError('permission_denied');
  }
  async function performSync(platform, expectedGeneration) {
    const binding = validateBinding(await bindingFor(platform), now());
    const attemptedAt = now();
    await setStatus(platform, {state: 'syncing', message: '正在由本机工作台验证会话…', lastAttempt: attemptedAt}, expectedGeneration);
    try {
      await permitted(platform);
      const cookies = await readPlatformCookies(api.cookies, platform, {now: now()});
      if (generation(platform) !== expectedGeneration) throw new BridgeError('not_connected');
      const result = await sync(binding, cookies, {now: now()});
      if (generation(platform) !== expectedGeneration) throw new BridgeError('not_connected');
      // Do not accept a custom transport's optimistic or merely "opened" result.
      if (result?.verified !== true || result?.saved !== true || result?.platform !== platform) throw new BridgeError('verification_failed');
      await setStatus(platform, {state: 'verified', message: '已验证登录并保存到本机工作台。', checkedAt: now(), lastAttempt: attemptedAt}, expectedGeneration);
      return {ok: true, platform, verified: true, saved: true};
    } catch (error) {
      const failure = publicError(error);
      await setStatus(platform, {state: 'waiting', code: failure.code, message: failure.message, lastAttempt: attemptedAt}, expectedGeneration);
      return {...failure, platform};
    }
  }
  async function connect(pairingURL) {
    const {platform} = parsePairingURL(pairingURL);
    return withFlight(platform, async () => {
      await permitted(platform);
      const epoch = nextGeneration(platform);
      const binding = validateBinding(await pair(pairingURL, {now: now()}), now());
      if (binding.platform !== platform || generation(platform) !== epoch) throw new BridgeError('not_connected');
      // This is our local bridge credential, not a website cookie. The one-use
      // pairing code and collected cookies are never written to storage.
      await api.storage.local.set({[bindingKey(platform)]: binding});
      return performSync(platform, epoch);
    });
  }
  async function synchronize(platform) {
    return withFlight(platform, () => performSync(platform, generation(platform)));
  }
  async function disconnect(platform) {
    platformInfo(platform);
    nextGeneration(platform);
    await api.alarms.clear(ALARM_PREFIX + platform);
    await api.storage.local.remove([bindingKey(platform), statusKey(platform)]);
    // The other platform can remain connected. Remove only this site's grant.
    await api.permissions.remove({origins: [platformInfo(platform).permission]});
    return {ok: true, platform, disconnected: true};
  }
  async function statuses() {
    const result = [];
    for (const [platform, info] of Object.entries(PLATFORMS)) {
      const values = await api.storage.local.get([bindingKey(platform), statusKey(platform)]);
      const binding = values[bindingKey(platform)], status = values[statusKey(platform)] || {};
      let valid = false, failure = null;
      try { validateBinding(binding, now()); valid = true; } catch (error) { if (binding) failure = publicError(error); }
      result.push({platform, label: info.label, connected: valid,
        state: failure ? 'waiting' : (valid ? (status.state || 'waiting') : 'disconnected'),
        message: failure?.message || (valid ? (status.message || '已配对，尚未验证登录。') : '未连接'),
        checkedAt: valid && Number.isFinite(status.checkedAt) ? status.checkedAt : null,
        origin: valid ? binding.origin : null, expiresAt: valid ? binding.expiresAt : null});
    }
    return {ok: true, items: result};
  }
  async function cookieChanged(change) {
    for (const platform of Object.keys(PLATFORMS)) {
      if (!relevantCookieChange(change, platform)) continue;
      try { validateBinding(await bindingFor(platform), now()); await permitted(platform); } catch { continue; }
      const alarm = ALARM_PREFIX + platform;
      if (await api.alarms.get(alarm)) continue;
      const previous = (await api.storage.local.get(statusKey(platform)))[statusKey(platform)] || {};
      const when = Math.max(now() + 30000, Number(previous.lastAttempt || 0) + 60000);
      await api.alarms.create(alarm, {when});
      await setStatus(platform, {state: 'pending', message: '检测到该平台会话变化，稍后自动验证。', lastAttempt: previous.lastAttempt || 0});
    }
  }
  async function alarmFired(alarm) {
    const platform = Object.keys(PLATFORMS).find(value => alarm?.name === ALARM_PREFIX + value);
    if (!platform) return;
    try { await synchronize(platform); } catch (error) {
      // Failures are static messages. There is no automatic retry timer;
      // another user action or matching cookie change may schedule a new try.
      if (error instanceof BridgeError && error.code === 'busy') return;
      const failure = publicError(error);
      if (await bindingFor(platform)) await setStatus(platform, {state: 'waiting', code: failure.code, message: failure.message, lastAttempt: now()});
    }
  }
  async function message(value) {
    try {
      if (!value || typeof value !== 'object') throw new BridgeError('invalid_message');
      if (value.type === 'status') return await statuses();
      if (value.type === 'connect') return await connect(value.pairingURL);
      if (value.type === 'sync') return await synchronize(value.platform);
      if (value.type === 'disconnect') return await disconnect(value.platform);
      throw new BridgeError('invalid_message');
    } catch (error) { return publicError(error); }
  }
  return {message, cookieChanged, alarmFired};
}
