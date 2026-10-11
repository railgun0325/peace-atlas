import { api, pathId } from './api.js';
import { el, button, dialog, field, check, submit, validateDates, recentDates, quarterDates, notify, errorBox, number, badge, beforeContent, setContent } from './ui.js';
import { collectParams, isSocial, socialModes, modeName, platformName, openSystemBrowserLogin, detectSocialSession, sessionCheckMessage } from './social.js';
import { openFirefoxImport } from './firefox.js';
import { openBrowserBridge } from './session_bridge.js';
import { readDraft, writeDraft, removeDraftIf, sameDraft, captureDraft, restoreDraft, preferredDraft, draftSynchronizer } from './drafts.js';
import { keywordQueries, keywordGroups, relatedKeywords, appendKeyword, normalizeSuggestions } from './keyword_tools.js';
import { batchPlanner } from './batch.js';
import { workflowValues, primaryResearchSource } from './workflow_model.js';
import { budgetLimits, durationText } from './budget_limits.js';

function actionForm(modal, label, handler, onSettled = () => {}) {
  const form = el('form', { class: 'stack', onSubmit: async event => { event.preventDefault(); await submit(form, handler); onSettled(); } });
  const error = el('div', { class: 'form-error' });
  const actions = el('div', { class: 'form-actions' }, button('取消', modal.close, 'secondary'), el('button', { class: 'button', type: 'submit' }, label));
  form.append(error, actions);
  modal.body.append(form);
  return { form, add: (...nodes) => beforeContent(error, ...nodes) };
}
export function dateFields(defaults = recentDates()) {
  const group = el('div', {});
  const setRange = range => { group.querySelector('[name=start_date]').value = range.start; group.querySelector('[name=end_date]').value = range.end; group.querySelector('[name=end_date]').dispatchEvent(new Event('input', { bubbles: true })); };
  group.append(el('div', { class: 'actions date-quick' }, button('近 7 天', () => setRange(recentDates(7)), 'secondary small'), button('近 30 天', () => setRange(recentDates(30)), 'secondary small'), button('本季度', () => setRange(quarterDates()), 'secondary small'), button('上季度', () => setRange(quarterDates('last')), 'secondary small')), el('div', { class: 'form-grid' }, field('开始日期', { name: 'start_date', type: 'date', value: defaults.start, required: true }), field('结束日期', { name: 'end_date', type: 'date', value: defaults.end, required: true })), el('p', { class: 'field-hint' }, '日期范围按日本时间 UTC+9 解释，包含结束日期当天。'));
  return group;
}

export function openImport(ctx) {
  const modal = dialog('导入公开资料', { subtitle: 'INGEST / JSON' });
  const { form, add } = actionForm(modal, '检查并导入', async values => {
    let parsed;
    try { parsed = JSON.parse(values.json); } catch { throw new Error('JSON 格式无法读取。请提供资料数组，或包含 items 数组的对象。'); }
    const items = Array.isArray(parsed) ? parsed : parsed?.items;
    if (!Array.isArray(items) || !items.length) throw new Error('请提供至少一条资料记录。');
    const result = await api('items/import', { method: 'POST', body: { items } });
    const existing = form.querySelector('.import-result'); existing?.remove();
    const changed = (result.inserted || 0) + (result.updated || 0);
    const repeated = Math.max(0, (result.duplicates || 0) - (result.updated || 0));
    beforeContent(form.querySelector('.form-error'), el('div', { class: 'import-result result-box', role: 'status' }, el('h3', {}, '导入处理完成'), el('p', {}, `新增 ${number(result.inserted)} 条 · 更新 / 补全 ${number(result.updated)} 条 · 仅重复 ${number(repeated)} 条 · 拒绝 ${number(result.rejected)} 条`), (result.issues || []).length > 0 && el('ul', {}, result.issues.map(issue => el('li', {}, typeof issue === 'string' ? issue : JSON.stringify(issue)))), changed > 0 && button('查看资料库', () => { modal.close(); ctx.navigate('library'); }, 'small', 'arrow')));
    if (changed) { notify(`已新增 ${number(result.inserted)} 条、更新 / 补全 ${number(result.updated)} 条资料。`); ctx.refresh(); }
  });
  const textField = field('资料 JSON', { name: 'json', type: 'textarea', rows: 10, required: true, placeholder: '[{ "title": "资料标题", "url": "https://…", "content": "原始材料正文", "type": "news", "source_name": "来源名称", "published_at": "2026-01-01T12:00:00+09:00" }]', hint: '支持字段：title、url、content、type（news/social）、source_name、published_at。可选 topics、entities、locations、account_id；日期未知可留空。' });
  let fileVersion = 0;
  textField.querySelector('textarea').addEventListener('input', () => { fileVersion++; });
  const file = el('input', { type: 'file', accept: '.json,application/json', id: 'import-file', onChange: async event => {
    const version = ++fileVersion;
    const selected = event.target.files[0]; if (!selected) return;
    try { if (selected.size > 10 * 1024 * 1024) throw new Error('文件超过 10 MB，请拆分后导入。'); const text = await selected.text(); if (version === fileVersion && modal.modal.isConnected) textField.querySelector('textarea').value = text; } catch (err) { if (version === fileVersion && modal.modal.isConnected) form.querySelector('.form-error').replaceChildren(errorBox(err)); }
  } });
  add(el('p', { class: 'intro' }, '把已有材料加入统一资料库。系统保留原始来源，按链接与内容去重；不会把导入账号自动标记为已核验。'), el('div', { class: 'file-field' }, el('label', { for: 'import-file' }, '选择 JSON 文件'), file), textField);
}

