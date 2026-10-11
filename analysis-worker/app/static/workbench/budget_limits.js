// Fallbacks match the local service; live capabilities remain authoritative.
const defaults = {
  research: { depth: { min: 1, max: 8, default: 2 }, max_accounts: { min: 0, max: 10000, default: 0 }, max_edges: { min: 0, max: 100000, default: 0 }, max_records: { min: 0, max: 1000000, default: 0 }, max_scrolls: { min: 1, max: 5000, default: 10 }, time_budget_seconds: { min: 0, max: 2592000, default: 0 }, max_records_per_account: { min: 1, max: 50000, default: 1000 }, max_neighbors_per_account: { min: 1, max: 5000, default: 100 } },
  collect: { max_records: { min: 1, max: 100, default: 30 }, max_scrolls: { min: 1, max: 20, default: 8 } },
  batch: { total_records: { min: 1, max: 1000000, default: 1000 }, max_tasks: 200 },
};

export function budgetLimits(capabilities = {}) {
  const supplied = capabilities.budget_limits || {};
  const result = {};
  for (const [kind, fields] of Object.entries(defaults)) {
    result[kind] = {};
    for (const [name, fallback] of Object.entries(fields)) {
      if (typeof fallback === 'number') { result[kind][name] = Number.isInteger(supplied[kind]?.[name]) && supplied[kind][name] > 0 ? supplied[kind][name] : fallback; continue; }
      const value = supplied[kind]?.[name];
      const legacyMax = kind === 'research' ? capabilities.research?.[name] : undefined;
      const max = Number.isInteger(value?.max) && value.max >= fallback.min ? value.max : Number.isInteger(legacyMax) && legacyMax >= fallback.min ? legacyMax : fallback.max;
      const min = Number.isInteger(value?.min) && value.min >= fallback.min && value.min <= max ? value.min : fallback.min;
      const preferred = Number.isInteger(value?.default) ? value.default : fallback.default;
      result[kind][name] = { min, max, default: Math.max(min, Math.min(max, preferred)) };
    }
  }
  return result;
}

export function durationText(seconds) {
  if (!Number.isFinite(Number(seconds)) || Number(seconds) < 0) return '待填写';
  const whole = Math.round(Number(seconds));
  const hours = Math.floor(whole / 3600), minutes = Math.floor(whole % 3600 / 60), rest = whole % 60;
  return [hours && `${hours} 小时`, minutes && `${minutes} 分钟`, rest && `${rest} 秒`].filter(Boolean).join(' ') || '0 秒';
}
