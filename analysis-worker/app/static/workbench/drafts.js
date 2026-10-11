export const COLLECTION_DRAFT_KEY = 'peace-atlas.collection-draft.v1';
const MAX_DRAFT_BYTES = 16000;
const FIELDS = new Set(['source', 'mode', 'workflow', 'keyword', 'keyword_mode', 'handle', 'seed_accounts', 'feed_url', 'start_date', 'end_date', 'max_records', 'relevance', 'max_accounts', 'max_scrolls', 'include_following', 'max_following', 'depth', 'max_edges', 'time_budget_seconds', 'analysis_mode', 'seed_news', 'seed_twitter', 'seed_instagram', 'expand_following', 'expand_followers', 'expand_mentions', 'research_max_accounts', 'research_max_scrolls', 'max_records_per_account', 'max_neighbors_per_account', 'research_goal', 'batch_kind', 'total_records', 'batch_title']);

export function normalizeDraft(value) {
  if (!value || value.schema !== 1 || !value.values || typeof value.values !== 'object' || Array.isArray(value.values) || !Number.isFinite(Date.parse(value.updated_at))) return null;
  const values = {};
  for (const [key, raw] of Object.entries(value.values)) if (FIELDS.has(key)) {
    if (typeof raw === 'string' && raw.length > 2400) return null;
    if (typeof raw === 'boolean' || typeof raw === 'string') values[key] = raw;
    else if (Number.isInteger(raw)) values[key] = String(raw);
  }
  const modeBySource = {};
  for (const [key, mode] of Object.entries(value.modeBySource || {})) if (['twitter', 'instagram'].includes(key) && ['keyword', 'account', 'hashtag'].includes(mode)) modeBySource[key] = mode;
  const result = { schema: 1, values, modeBySource, updated_at: value.updated_at, ...(Number.isInteger(value.revision) && value.revision >= 0 ? { revision: value.revision } : {}) };
  return JSON.stringify(result).length <= MAX_DRAFT_BYTES ? result : null;
}

export function readDraft(storage = globalThis.localStorage) {
  try { return normalizeDraft(JSON.parse(storage?.getItem(COLLECTION_DRAFT_KEY) || 'null')); } catch { return null; }
}

export function writeDraft(draft, storage = globalThis.localStorage) {
  const safe = normalizeDraft(draft);
  if (!safe) throw new Error('草稿内容无法保存，请检查输入长度。');
  try { storage?.setItem(COLLECTION_DRAFT_KEY, JSON.stringify(safe)); return Boolean(storage); } catch { return false; }
}

export function removeDraft(storage = globalThis.localStorage) { try { storage?.removeItem(COLLECTION_DRAFT_KEY); } catch {} }

// A revision may be added after a save; it does not change which local input
// snapshot owns the draft. Compare its contents and capture time instead.
export function sameDraft(left, right) {
  const a = normalizeDraft(left), b = normalizeDraft(right);
  if (!a || !b) return false;
  const entries = value => Object.entries(value).sort(([one], [two]) => one.localeCompare(two));
  return a.updated_at === b.updated_at && JSON.stringify(entries(a.values)) === JSON.stringify(entries(b.values)) && JSON.stringify(entries(a.modeBySource)) === JSON.stringify(entries(b.modeBySource));
}

export function removeDraftIf(expected, storage = globalThis.localStorage) {
  if (!sameDraft(readDraft(storage), expected)) return false;
  try { storage?.removeItem(COLLECTION_DRAFT_KEY); return Boolean(storage); } catch { return false; }
}

export function captureDraft(form, modeBySource = {}, now = new Date()) {
  const values = {};
  for (const input of form.querySelectorAll('input[name],select[name],textarea[name]')) if (FIELDS.has(input.name)) values[input.name] = input.type === 'checkbox' ? input.checked : input.value;
  return normalizeDraft({ schema: 1, values, modeBySource, updated_at: now.toISOString() });
}

export function restoreDraft(form, draft) {
  for (const input of form.querySelectorAll('input[name],select[name],textarea[name]')) if (Object.hasOwn(draft?.values || {}, input.name)) {
    const value = draft.values[input.name];
    if (input.type === 'checkbox') input.checked = value === true;
    else if (input.tagName === 'SELECT') { if ([...input.options].some(option => option.value === value)) input.value = value; }
    else input.value = String(value);
  }
}

export function preferredDraft(local, server) {
  const left = normalizeDraft(local), right = normalizeDraft(server);
  if (!left) return right;
  if (!right) return left;
  return Date.parse(left.updated_at) >= Date.parse(right.updated_at) ? left : right;
}

// Writes are serialized so a slower save cannot overwrite a later edit or the
// clear issued only after the task has been accepted by the backend.
export function draftSynchronizer({ save, clear, onStatus = () => {}, delay = 350, setTimer = setTimeout, cancelTimer = clearTimeout }) {
  let pending = null, timer = null, chain = Promise.resolve(), closed = false;
  const queue = work => { chain = chain.catch(() => {}).then(work); return chain; };
  const flush = () => {
    if (timer !== null) cancelTimer(timer);
    timer = null;
    const draft = pending; pending = null;
    if (!draft) return chain;
    return queue(async () => { try { await save(draft); onStatus('saved'); } catch (error) { onStatus('local', error); } });
  };
  return {
    changed(draft) { if (closed) return; pending = draft; onStatus('local'); if (timer !== null) cancelTimer(timer); timer = setTimer(flush, delay); },
    flush,
    // The caller owns the submitted snapshot. New edits keep using this
    // synchronizer, including edits made while the conditional clear awaits.
    async clearIf(unchanged) { await flush(); return queue(async () => { if (!unchanged()) return false; await clear(); return true; }); },
    async clear() { closed = true; pending = null; if (timer !== null) cancelTimer(timer); timer = null; await queue(clear); },
    close() { closed = true; return flush(); },
  };
}
