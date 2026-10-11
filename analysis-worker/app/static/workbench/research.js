import { api, apiPage, pageNumber, pathId } from './api.js';
import { reviewProgress, reviewResults } from './review.js';
import { el, button, linkButton, downloadButton, heading, badge, dateText, number, empty, loading, errorBox, dialog, markdown, sourceLink, pagination, notify, field, check, submit, setContent, appendContent } from './ui.js';
import { openReportCreate, openReportRetry } from './actions.js';
import { jobTitle, diagnosticsView, socialSessions, platformName, modeName, isSocial, evidenceMetadata } from './social.js';
import { batchSummary, hardBudgetPartial, researchAnalysisPending } from './batch.js';
import { durationText } from './budget_limits.js';
import { openResearchResume } from './research_resume.js';
import { reportNoteProgressText, reportCheckpointProgressText } from './report_progress.js';

export async function openReport(id, ctx) {
  const modal = dialog('研究报告', { drawer: true, subtitle: 'RESEARCH / EVIDENCE SNAPSHOT' });
  modal.body.append(loading('正在读取报告与证据快照…'));
  try {
    const report = await api(`reports/${pathId(id)}`, { signal: ctx.signal });
    if (!modal.modal.isConnected) return;
    const snapshot = report.snapshot || {};
    const sources = Array.isArray(snapshot) ? snapshot : snapshot.sources || snapshot.items || snapshot.source_list || [];
    const download = downloadButton('下载 Markdown', `reports/${pathId(id)}/download`, ctx);
    modal.footer.append(download, button('关闭', modal.close, 'secondary small'));
    setContent(modal.body, el('div', { class: 'detail-topline' }, badge(report.mode), el('span', { class: 'muted' }, `${number(report.source_count)} 条来源材料`)), el('h3', { class: 'detail-title' }, report.title), el('p', { class: 'muted' }, `${report.start_date || '未指定'} — ${report.end_date || '未指定'} · 日本时间 UTC+9`), el('p', { class: 'field-hint' }, '本报告保留生成时的材料快照。证据整理与 AI 分析模式分别标明；来源链接供进一步核对。'), markdown(report.markdown), sources.length > 0 && el('section', { class: 'snapshot-sources' }, el('h3', {}, '保存的来源快照'), el('ol', {}, sources.map(source => el('li', {}, el('strong', {}, source.title || source.source_name || source.id || '原始材料'), source.url && sourceLink(source.url), source.published_at && el('span', { class: 'muted small-text' }, dateText(source.published_at)), evidenceMetadata(source))))));
    if (snapshot.harness_state) {
      const harness = snapshot.harness_state;
      const names = { list_evidence: '浏览材料目录', read_evidence: '读取原文', inspect_account: '核对保存账号资料', trace_observed_path: '追查有向关系路径', prioritize_frontier: '比较调查优先级', compare_evidence: '对照材料说法', check_citation: '逐字核对引用' };
      modal.body.append(el('details', { class: 'harness-evidence-trace' }, el('summary', {}, `DSH 工具取证记录 · ${number(harness.model_calls)} 轮决策 / ${number(harness.tool_calls)} 次调用`), el('p', { class: 'field-hint' }, '这里只显示保存材料中的工具取证。模型调查重点和推断不授予账号核验，也不新增观察关系。'), el('ol', {}, (Array.isArray(harness.trace) ? harness.trace : []).map(row => el('li', {}, el('strong', {}, names[row.name] || '已拒绝的工具请求'), ` · ${row.ok ? '已保存结果' : '请求未通过范围检查'}`, row.args?.citation_id && el('span', { class: 'mono' }, ` · [${row.args.citation_id}]`), row.name === 'read_evidence' && typeof row.result?.text === 'string' && el('p', {}, row.result.text.slice(0, 200)), row.name === 'check_citation' && el('p', {}, row.result?.valid === true ? '摘录与保存原文逐字匹配；事实真实性仍需核验。' : '摘录未通过逐字核对。'))))));
    }
  } catch (err) { if (!ctx.signal.aborted) setContent(modal.body, errorBox(err)); }
}

export async function renderReports(ctx) {
  const data = await apiPage('reports', { page: ctx.params.get('page'), page_size: 20 }, { signal: ctx.signal });
  const page = data.page;
  if (ctx.params.has('page') && String(page) !== ctx.params.get('page')) ctx.replaceParams?.({ ...Object.fromEntries(ctx.params), page });
  const content = el('div', {}, heading('07', '研究报告', 'RESEARCH & SYNTHESIS', '从材料到判断，保留完整的引用与证据快照。', [button('生成报告', () => openReportCreate(ctx), '', 'plus')]));
  content.append(el('div', { class: 'report-explainer' }, el('span', { class: 'eyebrow' }, 'TWO DISTINCT MODES'), el('p', {}, el('strong', {}, '证据整理'), '汇集原始材料与可核对的线索。', el('strong', {}, ' AI 分析'), '使用已配置模型，在同一证据范围内生成研究文本。')));
  const reports = data.items || data.reports || [];
  content.append(reports.length ? el('div', { class: 'report-grid' }, reports.map((report, index) => el('article', { class: 'report-card' }, el('div', { class: 'report-card-top' }, el('span', { class: 'report-number mono' }, String((page - 1) * 20 + index + 1).padStart(2, '0')), badge(report.mode)), el('div', { class: 'eyebrow' }, `${report.start_date || '—'} / ${report.end_date || '—'}`), el('h2', {}, report.title || '研究报告'), el('p', { class: 'muted' }, `${number(report.source_count)} 条来源 · ${dateText(report.created_at)}`), el('div', { class: 'actions' }, button('阅读报告', () => openReport(report.id, ctx), '', 'arrow'), downloadButton('下载', `reports/${pathId(report.id)}/download`, ctx, 'text small'))))) : empty('让材料形成一份可复查的报告', '先将新闻或社媒资料加入资料库，再选择日期与报告模式。没有实际材料时，系统不会生成空洞报告。', [button('生成报告', () => openReportCreate(ctx), 'secondary', 'plus'), linkButton('查看资料库', '#library', 'text', 'arrow')]));
  content.append(pagination({ ...data, page, page_size: 20 }, next => ctx.navigate('reports', { page: next })));
  const selected = ctx.params.get('selected'); if (selected) queueMicrotask(() => { if (!ctx.signal.aborted) openReport(selected, ctx); });
  return content;
}

