import { api, apiPage, pathId } from './api.js';
import { el, heading, button, linkButton, downloadButton, badge, number, dateText, loading, empty, errorBox, sourceLink, setContent, dialog, field, notify, pagination } from './ui.js';
import { openCollect, openReportCreate } from './actions.js';
import { readDraft } from './drafts.js';
import { interactiveGraph } from './interactive_graph.js';
import { renderRegistry, openResearchRelevance } from './registry.js';
import { accountTypeControls } from './account_type.js';
import { relationshipNames, nodeStateNames, accountState, accountTypeInfo, accountTypeBasisName, researchRelevance, explicitResearchRoots, selectGraph, selectResearchGraph, discoveryForest, graphPositions } from './network_model.js';
import { openResearchResume, frontierRecoveryStatus } from './research_resume.js';
import { continuationState } from './research_resume_model.js';

const active = status => ['queued', 'running'].includes(status);
const short = (text, length = 20) => [...String(text || '')].slice(0, length).join('');
const discoveryReasons = { user_supplied_account: '用户指定的公开账号', following: '上一层账号的公开关注列表', followers: '上一层账号的公开粉丝列表', mention: '原帖中明确提及的账号', mentions: '原帖中明确提及的账号', post_author: '关键词命中帖子的作者', news_link: '新闻原文中的公开账号链接' };
import { openAutoReview } from './review.js';

const decisionActions = { seed_query: '检索关键词入口', visit_account: '读取账号与相关帖子', admit_deferred_account: '准入已保存的公开账号候选', retry_analysis: '继续已保存证据的 AI 分析', reprioritize_frontier: '根据新证据调整探索顺序', review_semantic_evidence: '核对材料的语义相关性', ai_decision_failed: 'AI 决策未完成，保留规则排序', ai_semantic_review_failed: 'AI 相关性核对未完成', stop_seed_branch: '结束当前检索分支', retain_unselected_platform_link: '保留未选平台的链接线索', reallocate_news_budget: '将剩余预算分配给新闻入口', stop_research: '结束本次研究' };
const discoveryReason = node => discoveryReasons[node.discovery_reason] || node.discovery_reason || '公开资料中发现';
function analysisCoverage(run, analysis) {
  const ai = analysis.ai || {}, summary = run.summary || {};
  const count = value => value !== null && value !== undefined && value !== '' && Number.isSafeInteger(Number(value)) && Number(value) >= 0 ? Number(value) : null;
  const pending = count(summary.analysis_pending_evidence) ?? count(ai.pending_evidence_count);
  const status = summary.analysis_status || (ai.complete === true ? 'completed' : ai.complete === false ? ai.used ? 'partial' : 'pending' : '');
  const analysisPending = pending > 0 || ['partial', 'failed', 'pending'].includes(status);
  const reviewed = count(ai.input_evidence_count), available = count(ai.available_evidence_count);
  const batches = count(ai.completed_batches), resumed = count(ai.resumed_batches);
  const known = [reviewed, available, pending, batches, resumed].some(value => value !== null);
  let text = known ? `AI 成功分析 ${reviewed === null ? '未记录' : number(reviewed)} / ${available === null ? '未记录' : number(available)} 条证据摘录，待审 ${pending === null ? '未记录' : number(pending)} 条；成功批次 ${batches === null ? '未记录' : number(batches)}，续用批次 ${resumed === null ? '未记录' : number(resumed)}。` : ai.used ? '本次 AI 已参与关系分析；旧结果未记录完整摘录覆盖状态，不能据此认定全部材料已审。' : ai.requested ? 'AI 本次未完成，已保留公开证据与规则分析。' : '本次使用公开证据与相关性规则。AI 是否参与检索决策见每条记录。';
  if (ai.identity_complete === false) text += ` 组织名称与身份变体整理尚未完成：${number(ai.identity_omitted_variant_count)} 条变体达到保存容量，容量限制及遗漏数量记录在审计中；原始资料仍可核对，这不表示相关判断已被反证。`;
  return { analysisPending, status, text, complete: ai.complete === true && !analysisPending };
}
function evidenceLinks(urls) { return el('div', { class: 'network-evidence' }, (urls || []).map((url, index) => sourceLink(url, `公开证据 ${index + 1}`))); }
function svgEl(name, attrs = {}, text) { const node = document.createElementNS('http://www.w3.org/2000/svg', name); for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value); if (text) node.textContent = text; return node; }

