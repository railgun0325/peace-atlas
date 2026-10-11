import { el, button, sourceLink, setContent } from './ui.js';
import { relationshipNames, accountState, accountStateName, accountType, accountTypeName, accountTypeCounts, accountTypeInfo, accountTypeBasisName, graphTierNames, graphStructure, graphEmphasis, networkEdgeKey } from './network_model.js';
import { createGraphLayout, stepGraphLayout, settleGraphLayout, graphLabelPlacements, createGraphMotion, advanceGraphMotion, graphMotionStepLimit, graphConfirmationDisplacement, GRAPH_STABLE_MOVEMENT } from './graph_layout.js';

const NS = 'http://www.w3.org/2000/svg';
const svgNode = (tag, attrs = {}, text = '') => { const node = document.createElementNS(NS, tag); for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value); node.textContent = text; return node; };
let sequence = 0;

// Keep every visible contour inside the existing physics radius. Type changes
// affect presentation only; they do not alter springs, spacing or edge evidence.
export function graphNodeContour(type, radius) {
  if (type === 'organization') {
    const half = radius / Math.SQRT2;
    return { tag: 'rect', attrs: { x: -half, y: -half, width: half * 2, height: half * 2, rx: Math.min(2, radius * .18) } };
  }
  return { tag: 'circle', attrs: { r: radius } };
}
function contour(type, radius, className) {
  const { tag, attrs } = graphNodeContour(type, radius);
  return svgNode(tag, { class: className, ...attrs });
}
function typeLegend(type, label) {
  const symbol = svgNode('svg', { class: 'graph-legend-symbol', viewBox: '-8 -8 16 16', 'aria-hidden': 'true' });
  symbol.append(contour(type, 7, 'graph-legend-contour'));
  return el('span', { class: `type-${type}` }, symbol, label);
}

