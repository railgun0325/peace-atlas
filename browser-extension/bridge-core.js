// Browser-independent validation and transport. No storage or background reads.
export const PLATFORMS = Object.freeze({
  twitter: Object.freeze({label: 'X / Twitter', url: 'https://x.com/', permission: 'https://x.com/*',
    domains: Object.freeze(['.x.com', 'x.com']), names: Object.freeze(['auth_token', 'ct0', 'twid']),
    required: Object.freeze(['auth_token', 'ct0']), httpOnly: 'auth_token'}),
  instagram: Object.freeze({label: 'Instagram', url: 'https://www.instagram.com/', permission: 'https://www.instagram.com/*',
    domains: Object.freeze(['.instagram.com', 'instagram.com', 'www.instagram.com']), names: Object.freeze(['sessionid', 'csrftoken', 'ds_user_id']),
    required: Object.freeze(['sessionid', 'csrftoken']), httpOnly: 'sessionid'}),
});
export const LOOPBACK_PERMISSION = 'http://127.0.0.1/*';
export const ALARM_PREFIX = 'workbench-bridge-sync-';
const PREFIX = '/api/v2/social/bridge/';
const TOKEN = /^[A-Za-z0-9_-]{32,256}$/;
const ERROR_MESSAGES = Object.freeze({
  invalid_platform: '仅支持 X 和 Instagram。',
  invalid_pairing_url: '请粘贴工作台生成的完整本机配对链接，不能填写网站或其他服务器地址。',
  permission_denied: '未获得所选平台的会话权限；尚未读取或同步登录信息。',
  missing_session: '当前浏览器尚未提供完整的平台登录会话。请在普通浏览器登录，扩展会在会话变化后重新尝试。',
  ambiguous_session: '检测到重复或不同作用域的会话，无法安全确定身份；本次未同步。',
  unsafe_cookie: '会话的域、路径、安全属性或到期时间不符合要求；本次未同步。',
  expired_session: '当前平台会话已到期，请在普通浏览器续登。',
  pairing_failed: '配对未完成。请确认工作台正在运行，并复制新的配对链接。',
  pairing_expired: '本机连接已到期或工作台已重启，请重新复制配对链接。',
  verification_failed: '工作台尚未确认平台已登录；保留原有会话，没有标记登录成功。',
  login_required: '平台要求重新登录，请在普通浏览器完成后再次验证。',
  rate_limited: '平台暂时限制请求，请稍后手动验证；扩展不会连续重试。',
  challenge: '平台要求额外验证，请在普通浏览器完成后再次验证。',
  busy: '工作台正在处理该平台任务，请任务结束后再次验证。',
  timeout: '本机工作台未及时确认结果。当前保存状态未确认，请查看工作台后再验证。',
  network_error: '无法连接本机工作台，请先启动应用；没有改发到其他地址。',
  invalid_response: '本机工作台返回的配对信息不符合协议，请重新配对。',
  not_connected: '请先使用工作台配对链接连接此平台。',
  invalid_message: '请求无效，未读取或同步登录信息。',
});