export async function renderNetwork(ctx) {
  if (ctx.params.get('registry') === '1') return renderRegistry(ctx);
  const runId = ctx.params.get('run');
  const content = el('div', { class: 'research-explorer' }, heading('02', runId ? '研究结果' : '研究工作台', runId ? 'FINDINGS / CONNECTIONS / EVIDENCE' : 'PLAN / COLLECT / EXPLORE', runId ? '查看本次研究的发现路径、关系与原始证据。执行进度和失败诊断在任务中心。' : '在这里创建工作、设置执行安排，并查看已有研究结果。任务中心负责后台执行。', [button('自动审核与核验', () => openAutoReview(ctx), 'secondary'), button('新建研究或采集', () => openCollect(ctx), '', 'plus'), linkButton('查看执行队列', '#jobs', 'secondary', 'arrow')]));
  if (!runId) {
    content.append(el('ol', { class: 'workspace-process', 'aria-label': '研究工作流程' }, el('li', {}, el('span', { class: 'mono' }, '01'), el('strong', {}, '创建工作'), el('p', {}, '选择采集材料或探索关系')), el('li', {}, el('span', { class: 'mono' }, '02'), el('strong', {}, '跟踪执行'), el('p', {}, '在任务中心查看队列和诊断')), el('li', {}, el('span', { class: 'mono' }, '03'), el('strong', {}, '查看成果'), el('p', {}, '材料进入资料库，研究保留发现路径'))));
    content.append(el('section', { class: 'workspace-start', 'aria-label': '选择工作内容' }, el('div', { class: 'panel-heading' }, el('h2', {}, '这次希望完成什么？'), el('span', { class: 'eyebrow' }, 'TWO GOALS / ONE WORKSPACE')), el('div', { class: 'workspace-choice-grid' },
      el('article', { class: 'workspace-choice' }, el('span', { class: 'eyebrow' }, 'A / MATERIALS'), el('h3', {}, '先取得可核对的材料'), el('p', {}, '按关键词、订阅源或指定账号收集新闻与帖子，保存正文、日期和原始链接。'), el('p', { class: 'workspace-output' }, '结果位置：资料库'), button('采集公开材料', () => openCollect(ctx, { kind: 'collect' }), 'secondary', 'library')),
      el('article', { class: 'workspace-choice workspace-choice-research' }, el('span', { class: 'eyebrow' }, 'B / CONNECTIONS'), el('h3', {}, '进一步查清账号之间的关联'), el('p', {}, '从研究主题出发，沿相关作者、公开关注和提及展开，记录每一层发现与关系证据。'), el('p', { class: 'workspace-output' }, '结果位置：本页研究结果 + 资料库'), button('探索账号与关系', () => openCollect(ctx, { kind: 'research' }), '', 'network')))));
    content.append(el('p', { class: 'workspace-dispatch-note' }, '两种工作都可以单次执行，也可以拆成连续任务串。“任务串”是执行安排，在创建表单中设置。'));
    const draft = readDraft();
    if (draft) content.append(el('section', { class: 'workspace-draft' }, el('div', {}, el('span', { class: 'eyebrow' }, 'UNSUBMITTED / AUTOSAVED'), el('h2', {}, '继续上次未提交的工作'), el('p', {}, short(draft.values.research_goal || draft.values.keyword || draft.values.handle || '来源、日期与预算已保存', 100))), button('继续草稿', () => openCollect(ctx), 'secondary', 'arrow')));
    const history = el('section', { class: 'workspace-history', 'aria-label': '已有研究结果' }, el('div', { class: 'panel-heading' }, el('h2', {}, '已有研究结果'), el('span', { class: 'eyebrow' }, 'FINDINGS / SAVED EVIDENCE')), el('p', { class: 'field-hint' }, '深度研究在这里保留发现树、关系和证据；普通采集的材料请到资料库查看。所有工作的执行记录在任务中心。'));
    const compose = ctx.params.get('compose');
    if (['collect', 'research', 'batch'].includes(compose)) {
      const initial = compose === 'batch' ? { workflow: 'batch' } : { kind: compose, workflow: 'single' };
      const source = ctx.params.get('source');
      if (['google_news', 'rss', 'twitter', 'instagram'].includes(source)) initial.source = source;
      const handle = ctx.params.get('handle');
      if (handle) initial.handle = handle;
      if (ctx.route === 'network') window.history.replaceState(null, '', '#network');
      setTimeout(() => { if (!ctx.signal.aborted) openCollect(ctx, initial); }, 0);
    }
    const results = el('div', {}, loading('正在读取已有研究结果…'));
    history.append(results); content.append(history);
    async function loadHistory() { try {
      const data = await apiPage('research', { page: ctx.params.get('page'), page_size: 25 }, { signal: ctx.signal });
      const page = data.page;
      if (ctx.params.has('page') && String(page) !== ctx.params.get('page')) ctx.replaceParams?.({ page });
      if (ctx.signal.aborted) return;
      setContent(results, data.items?.length ? el('div', { class: 'research-run-list' }, data.items.map(run => el('a', { class: 'research-run-card', href: `#network?run=${pathId(run.id)}` }, el('div', { class: 'research-run-code mono' }, 'R / ' + short(run.id, 8)), el('div', {}, el('h3', {}, run.params?.research_goal || run.params?.keyword || '@' + run.params?.handle), el('p', { class: 'muted small-text' }, `${dateText(run.updated_at, true)} · ${run.params?.start_date} — ${run.params?.end_date} · 深度上限 ${run.params?.depth}`)), badge(run.job_status || run.status)))) : empty('还没有研究结果', '选择上方“探索账号与关系”后，结果会逐分支保存。也可以先采集材料，建立资料库。', [], true), pagination({ ...data, page, page_size: 25 }, next => ctx.navigate('network', { page: next })));
    } catch (error) { if (ctx.signal.aborted) return; setContent(results, errorBox(error), el('p', { class: 'field-hint' }, '已有研究暂时无法读取。上方创建入口仍可使用；新任务会单独确认是否创建成功。')); } }
    loadHistory();
    return content;
  }
  let graph = await api(`research/${pathId(runId)}`, { signal: ctx.signal });
  let selectedKey = ctx.params.get('account') || graph.nodes?.[0]?.account_key || '', view = 'tree', zoom = 1;
  const filters = { query: '', platform: '', relationship: '', maxDepth: Math.max(3, graph.run?.params?.depth || 3) };
  const positions = new Map(), viewportState = {};
  let graphController = null;
  ctx.signal.addEventListener('abort', () => graphController?.abort(), { once: true });
  const head = el('section', { class: 'research-run-head' }), stats = el('div', { class: 'research-stats' }), errors = el('div');
  const recovery = el('div');
  const stage = el('div', { class: 'research-stage' }), inspector = el('aside', { class: 'network-inspector', 'aria-label': '账号与证据详情' });
  const visual = el('div', { class: 'network-visual' }), tree = el('div', { class: 'discovery-tree' });
  const claims = el('section', { class: 'research-findings' }), decisions = el('section', { class: 'research-decisions' }), sources = el('section', { class: 'research-sources' });
  const queryInput = el('input', { type: 'search', placeholder: '查找账号或用户名', 'aria-label': '查找网络中的账号', onInput: event => { filters.query = event.target.value; drawGraph(); } });
  const platformSelect = el('select', { 'aria-label': '筛选平台', onChange: event => { filters.platform = event.target.value; drawGraph(); } }, el('option', { value: '' }, '所有平台'), el('option', { value: 'twitter' }, 'Twitter / X'), el('option', { value: 'instagram' }, 'Instagram'));
  const relationshipSelect = el('select', { 'aria-label': '筛选关系类型', onChange: event => { filters.relationship = event.target.value; drawGraph(); } });
  const depthSelect = el('select', { 'aria-label': '显示层级', onChange: event => { filters.maxDepth = Number(event.target.value); drawGraph(); } }, el('option', { value: filters.maxDepth }, '所有层级'), Array.from({ length: filters.maxDepth }, (_, value) => value).map(value => el('option', { value }, `显示到第 ${value} 层`)));
  const treeButton = button('发现树', () => { view = 'tree'; drawGraph(); }, 'secondary small');
  const graphButton = button('关系网络', () => { view = 'graph'; drawGraph(); }, 'secondary small');
  const tools = el('div', { class: 'network-toolbar' }, el('div', { class: 'network-view-switch' }, treeButton, graphButton), queryInput, platformSelect, relationshipSelect, depthSelect);
  stage.append(el('section', { class: 'network-canvas' }, tools, tree, visual), inspector);
  content.append(linkButton('← 返回研究工作台', '#network', 'text small'), head, recovery, errors, stats, stage, claims, decisions, sources);

  function pick(node) { selectedKey = node.account_key; drawInspector(node); drawGraph(false); }
  function drawTree(nodes) {
    const render = node => {
      const row = el('button', { type: 'button', class: `tree-account ${selectedKey === node.account_key ? 'selected' : ''}`, onClick: () => pick(node) }, el('span', { class: 'tree-depth mono' }, String(node.depth).padStart(2, '0')), el('div', {}, el('strong', {}, node.name || node.handle || node.account_key), el('span', { class: 'mono muted small-text' }, node.account_key)), el('span', { class: 'tree-status' }, nodeStateNames[node.status] || node.status));
      const note = el('p', { class: 'tree-reason' }, node.parentOutsideView ? `上一层 ${node.parent_key} 在当前筛选范围外。` : discoveryReason(node));
      if (!node.children.length) return el('li', {}, row, note);
      const branch = el('details', { open: true }, el('summary', { class: 'tree-toggle' }, `${node.children.length} 个下层账号`), el('ul', {}, node.children.map(render)));
      return el('li', {}, row, note, branch);
    };
    return el('ul', { class: 'tree-roots' }, discoveryForest(nodes).map(render));
  }
  function drawSvg(data) {
    return interactiveGraph(data, { positions, viewportState, signal: graphController.signal, selected: selectedKey,
      onShowGeneral: value => { viewportState.showGeneral = value; drawGraph(); },
      onInvestigate: node => ctx.navigate('network', { compose: 'research', source: node.platform, handle: node.handle }),
      onNode: node => { selectedKey = node.account_key; drawInspector(node); }, onEdge: edge => setContent(inspector, el('div', { class: 'eyebrow' }, 'OBSERVED RELATION / SOURCE'), el('h2', {}, relationshipNames[edge.relationship] || edge.relationship), el('p', {}, `${edge.source_key} → ${edge.target_key}`), el('p', { class: 'field-hint' }, '关注和共同材料本身不能确认组织隶属。关系来自公开来源；提及、引用或共同外链不证明合作。'), evidenceLinks(edge.evidence_urls)) });
  }
  function drawGraph(updateInspector = true) {
    const saved = selectGraph(graph, filters);
    const params = graph.run?.params || {};
    const visible = selectResearchGraph(saved, { showGeneral: viewportState.showGeneral === true, explicitRootKeys: explicitResearchRoots(params) });
    treeButton.setAttribute('aria-pressed', view === 'tree'); graphButton.setAttribute('aria-pressed', view === 'graph');
    tree.hidden = view !== 'tree'; visual.hidden = view !== 'graph';
    // The discovery tree is a saved history, including branches later excluded from research.
    setContent(tree, saved.nodes.length ? drawTree(saved.nodes) : empty('当前范围没有账号', graph.items?.length ? '已取得材料，但尚未发现可确认的社媒账号；也可以清除筛选条件。' : '研究结果会在每个分支完成后出现。失败原因保留在执行记录中。'));
    graphController?.abort(); graphController = new AbortController();
    setContent(visual, view === 'graph' ? drawSvg(visible) : el('div'));
    if (updateInspector) { const available = view === 'tree' ? saved : visible; const retained = saved.nodes.find(node => node.account_key === selectedKey);
      drawInspector(available.nodes.find(node => node.account_key === selectedKey) || (retained && researchRelevance(retained).default_hidden ? retained : null) || available.nodes[0]); }
  }
  function drawInspector(node) {
    if (!node) { setContent(inspector, el('div', { class: 'eyebrow' }, 'ACCOUNT / EVIDENCE'), el('h2', {}, '等待发现账号'), el('p', { class: 'muted' }, '选择树或网络中的账号，查看发现路径、已观察关系和原始帖子。')); return; }
    const accountItems = (graph.items || []).filter(item => item.account_id === node.account_id || item.account_key === node.account_key);
    const relations = (graph.edges || []).filter(edge => edge.source_key === node.account_key || edge.target_key === node.account_key);
    const type = accountTypeInfo(node);
    setContent(inspector, el('div', { class: 'eyebrow' }, `ACCOUNT / LAYER ${node.depth}`), el('h2', {}, node.name || node.handle), el('p', { class: 'mono muted small-text' }, node.account_key), badge(accountState(node)), el('p', { class: 'network-discovery-note' }, discoveryReason(node)), node.parent_key && el('p', { class: 'field-hint' }, `上一层：${node.parent_key}`), evidenceLinks(node.evidence_urls), node.url && sourceLink(node.url, '访问公开主页'), el('div', { class: 'divider' }), el('h3', {}, `已观察关系 · ${relations.length}`), relations.length ? el('ul', { class: 'network-relation-list' }, relations.map(edge => el('li', {}, el('strong', {}, `${relationshipNames[edge.relationship] || edge.relationship} → ${edge.source_key === node.account_key ? edge.target_key : edge.source_key}`), evidenceLinks(edge.evidence_urls)))) : el('p', { class: 'muted small-text' }, '本次尚无可保存的关系证据。'), el('div', { class: 'divider' }), el('h3', {}, `保存的帖子 · ${accountItems.length}`), accountItems.map(item => el('article', { class: 'network-post' }, el('time', { class: 'mono muted small-text' }, dateText(item.published_at)), el('p', {}, short(item.content || item.title, 330)), sourceLink(item.url, '核对原帖'))));
    inspector.querySelector('h2').after(el('div', { class: 'account-type-details' }, el('span', { class: 'account-type-label' }, type.label),
      el('p', { class: 'field-hint' }, `类型依据：${accountTypeBasisName(type)}${type.manual_locked ? ' · 人工锁定' : ''}`),
      type.confidence_scope && el('p', { class: 'field-hint' }, type.confidence_scope), type.note && el('p', { class: 'field-hint' }, type.note), evidenceLinks(type.evidence_urls), accountTypeControls(ctx, node, refresh),
      button('以此为起点继续研究', () => ctx.navigate('network', { compose: 'research', source: node.platform, handle: node.handle }), 'secondary small')));
    const relevance = researchRelevance(node);
    inspector.append(el('p', { class: 'field-hint account-relevance-details' }, `${relevance.state === 'excluded' ? '人工排除研究' : relevance.state === 'general_platform' ? '通用平台账号' : relevance.state === 'included' ? '已明确纳入研究' : '研究相关性未判定'} · ${relevance.reason || '保留账号与来源供后续核对。'}`),
      node.account_id && button('更正研究相关性', () => openResearchRelevance(ctx, node, refresh), 'secondary small'));
  }
  async function review(claim, status) {
    const modal = dialog(status === 'confirmed' ? '确认公开关系证据' : '排除候选判断', { subtitle: 'HUMAN REVIEW / PUBLIC EVIDENCE' });
    const url = field('支持该关系的公开证据链接', { name: 'evidence_url', type: 'url', value: claim.evidence_urls?.[0] || '', required: status === 'confirmed', placeholder: 'https://…' });
    const note = field('核对说明', { name: 'note', type: 'textarea', placeholder: '说明该来源具体支持或否定什么', maxlength: 1600 });
    const error = el('div'); modal.body.append(el('p', {}, claim.statement || claim.summary), evidenceLinks(claim.evidence_urls), url, note, error);
    modal.footer.append(button('取消', modal.close, 'secondary'), button(status === 'confirmed' ? '保存人工确认' : '保存排除结论', async event => {
      if (!url.querySelector('input').reportValidity()) return;
      const control = event.currentTarget; control.disabled = true;
      try { await api(`research/${pathId(runId)}/claims/${pathId(claim.id)}/review`, { method: 'POST', body: { status, evidence_url: url.querySelector('input').value, note: note.querySelector('textarea').value } }); modal.close(); notify('复核记录与证据已保存。'); await refresh(); }
      catch (err) { setContent(error, errorBox(err)); } finally { control.disabled = false; }
    }));
  }
  function draw() {
    const run = graph.run || {}, params = run.params || {}, analysis = run.analysis || graph.analysis || {};
    const coverage = analysisCoverage(run, analysis), analysisPending = coverage.analysisPending;
    const continuation = continuationState(run);
    const continueControl = button('继续研究', () => openResearchResume(ctx, { run, graph }), 'secondary small', 'refresh');
    continueControl.disabled = !continuation.available;
    continueControl.title = continuation.reason;
    setContent(head, el('div', { class: 'research-run-title' }, el('div', { class: 'eyebrow' }, `RESEARCH / ${short(run.id, 8)}`), el('h2', {}, params.research_goal || params.keyword || '@' + params.handle), el('p', { class: 'muted small-text' }, `${params.start_date} — ${params.end_date} / UTC+9 · 每完成一个分支即保存`)), el('div', { class: 'actions' }, badge(run.status), linkButton('查看执行记录', `#jobs?selected=${pathId(run.job_id || run.id)}`, 'secondary small'), !active(run.status) && continueControl, !active(run.status) && analysisPending && button(coverage.status === 'failed' ? '仅重试 AI 分析' : '继续 AI 分析', async event => { const control = event.currentTarget; control.disabled = true; try { const job = await api(`jobs/${pathId(run.job_id || run.id)}/retry`, { method: 'POST' }); ctx.navigate('jobs', { selected: job.id }); } catch (err) { setContent(errors, errorBox(err)); control.disabled = false; } }, 'secondary small', 'refresh'), active(run.status) && button('停止研究', async event => { const control = event.currentTarget; control.disabled = true; try { await api(`jobs/${pathId(run.job_id || run.id)}/cancel`, { method: 'POST' }); await refresh(); } catch (err) { setContent(errors, errorBox(err)); } }, 'danger small'), !active(run.status) && button('生成研究分析报告', () => openReportCreate(ctx, { run }), 'secondary small', 'plus'), downloadButton('下载研究摘要', `research/${pathId(runId)}/report`, ctx, 'text small'), downloadButton('导出关系与证据', `research/${pathId(runId)}/export`, ctx, 'text small')));
    if (analysisPending) head.append(el('p', { class: 'warning-note analysis-resume-note', role: 'status' }, coverage.status === 'failed' ? 'AI 分析尚未完成。已保存采集快照；重试只补待审分析，不重新打开采集。' : '已保存采集快照；继续只补待审 AI 分析，不重新打开采集。'));
    else if (run.summary?.analysis_status === 'fallback') head.append(el('p', { class: 'field-hint', role: 'status' }, '自动模式的 AI 本次未完成，结果采用证据规则分析。'));
    setContent(recovery, frontierRecoveryStatus(run, graph));
    setContent(stats, [['账号', graph.total_nodes ?? graph.nodes?.length], ['关系', graph.total_edges ?? graph.edges?.length], ['材料', graph.total_items ?? graph.items?.length], ['待核对判断', graph.claims?.filter(claim => claim.status === 'candidate').length]].map(([label, value], index) => el('div', {}, el('span', { class: 'eyebrow' }, `${String(index + 1).padStart(2, '0')} / ${label}`), el('strong', { class: 'mono' }, number(value)))));
    const selectedRelationship = relationshipSelect.value;
    setContent(relationshipSelect, el('option', { value: '' }, '所有关系'), [...new Set((graph.edges || []).map(edge => edge.relationship))].map(value => el('option', { value, selected: value === selectedRelationship }, relationshipNames[value] || value)));
    setContent(claims, el('div', { class: 'research-section-title' }, el('div', { class: 'eyebrow' }, 'AFFILIATION / ASSOCIATION / REVIEW'), el('h2', {}, '关系分析与隶属线索'), el('p', { class: 'field-hint' }, '公开声明、已观察关系和 AI 候选推断分别标明。人工确认需要支持该关系的公开证据，不会自动更改账号核验或组织归属。')), graph.claims?.length ? el('div', { class: 'research-claim-grid' }, graph.claims.map(claim => el('article', { class: `research-claim claim-${claim.status}` }, el('div', { class: 'eyebrow' }, claim.basis === 'reported' ? 'PUBLIC SELF DECLARATION' : claim.basis === 'inferred' ? 'INFERENCE / NEEDS REVIEW' : 'PUBLIC OBSERVATION'), el('h3', {}, `${claim.subject_key} → ${claim.object_label || claim.object_key || '待核对关系'}`), el('p', {}, claim.statement || claim.summary), el('span', { class: 'claim-status' }, ({ candidate: '候选 · 尚未确认', confirmed: '已确认', rejected: '已排除', inconclusive: '已审核 · 证据不足' })[claim.review_outcome || claim.status] || claim.status), evidenceLinks(claim.evidence_urls), claim.review_note && el('p', { class: 'field-hint' }, `复核说明：${claim.review_note}`), claim.review_evidence_url && sourceLink(claim.review_evidence_url, '审核依据'), el('div', { class: 'actions' }, button('确认证据', () => review(claim, 'confirmed'), 'secondary small'), button('排除此判断', () => review(claim, 'rejected'), 'text small'), button('记录证据不足', () => review(claim, 'inconclusive'), 'text small'))))) : el('p', { class: 'research-empty-findings' }, '本次材料尚不足以支持组织隶属或联动判断。系统会保留已观察关系，供后续研究核对。'));
    const rows = analysis.decisions || run.metadata?.decisions || [];
    setContent(decisions, el('div', { class: 'research-section-title' }, el('div', { class: 'eyebrow' }, 'ACTION / OBSERVATION / NEXT STEP'), el('h2', {}, '检索如何继续，为什么停止'), el('p', { class: 'field-hint analysis-coverage', role: 'status' }, coverage.text), (analysis.ai?.requested || analysis.ai?.used || analysisPending) && el('p', { class: 'field-hint analysis-excerpt-note' }, `${coverage.complete ? '当前保存证据摘录的 AI 分析已完成。' : '分析覆盖以成功处理的证据摘录为准。'}摘录不等于全部原文；候选结论仍需来源和人工核对。`)), rows.length ? el('ol', { class: 'research-decision-list' }, rows.map((row, index) => el('li', {}, el('span', { class: 'mono' }, String(index + 1).padStart(2, '0')), el('div', {}, el('strong', {}, decisionActions[row.action] || row.action || row.stage || '检索决策'), el('p', {}, row.reason || row.message || ''), el('span', { class: 'eyebrow' }, `${row.method === 'ai' ? 'AI ASSISTED' : 'EVIDENCE RULES'}${row.outcome ? ' / ' + row.outcome : ''}`))))) : el('p', { class: 'muted' }, '决策记录将在研究分支执行后保存。'), el('p', { class: 'research-coverage' }, graph.coverage || '只包含本次实际访问的公开资料。'), el('details', { class: 'technical-details' }, el('summary', {}, '来源诊断与运行上限'), el('pre', {}, JSON.stringify({ summary: run.summary, diagnostics: run.diagnostics, params }, null, 2))));
    const shownItems = graph.items?.length || 0, totalItems = graph.total_items ?? shownItems;
    setContent(sources, el('details', {}, el('summary', {}, `本次保存的材料 · 共 ${number(totalItems)} 条${totalItems > shownItems ? ` / 当前显示 ${number(shownItems)} 条` : ''}`), totalItems > shownItems && el('p', { class: 'field-hint material-coverage' }, '为保持研究图加载流畅，此处显示部分材料；材料总数来自实际保存记录。更多正文、日期与原始来源可在资料库分页查看。'), totalItems > shownItems && linkButton('到资料库查看材料', '#library', 'secondary small'), el('ol', {}, (graph.items || []).map(item => el('li', {}, el('strong', {}, item.title), el('span', { class: 'muted small-text' }, dateText(item.published_at)), sourceLink(item.url))))));
    drawGraph();
  }
  async function refresh() { const next = await api(`research/${pathId(runId)}`, { signal: ctx.signal }); if (ctx.signal.aborted) return; graph = next; setContent(errors); draw(); }
  draw(); ctx.poll(async () => { if (active(graph.run?.status)) try { await refresh(); } catch (err) { if (!ctx.signal.aborted) setContent(errors, errorBox(err)); } }, 5000);
  return content;
}
