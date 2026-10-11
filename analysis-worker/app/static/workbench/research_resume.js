import { api, pathId } from './api.js';
import { el, button, dialog, field, number, errorBox, setContent, notify } from './ui.js';
import { durationText } from './budget_limits.js';
import { continuationFields, continuationLimits, continuationRequest, continuationState } from './research_resume_model.js';

export function frontierRecoveryStatus(run, graph = {}) {
  const state = continuationState(run), summary = run.summary || {};
  const saved = [graph.total_nodes ?? summary.account_count ?? summary.accounts,
    graph.total_edges ?? summary.edge_count ?? summary.relations,
    graph.total_items ?? summary.record_count ?? summary.records];
  const known = value => value === null || value === undefined ? '未记录' : number(value);
  return el('div', { class: 'frontier-recovery-status research-coverage', role: 'status' },
    el('p', {}, `已保存账号 ${known(saved[0])} · 关系 ${known(saved[1])} · 材料 ${known(saved[2])}。`),
    el('p', {}, `待探索账号 ${number(state.queued)} · 待准入账号 ${known(state.deferred)} · 待保存材料 ${known(state.records)} · 待保存关系 ${known(state.relations)}。`),
    state.pendingQueries !== null && (run.params?.mode !== 'account' || state.pendingQueries > 0)
      && el('p', {}, `待继续检索入口 ${number(state.pendingQueries)} 个。`, state.pendingQueries > 0 ? '原查询尚未检索完，继续研究将沿用原查询与日期范围，补查尚未完成的部分。' : ''),
    state.legacyGap && el('p', { class: 'warning-note' }, '旧记录未保存部分候选的原始链接或待执行分支，缺口未知，无法恢复这部分候选。继续研究仅能处理已保存的待执行线索。'),
    !state.active && !state.available && el('p', { class: 'field-hint' }, state.reason));
}