const active = status => ['queued', 'running'].includes(status);
function progressBar(value, label) { const progress = el('progress', { max: '100', value: String(Math.max(0, Math.min(100, Number(value) || 0))), 'aria-label': label }); return el('div', { class: 'progress-line' }, progress, el('span', { class: 'mono' }, `${Math.round(Number(value) || 0)}%`)); }

export async function renderJobs(ctx) {
  let page = pageNumber(ctx.params.get('page'));
  let batchPage = pageNumber(ctx.params.get('batch_page'));
  const batchId = ctx.params.get('batch');
  const initial = await Promise.allSettled([apiPage('jobs', { page, page_size: 20 }, { signal: ctx.signal }), apiPage('job-batches', { page: batchPage, page_size: 10 }, { signal: ctx.signal })]);
  if (initial[0].status === 'rejected') throw initial[0].reason;
  let data = initial[0].value;
  let batches = initial[1].status === 'fulfilled' ? initial[1].value : null;
  let batchesError = initial[1].status === 'rejected' ? initial[1].reason : null;
  page = data.page; batchPage = batches?.page || batchPage;
  function syncPages() {
    const params = Object.fromEntries(ctx.params);
    if (ctx.params.has('page') || page > 1) params.page = page;
    if (ctx.params.has('batch_page') || batchPage > 1) params.batch_page = batchPage;
    ctx.replaceParams?.(params);
  }
  syncPages();
  let batchRequestVersion = 0;
  let batchBusy = false;
  const content = el('div', {}, heading('04', '任务中心', 'EXECUTION / QUEUE / DIAGNOSTICS', '查看已创建工作的队列、进度和诊断，在这里取消或重试。创建工作统一到研究工作台。', [linkButton('去研究工作台', '#network', '', 'arrow')]));
  const list = el('div', { class: 'job-list' }); const paging = el('div'); const pollError = el('div');
  const batchList = el('section', { class: 'batch-registry' });
  const batchDetail = el('section', { class: 'batch-detail' });
  const batchErrors = el('div');
  batchDetail.hidden = !batchId;
  let detail = null;
  function drawBatches() {
    batchList.replaceChildren(el('div', { class: 'panel-heading' }, el('h2', {}, '批量任务串'), el('span', { class: 'eyebrow' }, 'TASK SERIES / PERSISTENT QUEUE')));
    if (batchesError) { batchList.append(errorBox(batchesError, refreshBatches)); return; }
    const items = batches?.items || [];
    batchList.append(items.length ? el('div', { class: 'batch-grid' }, items.map(batch => {
      const summary = batchSummary(batch);
      return el('article', { class: `batch-card ${batch.id === batchId ? 'selected' : ''}` }, el('div', { class: 'detail-topline' }, badge(batch.status), el('span', { class: 'mono' }, `${number(summary.tasks)} TASKS`)), el('h3', {}, batch.title || '公开材料任务串'), el('p', { class: 'field-hint' }, `成功 ${number(summary.succeeded)} · 已结束 ${number(summary.ended)} · 执行或排队 ${number(summary.active)}`), progressBar(batch.progress, '任务串进度'), el('p', { class: 'batch-count' }, `已新增 ${number(summary.saved)} 条 / 总体目标 ${number(summary.target)} 条`), button('查看任务串', () => ctx.navigate('jobs', { page, batch_page: batchPage, batch: batch.id }), 'secondary small', 'arrow'));
    })) : empty('尚无任务串执行记录', '在研究工作台创建工作并选择“连续任务串”。提交后的子任务会在这里排队，分别记录结果。', [linkButton('到工作台规划任务串', '#network?compose=batch', 'secondary small', 'arrow')], true));
    batchList.append(pagination({ ...(batches || {}), page: batchPage, page_size: 10 }, next => ctx.navigate('jobs', { page, batch_page: next, ...(batchId ? { batch: batchId } : {}) })));
  }
  async function refreshBatches() {
    try { const updated = await apiPage('job-batches', { page: batchPage, page_size: 10 }, { signal: ctx.signal }); if (ctx.signal.aborted) return; batches = updated; batchPage = updated.page; batchesError = null; }
    catch (error) { if (!ctx.signal.aborted) batchesError = error; }
    if (!ctx.signal.aborted) { syncPages(); drawBatches(); }
  }
  async function batchAction(operation) {
    if (!batchId || batchBusy || ctx.signal.aborted) return;
    batchBusy = true;
    batchErrors.replaceChildren(); batchDetail.querySelectorAll('button').forEach(item => item.disabled = true);
    try {
      const result = await api(`job-batches/${pathId(batchId)}/${operation}`, { method: 'POST', signal: ctx.signal });
      if (ctx.signal.aborted) return;
      if (operation === 'retry') { if (!result.batch?.id) throw new Error('服务尚未确认重试任务串，请刷新后检查。'); notify('已为未完成任务创建新的重试任务串，成功任务保留。'); ctx.navigate('jobs', { page, batch: result.batch.id }); }
      else { notify('已提交任务串取消请求；运行中的任务会在保存当前批次后停止。'); await refreshSelectedBatch(); await refreshBatches(); await refreshList(); }
    } catch (error) { if (!ctx.signal.aborted) batchErrors.replaceChildren(errorBox(error)); }
    finally { batchBusy = false; if (!ctx.signal.aborted) batchDetail.querySelectorAll('button').forEach(item => item.disabled = false); }
  }
  async function refreshSelectedBatch() {
    if (!batchId) return;
    const version = ++batchRequestVersion;
    try {
      const batch = await api(`job-batches/${pathId(batchId)}`, { signal: ctx.signal });
      if (ctx.signal.aborted || version !== batchRequestVersion) return;
      const summary = batchSummary(batch);
      const retryableJobs = (batch.jobs || []).filter(job => ['failed', 'partial', 'cancelled', 'interrupted'].includes(job.status) && !hardBudgetPartial(job));
      const analysisOnlyRetry = retryableJobs.length > 0 && retryableJobs.every(researchAnalysisPending);
      const retryLabel = analysisOnlyRetry ? retryableJobs.every(job => job.result?.summary?.analysis_status === 'failed') ? '仅重试 AI 分析任务' : '继续 AI 分析任务' : '重试未完成任务';
      const budgetStopped = summary.active === 0 && summary.retryable === 0 && (batch.jobs || []).some(hardBudgetPartial) && (batch.jobs || []).every(job => job.status === 'succeeded' || hardBudgetPartial(job));
      const controls = el('div', { class: 'actions' }, summary.active > 0 && button('取消未完成任务', () => batchAction('cancel'), 'danger small'), summary.retryable > 0 && button(retryLabel, () => batchAction('retry'), 'secondary small', 'refresh'), budgetStopped && el('p', { class: 'warning-note' }, '预算停止的研究保留各自的发现路径。请打开对应研究结果，逐个调整上限后继续研究。'));
      if (batchBusy) controls.querySelectorAll('button').forEach(item => item.disabled = true);
      if (analysisOnlyRetry && summary.retryable > 0) controls.prepend(el('p', { class: 'warning-note analysis-resume-note' }, '已保存各任务的采集快照；仅补待审 AI 分析，不重新打开采集。'));
      if (budgetStopped) controls.prepend(el('p', { class: 'warning-note' }, '未完成任务已达到材料、账号或关系预算。继续使用相同上限不会增加覆盖，可按关键词和日期规划后续任务串。'));
      batchDetail.replaceChildren(el('div', { class: 'eyebrow' }, 'SERIES / AGGREGATED RESULTS'), el('div', { class: 'detail-topline' }, el('h2', {}, batch.title || '当前任务串'), badge(batch.status)), progressBar(batch.progress, '当前任务串进度'), el('div', { class: 'batch-metrics' }, el('p', {}, `成功 ${number(summary.succeeded)} / ${number(summary.tasks)} 个任务`), el('p', {}, `已结束 ${number(summary.ended)} · 执行或排队 ${number(summary.active)} · 可重试 ${number(summary.retryable)}`), el('p', {}, `实际新增 ${number(summary.saved)} 条 · 总体材料目标 ${number(summary.target)} 条`)), el('p', { class: summary.complete ? 'field-hint' : 'warning-note' }, summary.complete ? (batch.target_reached ? '所有任务均已完成，实际新增材料已达到本串目标。' : '所有任务均已完成；实际材料少于总体目标，请查看各任务的来源覆盖与筛选诊断。') : '已结束包括失败、取消、中断或部分完成。请以每条任务的状态与诊断判断结果；进度百分比不代表全部成功。'), controls, batchErrors, el('ol', { class: 'batch-job-list' }, (batch.jobs || []).map((job, index) => el('li', {}, el('div', { class: 'batch-job-heading' }, el('span', { class: 'mono' }, String(index + 1).padStart(2, '0')), el('strong', {}, jobTitle(job)), badge(job.status)), progressBar(job.progress, '子任务进度'), el('p', { class: 'field-hint' }, `${job.params?.start_date || '—'} — ${job.params?.end_date || '—'} · 保存上限 ${number(job.target_records || job.params?.max_records)} · 实际新增 ${number(job.result?.inserted)} 条`), el('div', { class: 'actions' }, button('查看任务详情', () => showDetail(job.id), 'secondary small', 'arrow'), job.kind === 'research' && job.result?.run_id && linkButton('查看发现树与关系', `#network?run=${pathId(job.result.run_id)}`, 'text small', 'network'))))));
    } catch (error) { if (!ctx.signal.aborted && version === batchRequestVersion) batchDetail.replaceChildren(errorBox(error, refreshSelectedBatch)); }
  }
  function draw() {
    const jobs = data.items || data.jobs || [];
    setContent(list, ...(jobs.length ? jobs.map(job => el('button', { class: 'job-row', type: 'button', onClick: () => showDetail(job.id) }, el('div', { class: 'job-kind mono' }, job.kind === 'login' ? 'LOG' : job.kind === 'report' ? 'RPT' : job.kind === 'research' ? 'RES' : 'COL'), el('div', { class: 'job-row-main' }, el('div', { class: 'item-meta' }, el('span', {}, job.kind === 'login' ? (job.params?.method === 'auto' ? '已有会话检测' : job.params?.method === 'firefox' ? '会话导入' : '浏览器登录') : job.kind === 'report' ? '报告生成' : job.kind === 'research' ? '深度研究' : '信息采集'), el('span', { class: 'mono' }, dateText(job.created_at, true))), el('h2', {}, jobTitle(job)), active(job.status) && progressBar(job.progress, '任务进度'), job.params?._evidence_review && el('p', { class: 'field-hint' }, reviewProgress(job.result)), job.kind === 'research' && job.result?.summary && el('p', { class: 'field-hint' }, `已保存 ${number(job.result.summary.record_count || 0)} 条材料 · 已处理 ${number(job.result.summary.visited_count || 0)} 个账号 · 待探索 ${number(job.result.summary.frontier_count || 0)} · ${job.result.activity?.message || job.result.summary.phase || '等待后续分支'}`), job.kind === 'report' && job.result?.report_checkpoint && el('p', { class: 'field-hint' }, reportCheckpointProgressText(job.result.report_checkpoint, job.status))), badge(job.status), el('span', { class: 'row-arrow', 'aria-hidden': 'true' }, '↗'))) : [empty('所有工作，都有记录', '创建采集、登录或报告任务后，在此跟踪状态、查看来源诊断、取消执行或重试。', [linkButton('去研究工作台', '#network', 'secondary', 'arrow')])]));
    setContent(paging, pagination({ ...data, page, page_size: 20 }, next => ctx.navigate('jobs', { page: next, batch_page: batchPage, ...(batchId ? { batch: batchId } : {}) })));
  }
  async function showDetail(id) {
    if (ctx.signal.aborted) return;
    if (detail?.modal.modal.isConnected) detail.modal.close();
    const modal = dialog('任务详情', { drawer: true, subtitle: 'TASK / DURABLE EXECUTION' });
    const summary = el('div'); const events = el('div'); const errors = el('div'); const controls = el('div', { class: 'actions' });
    const instance = { id, modal, status: null, summary, events, errors, controls, requestVersion: 0 };
    detail = instance;
    modal.body.append(loading('读取任务状态…'));
    modal.modal.addEventListener('close', () => { if (detail === instance) detail = null; }, { once: true });
    await updateDetail(instance);
  }
  async function action(instance, operation) {
    if (instance.busy || ctx.signal.aborted || detail !== instance || !instance.modal.modal.isConnected) return;
    instance.busy = true;
    setContent(instance.errors);
    instance.controls.querySelectorAll('button').forEach(item => item.disabled = true);
    try {
      const result = await api(`jobs/${pathId(instance.id)}/${operation}`, { method: 'POST', signal: ctx.signal });
      if (ctx.signal.aborted) return;
      if (operation === 'retry') { if (!result.id) throw new Error('服务尚未确认重试任务，请刷新任务中心后检查。'); notify('已创建新的重试任务。'); if (detail === instance && instance.modal.modal.isConnected) await showDetail(result.id); }
      else { notify('取消请求已提交。运行中的任务将在可中断位置停止。'); if (detail === instance) await updateDetail(instance); }
      await refreshList();
    } catch (error) { if (!ctx.signal.aborted && instance.modal.modal.isConnected) setContent(instance.errors, errorBox(error)); }
    finally { instance.busy = false; instance.controls.querySelectorAll('button').forEach(item => item.disabled = false); }
  }
  async function updateDetail(instance) {
    const version = ++instance.requestVersion;
    try {
      const job = await api(`jobs/${pathId(instance.id)}`, { signal: ctx.signal });
      if (ctx.signal.aborted || !instance.modal.modal.isConnected || detail !== instance || version !== instance.requestVersion) return;
      const result = job.result || {};
      const budgetStopped = hardBudgetPartial(job);
      const results = ['fetched', 'inserted', 'updated', 'duplicates', 'rejected', 'source_count', 'accounts_inserted', 'accounts_existing', 'relations_inserted', 'relations_updated'].filter(key => result[key] !== undefined).map(key => `${({ fetched: '获取', inserted: '新增', updated: '补全', duplicates: '重复', rejected: '拒绝', source_count: '来源', accounts_inserted: '新账号', accounts_existing: '已有账号', relations_inserted: '新关系', relations_updated: '关系补全' })[key]} ${number(result[key])}`);
      const reading = result.report_checkpoint?.coverage_progress, harness = result.report_checkpoint?.harness_state;
      setContent(instance.summary, el('div', { class: 'detail-topline' }, badge(job.status), el('span', { class: 'mono small-text muted' }, job.id)), el('h3', { class: 'detail-title' }, jobTitle(job)), progressBar(job.progress, '任务进度'), job.kind === 'login' && active(job.status) && el('p', { class: 'login-task-note' }, job.params?.method === 'auto' ? '正在检测并复用已有会话，不发起新的登录。验证成功后才用于采集；缺失时会提示在系统默认浏览器完成登录。' : job.params?.method === 'firefox' ? '正在读取所选 Firefox 配置中的 X 会话并验证。验证成功后才保存到工作台；取消或失败会保留原会话。不需要重新输入密码。' : `请在打开的浏览器窗口中手动登录 ${platformName(job.params?.platform)}。最多等待 10 分钟。点击下方“取消任务”可结束等待并关闭登录浏览器。`), isSocial(job.params?.source) && el('p', { class: 'field-hint' }, `${modeName(job.params.mode)} · ${job.params.relevance === 'broad' ? '按检索条件' : '研究相关'} · ${job.params.start_date || '—'} — ${job.params.end_date || '—'} / 日本时间 UTC+9`), job.cancel_requested && active(job.status) && el('p', { class: 'warning-note' }, '已请求取消，等待当前操作结束。'), el('dl', { class: 'metadata' }, el('dt', {}, '创建时间'), el('dd', {}, dateText(job.created_at, true)), el('dt', {}, '开始时间'), el('dd', {}, job.started_at ? dateText(job.started_at, true) : '尚未开始'), el('dt', {}, '结束时间'), el('dd', {}, job.finished_at ? dateText(job.finished_at, true) : '尚未结束')), job.error && errorBox(new Error(typeof job.error === 'string' ? job.error : JSON.stringify(job.error))), results.length > 0 && el('p', { class: 'result-box' }, results.join(' · ')), (job.params?._evidence_review || job.params?._account_classification) && reviewResults(result), (job.params?._evidence_review || job.params?._account_classification) && downloadButton('导出完整审核记录', `reviews/${pathId(job.id)}/export`, { signal: ctx.signal }), diagnosticsView(result.diagnostics), job.kind === 'research' && result.run_id && linkButton('查看发现树、关系与证据', `#network?run=${pathId(result.run_id)}`, 'secondary', 'network'), job.kind === 'login' && job.status === 'succeeded' && button('查看会话与采集能力', () => { instance.modal.close(); ctx.navigate('settings'); }, 'secondary', 'arrow'), result.report_id && button('阅读生成报告', () => { instance.modal.close(); ctx.navigate('reports', { selected: result.report_id }); }, '', 'arrow'), el('details', { class: 'technical-details' }, el('summary', {}, '查看任务参数与结果'), el('pre', {}, JSON.stringify(job.params?._evidence_review ? { params: { ...job.params, review_snapshot: `${job.params.review_snapshot?.length || 0} 项（原始快照见完整导出）` }, result: { ...job.result, batches: `${job.result?.batches?.length || 0} 批工具记录（见完整导出）`, current_harness: job.result?.current_harness ? { phase: job.result.current_harness.phase, model_calls: job.result.current_harness.model_calls, tool_calls: job.result.current_harness.tool_calls } : undefined } } : { params: job.params, result: job.result }, null, 2))));
      if (job.kind === 'research') {
        const details = result.summary || {}, activity = result.activity || {};
        instance.summary.append(el('section', { class: 'research-live-progress result-box', role: 'status' }, el('strong', {}, ({ seed: '查询入口', account: '逐层读取账号', analysis: '分析保存证据', finished: '研究已结束' })[details.phase] || (active(job.status) ? '正在探索公开范围' : '研究状态')),
          el('p', {}, activity.message || '每个完成的分支都会保存；候选数量可随探索变化。'),
          el('p', {}, `已用 ${durationText(details.elapsed_seconds || 0)} · 查询已完成 ${number(details.completed_query_count || 0)} / ${number(details.planned_query_count || 0)} · 账号已处理 ${number(details.visited_count || 0)} · 待探索 ${number(details.frontier_count || 0)}`),
          el('p', {}, `已保存材料 ${number(details.record_count || 0)} · 账号 ${number(details.account_count || 0)} · 关系 ${number(details.edge_count || 0)} · 待审分析证据 ${number(details.analysis_pending_evidence || 0)}`),
          details.stopped_reason && el('p', { class: 'warning-note' }, details.stopped_reason),
          active(job.status) && el('p', { class: 'field-hint' }, '探索期间会发现新的分支，当前百分比仅描述已知工作，不代表全部平台内容的覆盖率。')));
      }
      const analysisRetry = researchAnalysisPending(job), analysisStatus = job.result?.summary?.analysis_status;
      if (reading) instance.summary.append(el('p', { class: 'field-hint report-reading-progress', role: 'status' }, `已阅读 ${number(reading.reviewed_source_count)} / ${number(reading.input_source_count)} 条保存材料 · 待审 ${number(reading.pending_source_count)} 条。成功批次已保存，重试复用原文未变的批次。`));
      const synthesisText = reportNoteProgressText(result.report_checkpoint, job.status), generation = result.report_checkpoint?.report_generation;
      if (synthesisText) instance.summary.append(el('p', { class: 'field-hint report-synthesis-progress', role: 'status' }, synthesisText));
      if (generation) instance.summary.append(el('p', { class: 'field-hint report-generation-progress', role: 'status' }, `报告阶段：${({ review_pending: '来源与研究目标复核', correction_required: '修正已发现的问题', completed: '报告草稿复核完成', complete: '报告草稿复核完成', draft: '撰写分析草稿' })[generation.phase] || generation.phase || '综合草稿'} · 已尝试草稿 ${number(generation.draft_attempt_count || 0)} 次。成功阅读的原文与批次继续复用。`));
      if (harness) instance.summary.append(el('p', { class: 'field-hint harness-progress', role: 'status' }, `DSH 工具调查：${number(harness.model_calls)} 轮决策 / ${number(harness.tool_calls)} 次调用 · ${harness.complete ? '取证完成，继续报告复核' : harness.phase === 'failed' ? '尚未完成，证据已保留' : harness.phase === 'cancelled' ? '已停止，证据已保留' : '正在查证保存材料'}。`));
      const runId = job.kind === 'research' && (job.result?.run_id || job.params?.resume_run_id);
      const continuationAvailable = job.result?.summary?.continuation_available;
      const controlState = `${job.status}:${budgetStopped}:${Boolean(analysisRetry)}:${analysisStatus || ''}:${runId || ''}:${continuationAvailable}`;
      if (instance.controlState !== controlState) {
        const continueControl = runId && button('继续研究', () => { instance.modal.close(); openResearchResume(ctx, { runId }); }, 'secondary small', 'refresh');
        if (continueControl && continuationAvailable === false) { continueControl.disabled = true; continueControl.title = job.result?.summary?.continuation_reason || '没有已保存的待执行线索可继续。'; }
        setContent(instance.controls, ...(active(job.status) ? [button('取消任务', () => action(instance, 'cancel'), 'danger small')]
          : runId ? [budgetStopped && el('p', { class: 'warning-note' }, '已达到本次预算。已有研究与证据保留；继续研究会读取当前保存线索，并要求提高对应上限。'), continueControl,
            analysisRetry && el('p', { class: 'warning-note analysis-resume-note' }, '已保存采集快照；此操作只补待审 AI 分析，不重新打开采集。'),
            analysisRetry && button(analysisStatus === 'failed' ? '仅重试 AI 分析' : '继续 AI 分析', () => action(instance, 'retry'), 'secondary small', 'refresh')]
          : budgetStopped ? [el('p', { class: 'warning-note' }, '已达到本次预算。当前任务没有可读取的研究记录，无法恢复已保存线索。')]
          : ['failed', 'cancelled', 'interrupted', 'partial'].includes(job.status) ? [button(job.kind === 'report' ? '免费预览报告续跑' : '创建重试任务', () => job.kind === 'report' ? openReportRetry(ctx, job, { onCreated: () => instance.modal.close() }) : action(instance, 'retry'), 'secondary small', 'refresh')] : []));
        instance.controlState = controlState;
        instance.status = job.status;
      }
      const records = job.events || [];
      if (instance.busy) instance.controls.querySelectorAll('button').forEach(item => item.disabled = true);
      const eventContent = records.length ? el('ol', { class: 'event-log' }, records.map(event => el('li', { class: `event-${['error', 'warning'].includes(event.level) ? event.level : 'info'}` }, el('time', { class: 'mono' }, dateText(event.created_at || event.timestamp, true)), el('p', {}, event.message || '')))) : el('p', { class: 'muted' }, '尚无任务事件。');
      setContent(instance.events, el('div', { class: 'divider' }), el('h3', {}, '执行记录'), eventContent);
      setContent(instance.modal.body, instance.summary, instance.controls, instance.errors, instance.events);
    } catch (error) { if (!ctx.signal.aborted && instance.modal.modal.isConnected && version === instance.requestVersion) setContent(instance.modal.body, errorBox(error, () => updateDetail(instance))); }
  }
  async function refreshList() {
    data = await apiPage('jobs', { page, page_size: 20 }, { signal: ctx.signal });
    if (ctx.signal.aborted) return;
    page = data.page;
    syncPages();
    setContent(pollError); draw();
  }
  content.append(el('div', { class: 'live-note' }, el('span', { class: 'status-dot' }), '任务状态每 5 秒刷新 · 完整队列与任务串可分页浏览 · 离开此页面即停止轮询'), pollError, batchList, batchDetail, el('div', { class: 'panel-heading' }, el('h2', {}, '全部任务'), el('span', { class: 'eyebrow' }, 'EVERY TASK / FULL HISTORY')), list, paging); draw(); drawBatches();
  if (batchId) await refreshSelectedBatch();
  ctx.poll(async () => {
    const results = await Promise.allSettled([refreshList(), refreshBatches(), ...(batchId ? [refreshSelectedBatch()] : []), ...(detail ? [updateDetail(detail)] : [])]);
    const failed = results.find(result => result.status === 'rejected');
    if (failed && !ctx.signal.aborted) setContent(pollError, errorBox(failed.reason));
  }, 5000);
  const selected = ctx.params.get('selected'); if (selected) queueMicrotask(() => { if (!ctx.signal.aborted) showDetail(selected); });
  return content;
}