export function openCollect(ctx, initial = {}) {
  const modal = dialog('新建研究或采集', { subtitle: 'RESEARCH / CONTENT & EXECUTION', dismissBackdrop: false });
  modal.modal.classList.add('collection-compose');
  const localDraft = readDraft();
  let draftRevision = 0;
  let draftCleared = false;
  let modeBySource = { ...(localDraft?.modeBySource || {}) };
  let suggestions = [];
  let plannedQueries = null;
  let plannedKey = '';
  let suggestionUndo = null;
  let planVersion = 0;
  let planTimer = null;
  let serverRevision = localDraft?.revision;
  let draftConflict = false;
  let localWritable = true;
  let social = null;
  let socialError = null;
  let limits = budgetLimits();
  let creationBusy = false;
  let executionEstimate = null, executionEstimateKey = "", estimating = false, estimateVersion = 0;
  const estimateView = el("section", { class: "research-estimate result-box", "aria-live": "polite" });
  let lastLocalDraft = localDraft;
  const { form, add } = actionForm(modal, '创建采集任务', async values => {
    const deep = isDeep();
    const batch = values.workflow === 'batch';
    const platform = social?.platforms?.find(item => item.id === values.source);
    const request = batch ? batchRequest(values) : { kind: deep ? 'research' : 'collect', params: deep ? researchFormParams(values) : collectParams(values, platform) };
    if (batch && !batchPlan.matches(request)) {
      await batchPlan.request(request);
      return;
    }
    if (deep && !batch) {
      const key = JSON.stringify(request.params);
      if (!executionEstimate || executionEstimateKey !== key) {
        const version = ++estimateVersion;
        estimating = true; update();
        try {
          const estimate = await api('research/estimate', { method: 'POST', body: request.params, signal: controller.signal });
          if (version !== estimateVersion || controller.signal.aborted) return;
          executionEstimate = estimate; executionEstimateKey = key;
          setContent(estimateView, el('h3', {}, '预计用时与开始确认'), el('p', {}, `大致 ${durationText(estimate.lower_seconds)} — ${durationText(estimate.upper_seconds)}；实际可超出此范围。`), el('p', { class: 'field-hint' }, estimate.basis), el('ul', {}, (estimate.assumptions || []).map(text => el('li', {}, text))), estimate.time_limit_may_interrupt && el('p', { class: 'warning-note' }, '当前总时长上限可能在范围探索完之前停止。可调高上限，或设为 0 按研究范围自然结束。'), el('p', {}, '确认以上范围和预计用时后，点击“确认预计用时并开始”。预估没有开始采集或调用模型。'));
          update(); estimateView.scrollIntoView({ block: "nearest" });
        } finally { if (version === estimateVersion) estimating = false; update(); }
        return;
      }
      request.preview_token = executionEstimate.preview_token;
    }
    if (batch && deep) request.preview_token = batchPlan.previewToken;
    const needed = requiredPlatforms();
    if (needed.some(id => checks.get(id)?.verified !== true)) throw new Error('所选社媒来源尚未通过登录检查。请完成会话检测，或取消本次研究中的该来源。');
    const submittedVersion = draftRevision;
    const submittedDraft = saveLocal();
    const unchanged = () => submittedVersion === draftRevision && (localWritable ? sameDraft(readDraft(), submittedDraft) : sameDraft(lastLocalDraft, submittedDraft));
    creationBusy = true; update();
    const job = await api(batch ? 'jobs/batch' : 'jobs', { method: 'POST', body: request });
    if (batch ? !job.batch?.id || !Array.isArray(job.jobs) || !job.jobs.length : !job.id) throw new Error('服务尚未确认任务创建，草稿已保留。请刷新任务中心后重试。');
    let cleared = false;
    let clearError = null;
    try { cleared = await draftSync.clearIf(unchanged); } catch (error) { clearError = error; }
    const current = unchanged();
    if (current) {
      draftCleared = true;
      removeDraftIf(submittedDraft);
      draftSync.close();
    }
    const target = batch ? { batch: job.batch.id } : { selected: job.id };
    const accepted = batch ? `任务串已创建，${number(job.jobs.length)} 个任务将排队执行。` : deep ? '深度研究已进入队列，将沿公开证据逐层探索账号。' : '采集任务已进入队列。';
    const activeOrigin = !controller.signal.aborted && !ctx.signal.aborted && modal.modal.isConnected;
    if (clearError) notify(clearError?.code === 'draft_conflict' || clearError?.code === 409 ? '任务已创建；另一窗口更新的草稿已保留。' : '任务已创建；后台草稿未清除，请重新连接后检查。', 'error');
    if (current && activeOrigin) { modal.close(); notify(accepted); ctx.navigate('jobs', target); }
    else {
      notify(`${accepted}${current && cleared ? '' : '后续草稿已保留。'}可在任务中心查看。`);
      if (activeOrigin) {
        form.querySelector('.accepted-task')?.remove();
        beforeContent(form.querySelector('.form-error'), el('div', { class: 'accepted-task result-box', role: 'status' }, el('p', {}, `${accepted}当前输入仍保留为草稿。`), button('查看已创建任务', () => { modal.close(); ctx.navigate('jobs', target); }, 'secondary small', 'arrow')));
      }
    }
  }, () => { creationBusy = false; if (modal.modal.isConnected) update(); });
  const submitStatus = el('p', { class: 'submit-status field-hint', role: 'status', 'aria-live': 'polite' });
  form.querySelector('.form-actions').before(submitStatus);
  form.addEventListener('invalid', event => {
    const details = event.target.closest('details'); if (details) details.open = true;
    const label = event.target.closest('.field')?.querySelector('label')?.textContent.replace(' *', '') || '输入';
    submitStatus.textContent = `${label}：${event.target.validationMessage}`;
  }, true);
  const draftStatus = el('p', { class: 'draft-status', role: 'status', 'aria-live': 'polite' }, localDraft ? '已恢复上次草稿 · 修改自动保存' : '修改自动保存 · 关闭后可继续');
  const syncConflict = button('保留当前输入并重新同步', async () => {
    syncConflict.disabled = true;
    try { const data = await api('drafts/collection'); serverRevision = data.draft?.revision ?? 0; draftConflict = false; changed(); await draftSync.flush(); }
    catch (error) { draftStatus.textContent = `草稿仍保留在本机：${error.message}`; }
    finally { syncConflict.disabled = false; }
  }, 'secondary small');
  syncConflict.hidden = true;
  const draftSync = draftSynchronizer({
    save: async draft => {
      if (draftConflict) throw new Error('请处理草稿同步冲突。');
      const data = await api('drafts/collection', { method: 'PUT', body: { ...draft, ...(Number.isInteger(serverRevision) ? { revision: serverRevision } : {}) } });
      serverRevision = data.draft?.revision;
      const current = readDraft();
      if (current?.updated_at === draft.updated_at && Number.isInteger(serverRevision)) writeDraft({ ...current, revision: serverRevision });
    },
    clear: async () => { await api('drafts/collection', { method: 'DELETE', ...(Number.isInteger(serverRevision) ? { body: { revision: serverRevision } } : {}) }); serverRevision = 0; },
    onStatus: (state, error) => {
      if (error?.code === 409 || error?.code === 'draft_conflict') draftConflict = true;
      syncConflict.hidden = !draftConflict;
      draftStatus.textContent = draftConflict ? '另一个窗口也修改了草稿。当前输入仍保留；确认保留后可重新同步。' : state === 'saved' ? '草稿已保存 · 可关闭后继续' : !localWritable ? error ? '草稿尚未保存：本机存储与后台连接均不可用，请保留此窗口。' : '本机存储暂不可用，正在同步草稿到应用…' : error ? '草稿已保存在本机 · 后台暂未同步，连接恢复后继续保存' : '草稿已保存在本机 · 正在同步';
    },
  });
  const workflow = field('执行安排', { name: 'workflow', value: 'single', options: [['single', '单次执行'], ['batch', '连续任务串']] });
  const batchKind = field('工作内容', { name: 'batch_kind', value: 'research', options: [['research', '探索账号与关系'], ['collect', '采集公开材料']] });
  const intent = el('div', { class: 'research-intent' });
  const source = field('采集来源', { name: 'source', value: initial.source || 'google_news', options: [['google_news', 'Google News · 公开新闻检索'], ['rss', 'RSS / Atom · 指定公开订阅源'], ['twitter', 'Twitter / X · 公开帖子与账号'], ['instagram', 'Instagram · 公开帖子与账号']] });
  const mode = field('采集方式', { name: 'mode', options: [['keyword', '关键词发现']] });
  const keyword = field('检索关键词 / 主题标签', { name: 'keyword', type: 'textarea', rows: 3, maxlength: 1200, required: true, placeholder: '革命, 労働者, 矢嶋尋, 戦争阻止, 全学連, 階級闘争', hint: '每行一组，也可用逗号（, 或 ，）、分号（; 或 ；）分隔。独立主题请写成“革命, 労働者, 全学連”；“沖縄 平和運動”中的空格表示同时包含两词，不是三个独立检索。组之间的匹配方式在下方选择。主题标签只能填写一个、不含空格，可省略 #。' });
  const keywordMode = field('多组关键词如何匹配', { name: 'keyword_mode', value: 'any', options: [['any', '匹配任一组 · 分别检索后合并去重（OR）'], ['all', '同时匹配全部组 · 合成一条检索（AND）']] });
  const queryPreview = el('div', { class: 'query-preview', 'aria-live': 'polite' });
  const suggestionList = el('div', { class: 'keyword-suggestions' });
  const planStatus = el('p', { class: 'field-hint', role: 'status' });
  const undoSuggestion = button('撤销最近添加', () => { if (suggestionUndo === null) return; keyword.querySelector('textarea').value = suggestionUndo; suggestionUndo = null; changed(); updateKeywords(false); }, 'text small');
  undoSuggestion.hidden = true;
  const assist = el('section', { class: 'keyword-assist', 'aria-label': '相关关键词建议' }, el('div', { class: 'detail-topline' }, el('strong', {}, '相关检索词'), el('span', { class: 'field-hint' }, '点击添加，保留你的原始输入')), suggestionList, el('div', { class: 'actions' }, button('生成相关词', () => requestPlan(false), 'secondary small', 'refresh'), button('AI 扩展研究词', () => requestPlan(true), 'secondary small'), undoSuggestion), planStatus);
  const keywordGroup = el('div', { class: 'keyword-group' }, keyword, keywordMode, queryPreview, assist);
  const researchGoal = field('你希望查清什么？（可选）', { name: 'research_goal', type: 'textarea', rows: 3, maxlength: 1200, placeholder: '例如：查清这些议题由哪些公开账号推动，它们之间有哪些可复查的联系与共同活动？', hint: '填写研究目标，程序根据相关性和新证据决定继续方向；下方设置是运行上限。留空时以关键词作为研究目标。' });
  const handle = field('公开账号用户名', { name: 'handle', value: initial.handle || '', maxlength: 50, placeholder: '填写 @ 后的用户名', hint: '请填写用户名，不填账号主页网址。' });
  const seedAccounts = field('其他研究起点（可选）', { name: 'seed_accounts', type: 'textarea', rows: 2, maxlength: 2000, placeholder: '每行一个用户名或公开账号主页地址', hint: '含主要账号最多 10 个起点，只接受已勾选平台的账号；这些起点不会自动成为已核验账号。' });
  const feed = field('订阅源地址', { name: 'feed_url', type: 'url', maxlength: 2000, placeholder: 'https://example.org/feed.xml' });
  const session = el('div', { class: 'collection-session', role: 'status' });
  const controller = new AbortController();
  const sessionCtx = { ...ctx, signal: controller.signal };
  const checks = new Map();
  const abort = () => controller.abort();
  ctx.signal.addEventListener('abort', abort, { once: true });
  modal.modal.addEventListener('close', () => { if (!draftCleared) { const current = readDraft(); if (!localWritable || (lastLocalDraft ? sameDraft(current, lastLocalDraft) : !current)) saveLocal(); draftSync.close(); } clearTimeout(planTimer); controller.abort(); ctx.signal.removeEventListener('abort', abort); }, { once: true });
  async function checkSession(platform) {
    checks.set(platform, { pending: true }); update();
    try {
      const result = await detectSocialSession(sessionCtx, platform);
      if (controller.signal.aborted) return;
      checks.set(platform, result);
      if (result.verified === true) social = await api('social', { signal: controller.signal });
    } catch (error) { if (!controller.signal.aborted) checks.set(platform, { verified: false, error }); }
    if (!controller.signal.aborted) update();
  }
  const following = check('include_following', '发现公开关注关系', false);
  const followingLimit = field('本次最多关注关系', { name: 'max_following', type: 'number', min: 0, max: 50, value: '10', required: true });
  const socialLimits = el('fieldset', { class: 'field-set collection-limits' }, el('legend', {}, '筛选范围与停止边界'), field('相关性筛选', { name: 'relevance', value: 'focused', options: [['focused', '研究相关（推荐）'], ['broad', '按检索条件']], hint: '研究相关：排除宽泛、无关结果，要求匹配日本和平 / 反战研究主题与检索条件。按检索条件：适合指定账号探索，材料仍为候选。此筛选不调用 AI。' }), el('div', { class: 'form-grid' }, field('最多访问账号', { name: 'max_accounts', type: 'number', min: 1, max: 10, value: '3', required: true }), field('每页最多滚动', { name: 'max_scrolls', type: 'number', min: 1, max: 20, value: '5', required: true })), following, followingLimit, el('p', { class: 'field-hint' }, '限量为本次任务总上限；达到滚动或访问边界会停止，不会无限滚动凑满保存条数。新账号保存为待核验，关注关系不代表组织归属。'));
  const seedSources = el('fieldset', { class: 'field-set research-sources' }, el('legend', {}, '从哪些来源开始探索？'), el('div', { class: 'actions' }, check('seed_news', '公开新闻', true), check('seed_twitter', 'X / Twitter', false), check('seed_instagram', 'Instagram', false)), el('p', { class: 'field-hint' }, '只检索与拓展勾选的来源；新闻无需社媒登录。可选多个，相关账号沿公开关系继续探索。'));
  const researchLimits = el('fieldset', { class: 'field-set research-limits collection-limits' }, el('legend', {}, '逐层探索与分析'), el('div', { class: 'form-grid' }, field('向下探索层数', { name: 'depth', type: 'number', min: 1, max: limits.research.depth.max, value: '2', required: true }), field('最多探索账号', { name: 'research_max_accounts', type: 'number', min: 0, max: limits.research.max_accounts.max, value: '0', hint: '0 表示不按总账号数提前停止。', required: true }), field('最多记录关系', { name: 'max_edges', type: 'number', min: 0, max: limits.research.max_edges.max, value: '0', hint: '0 表示不按总关系数提前停止。', required: true }), field('每页最多滚动', { name: 'research_max_scrolls', type: 'number', min: 1, max: limits.research.max_scrolls.max, value: String(limits.research.max_scrolls.default), required: true }), field('单个任务运行时间上限（秒）', { name: 'time_budget_seconds', type: 'number', min: 0, max: limits.research.time_budget_seconds.max, value: String(limits.research.time_budget_seconds.default), required: true }), field('每个账号 / 查询的读取条数范围', { name: 'max_records_per_account', type: 'number', min: 1, max: limits.research.max_records_per_account.max, value: '1000', required: true }), field('每个关系方向的候选账号范围', { name: 'max_neighbors_per_account', type: 'number', min: 1, max: limits.research.max_neighbors_per_account.max, value: '100', required: true }), field('关系分析方式', { name: 'analysis_mode', value: 'auto', options: [['auto', '自动 · 有 AI 时增强分析'], ['rules', '公开证据整理 · 不调用 AI'], ['ai', 'AI 分析 · 使用已配置模型']] })), el('div', { class: 'actions' }, check('expand_following', '沿关注账号展开', true), check('expand_followers', '沿被关注账号展开', false), check('expand_mentions', '沿帖子提及账号展开', true)), el('p', { class: 'field-hint' }, '每个新账号都记录发现路径和公开证据。分析会区分已观察关系、可能联动和有明确来源的隶属线索；关注、提及或相似发帖不等于组织隶属。日期、探索层数和单页读取范围定义本次有限研究。总账号、关系、材料或运行时间设为 0 时不另设提前停止条件；平台访问受阻、来源耗尽或用户取消时保存已有结果。'));
  const budgetHint = el('p', { class: 'field-hint research-budget-hint' });
  const timePresets = el('div', { class: 'actions time-presets', 'aria-label': '运行时间快捷设置' }, [[0, '按范围完成'], [900, '15 分钟'], [1800, '30 分钟'], [3600, '1 小时'], [10800, '3 小时']].map(([seconds, label]) => button(label, () => { form.elements.time_budget_seconds.value = String(seconds); form.elements.time_budget_seconds.dispatchEvent(new Event('input', { bubbles: true })); }, 'secondary small')));
  researchLimits.append(timePresets, budgetHint);
  const advancedResearch = el('details', { class: 'research-budget technical-details' }, el('summary', {}, '研究范围与可选停止条件 · 总量 / 时长为 0 时按范围完成'), researchLimits);
  const batchFields = el('fieldset', { class: 'field-set batch-target' }, el('legend', {}, '连续任务串 · 总体目标与分段计划'), field('任务串标题（可选）', { name: 'batch_title', maxlength: 160, placeholder: '例如：近期反战与学生运动公开资料' }), field('总体材料目标', { name: 'total_records', type: 'number', min: 1, max: 10000, value: '1000', required: true }), el('p', { class: 'field-hint' }, '总体目标是整串的保存上限。每个任务使用上方的材料上限及探索预算；先检查分段计划，再加入队列。不是定时重复执行。'));
  const batchPlan = batchPlanner({ signal: controller.signal, getLimits: () => limits, onPlan: () => { if (modal.modal.isConnected) update(); } });
  const previewBatch = button('生成任务串预览', async () => {
    if (!form.reportValidity()) return;
    previewBatch.disabled = true;
    form.querySelector('.form-error').replaceChildren();
    try { await batchPlan.request(batchRequest(Object.fromEntries(new FormData(form)))); }
    catch (error) { form.querySelector('.form-error').replaceChildren(errorBox(error)); }
    finally { update(); }
  }, 'secondary', 'refresh');
  const batchSection = el('section', { class: 'batch-composer' }, batchFields, previewBatch, batchPlan.node);
  const dateNote = el('p', { class: 'warning-note' }, '仅纳入所选日本日期内、具有可确认发布时间的社媒材料。日期未知或详情不可读取的帖子会被排除，并在任务诊断中说明。');
  const toggle = (node, visible, required = false) => { node.hidden = !visible; node.querySelectorAll('input,select,textarea').forEach(input => { input.disabled = !visible; if (input.type !== 'checkbox') input.required = visible && required; }); };
  let previousSource = '';
  function isDeep() { return batchKind.querySelector('select').value === 'research'; }
  function requiredPlatforms() {
    if (isDeep()) return ['twitter', 'instagram'].filter(id => form.elements[`seed_${id}`]?.checked);
    const selected = source.querySelector('select').value;
    return isSocial(selected) ? [selected] : [];
  }
  function update() {
    const deep = isDeep();
    const batch = workflow.querySelector('select').value === 'batch';
    if (deep) source.querySelector('select').value = primaryResearchSource(Object.fromEntries([...seedSources.querySelectorAll('input')].map(input => [input.name, input.checked])), source.querySelector('select').value);
    const selected = source.querySelector('select').value;
    const socialSource = isSocial(selected);
    const platform = social?.platforms?.find(item => item.id === selected);
    const verification = checks.get(selected);
    const modes = socialModes(selected, platform).filter(value => !deep || value !== 'hashtag');
    const select = mode.querySelector('select');
    const preferred = selected === previousSource ? select.value : modeBySource[selected] || (initial.handle && selected === initial.source ? 'account' : 'keyword');
    select.replaceChildren(...modes.map(value => el('option', { value, selected: value === preferred }, modeName(value))));
    previousSource = selected;
    const account = socialSource && select.value === 'account';
    dateNote.textContent = account
      ? '直接读取指定账号主页，只保存该作者在所选日本日期内的帖子。历史覆盖取决于页面可见范围和滚动上限；不会把推荐或他人转帖当成该账号原帖。'
      : '仅纳入所选日本日期内、具有可确认发布时间的社媒材料。日期未知或详情不可读取的帖子会被排除，并在任务诊断中说明。';
    source.hidden = deep; source.querySelector('select').disabled = false; source.querySelector('select').required = !deep; source.querySelector('option[value=rss]').disabled = deep;
    toggle(mode, socialSource, true); toggle(keywordGroup, deep || selected !== 'rss' && !account, false); keyword.querySelector('textarea').required = !keywordGroup.hidden && !account;
    mode.querySelector('label').textContent = deep ? '研究起点' : '采集方式';
    keyword.querySelector('label').textContent = deep && account ? '限定主题的关键词（可选）' : '检索关键词 / 主题标签';
    toggle(handle, account, true); toggle(feed, !deep && selected === 'rss', true);
    toggle(seedAccounts, deep && account, false);
    toggle(socialLimits, !deep && socialSource, true); toggle(followingLimit, !deep && socialSource && following.querySelector('input').checked, true);
    toggle(seedSources, deep, false); toggle(advancedResearch, deep, true); toggle(researchGoal, deep, false);
    seedSources.querySelector('legend').textContent = account && deep ? '沿哪些平台的公开账号拓展？' : '从哪些来源开始探索？';
    if (account && deep) { form.elements.seed_news.checked = false; form.elements.seed_news.disabled = true; }
    toggle(batchSection, batch, false); form.elements.total_records.required = batch;
    keywordMode.hidden = !deep && select.value === 'hashtag'; keywordMode.querySelector('select').disabled = keywordMode.hidden;
    if ((!deep || batch) && Number(form.elements.max_records.value) === 0) form.elements.max_records.value = String(deep ? 1000 : limits.collect.max_records.default);
    const activeLimits = limits[deep ? 'research' : 'collect'];
    for (const [name, budget] of Object.entries({ max_records: activeLimits.max_records, max_scrolls: limits.collect.max_scrolls, research_max_scrolls: limits.research.max_scrolls, time_budget_seconds: limits.research.time_budget_seconds, total_records: limits.batch.total_records })) {
      form.elements[name].min = String(budget.min); form.elements[name].max = String(budget.max);
    }
    timePresets.querySelectorAll('button').forEach((item, index) => { item.disabled = [0, 900, 1800, 3600, 10800][index] > limits.research.time_budget_seconds.max; });
    budgetHint.textContent = `总量和总时长填 0 表示不额外提前停止。每页滚动可设 ${limits.research.max_scrolls.min}–${number(limits.research.max_scrolls.max)} 次；保存预算可设 ${limits.research.max_records.min}–${number(limits.research.max_records.max)} 条；单个任务可设 ${durationText(limits.research.time_budget_seconds.min)}–${durationText(limits.research.time_budget_seconds.max)}。当前时长设置：${Number(form.elements.time_budget_seconds.value) === 0 ? '按范围完成' : durationText(form.elements.time_budget_seconds.value)}。这些都是上限，来源耗尽或连续没有新内容时会提前结束；增加上限不会强行凑满材料。`;
    intent.replaceChildren(el('span', { class: 'eyebrow' }, deep ? 'CONTENT / DISCOVER & CONNECT' : 'CONTENT / COLLECT & PRESERVE'), el('p', {}, deep ? '探索账号与关系：先取得相关材料，再沿关注、被关注与提及关系展开。研究结果包括发现路径、关系证据和待复核的联动判断。' : '采集公开材料：按关键词、主题标签或指定账号读取材料，去重后保存到资料库；不逐层展开账号发现树。'), el('p', { class: 'field-hint' }, batch ? '执行安排：连续任务串。按关键词组与日期拆分；每个任务独立保存结果，先预览，再统一排队。' : '执行安排：单次任务。创建一个日期、层数和每页范围明确的任务；关闭窗口后仍在后台执行。'));
    const needed = requiredPlatforms();
    session.hidden = !needed.length; dateNote.hidden = !needed.length;
    const submitButton = form.querySelector('button[type=submit]');
    let currentPreview = false;
    if (batch) { try { currentPreview = batchPlan.matches(batchRequest(Object.fromEntries(new FormData(form)))); } catch { /* Submission reports field-specific errors. */ } }
    const blocked = [];
    if (needed.length && !social) blocked.push('正在检查来源会话');
    else if (needed.length && !social.runtime?.ready) blocked.push(`${needed.map(platformName).join('、')} 的浏览器采集组件尚未就绪，请检查能力与设置`);
    else for (const id of needed) {
      if (checks.get(id)?.pending) blocked.push(`${platformName(id)} 正在检测已有登录`);
      else if (checks.get(id)?.verified !== true || !social?.platforms?.find(item => item.id === id)?.session_available) blocked.push(`${platformName(id)} 尚未通过会话检测，请在上方检测或连接已登录浏览器`);
    }
    if (!deep && socialSource && !modes.length) blocked.push('该来源暂未提供可用采集方式');
    estimateView.hidden = !deep || batch || !executionEstimate;
    const estimationPlanning = deep && !batch && !executionEstimate;
    const planning = batch && !currentPreview || estimationPlanning;
    submitButton.textContent = estimating ? '正在预估用时…' : creationBusy ? '正在创建任务…' : batchPlan.pending && batch ? '正在生成预览…' : estimationPlanning ? '预估用时与范围' : planning ? '预览任务串' : batch ? deep ? '创建研究任务串' : '创建采集任务串' : deep ? '确认预计用时并开始' : '开始资料采集';
    submitButton.disabled = estimating || creationBusy || batch && batchPlan.pending || !planning && blocked.length > 0;
    previewBatch.disabled = creationBusy || batchPlan.pending;
    submitStatus.textContent = estimating ? '正在按当前输入计算粗略用时范围；修改参数会使旧预估失效。' : estimationPlanning ? '第 1 步：先预估用时与范围。预估不开始采集，无需先登录。' : creationBusy ? '正在等待后台确认，草稿会保留到创建成功。' : batchPlan.pending && batch ? '正在生成预览；修改输入会使本次预览过期。' : planning ? '第 1 步：点击预览任务串，检查每段关键词、日期和预算。预览不会执行采集，也无需先登录。' : blocked.length ? `暂时无法开始：${blocked.join('；')}。` : batch ? '第 2 步：预览已确认，点击创建将整串任务加入队列。修改输入后需重新预览。' : deep ? '第 2 步：确认预计用时与范围后开始。修改输入后需要重新预估。' : '创建后可在任务中心查看进度和停止原因。';
    submitStatus.classList.toggle('warning-note', !planning && blocked.length > 0);
    form.elements.max_records.closest('.field').querySelector('label').firstChild.textContent = batch ? '每个任务最多保存条数' : deep ? '总材料停止上限（0 = 按范围完成）' : '最多保存条数';
    session.replaceChildren();
    updateKeywords(false);
    if (!needed.length) return;
    if (!social) { session.append(el('p', {}, '正在检查浏览器与登录会话…')); return; }
    if (deep) {
      for (const id of needed) {
        const checked = checks.get(id);
        const details = social.platforms?.find(item => item.id === id);
        const repair = checked && !checked.pending && !checked.verified;
        session.append(el('div', { class: 'research-session-state' }, el('strong', {}, platformName(id)), el('p', { class: checked?.verified ? 'field-hint' : 'warning-note' }, checked?.pending ? '正在检测并复用已有登录…' : checked?.error ? checked.error.message : checked ? sessionCheckMessage(checked) : details?.detail || '等待检查来源会话…'), repair && el('div', { class: 'actions' }, button('再次检测已有登录', () => checkSession(id), 'secondary small'), button('用系统默认浏览器登录', async () => { try { await openSystemBrowserLogin(sessionCtx, id); } catch (error) { form.querySelector('.form-error').replaceChildren(errorBox(error)); } }, 'secondary small'), button('连接已登录的 Chrome / Edge', () => openBrowserBridge(sessionCtx, id), 'secondary small')), repair && el('p', { class: 'field-hint' }, '也可取消勾选该来源，先完成其他公开来源的研究。')));
        if (social.runtime?.ready && details && !checked) checkSession(id);
      }
      if (socialError) session.append(errorBox(socialError));
      return;
    }
    session.append(el('div', { class: 'detail-topline' }, el('strong', {}, platformName(selected)), badge(platform?.state || 'runtime_unavailable')), el('p', { class: 'field-hint' }, platform?.detail || '来源状态暂不可用，请检查“能力与设置”。'));
    if (socialError) session.append(errorBox(socialError));
    if (verification?.pending) session.append(el('p', { class: 'field-hint' }, '正在检测已有登录。优先验证工作台会话，再尝试复用支持的本机浏览器会话…'));
    else if (verification?.error) session.append(errorBox(verification.error));
    else if (verification) session.append(el('p', { class: verification.verified ? 'field-hint' : 'warning-note' }, sessionCheckMessage(verification)));
    if (social.runtime?.ready && platform && !verification) { checkSession(selected); return; }
    if (verification?.verified !== true && platform) {
      const result = el('div');
      const login = button('用系统默认浏览器登录', async () => {
        login.disabled = true; result.replaceChildren();
        try { await openSystemBrowserLogin(sessionCtx, selected); result.append(el('p', { class: 'field-hint' }, '已请求打开系统默认浏览器。完成登录后回工作台再次检测；此操作尚未更新采集会话。')); }
        catch (error) { result.append(errorBox(error)); }
        finally { login.disabled = false; }
      }, 'secondary small', 'external');
      const detect = button('检测并复用已有登录', () => checkSession(selected), 'small', 'refresh');
      detect.disabled = !social.runtime?.ready || verification?.pending === true;
      const sync = selected === 'twitter' ? button('同步 Firefox 会话', () => { modal.close(); openFirefoxImport(ctx, () => openSystemBrowserLogin(ctx, 'twitter')); }, 'secondary small') : null;
      if (sync) sync.disabled = !social.runtime?.ready;
      const bridge = button('连接已登录的 Chrome / Edge', () => openBrowserBridge(sessionCtx, selected), 'secondary small');
      session.append(el('p', { class: 'field-hint' }, `已有登录会优先验证并复用。默认 Firefox 中已登录的 ${selected === 'twitter' ? 'X' : 'Instagram'} 会话可自动导入；Chrome / Edge 会话可通过本地会话连接扩展同步。`), el('div', { class: 'actions' }, detect, login, sync, bridge), result);
    }
    if (!social.runtime?.ready) session.append(button('查看来源设置', () => { modal.close(); ctx.navigate('settings'); }, 'secondary small', 'arrow'));
  }
  function saveLocal() {
    const draft = captureDraft(form, modeBySource);
    if (!draft) { draftStatus.textContent = '草稿过长，暂时无法保存；请缩短输入后继续。'; return; }
    lastLocalDraft = draft;
    localWritable = writeDraft(draft);
    draftSync.changed(draft);
    return draft;
  }
  function changed(event) { if (draftCleared) return; draftRevision++; executionEstimate = null; executionEstimateKey = ""; estimateVersion++; estimating = false; saveLocal(); if (workflow.querySelector('select').value === 'batch') batchPlan.invalidate(); else update(); if (['start_date', 'end_date', 'source', 'mode', 'workflow', 'research_goal', 'seed_news', 'seed_twitter', 'seed_instagram', 'batch_kind'].includes(event?.target?.name)) updateKeywords(); }
  function activeMode() { return mode.querySelector('select').value || 'keyword'; }
  function planInputKey() { return JSON.stringify({ keyword: keyword.querySelector('textarea').value.trim(), keyword_mode: keywordMode.querySelector('select').value, source: source.querySelector('select').value, mode: activeMode(), start_date: form.elements.start_date.value, end_date: form.elements.end_date.value }); }
  function updateKeywords(schedule = true) {
    const value = keyword.querySelector('textarea').value;
    const selectedMode = activeMode();
    queryPreview.replaceChildren(el('strong', {}, '将实际执行的检索'));
    try {
      const localQueries = keywordQueries(value, keywordMode.querySelector('select').value, selectedMode);
      const queries = plannedQueries && plannedKey === planInputKey() ? plannedQueries : localQueries;
      queryPreview.append(queries.length ? el('ol', {}, queries.map(query => el('li', {}, query))) : el('p', { class: 'field-hint' }, selectedMode === 'account' ? '直接读取指定账号，并沿有来源的公开关系展开；留空关键词时不限制主题。' : '填写关键词后，在这里查看检索预览。'));
    } catch (error) { queryPreview.append(el('p', { class: 'warning-note' }, error.message)); }
    const existing = new Set(keywordGroups(value).map(term => term.replace(/^#/, '')));
    const suggested = [...relatedKeywords(value, selectedMode), ...suggestions].filter((item, index, all) => !existing.has(item.term.replace(/^#/, '')) && all.findIndex(other => other.term === item.term) === index).slice(0, 12);
    suggestionList.replaceChildren(...suggested.map(item => button(item.term, () => {
      const input = keyword.querySelector('textarea');
      if (selectedMode === 'hashtag' && input.value.trim()) { planStatus.textContent = '主题标签只能填写一个。请先清空现有标签，再选择建议；你的输入已保留。'; return; }
      suggestionUndo = input.value;
      try { input.value = appendKeyword(input.value, item.term); changed(); updateKeywords(); }
      catch (error) { planStatus.textContent = error.message; }
    }, 'secondary small keyword-chip')));
    if (!suggested.length) suggestionList.append(el('p', { class: 'field-hint' }, value.trim() ? '可生成相关词或使用 AI 扩展；所有建议均由你选择添加。' : '输入一个研究词后，自动显示相关建议。'));
    undoSuggestion.hidden = suggestionUndo === null;
    if (schedule) { clearTimeout(planTimer); planVersion++; if (value.trim() && !keywordGroup.hidden && selectedMode !== 'hashtag') planTimer = setTimeout(() => requestPlan(false), 800); }
  }
  async function requestPlan(useAI) {
    clearTimeout(planTimer);
    const value = keyword.querySelector('textarea').value.trim();
    if (!value) { planStatus.textContent = '先填写一个关键词，再扩展相关检索词。'; return; }
    const version = ++planVersion;
    const inputKey = planInputKey();
    planStatus.textContent = useAI ? '正在结合研究目标生成相关词…' : '正在整理相关检索词…';
    try {
      const data = await api('research/plan', { method: 'POST', body: { keyword: value, keyword_mode: keywordMode.querySelector('select').value, source: source.querySelector('select').value, mode: activeMode(), seed_sources: selectedSeedSources(), research_goal: researchGoal.querySelector('textarea').value.trim(), start_date: form.elements.start_date.value, end_date: form.elements.end_date.value, use_ai: useAI }, signal: controller.signal, timeout: useAI ? 120000 : 25000 });
      if (controller.signal.aborted || version !== planVersion) return;
      suggestions = normalizeSuggestions(data);
      if (Array.isArray(data.queries) && data.queries.length && data.queries.every(item => typeof item.query === 'string')) { plannedQueries = data.queries.map(item => item.query); plannedKey = inputKey; }
      planStatus.textContent = data.message || (useAI && data.ai?.used !== true ? `已提供相关主题建议${data.ai?.message ? `：${data.ai.message}` : '；AI 未参与本次扩展'}。点击可逐个添加。` : '建议已更新。点击逐个添加，原始输入会保留。');
      updateKeywords(false);
    } catch (error) { if (!controller.signal.aborted && version === planVersion) planStatus.textContent = `相关词生成未完成：${error.message}。你的输入与本地建议已保留。`; }
  }
  function selectedSeedSources() { return ['google_news', 'twitter', 'instagram'].filter(id => form.elements[id === 'google_news' ? 'seed_news' : `seed_${id}`]?.checked); }
  function batchRequest(values) {
    const kind = batchKind.querySelector('select').value;
    const total_records = Number(values.total_records);
    if (!Number.isInteger(total_records) || total_records < limits.batch.total_records.min || total_records > limits.batch.total_records.max) throw new Error(`总体材料目标必须是 ${limits.batch.total_records.min}–${limits.batch.total_records.max} 之间的整数。`);
    return { title: String(values.batch_title || '').trim(), kind, params: kind === 'research' ? researchFormParams(values) : collectParams(values, social?.platforms?.find(item => item.id === values.source)), total_records };
  }
  function researchFormParams(values) {
    validateDates(values);
    const bounded = (value, min, max, label) => { const parsed = Number(value); if (!Number.isInteger(parsed) || parsed < min || parsed > max) throw new Error(`${label}必须是 ${min}–${max} 之间的整数。`); return parsed; };
    const researchMode = isSocial(values.source) && values.mode === 'account' ? 'account' : 'keyword';
    const keywordText = String(values.keyword || '').trim();
    if ((researchMode !== 'account' || keywordText) && !keywordQueries(keywordText, values.keyword_mode || 'any').length) throw new Error('请填写至少一组研究关键词。');
    const accountHandle = String(values.handle || '').trim().replace(/^@/, '').toLowerCase();
    if (researchMode === 'account' && !(values.source === 'twitter' ? /^[a-z0-9_]{1,15}$/ : /^[a-z0-9_.]{1,30}$/).test(accountHandle)) throw new Error('请填写有效的公开账号用户名。');
    const seed_accounts = researchMode === 'account' ? [...new Set(String(values.seed_accounts || '').split(/[\r\n,，;；]+/).map(value => value.trim()).filter(Boolean))] : [];
    if (seed_accounts.length > 9) throw new Error('主要账号之外最多填写 9 个研究起点。');
    const seed_sources = selectedSeedSources();
    if (!seed_sources.length) throw new Error('请选择至少一个发现来源。');
    if (values.source === 'rss') throw new Error('RSS 订阅适合简单采集。请选择公开新闻或社媒作为深度研究起点。');
    if (!seed_sources.includes(values.source)) throw new Error('请勾选当前采集来源作为发现起点，或选择另一个起点来源。');
    const expand_relations = ['following', 'followers', 'mentions'].filter(id => form.elements[`expand_${id}`].checked);
    if (!expand_relations.length) throw new Error('请选择至少一种逐层探索关系。');
    return { keyword: keywordText, keyword_mode: values.keyword_mode || 'any', source: values.source, mode: researchMode, ...(researchMode === 'account' ? { handle: accountHandle, seed_accounts } : {}), research_goal: values.research_goal?.trim() || '', seed_sources, start_date: values.start_date, end_date: values.end_date, depth: bounded(values.depth, 1, limits.research.depth.max, '探索层数'), max_accounts: bounded(values.research_max_accounts, 0, limits.research.max_accounts.max, '最多探索账号'), max_edges: bounded(values.max_edges, 0, limits.research.max_edges.max, '最多记录关系'), max_records: bounded(values.max_records, limits.research.max_records.min, limits.research.max_records.max, '最多保存条数'), max_scrolls: bounded(values.research_max_scrolls, limits.research.max_scrolls.min, limits.research.max_scrolls.max, '每页最多滚动'), time_budget_seconds: bounded(values.time_budget_seconds, limits.research.time_budget_seconds.min, limits.research.time_budget_seconds.max, '运行时间上限'), max_records_per_account: bounded(values.max_records_per_account, 1, limits.research.max_records_per_account.max, '每个账号 / 查询读取范围'), max_neighbors_per_account: bounded(values.max_neighbors_per_account, 1, limits.research.max_neighbors_per_account.max, '每个方向候选范围'), expand_relations, analysis_mode: values.analysis_mode || 'auto', relevance: values.relevance || 'focused' };
  }
  source.querySelector('select').addEventListener('change', () => { if (isSocial(previousSource)) modeBySource[previousSource] = mode.querySelector('select').value; if (isDeep()) { const id = source.querySelector('select').value; const checkbox = form.elements[id === 'google_news' ? 'seed_news' : `seed_${id}`]; if (checkbox) checkbox.checked = true; } update(); });
  mode.querySelector('select').addEventListener('change', () => { modeBySource[source.querySelector('select').value] = mode.querySelector('select').value; update(); });
  workflow.querySelector('select').addEventListener('change', update);
  batchKind.querySelector('select').addEventListener('change', update);
  seedSources.addEventListener('change', update);
  following.querySelector('input').addEventListener('change', update);
  keyword.querySelector('textarea').addEventListener('input', () => { suggestions = []; suggestionUndo = null; updateKeywords(); });
  keywordMode.querySelector('select').addEventListener('change', () => updateKeywords());
  form.addEventListener('input', changed); form.addEventListener('change', changed);
  add(el('div', { class: 'draft-toolbar' }, draftStatus, syncConflict), el('div', { class: 'form-grid workflow-choices' }, batchKind, workflow), intent, el('p', { class: 'intro' }, '任务在本地后台执行；关闭窗口不影响已创建的任务。未提交的输入会自动保存，误点窗口外不会关闭。'), source, seedSources, session, mode, researchGoal, keywordGroup, handle, seedAccounts, feed, dateFields(), dateNote, field('最多保存条数', { name: 'max_records', type: 'number', value: '0', min: 0, max: limits.research.max_records.max, required: true }), socialLimits, advancedResearch, batchSection, estimateView);
  function applyChoices(draft) {
    const values = workflowValues(draft?.values, initial);
    restoreDraft(form, { values });
    if (initial.handle) modeBySource[values.source] = 'account';
    previousSource = '';
  }
  applyChoices(localDraft);
  update();
  api('capabilities', { signal: controller.signal }).then(data => { if (!controller.signal.aborted) { const next = budgetLimits(data); if (JSON.stringify(next) !== JSON.stringify(limits)) { limits = next; batchPlan.invalidate(); } update(); } }).catch(() => { /* Offline fallback matches the packaged service. */ });
  api('drafts/collection', { signal: controller.signal }).then(data => {
    if (controller.signal.aborted) return;
    const revision = data.draft?.revision ?? 0;
    if (draftRevision) {
      if (revision > (serverRevision ?? 0)) { draftConflict = true; syncConflict.hidden = false; draftStatus.textContent = '另一个窗口也修改了草稿。当前输入仍保留；确认保留后可重新同步。'; }
      else if (!data.draft && serverRevision === undefined) serverRevision = 0;
      return;
    }
    const restored = preferredDraft(localDraft, data.draft);
    if (!restored) { serverRevision = 0; return; }
    if (!sameDraft(restored, data.draft) && revision !== (serverRevision ?? 0)) { draftConflict = true; syncConflict.hidden = false; draftStatus.textContent = '另一个窗口也修改了草稿。当前输入仍保留；确认保留后可重新同步。'; return; }
    serverRevision = revision;
    modeBySource = { ...restored.modeBySource }; applyChoices(restored);
    update(); lastLocalDraft = { ...captureDraft(form, modeBySource), ...(revision > 0 ? { revision } : {}) }; localWritable = writeDraft(lastLocalDraft); draftStatus.textContent = '已恢复上次草稿 · 修改自动保存';
  }).catch(() => { if (!controller.signal.aborted) draftStatus.textContent = localDraft ? '已恢复本机草稿 · 后台暂未连接' : '修改会保存在本机；后台草稿暂未连接'; });
  api('social', { signal: controller.signal }).then(data => { if (!modal.modal.isConnected) return; social = data; update(); }).catch(error => { if (!controller.signal.aborted) { social = { runtime: { ready: false }, platforms: [] }; socialError = error; update(); } });
}

export async function openReportCreate(ctx, { run = null } = {}) {
  const modal = dialog('生成研究报告', { subtitle: 'ANALYSIS / TRACEABLE EVIDENCE' });
  const { form, add } = actionForm(modal, '创建报告任务', async values => {
    validateDates(values);
    const include_news = form.elements.include_news.checked; const include_social = form.elements.include_social.checked;
    if (!include_news && !include_social) throw new Error('请至少选择一种材料类型。');
    const minutes = Number(values.report_minutes);
    if (!Number.isSafeInteger(minutes) || minutes < 0 || minutes > 43200) throw new Error('最长运行时间须为 0 至 43200 分钟的整数；0 表示按保存材料范围完成。');
    const harness = values.mode === 'ai' && values.analysis_harness === 'dsh';
    const modelCalls = Number(values.harness_max_model_calls), toolCalls = Number(values.harness_max_tool_calls);
    if (harness && (!Number.isSafeInteger(modelCalls) || (modelCalls !== 0 && modelCalls < 2) || modelCalls > 4096 || !Number.isSafeInteger(toolCalls) || (toolCalls !== 0 && toolCalls < 2) || toolCalls > 65536)) throw new Error('工具分析决策轮数须为 0 或 2 至 4096，工具调用数须为 0 或 2 至 65536；0 表示不额外按次数截断。');
    const job = await api('jobs', { method: 'POST', body: { kind: 'report', params: { ...(values.title.trim() ? { title: values.title.trim() } : {}), start_date: values.start_date, end_date: values.end_date, mode: values.mode, include_news, include_social, time_budget_seconds: minutes * 60, ...(harness ? { analysis_harness: 'dsh', harness_max_model_calls: modelCalls, harness_max_tool_calls: toolCalls } : {}), ...(run ? { research_run_id: run.id, read_original: false } : {}) } } });
    modal.close(); notify('报告任务已进入队列。'); ctx.navigate('jobs', { selected: job.id });
  });
  const mode = field('报告模式', { name: 'mode', value: run ? 'ai' : 'evidence', options: [['evidence', '证据整理 · 无需 AI 配置'], ['ai', 'AI 分析 · 使用已配置模型']] });
  const info = el('p', { class: 'field-hint' }, '正在检查 AI 配置…');
  const harness = field('AI 调查方式', { name: 'analysis_harness', value: 'off', options: [['off', '标准分析 · 逐条阅读与报告复核'], ['dsh', 'DSH 工具分析 · 主动查证与比较']] });
  const harnessLimits = el('fieldset', { class: 'field-set harness-limits' }, el('legend', {}, '工具分析范围'), harness, el('div', { class: 'form-grid' }, field('最多 AI 决策轮数', { name: 'harness_max_model_calls', type: 'number', value: run ? '0' : '24', min: 0, max: 4096, step: 1 }), field('最多工具调用数', { name: 'harness_max_tool_calls', type: 'number', value: run ? '0' : '64', min: 0, max: 65536, step: 1 })), el('p', { class: 'field-hint' }, 'DSH 在本报告保存范围内自主读取原文、追查有向关系、对照说法并核对摘录。分析线索需经过报告复核，不会自动更改账号核验或写入新关系。轮数是运行上限，不是费用额度。'));
  const updateHarness = () => { harnessLimits.hidden = mode.querySelector('select').value !== 'ai'; const enabled = harness.querySelector('select').value === 'dsh'; for (const name of ['harness_max_model_calls', 'harness_max_tool_calls']) form.elements[name].disabled = !enabled; };
  const range = run ? { start: run.params?.start_date || recentDates().start, end: run.params?.end_date || recentDates().end } : recentDates();
  add(el('p', { class: 'intro' }, run ? '分析这次研究保存的材料、公开关系与覆盖限制，回答原研究目标。公开帖文和账号自称不自动证明主体或隶属；报告保留独立证据快照。' : '报告只使用资料库中所选日期的实际材料，并保留生成时的证据快照。候选账号材料不会作为已核验结论。'), run && el('p', { class: 'field-hint' }, `研究目标：${run.params?.research_goal || run.params?.keyword || '比较本次公开证据'}`), field('报告标题', { name: 'title', value: run?.params?.research_goal ? `${run.params.research_goal.slice(0, 145)} · 研究分析` : '', placeholder: '可留空，由系统生成' }), dateFields(range), el('fieldset', { class: 'field-set' }, el('legend', {}, '材料范围'), el('div', { class: 'actions' }, check('include_news', '新闻资料'), check('include_social', '社媒资料'))), mode, field('最长运行时间（分钟）', { name: 'report_minutes', type: 'number', value: '0', min: 0, max: 43200, step: 1, hint: '0 表示不按总时长提前停止，按所选保存材料范围完成。按模型上下文容量安排输入与压缩，不设置费用额度。来源逐片阅读后综合；成功批次会保存，失败可重试继续。' }), info);
  form.insertBefore(harnessLimits, info);
  if (run) form.elements.report_minutes.value = '0';
  mode.querySelector('select').addEventListener('change', updateHarness); harness.querySelector('select').addEventListener('change', updateHarness); updateHarness();
  try { const caps = await api('capabilities'); if (!modal.modal.isConnected) return; const configured = caps.llm?.configured, ready = caps.analysis_harness?.available === true; info.textContent = configured ? `AI 配置已就绪：${caps.llm.model || '已配置模型'}。${ready ? 'DSH 研究工具已安装，实际调用与证据记录见任务中心。' : 'DSH 研究工具尚未安装，可使用标准 AI 分析。'}` : '尚未配置 AI 模型，可以先生成证据整理报告。'; mode.querySelector('option[value=ai]').disabled = !configured; harness.querySelector('option[value=dsh]').disabled = !configured || !ready; if (!configured) mode.querySelector('select').value = 'evidence'; if (configured && ready && run) harness.querySelector('select').value = 'dsh'; updateHarness(); } catch (err) { info.replaceChildren(errorBox(err)); }
}

export async function openReportRetry(ctx, job, { onCreated = () => {} } = {}) {
  const modal = dialog('免费预览报告续跑', { subtitle: 'REPORT / INPUT & SAVED PROGRESS' });
  const original = job.params || {};
  const isAI = original.mode === 'ai';
  let ready = false, configured = !isAI, preview = null, previewVersion = 0, previewBusy = false, creating = false;
  const controller = new AbortController();
  const relay = () => controller.abort(ctx.signal?.reason);
  if (ctx.signal?.aborted) relay();
  ctx.signal?.addEventListener('abort', relay, { once: true });
  modal.modal.addEventListener('close', () => { controller.abort(); ctx.signal?.removeEventListener('abort', relay); }, { once: true });
  const { form, add } = actionForm(modal, '创建报告续跑任务', async values => {
    const mode = values.input_mode;
    const input = mode === 'original' ? preview?.original_input : mode === 'latest' ? preview?.current_input : null;
    if (creating || previewBusy || !validInput(input)) throw new Error('所选输入尚未通过免费预览，请重新预览或明确选择可用输入。');
    const minutes = Number(values.report_minutes), modelCalls = Number(values.harness_max_model_calls), toolCalls = Number(values.harness_max_tool_calls);
    const harness = isAI && values.analysis_harness === 'dsh';
    if (!Number.isSafeInteger(minutes) || minutes < 0 || minutes > 43200) throw new Error('最长运行时间须为 0 至 43200 分钟的整数；0 表示按保存材料范围完成。');
    if (isAI && !configured) throw new Error('AI 连接必须就绪后才能续跑。');
    if (harness && (!ready || !configured)) throw new Error('DSH 运行工具和 AI 连接必须就绪后才能续跑。');
    if (harness && (!Number.isSafeInteger(modelCalls) || (modelCalls !== 0 && modelCalls < 2) || modelCalls > 4096 || !Number.isSafeInteger(toolCalls) || (toolCalls !== 0 && toolCalls < 2) || toolCalls > 65536)) throw new Error('工具分析决策轮数须为 0 或 2 至 4096，工具调用数须为 0 或 2 至 65536；0 表示不额外按次数截断。');
    creating = true; update();
    try {
      const result = await api(`jobs/${pathId(job.id)}/retry`, { method: 'POST', signal: controller.signal, body: {
        input_mode: mode, input_sha256: input.sha256, ...(isAI ? { analysis_harness: harness ? 'dsh' : 'off' } : {}), time_budget_seconds: minutes * 60,
        ...(harness ? { harness_max_model_calls: modelCalls, harness_max_tool_calls: toolCalls } : {}) } });
      if (!result.id) throw new Error('服务尚未确认续跑任务，请刷新任务中心检查。');
      if (controller.signal.aborted || !modal.modal.isConnected) return;
      modal.close(); onCreated(result); notify(mode === 'original' ? '已创建原输入报告续跑任务；保存阶段将在执行时验证是否可复用。' : '已按最新审核上下文创建报告任务；仅完全匹配的已保存阅读片段可候选复用。'); ctx.navigate('jobs', { selected: result.id });
    } catch (error) {
      if ([409, 422, 'request_error', 'validation_error'].includes(error.code)) { preview = null; setContent(previewView, el('p', { class: 'warning-note' }, '预览已失效，请重新免费预览后再确认输入。')); }
      throw error;
    } finally { creating = false; update(); }
  }, () => update());
  const inputMode = field('续跑使用的输入', { name: 'input_mode', value: 'original', options: [['original', '原输入续跑'], ['latest', '使用最新审核上下文重新生成']],
    hint: '默认保留原输入。原快照不可用时不会自动改用最新输入；请阅读影响后明确选择。' });
  const harness = field('AI 调查方式', { name: 'analysis_harness', value: original.analysis_harness === 'dsh' ? 'dsh' : 'off', options: [['off', '标准分析 · 逐条阅读与报告复核'], ['dsh', 'DSH 工具分析 · 主动查证与比较']] });
  const limits = el('div', { class: 'form-grid' }, field('最多 AI 决策轮数', { name: 'harness_max_model_calls', type: 'number', value: String(original.harness_max_model_calls ?? 24), min: 0, max: 4096, step: 1 }), field('最多工具调用数', { name: 'harness_max_tool_calls', type: 'number', value: String(original.harness_max_tool_calls ?? 64), min: 0, max: 65536, step: 1 }));
  harness.hidden = !isAI; limits.hidden = !isAI;
  const previewView = el('section', { class: 'report-retry-preview result-box', 'aria-live': 'polite' });
  const info = el('p', { class: 'field-hint report-retry-readiness' }, isAI ? '正在检查运行工具与 AI 连接…' : '证据整理模式不调用模型。');
  const refresh = button('重新免费预览', () => refreshPreview(), 'secondary small');
  add(el('p', { class: 'intro' }, '先免费检查输入变化与保存进度，再确认续跑方式。预览不调用模型，也不读取公开网络；不会创建任务。'), el('p', { class: 'field-hint' }, `${original.title || '研究报告'} · ${original.start_date || '未指定'} — ${original.end_date || '未指定'} · 日本时间 UTC+9`), inputMode, refresh, previewView, harness, limits,
    field('最长运行时间（分钟）', { name: 'report_minutes', type: 'number', value: String(Math.max(0, Math.ceil((original.time_budget_seconds ?? 0) / 60))), min: 0, max: 43200, step: 1, hint: '0 表示不按总时长提前停止。决策与工具次数是技术运行上限，不是费用额度。' }), info);
  const submitButton = form.querySelector('button[type=submit]'); submitButton.disabled = true;
  const validInput = input => input?.available === true && typeof input.sha256 === 'string' && /^[a-f0-9]{64}$/i.test(input.sha256);
  const count = value => Number.isSafeInteger(value) && value >= 0 ? number(value) : '未知';
  const update = () => {
    const enabled = isAI && harness.querySelector('select').value === 'dsh';
    for (const name of ['harness_max_model_calls', 'harness_max_tool_calls']) form.elements[name].disabled = !enabled;
    const mode = inputMode.querySelector('select').value;
    const input = mode === 'original' ? preview?.original_input : mode === 'latest' ? preview?.current_input : null;
    submitButton.disabled = creating || previewBusy || !validInput(input) || !configured || (enabled && !ready);
    refresh.disabled = creating || previewBusy;
    inputMode.querySelector('select').disabled = creating;
  };
  const drawPreview = () => {
    if (!preview) return;
    const mode = inputMode.querySelector('select').value, selected = mode === 'latest' ? preview.current_input : preview.original_input;
    const changes = preview.changes || {}, reuse = preview.reading_reuse || {}, stages = preview.stage_reuse?.[mode] || {};
    const stageNames = { reading: '逐条阅读', harness: 'DSH 调查', draft: '综合草稿', compression: '综合压缩' };
    const stageLabels = { requires_runtime_validation: '候选复用，运行时再验证', matching_fragments_only: '仅复用完全匹配的片段', invalidated: '完整上下文已变化，重新执行', not_saved: '未保存，需执行', unavailable: '不可用' };
    const cost = { resume_candidate_requires_runtime_validation: '最新输入与保存输入一致；各阶段仍需运行时验证。', new_analysis_with_matching_readings_only: '最新输入需要新的分析，仅完全匹配的已保存阅读片段可候选复用。', unavailable: '当前输入不可用于重新生成，请查看不可用原因。' }[preview.cost_level] || '费用影响尚未确定。';
    for (const [value, input] of [['original', preview.original_input], ['latest', preview.current_input]]) inputMode.querySelector(`option[value=${value}]`).disabled = !validInput(input);
    setContent(previewView,
      el('p', {}, `原输入 ${count(preview.original_input?.source_count)} 条来源 · 当前输入 ${count(preview.current_input?.source_count)} 条来源`),
      !validInput(preview.original_input) && el('p', { class: 'warning-note' }, `原输入不可用：${preview.original_input?.reason || '完整快照或校验标识不可用，请重新免费预览。'}`),
      !validInput(preview.current_input) && el('p', { class: 'warning-note' }, `最新输入不可用：${preview.current_input?.reason || '当前来源或校验标识不可用，请重新免费预览。'}`),
      el('p', {}, changes.compared_to === 'unknown_original' ? '原输入不可比较，变更数量未知。' : `来源变更 ${count(changes.source_count)} 条 · 账号变更 ${count(changes.account_count)} 个 · 主张变更 ${count(changes.claim_count)} 条（各自包括新增、移除与修改）`),
      el('p', {}, mode === 'latest' ? '使用最新审核上下文重新生成；保留原任务的目标和日期，更新当前来源、账号与主张上下文。已有报告记录保留。' : '原输入续跑保留完整保存的来源和审核上下文；后来发生的审核变更不会自动带入本次续跑。'),
      !validInput(selected) && el('p', { class: 'warning-note' }, '当前选择不能续跑。请重新预览，或明确选择另一个可用输入；不会自动切换。'),
      mode === 'latest' && el('p', {}, `最新输入的候选阅读复用：匹配 ${count(reuse.matched)} / ${count(reuse.total)} 个片段 · 待阅读 ${count(reuse.pending)} 个片段。原文指纹及引用完全一致才可复用；真实性需独立核验，费用以实际请求为准。`),
      isAI && el('ul', {}, Object.entries(stageNames).map(([key, label]) => el('li', {}, `${label}：${key === 'harness' && harness.querySelector('select').value !== 'dsh' ? '本次未选择工具调查' : stageLabels[stages[key]] || '未记录，需运行时验证'}`))),
      mode === 'latest' && el('p', { class: 'field-hint' }, cost),
      isAI && el('p', { class: 'field-hint' }, '续跑可能继续调用模型，费用按实际请求产生；保存阶段需运行时验证，不保证免除后续模型费用。'));
    update();
  };
  const refreshPreview = async () => {
    if (creating || controller.signal.aborted) return;
    const version = ++previewVersion; preview = null; previewBusy = true; update(); setContent(form.querySelector('.form-error')); setContent(previewView, el('p', {}, '正在免费检查输入与保存进度…'));
    try {
      const result = await api(`jobs/${pathId(job.id)}/retry-preview`, { signal: controller.signal });
      if (controller.signal.aborted || !modal.modal.isConnected || version !== previewVersion) return;
      if (result.kind !== 'report' || !result.original_input || !result.current_input) throw new Error('服务尚未返回完整报告续跑预览，请刷新后重试。');
      preview = result; drawPreview();
    } catch (error) { if (!controller.signal.aborted && modal.modal.isConnected && version === previewVersion) setContent(previewView, errorBox(error)); }
    finally { if (version === previewVersion) { previewBusy = false; update(); } }
  };
  inputMode.querySelector('select').addEventListener('change', () => { drawPreview(); update(); });
  harness.querySelector('select').addEventListener('change', () => { drawPreview(); update(); }); update();
  await Promise.allSettled([refreshPreview(), ...(isAI ? [(async () => {
    try {
      const caps = await api('capabilities', { signal: controller.signal }); if (controller.signal.aborted || !modal.modal.isConnected) return;
      ready = caps.analysis_harness?.available === true; configured = caps.llm?.configured === true;
      harness.querySelector('option[value=dsh]').disabled = !ready || !configured;
      info.textContent = configured ? (ready ? 'AI 连接与 DSH 工具已就绪。保存阶段能否复用会在实际续跑时验证。' : 'DSH 工具尚未安装，可明确选择标准分析。') : 'AI 连接尚未就绪，请先在能力与设置中保存连接。';
    } catch (error) { if (!controller.signal.aborted && modal.modal.isConnected) info.replaceChildren(errorBox(error)); }
    finally { update(); }
  })()] : [])]);
}

export function openAccountEdit(ctx, account = null) {
  const modal = dialog(account ? '编辑研究账号' : '新增研究账号', { subtitle: 'SOURCES / ACCOUNT REGISTRY' });
  const { add } = actionForm(modal, account ? '保存修改' : '保存为待核验账号', async values => {
    const payload = { ...values, tags: values.tags.split(/[,，]/).map(value => value.trim()).filter(Boolean) };
    await api(account ? `accounts/${pathId(account.id)}` : 'accounts', { method: account ? 'PATCH' : 'POST', body: payload });
    modal.close(); notify(account ? '账号信息已更新。' : '账号已保存，等待核验。'); ctx.refresh();
  });
  add(el('p', { class: 'intro' }, '记录公开账号及其来源。核验是独立操作，需要提供可复查的证据链接。'), el('div', { class: 'form-grid' }, field('显示名称', { name: 'name', value: account?.name || '', required: true }), field('用户名 / Handle', { name: 'handle', value: account?.handle || '', required: true })), el('div', { class: 'form-grid' }, field('平台', { name: 'platform', value: account?.platform || 'twitter', options: [['twitter', 'Twitter / X'], ['instagram', 'Instagram'], ['youtube', 'YouTube'], ['mastodon', 'Mastodon'], ['rss', 'RSS'], ['other', '其他公开来源']] }), field('所属组织', { name: 'organization', value: account?.organization || '' })), field('公开账号链接', { name: 'url', type: 'url', value: account?.url || '', required: true }), field('标签', { name: 'tags', value: (account?.tags || []).join(', '), placeholder: '使用逗号分隔' }));
}

export function openVerification(ctx, account, verified = true) {
  const modal = dialog(verified ? '核验公开账号' : '排除公开账号', { subtitle: 'SOURCE REVIEW' });
  const { add } = actionForm(modal, verified ? '确认核验' : '确认排除', async values => {
    await api(`accounts/${pathId(account.id)}/verify`, { method: 'POST', body: { verified, evidence_url: values.evidence_url || '' } });
    modal.close(); notify(verified ? '账号已核验。' : '账号已排除，记录保留以供追溯。'); ctx.refresh();
  });
  add(el('div', { class: 'review-account' }, el('strong', {}, account.name || account.handle), badge(account.status)), el('p', { class: 'intro' }, verified ? '请先人工核对账号主体与公开来源，再记录核验依据。取消或关闭窗口不会更改账号状态。' : '排除后，该账号仍保留在资料库中。此操作不会删除已有原始材料。'), field('核验依据链接', { name: 'evidence_url', type: 'url', value: account.evidence_url || '', required: verified, placeholder: 'https://…' }));
}