// Display positions never change account identities or relationship evidence.
export function interactiveGraph(data, { onNode = () => {}, onEdge = () => {}, onInvestigate, onShowGeneral, selected = '', selectedEdge = '', signal, positions = new Map(), viewportState = {} } = {}) {
  const root = el('div', { class: 'interactive-network' });
  const width = 940, height = 600, id = `graph-arrow-${++sequence}`;
  const svg = svgNode('svg', { class: 'network-svg interactive-svg', viewBox: `0 0 ${width} ${height}`, role: 'group', 'aria-label': '可拖拽账号关系网络。拖动账号推开附近节点，拖动背景平移，滚轮缩放，双击固定或释放账号，方向键移动并固定账号。' });
  const defs = svgNode('defs'), marker = svgNode('marker', { id, markerWidth: 8, markerHeight: 8, refX: 7, refY: 3, orient: 'auto', markerUnits: 'userSpaceOnUse' });
  marker.append(svgNode('path', { d: 'M0 0L7 3L0 6', fill: 'none', stroke: '#85887b' })); defs.append(marker); svg.append(defs);
  const viewport = svgNode('g'), lines = svgNode('g'), cards = svgNode('g'); viewport.append(lines, cards); svg.append(viewport);
  const restoredViewport = Number.isFinite(viewportState.scale) && Number.isFinite(viewportState.tx) && Number.isFinite(viewportState.ty);
  let scale = restoredViewport ? viewportState.scale : 1, tx = restoredViewport ? viewportState.tx : 0, ty = restoredViewport ? viewportState.ty : 0, drag = null, frame = 0, dead = false, hovered = '', selectedConnection = null, selectTimer = 0;
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  let moving = viewportState.moving ?? !reduced;
  let interactionSettling = Boolean(viewportState.settling), motion = createGraphMotion(), totalSteps = 0, autoFit = !restoredViewport;
  const layout = settleGraphLayout(createGraphLayout(data, positions, width, height), viewportState.preservePositions ? 0 : positions.size ? 8 : Math.min(90, 50 + data.nodes.length));
  viewportState.preservePositions = false;
  const { nodes, byKey } = layout;
  const structure = graphStructure(data);
  const exploration = viewportState.exploration ||= { startKey: selected || nodes[0]?.account_key || '', direction: 'all', maxHops: null, tier: 'all', endKey: '' };
  if (!byKey.has(exploration.startKey)) exploration.startKey = byKey.has(selected) ? selected : nodes[0]?.account_key || '';
  let emphasis = graphEmphasis(data, { ...exploration, structure });
  selectedConnection = data.edges.find(edge => (edge.id || `${edge.source_key}|${edge.target_key}|${edge.relationship}|${edge.basis || ''}`) === selectedEdge) || null;
  const lineNodes = [];
  const cardNodes = new Map();
  const zoomLabel = el('span', { class: 'mono', 'aria-live': 'polite' }, '100%');
  const layoutStatus = el('span', { class: 'network-layout-status', role: 'status' });
  function status(state) {
    root.dataset.layoutState = state;
    root.dataset.layoutSteps = String(totalSteps);
    root.dataset.layoutMovement = String(motion.movement);
    root.dataset.layoutAlpha = String(motion.alpha);
    root.dataset.layoutQuiet = String(motion.quiet);
    root.dataset.layoutQuietSteps = String(motion.quietSteps);
    root.dataset.layoutConfirmationSteps = String(motion.confirmationSteps);
    root.dataset.layoutConfirmationDisplacement = String(motion.confirmationDisplacement);
    root.dataset.layoutConfirmationResets = String(motion.confirmationResets);
    viewportState.settling = interactionSettling;
    const text = { dragging: '拖动中 · 邻居实时调整', settling: '布局整理中', confirming: '确认布局稳定中', stable: '布局已稳定', 'held-stable': '已稳定 · 移动鼠标继续拖动', paused: '布局已暂停' }[state] || '';
    if (layoutStatus.textContent !== text) layoutStatus.textContent = text;
  }
  function transform() { viewport.setAttribute('transform', `translate(${tx} ${ty}) scale(${scale})`); zoomLabel.textContent = `${Math.round(scale * 100)}%`; Object.assign(viewportState, { scale, tx, ty, moving }); paint(); }
  function zoom(next, center = { x: width / 2, y: height / 2 }) { autoFit = false; next = Math.max(.25, Math.min(3, next)); tx = center.x - (center.x - tx) * next / scale; ty = center.y - (center.y - ty) * next / scale; scale = next; transform(); }
  function fit() {
    if (!nodes.length) { scale = 1; tx = ty = 0; transform(); return; }
    const xs = nodes.map(n => n.x), ys = nodes.map(n => n.y), minX = Math.min(...xs) - 65, maxX = Math.max(...xs) + 120, minY = Math.min(...ys) - 45, maxY = Math.max(...ys) + 45;
    scale = Math.max(.25, Math.min(1.5, (width - 60) / (maxX - minX), (height - 60) / (maxY - minY))); tx = width / 2 - (minX + maxX) / 2 * scale; ty = height / 2 - (minY + maxY) / 2 * scale; transform();
  }
  const animation = button(moving ? '暂停布局动效' : '开启布局动效', () => { moving = !moving; viewportState.moving = moving; animation.textContent = moving ? '暂停布局动效' : '开启布局动效'; animation.setAttribute('aria-pressed', moving); if (moving) reheat(); else { interactionSettling = false; cancelAnimationFrame(frame); frame = 0; status('paused'); } }, 'secondary small');
  animation.setAttribute('aria-pressed', moving);
  const toolbar = el('div', { class: 'network-zoom actions' }, button('缩小', () => zoom(scale / 1.2), 'secondary small'), zoomLabel, button('放大', () => zoom(scale * 1.2), 'secondary small'), button('适应画布', () => { autoFit = false; fit(); }, 'secondary small'), animation, layoutStatus, button('重置布局', () => {
    positions.clear(); const fresh = settleGraphLayout(createGraphLayout(data, positions, width, height));
    nodes.forEach(node => { const initial = fresh.byKey.get(node.account_key); Object.assign(node, { x: initial.x, y: initial.y, vx: 0, vy: 0, pinned: false }); }); autoFit = true; fit(); reheat(true);
  }, 'text small'));
  const control = (label, options, onChange) => el('label', { class: 'graph-explore-field' }, el('span', {}, label), el('select', { 'aria-label': label, onChange }, options.map(([value, text]) => el('option', { value }, text))));
  const nodeOptions = nodes.map(node => [node.account_key, `${node.name || node.handle || node.account_key} · ${node.account_key}`]);
  const originControl = control('关系起点', nodeOptions, event => { exploration.startKey = event.target.value; if (exploration.endKey === exploration.startKey) exploration.endKey = ''; refreshExploration(); });
  const directionControl = control('关注方向', [['all', '全部关系'], ['out', '我关注'], ['in', '关注我']], event => { exploration.direction = event.target.value; if (exploration.direction !== 'all' && exploration.maxHops === null) exploration.maxHops = 1; refreshExploration(); });
  const hopsControl = control('关系跳数', [['', '全图'], ['1', '1 跳'], ['2', '2 跳'], ['3', '3 跳']], event => { exploration.maxHops = event.target.value ? Number(event.target.value) : null; refreshExploration(); });
  const tierControl = control('结构层级', [['all', '全部层级'], ...Object.entries(graphTierNames)], event => { exploration.tier = event.target.value; refreshExploration(); });
  const endControl = control('路径终点', [['', '不指定终点'], ...nodeOptions], event => { exploration.endKey = event.target.value; refreshExploration(); });
  const selectedOrigin = button('以选中账号为起点', () => { if (byKey.has(selected)) { exploration.startKey = selected; if (exploration.endKey === selected) exploration.endKey = ''; refreshExploration(); } }, 'secondary small');
  const investigate = typeof onInvestigate === 'function' ? button('以起点账号继续研究', () => { const node = byKey.get(exploration.startKey); if (node) onInvestigate(node); }, 'secondary small', 'arrow') : null;
  const summary = el('div', { class: 'graph-node-summary', role: 'status' });
  const pathResult = el('section', { class: 'graph-path-result', 'aria-label': '路径与公开证据' });
  const relevance = data.relevance_scope || {};
  const typeCounts = accountTypeCounts(data.nodes);
  const showGeneral = typeof onShowGeneral === 'function' && el('label', { class: 'checkbox graph-show-general' }, el('input', { type: 'checkbox', checked: relevance.show_general === true,
    onChange: event => { viewportState.preservePositions = true; onShowGeneral(event.target.checked); } }), el('span', {}, '显示通用账号'));
  const relevanceNote = typeof onShowGeneral === 'function' && el('p', { class: 'field-hint graph-relevance-note', role: 'status' },
    `当前显示 ${data.nodes.length} 个账号；此筛选范围隐藏通用账号 ${relevance.hidden_general || 0}、人工排除 ${relevance.hidden_excluded || 0} 个。${relevance.shown_general ? `显示的 ${relevance.shown_general} 个通用账号仅供核对原始链，不参与重点或桥接排名。` : '原始关系与材料仍保留；打开“显示通用账号”可核对平台号连接的原始路径。'} 人工排除不由此开关恢复。`);
  const explorationTools = el('div', { class: 'graph-exploration' }, el('div', { class: 'graph-explore-controls' }, originControl, directionControl, hopsControl, tierControl, endControl),
    el('div', { class: 'actions' }, selectedOrigin, investigate, showGeneral), relevanceNote,
    el('p', { class: 'field-hint graph-structure-note' }, '“我”指关系起点账号。1–3 跳强调公开关系路径，其他节点仍保留并淡化。重点/桥接/外围仅描述当前显示的关系结构，外围仍可追查；不足以判断组织隶属或实际影响力。'),
    el('div', { class: 'graph-type-legend' }, typeLegend('organization', `组织 ${typeCounts.organization} · 绿色方块`), typeLegend('individual', `个人 ${typeCounts.individual} · 棕色圆点`), el('span', {}, '轮廓与颜色表示组织/个人粗分；✓ 表示已核验，核验状态另见详情。')), summary, pathResult);
  root.append(toolbar, explorationTools, svg, el('p', { class: 'field-hint' }, '拖动拉动相连节点并推开邻近节点 · 松手后整理至稳定 · 双击固定/释放 · 背景平移 · 滚轮缩放。悬停突出实际邻居，点账号或连线查看依据；距离不表示关联强弱。'));
  function refreshExploration() {
    emphasis = graphEmphasis(data, { ...exploration, structure });
    root.dataset.graphOrigin = exploration.startKey;
    root.dataset.graphDirection = exploration.direction;
    root.dataset.graphHops = exploration.maxHops ?? 'all';
    for (const [element, value] of [[originControl, exploration.startKey], [directionControl, exploration.direction], [hopsControl, exploration.maxHops ?? ''], [tierControl, exploration.tier], [endControl, exploration.endKey]]) element.querySelector('select').value = String(value);
    selectedOrigin.disabled = !byKey.has(selected);
    if (investigate) investigate.disabled = !byKey.has(exploration.startKey);
    const key = byKey.has(selected) ? selected : exploration.startKey, node = byKey.get(key), metric = structure.node_metrics[key], type = accountTypeInfo(node);
    setContent(summary, node && el('details', {}, el('summary', {}, `${node.name || node.handle || key} · ${type.label} · ${graphTierNames[metric.tier]} · 查看依据`),
      el('p', { class: 'field-hint' }, `${metric.ranking_excluded ? '' : `研究相关范围的有来源入边邻居 ${metric.in_degree} · 出边邻居 ${metric.out_degree} · 独立邻居 ${metric.degree}。`}${metric.reasons.join(' ')}`),
      el('p', { class: 'field-hint' }, `类型依据：${accountTypeBasisName(type)}${type.manual_locked ? ' · 人工锁定' : ''}`), type.evidence_urls.length && el('div', { class: 'network-evidence' }, type.evidence_urls.map((url, index) => sourceLink(url, `类型依据 ${index + 1}`)))),
      structure.scope.partial && el('p', { class: 'field-hint' }, `当前显示 ${structure.scope.displayed_nodes} / ${structure.scope.total_nodes} 个保存账号；未展示的关系可能改变结构层级。`));
    if (!exploration.endKey) setContent(pathResult, el('p', { class: 'field-hint' }, '选择路径终点，可逐跳核对原始方向和公开证据。全图模式最多查找 3 跳的最短有来源路径。'));
    else if (!emphasis.path) setContent(pathResult, el('p', { class: 'warning-note' }, exploration.endKey === exploration.startKey ? '起点与终点相同，请选择另一个终点。' : `当前方向与跳数内未找到逐跳有公开证据的路径。${relevance.hidden_general ? '此范围已隐藏通用平台账号，可打开“显示通用账号”核对原始链。' : ''}缺少路径不表示账号之间没有关系。`));
    else {
      const steps = emphasis.path.steps.map((step, index) => el('li', {},
        button(`${index + 1}. ${step.source_key} → ${step.target_key} · ${relationshipNames[step.relationship] || step.relationship}`, () => chooseEdge(step.edge), 'text small'),
        step.reversed && el('p', { class: 'field-hint' }, '本跳逆向浏览；箭头保留来源中实际观察的方向。'),
        el('div', { class: 'network-evidence' }, step.evidence_urls.map((url, evidenceIndex) => sourceLink(url, `公开证据 ${evidenceIndex + 1}`)))));
      setContent(pathResult, el('h3', {}, `有来源的最短路径 · ${emphasis.path.hops} 跳`), el('ol', { class: 'graph-path-steps' }, steps),
        el('p', { class: 'field-hint' }, '每跳关系分别有来源；连通路径本身不能证明合作、组织隶属或协调。'));
    }
    paint();
  }
  function chooseNode(node) { selected = node.account_key; selectedConnection = null; refreshExploration(); onNode(node); }
  function chooseEdge(edge) { selectedConnection = edge; selected = ''; paint(); onEdge(edge); }
  for (const edge of data.edges) {
    if (!byKey.has(edge.source_key) || !byKey.has(edge.target_key)) continue;
    const path = svgNode('path', { class: `network-edge ${edge.relationship === 'follows' ? 'follows' : 'associated'}`, tabindex: '0', role: 'button', 'aria-label': `${edge.source_key} ${relationshipNames[edge.relationship] || edge.relationship} ${edge.target_key}` });
    const hit = svgNode('path', { class: 'network-edge-hit', 'aria-hidden': 'true' });
    path.append(svgNode('title', {}, relationshipNames[edge.relationship] || edge.relationship));
    for (const target of [path, hit]) { target.addEventListener('click', () => chooseEdge(edge)); target.addEventListener('pointerenter', () => { hovered = edge.source_key; paint(); path.classList.add('highlighted'); }); target.addEventListener('pointerleave', () => { hovered = ''; paint(); }); }
    path.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); chooseEdge(edge); } });
    lines.append(hit, path); lineNodes.push([edge, path, hit, networkEdgeKey(edge)]);
  }
  for (const node of nodes) {
    const type = accountType(node), metric = structure.node_metrics[node.account_key];
    const group = svgNode('g', { class: `network-node type-${type} tier-${metric.tier}`, tabindex: '0', role: 'button', 'aria-label': `${node.name || node.handle}，${accountTypeName(node)}，${accountStateName(node)}，${graphTierNames[metric.tier]}结构。回车查看，方向键移动并固定，双击固定或释放。`, 'data-key': node.account_key, 'data-account-type': type, 'data-radius': node.radius, 'data-tier': metric.tier });
    group.append(svgNode('circle', { class: 'network-node-hit', r: Math.max(18, node.radius + 6) }), contour(type, node.radius + 5, 'network-node-halo'), contour(type, node.radius, `network-node-dot ${accountState(node) === 'verified' ? 'verified' : ''}`), svgNode('text', { class: 'network-node-name', y: 4 }), svgNode('title', {}, `${node.name || node.handle}\n${node.account_key}\n${accountTypeName(node)} · ${accountStateName(node)}\n${metric.degree} 个有来源邻居`));
    if (accountState(node) === 'verified') group.append(svgNode('path', { class: 'network-node-verification-mark', 'aria-hidden': 'true', d: `M${-node.radius * .38} 0L${-node.radius * .08} ${node.radius * .3}L${node.radius * .4} ${-node.radius * .3}` }));
    group.addEventListener('pointerdown', event => { if (drag || event.button !== 0 || event.isPrimary === false) return; event.preventDefault(); event.stopPropagation(); const p = point(event); autoFit = false; drag = { type: 'node', node, pointerId: event.pointerId, x: p.x, y: p.y, startX: p.x, startY: p.y, moved: false, wasPinned: node.pinned }; group.setPointerCapture(event.pointerId); reheat(true); });
    group.addEventListener('click', event => { if (group.dataset.dragged === 'true') { group.dataset.dragged = ''; return; } clearTimeout(selectTimer); if (event.detail > 1) return; selectTimer = setTimeout(() => chooseNode(node), 300); });
    group.addEventListener('dblclick', () => { clearTimeout(selectTimer); node.pinned = !node.pinned; paint(); reheat(true); });
    group.addEventListener('pointerenter', () => { hovered = node.account_key; paint(); });
    group.addEventListener('pointerleave', () => { if (!drag) { hovered = ''; paint(); } });
    group.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); chooseNode(node); } else if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) { event.preventDefault(); autoFit = false; node.pinned = true; node.x += event.key === 'ArrowLeft' ? -15 : event.key === 'ArrowRight' ? 15 : 0; node.y += event.key === 'ArrowUp' ? -15 : event.key === 'ArrowDown' ? 15 : 0; stepGraphLayout(layout, { held: node.account_key }); paint(); reheat(true); } });
    cards.append(group); cardNodes.set(node.account_key, group);
  }
  const point = event => { const p = svg.createSVGPoint(); p.x = event.clientX; p.y = event.clientY; return p.matrixTransform(svg.getScreenCTM().inverse()); };
  svg.addEventListener('pointerdown', event => { if (drag || event.button !== 0 || event.isPrimary === false || event.target.closest('.network-node,.network-edge,.network-edge-hit')) return; event.preventDefault(); const p = point(event); drag = { type: 'canvas', pointerId: event.pointerId, x: p.x, y: p.y }; svg.setPointerCapture(event.pointerId); });
  svg.addEventListener('pointermove', event => { if (!drag || event.pointerId !== drag.pointerId) return; const p = point(event), dx = p.x - drag.x, dy = p.y - drag.y; if (drag.type === 'node') { drag.node.x += dx / scale; drag.node.y += dy / scale; drag.node.vx = drag.node.vy = 0; drag.moved ||= Math.hypot(p.x - drag.startX, p.y - drag.startY) > 3; stepGraphLayout(layout, { held: drag.node.account_key }); paint(); reheat(true); } else { autoFit = false; tx += dx; ty += dy; transform(); } drag.x = p.x; drag.y = p.y; });
  const release = event => { if (!drag || event.pointerId !== drag.pointerId) return; const nodeDrag = drag.type === 'node'; if (nodeDrag) { cardNodes.get(drag.node.account_key).dataset.dragged = String(drag.moved); drag.node.pinned = drag.wasPinned; drag.node.vx = drag.node.vy = 0; } drag = null; hovered = ''; paint(); if (nodeDrag) reheat(true); };
  for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) svg.addEventListener(type, release);
  svg.addEventListener('wheel', event => { event.preventDefault(); zoom(scale * Math.exp(-event.deltaY * .0015), point(event)); }, { passive: false });
  function paint() {
    const focus = hovered || selected, neighbours = new Set(focus ? [focus] : selectedConnection ? [selectedConnection.source_key, selectedConnection.target_key] : []);
    if (focus) for (const [a, b] of layout.springs) { if (a.account_key === focus) neighbours.add(b.account_key); if (b.account_key === focus) neighbours.add(a.account_key); }
    const labels = graphLabelPlacements(nodes, scale, { selected, hovered });
    for (const node of nodes) {
      const group = cardNodes.get(node.account_key); if (!group) continue;
      group.setAttribute('transform', `translate(${node.x.toFixed(2)} ${node.y.toFixed(2)})`); group.classList.toggle('selected', node.account_key === selected); group.classList.toggle('highlighted', node.account_key === hovered); group.classList.toggle('dimmed', emphasis.active ? !emphasis.nodeKeys.has(node.account_key) : Boolean(neighbours.size && !neighbours.has(node.account_key))); group.classList.toggle('pinned', node.pinned);
      group.classList.toggle('emphasized', emphasis.active && emphasis.nodeKeys.has(node.account_key));
      group.setAttribute('aria-pressed', String(node.account_key === selected)); group.dataset.pinned = String(node.pinned);
      const text = group.querySelector('text'), placement = labels.get(node.account_key); text.classList.toggle('label-hidden', !placement); if (placement) { text.textContent = placement.text; text.setAttribute('x', placement.x); text.setAttribute('text-anchor', placement.anchor); text.style.fontSize = `${placement.font}px`; }
      positions.set(node.account_key, { x: node.x, y: node.y, pinned: node.pinned });
    }
    for (const [edge, path, hit, semanticKey] of lineNodes) {
      const a = byKey.get(edge.source_key), b = byKey.get(edge.target_key), dx = b.x - a.x, dy = b.y - a.y, length = Math.max(1, Math.hypot(dx, dy));
      const d = `M${a.x + dx / length * (a.radius + 1)} ${a.y + dy / length * (a.radius + 1)}L${b.x - dx / length * (b.radius + 2)} ${b.y - dy / length * (b.radius + 2)}`; path.setAttribute('d', d); hit.setAttribute('d', d);
      const highlighted = focus ? edge.source_key === focus || edge.target_key === focus : edge === selectedConnection;
      const emphasized = emphasis.active && emphasis.edgeKeys.has(semanticKey), inPath = Boolean(emphasis.path && emphasized);
      path.classList.toggle('highlighted', Boolean(highlighted || emphasized)); path.classList.toggle('path-highlighted', inPath); path.classList.toggle('dimmed', emphasis.active ? !emphasized : Boolean(neighbours.size && !highlighted)); if (highlighted || emphasized) path.setAttribute('marker-end', `url(#${id})`); else path.removeAttribute('marker-end');
    }
  }
  function tick() {
    frame = 0;
    if (dead || !root.isConnected || signal?.aborted || (!moving && !interactionSettling)) return;
    const held = drag?.type === 'node' ? drag.node.account_key : '';
    const confirming = motion.quiet >= 30, started = performance.now(), deadline = started + 4;
    let movement = 0, steps = 0, confirmationDisplacement = 0;
    // Bound each chunk by work time so larger networks keep accepting gestures.
    // Confirmation can check many tiny steps quickly, but renewed motion exits
    // the chunk immediately and returns to normal animated settling.
    for (let i = 0; i < graphMotionStepLimit(motion, nodes.length); i++) {
      movement = Math.max(movement, stepGraphLayout(layout, { held, alpha: motion.alpha })); totalSteps++; steps++;
      if (confirming) confirmationDisplacement = Math.max(confirmationDisplacement,graphConfirmationDisplacement(motion,nodes));
      if ((confirming && movement > GRAPH_STABLE_MOVEMENT) || ((confirming || nodes.length >= 100) && performance.now() >= deadline)) break;
    }
    advanceGraphMotion(motion, movement, steps, nodes, confirmationDisplacement); paint();
    root.dataset.layoutFrameWorkMs = String(performance.now() - started);
    if (motion.stable) {
      for (const node of nodes) node.vx = node.vy = 0;
      interactionSettling = false;
      if (autoFit) { fit(); autoFit = false; }
      status(held ? 'held-stable' : 'stable');
    } else { status(held ? 'dragging' : motion.quiet >= 30 ? 'confirming' : 'settling'); frame = requestAnimationFrame(tick); }
  }
  function start() { if (!frame && (moving || interactionSettling) && !dead) frame = requestAnimationFrame(tick); }
  function reheat(interaction = false) { motion = createGraphMotion(); interactionSettling ||= interaction; status(drag?.type === 'node' ? 'dragging' : 'settling'); start(); }
  root.addEventListener('graph-clear-selection', () => { selected = hovered = ''; selectedConnection = null; refreshExploration(); });
  const timer = setTimeout(start, 0);
  signal?.addEventListener('abort', () => { dead = true; cancelAnimationFrame(frame); clearTimeout(timer); clearTimeout(selectTimer); }, { once: true });
  status(moving || interactionSettling ? 'settling' : 'paused'); refreshExploration(); if (restoredViewport) transform(); else fit();
  return root;
}
