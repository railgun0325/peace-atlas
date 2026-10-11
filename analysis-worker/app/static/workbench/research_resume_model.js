import { budgetLimits } from './budget_limits.js';

export const continuationFields = {
  max_accounts: '账号上限', max_edges: '关系上限', max_records: '材料上限', time_budget_seconds: '本次运行时长（秒）',
};
const count = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
const hardStops = { account_budget: 'max_accounts', edge_budget: 'max_edges', record_budget: 'max_records' };

export function continuationState(run = {}) {
  const summary = run.summary || {};
  const deferred = count(summary.deferred_count);
  const records = count(summary.deferred_record_count);
  const relations = count(summary.deferred_relation_count);
  const queued = Array.isArray(run.frontier) ? run.frontier.length : 0;
  const pendingWork = count(summary.pending_account_work_count) || 0;
  const pendingQueries = count(summary.pending_seed_query_count) ?? count(summary.continuation_pending_queries);
  const legacyGap = summary.frontier_recovery_required === true || summary.collection_recovery_required === true
    || (!summary.deferred_frontier_version && Number(summary.account_budget_skipped) > 0)
    || (!summary.pending_work_version && ['record_budget', 'edge_budget'].includes(summary.collection_stop_reason || summary.stop_reason));
  const savedBacklog = queued > 0 || pendingWork > 0 || deferred > 0 || records > 0 || relations > 0 || pendingQueries > 0;
  const active = ['queued', 'running'].includes(run.status);
  const available = !active && (typeof summary.continuation_available === 'boolean' ? summary.continuation_available : savedBacklog);
  const reason = active ? '该研究已有待执行或运行中的任务。'
    : summary.continuation_reason || (available ? '' : legacyGap ? '旧记录没有保存可恢复的候选或待执行分支；这部分缺口未知，无法恢复。' : '没有已保存的待探索账号、材料、关系或检索入口可继续。');
  return { deferred, records, relations, queued, pendingWork, pendingQueries, legacyGap, savedBacklog, active, available, reason,
    stopReason: summary.collection_stop_reason || summary.stop_reason || '' };
}

export function continuationLimits(capabilities) {
  if (!capabilities?.research?.available) throw new Error('当前服务没有提供继续研究能力。');
  const caps = capabilities.research;
  for (const name of ['max_accounts', 'max_edges']) {
    if (!Number.isSafeInteger(caps[name]) || caps[name] < 1) throw new Error('服务未返回完整研究上限，请重新读取。');
  }
  const budgets = budgetLimits(capabilities).research;
  return { max_accounts: { min: caps.optional_stops ? 0 : 1, max: caps.max_accounts }, max_edges: { min: caps.optional_stops ? 0 : 1, max: caps.max_edges },
    max_records: budgets.max_records, time_budget_seconds: budgets.time_budget_seconds };
}

export function continuationRequest(run, values, limits) {
  const state = continuationState(run);
  if (!state.available) throw new Error(state.reason);
  const request = {};
  for (const [name, label] of Object.entries(continuationFields)) {
    const value = Number(values[name]), bound = limits[name];
    if (!String(values[name] ?? '').trim() || !Number.isSafeInteger(value) || value < bound.min || value > bound.max)
      throw new Error(`${label}必须是 ${bound.min}–${bound.max} 之间的整数。`);
    if (value !== 0 && (run.params?.[name] === 0 || value < Number(run.params?.[name] || 0)))
      throw new Error(`${label}不能低于当前研究上限 ${run.params[name]}。`);
    request[name] = value;
  }
  const stops = new Set([state.stopReason, ...(Array.isArray(run.summary?.limit_hits) ? run.summary.limit_hits : [])]);
  for (const reason of stops) {
    const stoppedField = hardStops[reason];
    if (!stoppedField) continue;
    const recorded = Number.isSafeInteger(run.summary?.[stoppedField]) ? run.summary[stoppedField] : Number(run.params?.[stoppedField] || 0);
    if (request[stoppedField] !== 0 && recorded >= limits[stoppedField].max)
      throw new Error(`已达到当前服务允许的${continuationFields[stoppedField]} ${limits[stoppedField].max}，当前能力无法继续扩容。`);
    if (request[stoppedField] !== 0 && request[stoppedField] <= recorded)
      throw new Error(`已达到${continuationFields[stoppedField]}，请提高该上限后继续。`);
  }
  return request;
}
