import { api } from './api.js';
import { el, errorBox, number } from './ui.js';
import { budgetLimits, durationText } from './budget_limits.js';

export function batchIntentKey(value) { return JSON.stringify(value); }

export function researchAnalysisPending(job) {
  if (job?.kind !== 'research') return false;
  const summary = job.result?.summary || {}, ai = job.result?.analysis?.ai || job.result?.ai || {};
  const pending = summary.analysis_pending_evidence ?? ai.pending_evidence_count;
  return Number(pending) > 0 || ['partial', 'failed', 'pending'].includes(summary.analysis_status);
}

export function hardBudgetPartial(job) {
  return job?.kind === 'research' && job.status === 'partial' && !researchAnalysisPending(job) && ['record_budget', 'account_budget', 'edge_budget'].includes(job.result?.summary?.stop_reason);
}

export function batchSummary(batch) {
  const tasks = Number(batch.task_count ?? batch.jobs?.length ?? 0);
  const counts = batch.counts || {};
  return {
    tasks,
    succeeded: Number(batch.succeeded_count ?? counts.succeeded ?? 0),
    ended: Number(batch.completed_count ?? tasks - Number(counts.queued || 0) - Number(counts.running || 0)),
    active: Number(batch.active_count ?? Number(counts.queued || 0) + Number(counts.running || 0)),
    retryable: Number(batch.retryable_count ?? (Array.isArray(batch.jobs) ? batch.jobs.filter(job => ['failed', 'partial', 'cancelled', 'interrupted'].includes(job.status) && !hardBudgetPartial(job)).length : ['failed', 'partial', 'cancelled', 'interrupted'].reduce((total, key) => total + Number(counts[key] || 0), 0))),
    saved: Number(batch.records_saved || 0),
    target: Number(batch.total_records || 0),
    complete: batch.status === 'succeeded' && tasks > 0 && Number(batch.succeeded_count ?? counts.succeeded ?? 0) === tasks,
  };
}

export function normalizeBatchPlan(value, limits = budgetLimits()) {
  if (!value || !Array.isArray(value.tasks) || !value.tasks.length || value.tasks.length > limits.batch.max_tasks) throw new Error('批量计划未包含有效任务，请调整目标后重试。');
  const tasks = value.tasks.map(task => {
    const maximum = limits[task?.kind]?.max_records.max;
    if (!['research', 'collect'].includes(task?.kind) || !task.params || typeof task.params !== 'object' || !Number.isInteger(task.params.max_records) || task.params.max_records < 1 || task.params.max_records > maximum) throw new Error('批量计划的任务预算无法确认，请重新生成。');
    return { kind: task.kind, params: task.params, label: String(task.label || task.params.keyword || task.params.handle || '公开资料任务') };
  });
  const totalRecords = Number(value.total_records), maxPossible = Number(value.max_possible_records);
  if (!Number.isInteger(totalRecords) || totalRecords < 1 || totalRecords > limits.batch.total_records.max || !Number.isInteger(maxPossible) || maxPossible < 1 || maxPossible > limits.batch.total_records.max) throw new Error('批量计划的总体预算无法确认，请重新生成。');
  return { preview_token: typeof value.preview_token === "string" ? value.preview_token : "", estimate: value.estimate || null, tasks, total_records: totalRecords, max_possible_records: maxPossible, warnings: Array.isArray(value.warnings) ? value.warnings.map(item => String(item)).slice(0, 20) : [], title: String(value.title || '') };
}

export function batchPlanner({ signal, onPlan = () => {}, getLimits = () => budgetLimits() }) {
  const node = el('section', { class: 'batch-plan', 'aria-live': 'polite' });
  let acceptedKey = '', version = 0, accepted = null, pending = false;
  const render = () => {
    node.replaceChildren(el('div', { class: 'eyebrow' }, 'BATCH / BOUNDED TASK SERIES'), el('h3', {}, '任务串预览'));
    if (!accepted) { node.append(el('p', { class: 'field-hint' }, '填写总体材料目标，然后生成计划。程序按关键词组和不重叠日期段拆分任务；任一组分别执行，全部组保持为一个检索组合。')); return; }
    node.append(el('p', {}, `${number(accepted.tasks.length)} 个排队任务 · 总体材料目标 ${number(accepted.total_records)} 条 · 本次计划最多 ${number(accepted.max_possible_records)} 条`), el('p', { class: 'field-hint' }, '材料目标是保存上限，实际数量取决于公开页面、相关性与可确认日期；不足时会说明，不会扩大日期或混入无关材料。'), accepted.warnings.length > 0 && el('ul', { class: 'warning-note' }, accepted.warnings.map(item => el('li', {}, item))));
    const research = accepted.tasks.filter(task => task.kind === 'research');
    if (accepted.estimate) node.append(el("p", { class: "batch-estimate" }, `预计依次运行 ${durationText(accepted.estimate.lower_seconds)} — ${durationText(accepted.estimate.upper_seconds)}；实际可超出该范围。${accepted.estimate.basis}`));
    if (research.length && research.every(task => Number(task.params.time_budget_seconds) > 0)) node.append(el('p', { class: 'field-hint batch-duration' }, `研究任务依次执行；按每个任务的时间上限计算，整串最多 ${durationText(research.reduce((sum, task) => sum + Number(task.params.time_budget_seconds || getLimits().research.time_budget_seconds.default), 0))}。来源耗尽或没有新证据时会提前结束。`));
    const rows = tasks => el('ol', { class: 'batch-task-list' }, tasks.map(task => el('li', {}, el('strong', {}, task.label), el('p', { class: 'field-hint' }, `${task.kind === 'research' ? '深度研究' : '简单采集'} · ${task.params.start_date || '未知日期'} — ${task.params.end_date || '未知日期'} · 最多 ${number(task.params.max_records)} 条`))));
    node.append(rows(accepted.tasks.slice(0, 10)));
    if (accepted.tasks.length > 10) node.append(el('details', { class: 'technical-details' }, el('summary', {}, `查看其余 ${number(accepted.tasks.length - 10)} 个任务`), rows(accepted.tasks.slice(10))));
  };
  render();
  return {
    node,
    get pending() { return pending; },
    get previewToken() { return accepted?.preview_token || ""; },
    matches: intent => accepted !== null && acceptedKey === batchIntentKey(intent),
    invalidate() { version++; accepted = null; acceptedKey = ''; pending = false; render(); onPlan(); },
    async request(intent) {
      const current = ++version;
      accepted = null; acceptedKey = ''; pending = true;
      node.replaceChildren(el('p', { role: 'status' }, '正在拆分关键词与日期段，规划本次任务串…')); onPlan();
      try {
        const data = normalizeBatchPlan(await api('research/batch-plan', { method: 'POST', body: intent, signal }), getLimits());
        if (signal?.aborted || current !== version) return null;
        accepted = data; acceptedKey = batchIntentKey(intent); pending = false; render(); onPlan(); return data;
      } catch (error) { if (!signal?.aborted && current === version) { accepted = null; acceptedKey = ''; pending = false; node.replaceChildren(errorBox(error)); onPlan(); } return null; }
    },
  };
}