export class BridgeError extends Error {
  constructor(code) { super(ERROR_MESSAGES[code] || ERROR_MESSAGES.verification_failed); this.name = 'BridgeError'; this.code = code in ERROR_MESSAGES ? code : 'verification_failed'; }
}
export function publicError(error) {
  const code = error instanceof BridgeError ? error.code : 'network_error';
  return {ok: false, code, message: ERROR_MESSAGES[code]};
}
export function platformInfo(platform) {
  if (!Object.hasOwn(PLATFORMS, platform)) throw new BridgeError('invalid_platform');
  return PLATFORMS[platform];
}
export function validateOrigin(origin) {
  if (typeof origin !== 'string' || !/^http:\/\/127\.0\.0\.1:[1-9]\d{0,4}$/.test(origin)) throw new BridgeError('invalid_pairing_url');
  const port = Number(origin.slice(origin.lastIndexOf(':') + 1));
  if (!Number.isInteger(port) || port > 65535 || port === 80) throw new BridgeError('invalid_pairing_url');
  const parsed = new URL(origin);
  if (parsed.origin !== origin) throw new BridgeError('invalid_pairing_url');
  return origin;
}
export function parsePairingURL(input) {
  if (typeof input !== 'string' || input.length > 1024) throw new BridgeError('invalid_pairing_url');
  const raw = input.trim();
  if (!/^http:\/\/127\.0\.0\.1:[1-9]\d{0,4}\/api\/v2\/social\/bridge\/connect\?/.test(raw)) throw new BridgeError('invalid_pairing_url');
  let url;
  try { url = new URL(raw); } catch { throw new BridgeError('invalid_pairing_url'); }
  if (url.username || url.password || url.hash || url.pathname !== PREFIX + 'connect' || !url.port) throw new BridgeError('invalid_pairing_url');
  const origin = validateOrigin(url.origin);
  const entries = [...url.searchParams];
  if (entries.length !== 2 || new Set(entries.map(([key]) => key)).size !== 2 || entries.some(([key]) => !['code', 'platform'].includes(key))) throw new BridgeError('invalid_pairing_url');
  const platform = url.searchParams.get('platform');
  platformInfo(platform);
  const code = url.searchParams.get('code');
  if (!/^[a-f0-9]{64}$/.test(code || '')) throw new BridgeError('invalid_pairing_url');
  return {origin, code, platform};
}
export function requiredPermissions(platform) {
  return {permissions: ['cookies'], origins: [platformInfo(platform).permission, LOOPBACK_PERMISSION]};
}

export function normalizeCookies(platform, input, {now = Date.now(), format = 'chrome'} = {}) {
  const info = platformInfo(platform);
  if (!Array.isArray(input) || input.length > info.names.length) throw new BridgeError('ambiguous_session');
  const names = new Set(), stores = new Set();
  const result = input.map(row => {
    if (!row || typeof row !== 'object' || !info.names.includes(row.name) || !info.domains.includes(row.domain)
      || row.path !== '/' || row.secure !== true || typeof row.httpOnly !== 'boolean'
      || (row.name === info.httpOnly && !row.httpOnly) || row.partitionKey !== undefined || row.partitioned === true) throw new BridgeError('unsafe_cookie');
    if (names.has(row.name)) throw new BridgeError('ambiguous_session');
    names.add(row.name);
    if (format === 'chrome') {
      if (typeof row.storeId !== 'string' || !row.storeId) throw new BridgeError('ambiguous_session');
      stores.add(row.storeId);
    }
    if (typeof row.value !== 'string' || !row.value.trim() || row.value.length > 4096 || /[\x00-\x20\x7f]/.test(row.value)) throw new BridgeError('unsafe_cookie');
    const expires = format === 'wire' ? row.expires : (row.session === true && row.expirationDate === undefined ? -1 : row.expirationDate);
    if (format === 'chrome' && expires === -1 && (row.session !== true || row.expirationDate !== undefined)) throw new BridgeError('unsafe_cookie');
    if (expires !== -1 && (typeof expires !== 'number' || !Number.isFinite(expires) || expires < 946684800 || expires > 4102444800)) throw new BridgeError('unsafe_cookie');
    if (expires !== -1 && expires <= now / 1000) throw new BridgeError('expired_session');
    const clean = {name: row.name, value: row.value, domain: row.domain, path: '/', secure: true, httpOnly: row.httpOnly, expires};
    // Unspecified cookies use the browser's default Lax restriction. Never
    // widen an unspecified cookie to cross-site SameSite=None on transfer.
    const sameSite = format === 'wire' ? row.sameSite : ({no_restriction: 'None', lax: 'Lax', strict: 'Strict', unspecified: 'Lax'})[row.sameSite];
    if (format === 'chrome' && !['no_restriction', 'lax', 'strict', 'unspecified'].includes(row.sameSite)) throw new BridgeError('unsafe_cookie');
    if (!['None', 'Lax', 'Strict'].includes(sameSite)) throw new BridgeError('unsafe_cookie');
    clean.sameSite = sameSite;
    return clean;
  });
  if (stores.size > 1) throw new BridgeError('ambiguous_session');
  if (info.required.some(name => !names.has(name))) throw new BridgeError('missing_session');
  return result;
}

