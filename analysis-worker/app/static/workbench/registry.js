import { api, pathId } from './api.js';
import { el, heading, button, linkButton, field, loading, empty, errorBox, setContent, sourceLink, badge, dateText, dialog, notify } from './ui.js';
import { interactiveGraph } from './interactive_graph.js';
import { selectGraph, selectResearchGraph, researchRelevance, relationshipNames, accountTypeInfo, accountTypeBasisName } from './network_model.js';
import { openAccountEdit } from './actions.js';
import { openAccountRelations } from './social.js';

export function openPublicVerification(ctx, account = null, accountIds = []) {
  const modal = dialog(account ? `自动核验 · ${account.name || account.handle}` : '批量核验公开账号', { subtitle: 'PUBLIC SOURCE / EXACT ACCOUNT LINK' });
  const source = account && field('独立公开依据网页（可选）', { name: 'source_url', type: 'url', value: account.evidence_url || '', placeholder: 'https://组织官网/关于我们', maxlength: 2048 });
  const errors = el('div');
  modal.body.append(el('p', {}, '自动检查组织公开网页中是否存在目标账号的准确主页链接。已登记的官方来源可自动匹配；其他账号可指定你已核对的组织网页。未找到依据、访问失败或只有账号自称时，保留候选状态。'), el('p', { class: 'field-hint' }, '核验确认公开账号身份，不能证明每篇内容可靠，也不会自动推断组织隶属。'), ...(source ? [source] : []), errors);
  modal.footer.append(button('取消', modal.close, 'secondary'), button('开始公开核验', async event => {
    if (source && !source.querySelector('input').reportValidity()) return;
    const control = event.currentTarget; control.disabled = true;
    try { const job = await api('accounts/check-public-sources', { method: 'POST', body: { account_ids: account ? [account.id] : accountIds.slice(0, 20), source_url: source?.querySelector('input').value.trim() || '' } }); modal.close(); notify('公开核验任务已创建，结果将保留在账号和任务记录中。'); ctx.navigate('jobs', { selected: job.id }); }
    catch (error) { setContent(errors, errorBox(error)); control.disabled = false; }
  }));
}

function independentSourceURL(value) {
  let parsed;
  try { parsed = new URL(value); } catch { throw new Error('请填写有效的独立公开 HTTPS 页面链接。'); }
  const host = parsed.hostname.toLowerCase();
  if (parsed.protocol !== 'https:' || parsed.port && parsed.port !== '443' || parsed.username || parsed.password
    || ['x.com', 'twitter.com', 'instagram.com'].some(domain => host === domain || host.endsWith('.' + domain))) {
    throw new Error('依据需要独立于社媒平台的公开 HTTPS 页面；链接中不能包含登录信息。');
  }
  return value.trim();
}

export function openAccountTypeCorrection(ctx, node, refresh) {
  const info = accountTypeInfo(node), modal = dialog('人工更正账号类型', { subtitle: 'ACCOUNT TYPE / MANUAL EVIDENCE' });
  const type = field('账号类型', { name: 'account_type', value: info.type, options: [['unknown', '未判定'], ['organization', '组织'], ['individual', '个人']] });
  const note = field('人工核对说明', { name: 'identity_note', type: 'textarea', value: info.note, maxlength: 1000 });
  const evidence = field('类型依据链接（每行一条）', { name: 'identity_evidence', type: 'textarea', value: info.evidence_urls.join('\n'), rows: 3,
    hint: '最多 5 条独立公开 HTTPS 页面。可留空；没有充分依据时选择“未判定”。' });
  const locked = el('input', { type: 'checkbox', checked: true, name: 'identity_locked' });
  const errors = el('div');
  modal.body.append(el('p', {}, '人工登记类型与身份核验分别保存。类型更正不会把账号变为已核验，也不证明组织隶属。'), type, note, evidence,
    el('label', { class: 'checkbox' }, locked, el('span', {}, '锁定人工类型（自动核验不覆盖）')), errors);
  modal.footer.append(button('取消', modal.close, 'secondary'), button('保存人工类型', async event => {
    const control = event.currentTarget; control.disabled = true;
    try {
      const values = evidence.querySelector('textarea').value.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
      if (values.length > 5) throw new Error('类型依据最多保存 5 条，请先缩小范围。');
      const urls = [...new Set(values.map(independentSourceURL))];
      await api(`accounts/${pathId(node.account_id)}/identity`, { method: 'PATCH', body: {
        type: type.querySelector('select').value, manual_locked: locked.checked, note: note.querySelector('textarea').value.trim(), evidence_urls: urls } });
      modal.close(); notify('人工类型与锁定设置已保存；账号核验状态保持独立。'); await refresh();
    } catch (error) { setContent(errors, errorBox(error)); control.disabled = false; }
  }));
}