const openDialogs = new Map();
export function openResearchResume(ctx, { run, graph, runId } = {}) {
  const id = run?.id || runId;
  if (!id || ctx.signal.aborted) return;
  const existing = openDialogs.get(id);
  if (existing?.modal.isConnected) { existing.modal.focus(); return existing; }
  const modal = dialog('继续研究', { subtitle: 'CONTINUE / SAVED RESEARCH', dismissBackdrop: false });
  modal.modal.classList.add('research-continuation');
  openDialogs.set(id, modal);
  const controller = new AbortController();
  const abort = () => { controller.abort(); if (modal.modal.isConnected) modal.close(); };
  ctx.signal.addEventListener('abort', abort, { once: true });
  modal.modal.addEventListener('close', () => { if (openDialogs.get(id) === modal) openDialogs.delete(id); controller.abort(); ctx.signal.removeEventListener('abort', abort); }, { once: true });
  let currentRun = run, currentGraph = graph || {}, limits = null, busy = false, loadError = false, estimate = null, estimateKey = "", estimateVersion = 0;
  const estimateView = el("section", { class: "research-estimate result-box", "aria-live": "polite" });
  const summary = el('div'), budgets = el('div', { class: 'form-grid' });
  const errors = el('div', { class: 'form-error' });
  const status = el('p', { class: 'submit-status field-hint', role: 'status', 'aria-live': 'polite' }, '正在读取当前研究与服务上限…');
  const retry = button('重新读取', load, 'secondary small'); retry.hidden = true;
  const submitControl = el('button', { class: 'button', type: 'submit', disabled: true }, '开始继续研究');
  const form = el('form', { class: 'stack', onSubmit: event => { event.preventDefault(); create(); } }, summary, budgets, estimateView, errors, status, retry,
    el('div', { class: 'form-actions' }, button('取消', modal.close, 'secondary'), submitControl));
  modal.body.append(form);

  function update() {
    if (!modal.modal.isConnected) return;
    submitControl.disabled = true;
    if (busy) { submitControl.textContent = '正在创建后续任务…'; status.textContent = '正在等待服务确认；同一研究只创建一个后续任务。'; return; }
    submitControl.textContent = estimate ? '确认预计用时并继续' : '预估续查用时';
    if (!limits || !currentRun) { status.textContent = loadError ? '无法确认当前研究与实际服务上限，请重新读取后继续。' : '正在读取当前研究与服务上限…'; return; }
    try {
      continuationRequest(currentRun, Object.fromEntries(new FormData(form)), limits);
      submitControl.disabled = false;
      status.textContent = `本次时长：${Number(form.elements.time_budget_seconds.value) === 0 ? '按范围完成' : durationText(form.elements.time_budget_seconds.value)}。账号、关系与材料上限是整个研究累计上限；已有结果保留，继续处理已保存的待执行线索。`;
      status.classList.remove('warning-note');
    } catch (error) { status.textContent = error.message; status.classList.add('warning-note'); }
  }

  async function load() {
    if (busy || controller.signal.aborted) return;
    limits = null; loadError = false; retry.hidden = true; retry.disabled = true; setContent(errors); update();
    try {
      const [latest, capabilities] = await Promise.all([
        api(`research/${pathId(id)}`, { signal: controller.signal }), api('capabilities', { signal: controller.signal })]);
      if (controller.signal.aborted || !modal.modal.isConnected) return;
      currentRun = latest.run; currentGraph = latest;
      if (!currentRun?.id) throw new Error('服务尚未返回当前研究，请重新读取。');
      limits = continuationLimits(capabilities);
      const params = currentRun.params || {};
      const start = params.mode === 'account' ? [params.handle && `@${params.handle}`, ...(params.seed_accounts || [])].filter(Boolean).join(' · ') : params.keyword;
      const sources = (params.seed_sources || [params.source]).map(source => ({ google_news: '公开新闻', twitter: 'X / Twitter', instagram: 'Instagram' })[source] || source).filter(Boolean).join('、');
      setContent(summary, el('p', { class: 'intro' }, params.research_goal || params.keyword || '沿已保存的公开线索继续研究'),
        el('p', { class: 'field-hint' }, `起点：${start || '原研究起点'}。`),
        el('p', { class: 'field-hint' }, `来源：${sources || '原研究来源'}。`),
        el('p', { class: 'field-hint' }, `${params.start_date || '—'} — ${params.end_date || '—'} / 日本时间 UTC+9 · 探索 ${params.depth || '未记录'} 层 · 每页滚动上限 ${params.max_scrolls || '未记录'} 次。`),
        frontierRecoveryStatus(currentRun, currentGraph));
      setContent(budgets, Object.entries(continuationFields).map(([name, label]) => field(label, {
        name, type: 'number', min: limits[name].min, max: limits[name].max, step: 1,
        value: String(params[name] ?? limits[name].default ?? limits[name].min), required: true,
        hint: name === 'time_budget_seconds' ? `${durationText(limits[name].min)}–${durationText(limits[name].max)}；0 = 按范围完成` : `累计上限，服务允许 ${limits[name].min}–${number(limits[name].max)}；0 = 不额外截断` })));
    } catch (error) {
      if (controller.signal.aborted || !modal.modal.isConnected) return;
      loadError = true; retry.hidden = false; setContent(errors, errorBox(error));
    } finally { retry.disabled = false; update(); }
  }

  async function create() {
    if (busy || !limits || !currentRun || controller.signal.aborted || !form.reportValidity()) return;
    let request;
    try { request = continuationRequest(currentRun, Object.fromEntries(new FormData(form)), limits); }
    catch (error) { setContent(errors, errorBox(error)); update(); return; }
    if (!estimate || estimateKey !== JSON.stringify(request)) {
      const version = ++estimateVersion;
      busy = true; setContent(errors); update();
      try {
        const result = await api(`research/${pathId(currentRun.id)}/continue-estimate`, { method: 'POST', body: request, signal: controller.signal });
        if (version !== estimateVersion || controller.signal.aborted) return;
        estimate = result; estimateKey = JSON.stringify(request);
        setContent(estimateView, el('h3', {}, '续查预计用时'), el('p', {}, `大致 ${durationText(result.lower_seconds)} — ${durationText(result.upper_seconds)}；实际可超出该范围。`), el('p', { class: 'field-hint' }, result.basis), el('p', {}, '检查范围与预计用时后，再点击确认继续；此预估没有执行采集。'));
        estimateView.scrollIntoView({ block: "nearest" });
      } catch (error) { if (!controller.signal.aborted) setContent(errors, errorBox(error)); }
      finally { busy = false; update(); }
      return;
    }
    request.preview_token = estimate.preview_token;
    busy = true; setContent(errors); update();
    try {
      // The server keeps scope and reserves the saved frontier atomically.
      const job = await api(`research/${pathId(currentRun.id)}/continue`, { method: 'POST', body: request });
      if (!job.id) throw new Error('服务尚未确认后续任务，请在任务中心检查后重试。');
      if (!controller.signal.aborted && modal.modal.isConnected) {
        modal.close(); notify('后续研究已进入队列，沿原研究的已保存线索继续。'); ctx.navigate('jobs', { selected: job.id });
      } else notify('后续研究已进入队列，可在任务中心查看。');
    } catch (error) { if (!controller.signal.aborted && modal.modal.isConnected) setContent(errors, errorBox(error)); }
    finally { busy = false; update(); }
  }
  form.addEventListener('input', () => { estimate = null; estimateKey = ''; estimateVersion++; setContent(estimateView); update(); }); form.addEventListener('change', () => { estimate = null; estimateKey = ''; estimateVersion++; setContent(estimateView); update(); });
  load();
  return modal;
}