export async function readPlatformCookies(cookieAPI, platform, options = {}) {
  const info = platformInfo(platform);
  const records = [];
  // Each API request specifies one fixed site and one necessary name. No
  // unrestricted getAll, other cookie stores, passwords, or profile files.
  for (const name of info.names) {
    const rows = await cookieAPI.getAll({url: info.url, name, path: '/'});
    if (!Array.isArray(rows)) throw new BridgeError('unsafe_cookie');
    records.push(...rows);
  }
  return normalizeCookies(platform, records, options);
}
export function relevantCookieChange(change, platform) {
  const info = platformInfo(platform), cookie = change?.cookie;
  return !!cookie && info.names.includes(cookie.name) && info.domains.includes(cookie.domain) && cookie.path === '/' && cookie.partitionKey === undefined;
}
export function validateBinding(binding, now = Date.now()) {
  if (!binding || typeof binding !== 'object') throw new BridgeError('not_connected');
  validateOrigin(binding.origin);
  platformInfo(binding.platform);
  if (!TOKEN.test(binding.token || '') || !Number.isFinite(binding.expiresAt)) throw new BridgeError('invalid_response');
  if (binding.expiresAt <= now) throw new BridgeError('pairing_expired');
  return binding;
}

async function request(origin, endpoint, payload, token, {fetchImpl = globalThis.fetch, timeoutMs = 25000} = {}) {
  validateOrigin(origin);
  if (!['pair', 'sync'].includes(endpoint)) throw new BridgeError('invalid_message');
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  const headers = {'Content-Type': 'application/json'};
  if (token) headers['X-Workbench-Bridge'] = token;
  try {
    const response = await fetchImpl(origin + PREFIX + endpoint, {method: 'POST', headers, body: JSON.stringify(payload),
      redirect: 'error', credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer', signal: controller.signal});
    if (!response || response.redirected || (response.url && response.url !== origin + PREFIX + endpoint)) throw new BridgeError('network_error');
    let data;
    try { data = await response.json(); } catch { throw new BridgeError('invalid_response'); }
    if (!response.ok) {
      const serverCode = data?.error?.code || data?.detail?.code || data?.code || data?.outcome;
      const aliases = {bridge_busy: 'busy', bridge_denied: 'pairing_expired', pair_denied: 'pairing_failed',
        invalid_scope: 'unsafe_cookie', invalid_session: 'unsafe_cookie', save_failed: 'verification_failed'};
      const code = Object.hasOwn(aliases, serverCode) ? aliases[serverCode] : serverCode;
      if (typeof code === 'string' && Object.hasOwn(ERROR_MESSAGES, code)) throw new BridgeError(code);
      throw new BridgeError(response.status === 401 ? 'pairing_expired' : (endpoint === 'pair' ? 'pairing_failed' : 'verification_failed'));
    }
    return data;
  } catch (error) {
    if (error instanceof BridgeError) throw error;
    throw new BridgeError(controller.signal.aborted ? 'timeout' : 'network_error');
  } finally { clearTimeout(timeout); }
}

export async function pairBridge(pairingURL, options = {}) {
  const {origin, code, platform} = parsePairingURL(pairingURL);
  const data = await request(origin, 'pair', {code, platform}, null, options);
  const now = options.now ?? Date.now();
  const expiresAt = typeof data?.expires_at === 'number' ? data.expires_at * 1000 : Date.parse(data?.expires_at);
  if (data?.platform !== platform || !TOKEN.test(data?.bridge_token || '') || !Number.isFinite(expiresAt) || expiresAt <= now || expiresAt > now + 24 * 60 * 60 * 1000 + 60000) throw new BridgeError('invalid_response');
  return {origin, platform, token: data.bridge_token, expiresAt};
}
export async function syncBridge(binding, cookies, options = {}) {
  validateBinding(binding, options.now ?? Date.now());
  const clean = normalizeCookies(binding.platform, cookies, {format: 'wire', now: options.now ?? Date.now()});
  const data = await request(binding.origin, 'sync', {platform: binding.platform, cookies: clean}, binding.token, options);
  if (data?.platform !== binding.platform || data?.verified !== true || data?.saved !== true || data?.source !== 'browser_extension') throw new BridgeError('verification_failed');
  // Backend responses are reduced to a fixed public shape; no server message,
  // token, path, or cookie payload is forwarded into the popup or logs.
  return {verified: true, saved: true, platform: binding.platform, source: 'browser_extension'};
}