export function openVerificationSources(ctx, node, refresh) {
  const modal = dialog('登记可信核验官网来源', { subtitle: 'PUBLIC SOURCE / MANUAL REGISTRATION' });
  const existing = Array.isArray(node.metadata?.verification_sources) ? node.metadata.verification_sources.slice(0, 5) : [];
  const rows = [], list = el('div'), errors = el('div');
  function addRow(source = {}) {
    const index = rows.length + 1;
    const url = field(`核验官网链接 ${index}`, { name: `verification_url_${index}`, type: 'url', value: source.url || '', maxlength: 2048 });
    const label = field(`来源说明 ${index}`, { name: `verification_label_${index}`, value: source.label || '', maxlength: 200 });
    const type = field(`来源对应的账号类型 ${index}`, { name: `verification_type_${index}`, value: source.account_type || 'unknown',
      options: [['unknown', '未判定'], ['organization', '已人工核对为组织'], ['individual', '已人工核对为个人']] });
    rows.push({ url, label, type }); list.append(el('fieldset', { class: 'field-set' }, el('legend', {}, `公开官网依据 ${index}`), url, label, type));
  }
  existing.forEach(addRow); if (rows.length < 5) addRow();
  const more = button('增加官网来源', () => { if (rows.length < 5) addRow(); more.disabled = rows.length >= 5; }, 'secondary small');
  more.disabled = rows.length >= 5;
  modal.body.append(el('p', {}, '只登记你已核对的独立公开官网。保存后后台会检查该页面是否准确回链到此账号；登记来源本身不授予已核验状态。'),
    el('p', { class: 'field-hint' }, '最多 5 项。空链接不会保存；清空原有链接会移除该项来源。来源类型默认“未判定”，仅在有依据时人工选择组织或个人。'), list, more, errors);
  modal.footer.append(button('取消', modal.close, 'secondary'), button('保存核验来源', async event => {
    const control = event.currentTarget; control.disabled = true;
    try {
      const sources = [];
      for (const row of rows) {
        const input = row.url.querySelector('input'), value = input.value.trim();
        if (!input.reportValidity()) { control.disabled = false; return; }
        if (value) sources.push({ url: independentSourceURL(value), label: row.label.querySelector('input').value.trim(), account_type: row.type.querySelector('select').value });
      }
      await api(`accounts/${pathId(node.account_id)}/verification-sources`, { method: 'PUT', body: { sources } });
      modal.close(); notify('核验官网来源已保存；后台核验结果会写入账号与任务记录。'); await refresh();
    } catch (error) { setContent(errors, errorBox(error)); control.disabled = false; }
  }));
}

export function openResearchRelevance(ctx, node, refresh) {
  const relevance = researchRelevance(node);
  const initial = relevance.basis === 'manual_review' ? relevance.state === 'included' ? 'included' : 'irrelevant' : 'unknown';
  const modal = dialog('人工更正研究相关性', { subtitle: 'RESEARCH RELEVANCE / MANUAL REVIEW' });
  const state = field('研究相关性', { name: 'research_relevance', value: initial,
    options: [['unknown', '恢复默认规则'], ['included', '明确纳入研究'], ['irrelevant', '人工排除研究']] });
  const savedReason = node.metadata?.research_relevance_override?.reason;
  const reason = field('相关性更正说明', { name: 'relevance_reason', type: 'textarea', value: relevance.basis === 'manual_review' && typeof savedReason === 'string' ? savedReason : '', maxlength: 1000 });
  const errors = el('div');
  modal.body.append(el('p', { class: 'mono' }, node.account_key), el('p', {}, '人工排除只影响研究展示和默认拓展；账号、类型、核验状态及原始关系材料仍保留。明确纳入可覆盖通用平台默认规则，不能恢复账号本身的“已排除”状态。'),
    el('p', { class: 'field-hint' }, '恢复默认规则会移除这条人工相关性决定；通用平台账号会再次默认隐藏。'), state, reason, errors);
  modal.footer.append(button('取消', modal.close, 'secondary'), button('保存研究相关性', async event => {
    const control = event.currentTarget; control.disabled = true;
    try {
      await api(`accounts/${pathId(node.account_id)}/relevance`, { method: 'PATCH', body: { state: state.querySelector('select').value, reason: reason.querySelector('textarea').value.trim() } });
      modal.close(); notify('研究相关性决定已保存；原始账号和材料仍保留。'); await refresh();
    } catch (error) { setContent(errors, errorBox(error)); control.disabled = false; }
  }));
}