export async function renderSettings(ctx) {
  const data = await api('capabilities', { signal: ctx.signal });
  const content = el('div', {}, heading('08', '能力与设置', 'SYSTEM CAPABILITIES', '配置状态与实际采集结果分别展示，连接可用不等于来源始终可获取。'));
  content.append(el('section', { class: 'capability-section' }, el('div', { class: 'section-side-label' }, el('span', { class: 'mono' }, 'A'), el('h2', {}, '公开来源')), el('div', { class: 'capability-list' }, (data.collectors || []).map(collector => el('div', { class: 'capability-row' }, el('div', {}, el('h3', {}, collector.name), el('p', {}, collector.detail || '实际可用性以任务执行结果为准。')), badge(collector.state))))));
  content.append(el('section', { class: 'capability-section' }, el('div', { class: 'section-side-label' }, el('span', { class: 'mono' }, 'B'), el('h2', {}, 'AI 分析')), el('div', { class: 'capability-list' }, el('div', { class: 'capability-row' }, el('div', {}, el('h3', {}, data.llm?.configured ? data.llm.model || '已配置模型' : '尚未配置 AI 模型'), el('p', {}, data.llm?.configured ? '仅表示服务配置存在，执行结果与错误会在任务中心呈现。' : '证据整理模式可以直接使用。AI 模型与凭据在本地运行环境中配置。')), badge(data.llm?.configured ? 'ready' : 'unavailable')))));
  content.insertBefore(await socialSessions(ctx, source => ctx.navigate('network', { compose: 'collect', source })), content.lastElementChild);
  const connection = el('section', { class: 'connection-settings' });
  content.append(connection);
  const backup = data.backup || { available: false, detail: '当前服务尚未提供研究数据备份，请更新本地应用。' };
  const backupButton = downloadButton('备份研究数据', 'backup', ctx);
  backupButton.disabled = backup.available !== true;
  const backupRows = el('div', { class: 'capability-list' });
  const restoreAvailable = typeof window.desktop?.restoreBackup === 'function';
  if (restoreAvailable) {
    const restoreStatus = el('div', { class: 'backup-restore-status', role: 'status', 'aria-live': 'polite', 'aria-atomic': 'true' });
    const restoreErrors = el('div');
    let restoreBusy = false;
    let restored = false;
    const restoreButton = button('恢复研究备份', async () => {
      if (restoreBusy || restored || ctx.signal.aborted) return;
      restoreBusy = true;
      restoreButton.disabled = true;
      restoreButton.setAttribute('aria-busy', 'true');
      setContent(restoreErrors);
      setContent(restoreStatus, el('p', {}, '请选择备份，并在原生窗口确认恢复。应用会依次检查文件、保存旧库并恢复研究数据。'));
      try {
        const result = await window.desktop.restoreBackup();
        if (ctx.signal.aborted) return;
        if (result?.cancelled === true) {
          setContent(restoreStatus, el('p', {}, '已取消恢复。'));
        } else if (result?.restored === true) {
          restored = true;
          setContent(restoreStatus, el('p', {}, '研究备份已恢复。应用将重新打开能力与设置，请在任务中心检查需要重试的任务。'), result.archive_path && el('p', { class: 'backup-archive-path' }, el('strong', {}, '恢复前的资料库已存档：'), el('span', {}, result.archive_path)));
          notify('研究备份已恢复，恢复前的资料库已自动存档。');
        } else {
          throw new Error('应用尚未确认恢复完成，请检查原生窗口提示后重试。');
        }
      } catch (error) {
        if (!ctx.signal.aborted) {
          setContent(restoreStatus);
          setContent(restoreErrors, errorBox(new Error(`恢复未完成：${error?.message || String(error || '请检查原生窗口提示后重试。')}`)));
        }
      } finally {
        restoreBusy = false;
        restoreButton.removeAttribute('aria-busy');
        restoreButton.disabled = restored;
      }
    }, 'secondary small', 'refresh');
    restoreButton.dataset.action = 'restore-backup';
    backupRows.append(el('div', { class: 'capability-row backup-restore-row' }, el('div', { class: 'backup-restore-copy' }, el('h3', {}, '使用已保存的研究备份'),
      el('p', {}, '选择备份恢复当前资料库。应用会检查文件并请你确认，恢复前的旧库会自动存档。'),
      el('p', { class: 'field-hint' }, '浏览器登录会话和 AI 连接保持不变。备份中的排队和运行任务恢复后需要在任务中心手动重试。'), restoreStatus, restoreErrors), restoreButton));
  }
  backupRows.prepend(el('div', { class: 'capability-row backup-snapshot-row' }, el('div', { class: 'backup-restore-copy' }, el('h3', {}, '保存当前研究数据快照'),
    el('p', {}, backup.detail || '可以在任务运行期间备份，文件保留备份时已提交的研究数据。'),
    el('p', { class: 'field-hint' }, '包含资料、账号、关系、研究结果、报告、任务记录与已同步草稿；不包含浏览器登录会话和 AI 密钥。请在下载列表中确认文件保存完成。'),
    el('details', {}, el('summary', {}, '如何恢复备份'), restoreAvailable && el('p', { class: 'field-hint' }, '桌面应用可点击下方“恢复研究备份”。以下步骤适用于手动替换数据文件。'), el('ol', { class: 'field-hint' },
      el('li', {}, '先点击“打开数据目录”，记下位置。浏览器模式请打开本地运行环境配置的数据目录。'),
      el('li', {}, '完全退出应用和本地服务，确认任务已停止运行。'),
      el('li', {}, '复制整个数据目录，作为恢复失败时的回退副本。'),
      el('li', {}, '把原 workbench.sqlite3 和已有的 workbench.sqlite3-wal、workbench.sqlite3-shm 一起移到旧库存档目录。'),
      el('li', {}, '把下载的备份复制到数据目录，改名为 workbench.sqlite3，再重新打开应用。')),
      el('p', { class: 'field-hint' }, '仅采用以上手工文件替换方式时，正在运行的任务会标记为中断，排队任务按正常队列继续执行。桌面“恢复研究备份”按钮会中断这两类任务，等待你在任务中心手动重试。'))), backupButton));
  content.append(el('section', { class: 'capability-section', 'aria-label': '研究数据备份' },
    el('div', { class: 'section-side-label' }, el('span', { class: 'mono' }, 'D'), el('h2', {}, '研究数据备份')),
    backupRows));
  if (window.desktop?.getConnection && window.desktop?.saveConnection) {
    try {
      const settings = await window.desktop.getConnection();
      if (ctx.signal.aborted) return content;
      let inputVersion = 0;
      const savedStatus = el('div', { class: 'connection-save-status', role: 'status', 'aria-live': 'polite' });
      const form = el('form', { class: 'stack', onSubmit: event => {
        event.preventDefault();
        submit(form, async values => {
          const submittedVersion = inputVersion;
          const result = await window.desktop.saveConnection({ base_url: values.base_url, model: values.model, api_key: values.api_key, clear_key: form.elements.clear_key?.checked || false });
          if (ctx.signal.aborted || !form.isConnected) return;
          loadError?.remove();
          runtimeError?.remove();
          if (inputVersion !== submittedVersion) {
            setContent(savedStatus, el('p', { class: 'field-hint' }, '刚才提交的 AI 连接已保存。之后修改的输入仍在这里，尚未保存。'),
              result?.recovery_archive && el('p', { class: 'backup-archive-path' }, el('strong', {}, '原加密配置已存档：'), el('span', {}, result.recovery_archive)));
            notify('AI 连接已保存，随后修改的输入已保留。');
            return;
          }
          form.elements.api_key.value = '';
          notify('AI 连接已保存。此操作不会调用模型或产生分析费用。');
          ctx.refresh();
        });
      } });
      form.addEventListener('input', () => { inputVersion++; setContent(savedStatus); });
      const loadError = settings.load_error && el('div', { class: 'warning-note', role: 'alert', 'data-testid': 'connection-recovery-warning' },
        el('strong', {}, '已保存的 AI 连接暂时无法读取'),
        el('p', {}, typeof settings.load_error.message === 'string' ? settings.load_error.message : '原加密配置保持原样，未使用无法读取的密钥。'),
        el('p', {}, '资料、研究和证据整理仍可使用。重新填写并保存 AI 连接后，可恢复模型分析；保存前应用会保留原加密配置。'));
      const runtimeError = settings.runtime_error && el('div', { class: 'warning-note', role: 'alert', 'data-testid': 'connection-runtime-warning' },
        el('strong', {}, 'AI 连接的运行状态需要恢复'),
        el('p', {}, typeof settings.runtime_error.message === 'string' ? settings.runtime_error.message : '连接保存后的运行状态未能确认，请退出后重新打开应用。'),
        el('p', {}, '原磁盘配置仍保留。请按提示恢复运行状态后，再继续模型分析。'));
      appendContent(form, el('div', {}, el('div', { class: 'eyebrow' }, 'PRIVATE CONNECTION / SYSTEM KEY STORAGE'), el('h2', {}, 'AI 连接设置'), el('p', { class: 'field-hint' }, '密钥保存在系统加密存储中，不会回显。保存仅更新连接配置，不会发起模型调用。')), loadError, runtimeError, el('div', { class: 'form-grid' }, field('服务地址', { name: 'base_url', type: 'url', value: settings.base_url || 'https://api.deepseek.com', required: true }), field('模型名称', { name: 'model', value: settings.model || 'deepseek-flash', required: true })), field('API Key', { name: 'api_key', type: 'password', value: '', autocomplete: 'new-password', placeholder: settings.configured ? '•••••••• 已保存 · 留空保持不变' : '输入你的 API Key', hint: settings.configured ? '留空保留已保存的密钥。输入新密钥会替换原有值。' : '可以先不配置密钥，使用证据整理模式。' }), settings.configured && check('clear_key', '清除已保存的密钥', false), settings.secure_storage === false && errorBox(new Error('系统加密存储当前不可用，暂时无法安全保存连接。')), savedStatus, el('div', { class: 'form-error' }), el('div', { class: 'actions' }, el('button', { class: 'button', type: 'submit', disabled: settings.secure_storage === false, 'data-action': 'save-connection' }, '保存 AI 连接')));
      connection.append(form);
    } catch (err) { connection.append(errorBox(err)); }
  } else {
    connection.append(el('div', { class: 'browser-settings-note' }, el('h2', {}, '桌面应用中可安全保存 AI 连接'), el('p', {}, '当前为浏览器模式，页面不会保存 API 密钥。可在桌面应用的“能力与设置”中配置，或通过本地运行环境提供模型配置。')));
  }
  if (window.desktop?.openData || window.desktop?.openLogs) {
    const filesystemActions = el('div', { class: 'local-folders' }, el('div', {}, el('h3', {}, '本地工作区'), el('p', {}, '资料、报告与运行日志保存在当前设备。')));
    const buttons = el('div', { class: 'actions' });
    const openFolder = async method => { try { const result = await method(); if (typeof result === 'string' && result) throw new Error(result); } catch (error) { notify(error.message || '无法打开目录。', 'error'); } };
    if (window.desktop.openData) buttons.append(button('打开数据目录', () => openFolder(() => window.desktop.openData()), 'secondary small'));
    if (window.desktop.openLogs) buttons.append(button('打开运行日志', () => openFolder(() => window.desktop.openLogs()), 'secondary small'));
    filesystemActions.append(buttons); connection.append(filesystemActions);
  }
  content.append(el('section', { class: 'settings-note' }, el('div', { class: 'eyebrow' }, 'WORKSPACE PRINCIPLES'), el('h2', {}, '研究可靠性，从边界清楚开始。'), el('div', { class: 'principles-grid' }, el('div', {}, el('strong', {}, '01 / 时间'), el('p', {}, '日期筛选使用日本时间 UTC+9，包含结束当天；发布时间未知的记录明确标注。')), el('div', {}, el('strong', {}, '02 / 核验'), el('p', {}, '自动发现与导入不会核验账号。人工核验需要公开证据链接，候选材料与已核验来源分开处理。')), el('div', {}, el('strong', {}, '03 / 结果'), el('p', {}, '任务失败、部分成功与空结果分别报告。历史报告保留生成时的证据快照，重试创建新的任务记录。')))));
  return content;
}
