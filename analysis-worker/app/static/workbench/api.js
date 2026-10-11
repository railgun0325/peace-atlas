export class ApiError extends Error {
  constructor(message, code, details) { super(message); this.name = 'ApiError'; this.code = code; this.details = details; }
}

export async function api(path, { method = 'GET', body, signal, timeout = 25000, responseType = 'json' } = {}) {
  const controller = new AbortController();
  const relay = () => controller.abort(signal.reason);
  if (signal?.aborted) relay();
  signal?.addEventListener('abort', relay, { once: true });
  const timer = setTimeout(() => controller.abort(new DOMException('请求超时，请重试。', 'TimeoutError')), timeout);
  try {
    const response = await fetch(`/api/v2/${path}`, {
      method, signal: controller.signal, cache: 'no-store',
      headers: body === undefined ? { Accept: 'application/json' } : { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (response.ok && responseType === 'download') {
      const disposition = response.headers.get('Content-Disposition') || '';
      if (!/^attachment\b/i.test(disposition)) throw new ApiError('服务尚未返回可下载文件，请刷新后重试。', 'invalid_download');
      return { blob: await response.blob(), filename: downloadFilename(disposition) };
    }
    const text = await response.text();
    let data;
    try { data = text ? JSON.parse(text) : {}; } catch { throw new ApiError('服务返回了无法读取的内容，请检查服务状态。', 'invalid_response'); }
    if (!response.ok || data.ok === false) {
      const detail = data.error || data.detail;
      const message = typeof detail === 'string' ? detail : detail?.message || data.message || (Array.isArray(detail) ? detail.map(d => d.msg || String(d)).join('；') : `请求失败（HTTP ${response.status}）`);
      throw new ApiError(message, detail?.code || response.status, detail?.details || detail);
    }
    return data;
  } catch (error) {
    if (controller.signal.aborted && !signal?.aborted) throw new ApiError('请求超时，请检查连接后重试。', 'timeout');
    if (error instanceof TypeError) throw new ApiError('无法连接本地服务，请检查应用是否正在运行。', 'network');
    throw error;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', relay);
  }
}

export function queryString(values) {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => { if (value !== '' && value !== null && value !== undefined) params.set(key, String(value)); });
  return params.toString();
}
export const pathId = id => encodeURIComponent(String(id));

export function downloadFilename(disposition, fallback = 'peace-atlas-export') {
  const encoded = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(disposition);
  const plain = /filename\s*=\s*(?:"([^"]+)"|([^;]+))/i.exec(disposition);
  let name = plain?.[1] || plain?.[2]?.trim() || fallback;
  if (encoded) { try { name = decodeURIComponent(encoded[1].trim()); } catch { /* Keep the plain name on malformed encoding. */ } }
  return name.replace(/[\u0000-\u001f\u007f\\/:*?"<>|]/g, '_').replace(/^[. ]+|[. ]+$/g, '').slice(0, 180) || fallback;
}

export async function download(path, options = {}) {
  const result = await api(path, { timeout: 90000, ...options, responseType: 'download' });
  if (options.signal?.aborted) throw options.signal.reason;
  const url = URL.createObjectURL(result.blob);
  const anchor = document.createElement('a');
  anchor.href = url; anchor.download = result.filename;
  document.body.append(anchor);
  try { anchor.click(); } finally { anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
  return result.filename;
}

export function pageNumber(value) { const page = Number(value); return Number.isSafeInteger(page) && page > 0 ? page : 1; }

// A saved URL can outlive its result set. Recover the last valid page rather
// than presenting an existing collection as empty or walking back one by one.
export async function apiPage(path, values, options = {}) {
  let page = pageNumber(values.page);
  let data = await api(`${path}?${queryString({ ...values, page })}`, options);
  const last = Math.max(1, Math.ceil(Number(data.total || 0) / values.page_size));
  if (page > last) { page = last; data = await api(`${path}?${queryString({ ...values, page })}`, options); }
  return { ...data, page, page_size: values.page_size };
}