export async function renderRegistry(ctx) {
  let graph = await api('accounts/network', { signal: ctx.signal });
  const content = el('div', { class: 'registry-network' }, heading('03', '账号关系图', 'PUBLIC CONNECTIONS / SOURCE REGISTRY', '跨研究查看已有公开关注和原帖提及。点账号查看身份核验，点连线回到证据。研究中的新证据会自动更新。', [linkButton('返回组织与账号', '#accounts', 'secondary'), button('核验候选账号', () => openPublicVerification(ctx), 'secondary')]));
  const notice = el('div'), stats = el('div', { class: 'account-stats' }), errors = el('div');
  const inspector = el('aside', { class: 'network-inspector', 'aria-label': '账号关系与来源详情' }), canvas = el('div', { class: 'network-canvas' });
  const stage = el('div', { class: 'research-stage' }, canvas, inspector);
  inspector.hidden = true;
  const search = field('查找账号', { name: 'query', type: 'search', value: ctx.params.get('query') || '', placeholder: '名称或用户名' });
  const platform = field('平台', { name: 'platform', options: [['', '所有平台'], ['twitter', 'Twitter / X'], ['instagram', 'Instagram']] });
  const relationship = field('关系类型', { name: 'relationship', options: [['', '全部已观察关系'], ...Object.entries(relationshipNames)] });
  const positions = new Map();
  const viewportState = {};
  let graphController = null, dragging = null, dirty = false, refreshing = false, selectedEdge = '';
  let signature = JSON.stringify(graph);
  const edgeKey = edge => edge.id || `${edge.source_key}|${edge.target_key}|${edge.relationship}|${edge.basis || ''}`;
  canvas.addEventListener('pointerdown', event => { if (!dragging && event.button === 0 && event.isPrimary !== false && event.target.closest('.interactive-svg')) dragging = { pointerId: event.pointerId }; }, { capture: true, signal: ctx.signal });
  for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) document.addEventListener(type, event => { if (dragging?.pointerId === event.pointerId) dragging = null; }, { signal: ctx.signal });
  ctx.signal.addEventListener('abort', () => graphController?.abort(), { once: true });
  let selectedKey = ctx.params.get('account') || '';
  function closeInspector() { selectedKey = selectedEdge = ''; inspector.hidden = true; stage.classList.remove('has-inspector'); canvas.querySelector('.interactive-network')?.dispatchEvent(new Event('graph-clear-selection')); }
  function showInspector() { inspector.hidden = false; stage.classList.add('has-inspector'); inspector.prepend(button('收起详情', closeInspector, 'text small graph-inspector-close')); }
  function inspect(node) {
    if (!node) { closeInspector(); return; }
    selectedKey = node.account_key;
    selectedEdge = '';
    const edges = graph.edges.filter(row => row.source_key === selectedKey || row.target_key === selectedKey);
    const check = node.metadata?.public_verification;
    const type = accountTypeInfo(node);
    const relevance = researchRelevance(node);
    setContent(inspector, el('span', { class: 'eyebrow' }, 'ACCOUNT / PUBLIC EVIDENCE'), el('h2', {}, node.name || node.handle), el('p', { class: 'mono muted' }, node.account_key), badge(node.account_status || 'candidate'), sourceLink(node.url, '公开账号主页'), node.evidence_url && sourceLink(node.evidence_url, '身份核验依据'), check && el('p', { class: 'field-hint' }, `${dateText(check.checked_at, true)} · ${check.message}`), node.account_status === 'candidate' && button('自动核验此账号', () => openPublicVerification(ctx, { ...node, id: node.account_id }), 'secondary small'), button('编辑账号', () => openAccountEdit({ ...ctx, refresh: refreshData }, { ...node, id: node.account_id }), 'secondary small'), el('div', { class: 'divider' }), el('h3', {}, `当前视图内的可追溯关系 · ${edges.length}`), edges.length ? edges.map(edge => { const otherKey = edge.source_key === selectedKey ? edge.target_key : edge.source_key, other = graph.nodes.find(row => row.account_key === otherKey); return el('details', { class: 'network-post' }, el('summary', {}, `${edge.source_key === selectedKey ? '→' : '←'} ${relationshipNames[edge.relationship] || edge.relationship} · ${other?.name || other?.handle || otherKey}`), el('p', { class: 'mono muted' }, `${edge.source_key} → ${edge.target_key}`), edge.evidence_urls.map((url, index) => el('p', {}, sourceLink(url, `关系来源 ${index + 1}`)))); }) : el('p', { class: 'muted' }, graph.limited ? '当前视图内没有该账号的可见连线。关系图只展示部分账号与来源，请打开关系记录核对。' : '尚未取得该账号的公开关系证据。可采集指定账号的公开帖子与关注列表，或继续中断的研究。'), button('查看关系记录', () => openAccountRelations({ ...node, id: node.account_id }, ctx), 'secondary small'), linkButton('探索此账号', `#network?compose=research&source=${node.platform}&handle=${encodeURIComponent(node.handle)}`, 'secondary small'));
    inspector.querySelector('h2').after(el('div', { class: 'account-type-details' }, el('span', { class: 'account-type-label' }, type.label),
      el('p', { class: 'field-hint' }, `类型依据：${accountTypeBasisName(type)}${type.manual_locked ? ' · 人工锁定' : ''}`),
      type.confidence_scope && el('p', { class: 'field-hint' }, type.confidence_scope), type.note && el('p', { class: 'field-hint' }, type.note),
      type.evidence_urls.map((url, index) => sourceLink(url, `类型依据 ${index + 1}`))));
    inspector.append(el('p', { class: 'field-hint account-relevance-details' }, `${relevance.state === 'excluded' ? '人工排除研究' : relevance.state === 'general_platform' ? '通用平台账号' : relevance.state === 'included' ? '已明确纳入研究' : '研究相关性未判定'} · ${relevance.reason || '保留账号与来源供后续核对。'}`));
    if (node.account_id) inspector.append(el('div', { class: 'actions' }, button('更正账号类型', () => openAccountTypeCorrection(ctx, node, refreshData), 'secondary small'),
      button('更正研究相关性', () => openResearchRelevance(ctx, node, refreshData), 'secondary small'),
      button('登记核验官网来源', () => openVerificationSources(ctx, node, refreshData), 'secondary small')));
    const scheduled = node.metadata?.verification_schedule;
    if (scheduled) inspector.append(el('div', { class: 'verification-schedule' }, el('p', { class: 'field-hint' }, `自动核验 · ${({ queued: '已排队', running: '核验中', pending_change: '新依据等待核验', completed: '已完成', succeeded: '已完成', partial: '部分完成', failed: '核验未完成', cancelled: '已取消', interrupted: '已中断' })[scheduled.state] || '已有核验任务记录'}，实际进度请查看执行记录。`),
      scheduled.job_id && linkButton('查看核验执行记录', `#jobs?selected=${pathId(scheduled.job_id)}`, 'text small')));
    showInspector();
  }
  function inspectEdge(edge) { selectedKey = ''; selectedEdge = edgeKey(edge); setContent(inspector, el('span', { class: 'eyebrow' }, 'OBSERVED CONNECTION / SOURCE'), el('h2', {}, relationshipNames[edge.relationship] || edge.relationship), el('p', {}, `${edge.source_key} → ${edge.target_key}`), el('p', { class: 'field-hint' }, edge.relationship === 'mentions' ? '保存的原帖中明确出现目标账号提及；提及不证明合作或隶属。' : '来自公开页面中实际观察并保存的关系。'), edge.evidence_quote && el('blockquote', {}, edge.evidence_quote), edge.evidence_urls.map(url => sourceLink(url, '关系依据')), edge.run_id && linkButton('查看原研究', `#network?run=${pathId(edge.run_id)}`, 'secondary small')); showInspector(); }
  function draw() {
    const visible = selectResearchGraph(selectGraph(graph, { query: search.querySelector('input').value, platform: platform.querySelector('select').value, relationship: relationship.querySelector('select').value }), { showGeneral: viewportState.showGeneral === true });
    const sources = graph.coverage_metadata?.sources || {};
    const sourceNames = { account_relations: '公开关注来源', research_relations: '研究关系来源', social_posts: '原帖来源' };
    setContent(stats, el('div', {}, el('strong', { class: 'mono' }, graph.total_nodes), el('span', {}, '保存账号')), el('div', {}, el('strong', { class: 'mono' }, `${graph.total_edges_exact === false ? '至少 ' : ''}${graph.total_edges}`), el('span', {}, '已汇总有来源的关系')), el('div', {}, el('strong', { class: 'mono' }, visible.nodes.length), el('span', {}, '当前显示')));
    setContent(notice, !graph.edges.length && el('p', { class: 'warning-note', role: 'status' }, graph.limited ? '当前展示范围没有可见连线。已有关系可能连接未展示的账号，请到账号关系记录中核对。' : '尚未保存可显示的关系。账号已展示，研究中断、平台未加载或未访问公开关注列表都可能导致连线为空。'), el('details', { class: 'graph-coverage' }, el('summary', {}, '覆盖范围与采集状态'), el('p', { class: 'field-hint' }, graph.coverage), graph.limited && el('p', { class: 'warning-note' }, '当前显示最多 300 个账号与 1,000 条关系；账号关系记录支持分页，研究结果可在各研究中查看。'), Object.entries(sources).filter(([, source]) => source.omitted_rows > 0).map(([name, source]) => el('p', { class: 'warning-note' }, `${sourceNames[name] || '公开来源'}保存 ${source.total_rows} 条，本图汇总其中 ${source.scanned_rows} 条；还有 ${source.omitted_rows} 条来源记录未纳入本图，关系总数尚未完整汇总。`)), graph.diagnostics.map(row => el('p', {}, badge(row.status), ` ${row.source || '公开来源'} · ${row.outcomes.join(' / ') || '研究中断或部分完成'} `, linkButton('查看原因与继续入口', `#jobs?selected=${pathId(row.job_id)}`, 'text small')))));
    graphController?.abort(); graphController = new AbortController();
    setContent(canvas, interactiveGraph(visible, { positions, viewportState, signal: graphController.signal, selected: selectedKey, selectedEdge, onNode: inspect, onEdge: inspectEdge,
      onShowGeneral: value => { viewportState.showGeneral = value; draw(); },
      onInvestigate: node => ctx.navigate('network', { compose: 'research', source: node.platform, handle: node.handle }) }));
    const edge = visible.edges.find(row => edgeKey(row) === selectedEdge);
    const retained = graph.nodes.find(row => row.account_key === selectedKey);
    if (edge) inspectEdge(edge); else inspect(visible.nodes.find(row => row.account_key === selectedKey) || (retained && researchRelevance(retained).default_hidden ? retained : null));
    dirty = false;
  }
  async function refreshData() {
    if (refreshing || ctx.signal.aborted) return;
    refreshing = true;
    try { const next = await api('accounts/network', { signal: ctx.signal }); if (ctx.signal.aborted) return; const nextSignature = JSON.stringify(next); if (nextSignature !== signature) { graph = next; signature = nextSignature; dirty = true; } setContent(errors); if (dirty && !dragging) draw(); }
    catch (error) { if (!ctx.signal.aborted) setContent(errors, errorBox(error)); }
    finally { refreshing = false; }
  }
  for (const control of [search, platform, relationship]) control.querySelector('input,select').addEventListener(control === search ? 'input' : 'change', draw);
  content.append(stats, notice, el('div', { class: 'filter-panel form-grid' }, search, platform, relationship, button('刷新关系数据', async event => { event.currentTarget.disabled = true; try { await refreshData(); } finally { event.currentTarget.disabled = false; } }, 'secondary small')), errors, stage);
  ctx.poll(() => document.hidden ? Promise.resolve() : refreshData(), 5000);
  draw(); return content;
}
