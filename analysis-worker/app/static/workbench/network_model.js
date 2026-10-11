export const relationshipNames = { follows: '关注', mentions: '正文提及', quotes: '引用', replies: '回复', reposts: '转发', shared_link: '共同外链', shared_url: '共同外链', shared_event: '共同活动线索', co_event: '共同活动线索', declared_affiliation: '公开隶属声明', affiliation: '隶属线索', profile_link: '主页链接', co_occurrence: '共同出现', coordination: '联动候选' };
export const nodeStateNames = { queued: '待探索', pending: '待探索', discovered: '已发现', running: '探索中', visited: '已探索', complete: '已探索', completed: '已探索', skipped: '停止分支', blocked: '无法访问', failed: '分支失败' };
export function accountState(node = {}) {
  const status = node.account_status || node.status;
  return status === 'verified' || status === 'excluded' ? status : 'candidate';
}
export function accountStateName(node) {
  return { verified: '已核验', excluded: '已排除', candidate: '候选账号' }[accountState(node)];
}
export function selectGraph(graph, { query = '', platform = '', relationship = '', maxDepth = 3 } = {}) {
  const term = query.toLocaleLowerCase();
  let nodes = (graph.nodes || []).filter(node => (!platform || node.platform === platform) && Number(node.depth || 0) <= maxDepth && (!term || [node.name, node.handle, node.account_key].join(' ').toLocaleLowerCase().includes(term)));
  if (relationship) {
    const related = new Set((graph.edges || []).filter(edge => edge.relationship === relationship).flatMap(edge => [edge.source_key, edge.target_key]));
    nodes = nodes.filter(node => related.has(node.account_key));
  }
  const keys = new Set(nodes.map(node => node.account_key));
  const edges = (graph.edges || []).filter(edge => keys.has(edge.source_key) && keys.has(edge.target_key) && (!relationship || edge.relationship === relationship));
  return { ...graph, nodes, edges, claims: (graph.claims || []).filter(claim => keys.has(claim.subject_key)) };
}

// A discovery path is a tree even when the account relationship graph cycles.
// Missing filtered parents become explicit roots; every node appears once.
export function discoveryForest(nodes) {
  const values = new Map(nodes.map(node => [node.account_key, { ...node, children: [] }]));
  const roots = [];
  for (const node of values.values()) {
    const parent = values.get(node.parent_key);
    const seen = new Set([node.account_key]);
    let cursor = parent, cycle = false;
    while (cursor) {
      if (seen.has(cursor.account_key)) { cycle = true; break; }
      seen.add(cursor.account_key); cursor = values.get(cursor.parent_key);
    }
    if (parent && !cycle) parent.children.push(node);
    else roots.push({ ...node, parentOutsideView: Boolean(node.parent_key && !parent), cycle });
  }
  const sort = list => { list.sort((a, b) => Number(a.depth || 0) - Number(b.depth || 0) || Number(b.score || 0) - Number(a.score || 0) || a.account_key.localeCompare(b.account_key)); list.forEach(node => sort(node.children)); };
  sort(roots); return roots;
}

export function graphPositions(nodes) {
  const levels = new Map();
  for (const node of nodes) { const depth = Math.max(0, Number(node.depth) || 0); if (!levels.has(depth)) levels.set(depth, []); levels.get(depth).push(node); }
  const positions = new Map();
  for (const [depth, layer] of levels) layer.forEach((node, index) => positions.set(node.account_key, { x: 38 + depth * 244, y: 42 + index * 91 }));
  return { positions, width: Math.max(680, 260 + Math.max(0, ...levels.keys()) * 244), height: Math.max(430, 70 + Math.max(0, ...[...levels.values()].map(layer => layer.length)) * 91) };
}

export const accountTypeNames = { organization: '组织', individual: '个人', unknown: '未判定' };
export const graphTierNames = { core: '重点', bridge: '桥接', peripheral: '外围' };
export function accountType(node = {}) {
  return Object.hasOwn(accountTypeNames, node.account_type) ? node.account_type : 'unknown';
}
export function accountTypeName(node) { return accountTypeNames[accountType(node)]; }
export function publicEvidenceURLs(value = {}) {
  const result = new Set();
  for (const candidate of [...(Array.isArray(value.evidence_urls) ? value.evidence_urls : []), value.evidence_url]) {
    if (typeof candidate !== 'string' || /\s/.test(candidate) || !/^https:\/\//i.test(candidate) || candidate.includes('\\')) continue;
    try {
      const url = new URL(candidate);
      if (url.protocol === 'https:' && !url.port && !url.username && !url.password) result.add(candidate);
    } catch { /* A malformed saved link cannot support a clickable path. */ }
  }
  return [...result];
}
export function accountTypeInfo(node = {}) {
  const details = node.account_type_details || {};
  const legacyEvidence = accountType(node) !== 'unknown' && typeof node.type_basis === 'string' && node.type_basis
    ? node.evidence_urls : [];
  return { type: accountType(node), label: accountTypeName(node),
    basis: typeof details.basis === 'string' ? details.basis : typeof node.type_basis === 'string' ? node.type_basis : '',
    confidence_scope: typeof details.confidence_scope === 'string' ? details.confidence_scope : '',
    manual_locked: details.manual_locked === true, reviewed_at: details.reviewed_at || '',
    note: typeof details.note === 'string' ? details.note : '',
    evidence_urls: publicEvidenceURLs({ evidence_urls: details.evidence_urls || node.type_evidence_urls || legacyEvidence }) };
}
export function accountTypeBasisName(info = {}) {
  const labels = { manual_review: '人工核对', manual: '人工核对', evidence_review: 'Agent 公开证据审核', registered_source_backlink: '已登记官网的准确账号回链',
    public_profile_statement: '公开主页自述', insufficient_source: '依据不足', unknown: '依据不足' };
  return Object.hasOwn(labels, info.basis) ? labels[info.basis] : info.basis ? '保存的公开类型说明' : '尚未登记可核对依据';
}
// Exact namespace/handle matches only; a popular or similarly named account is not a platform account.
export const generalPlatformKeys = new Set(['twitter:youtube', 'twitter:teamyoutube', 'twitter:google', 'twitter:instagram', 'twitter:x', 'instagram:instagram']);
export function explicitResearchRoots(params = {}) {
  const roots = new Set(), reserved = new Set(['home', 'search', 'explore', 'i', 'intent', 'login', 'signup', 'accounts', 'p', 'reel', 'reels', 'stories', 'direct', 'about', 'privacy', 'settings', 'following', 'followers', 'web', 'api']);
  const patterns = { twitter: /^[A-Za-z0-9_]{1,15}$/, instagram: /^[A-Za-z0-9_.]{1,30}$/ };
  function key(value, source = '', homepageOnly = false) {
    if (typeof value !== 'string') return '';
    let platform = source, handle = value.trim().replace(/^@+/, '');
    if (/^[A-Za-z][A-Za-z\d+.-]*:/.test(value)) {
      if (!publicEvidenceURLs({ evidence_url: value }).length) return '';
      const url = new URL(value), host = url.hostname.toLowerCase();
      platform = ['x.com', 'www.x.com', 'twitter.com', 'www.twitter.com'].includes(host) ? 'twitter'
        : ['instagram.com', 'www.instagram.com'].includes(host) ? 'instagram' : '';
      if (!platform || source && platform !== source || !/^\/[^/]+\/?$/.test(url.pathname)) return '';
      handle = url.pathname.replace(/^\/|\/$/g, '');
    } else if (homepageOnly) return '';
    if (!Object.hasOwn(patterns, platform) || !patterns[platform].test(handle) || reserved.has(handle.toLowerCase())) return '';
    return `${platform}:${handle.toLowerCase()}`;
  }
  if (params.mode === 'account') { const root = key(params.handle, params.source); if (root) roots.add(root); }
  if (Array.isArray(params.seed_accounts)) for (const seed of params.seed_accounts) {
    const root = key(seed, '', true) || key(seed, params.source);
    if (root) roots.add(root);
  }
  return roots;
}
export function researchRelevance(node = {}, { explicitRootKey = '', explicitRootKeys = [] } = {}) {
  const explicit = Boolean(explicitRootKey && explicitRootKey === node.account_key || [...explicitRootKeys].includes(node.account_key));
  if (accountState(node) === 'excluded' || node.status === 'excluded') return { state: 'excluded', default_hidden: true, skip_expansion: true, basis: 'manual_status', reason: '此账号已人工排除；原始材料仍保留。' };
  const saved = node.research_relevance;
  if (saved && ['general_platform', 'excluded', 'included', 'unknown'].includes(saved.state)) {
    const state = saved.state;
    if (state !== 'excluded' && explicit) return { state: 'included', default_hidden: false, skip_expansion: false, basis: 'explicit_root', reason: '用户明确指定的研究起点；不代表主题相关性、身份或隶属已获证明。' };
    return { state, default_hidden: state === 'excluded' || state === 'general_platform' && saved.default_hidden !== false,
      skip_expansion: state === 'excluded' || saved.skip_expansion === true,
      basis: typeof saved.basis === 'string' ? saved.basis : '', reason: typeof saved.reason === 'string' ? saved.reason : '' };
  }
  const patterns = { twitter: /^[A-Za-z0-9_]{1,15}$/, instagram: /^[A-Za-z0-9_.]{1,30}$/ };
  const fieldsPresent = node.platform != null || node.handle != null;
  const handle = typeof node.handle === 'string' ? node.handle.trim().replace(/^@+/, '') : '';
  const fieldKey = Object.hasOwn(patterns, node.platform) && patterns[node.platform].test(handle) ? `${node.platform}:${handle.toLowerCase()}` : '';
  const raw = node.account_key;
  const parts = typeof raw === 'string' ? raw.split(':') : [];
  const key = raw == null ? fieldKey : parts.length === 2 && Object.hasOwn(patterns, parts[0]) && patterns[parts[0]].test(parts[1]) ? `${parts[0]}:${parts[1].toLowerCase()}` : '';
  const consistent = Boolean(key) && (raw == null || !fieldsPresent || fieldKey === key);
  if (explicit) return { state: 'included', default_hidden: false, skip_expansion: false, basis: 'explicit_root', reason: '用户明确指定的研究起点；不代表主题相关性、身份或隶属已获证明。' };
  if (consistent && generalPlatformKeys.has(key)) {
    return { state: 'general_platform', default_hidden: true, skip_expansion: true,
      basis: 'exact_platform_handle', reason: '通用官方平台账号；默认不作为本次研究拓展线索。' };
  }
  return { state: 'unknown', default_hidden: false, skip_expansion: false, basis: consistent ? 'no_explicit_signal' : 'identity_conflict', reason: '' };
}
export function selectResearchGraph(graph, { showGeneral = graph.relevance_scope?.show_general === true, explicitRootKey = graph.relevance_scope?.explicit_root_key || '', explicitRootKeys = graph.relevance_scope?.explicit_root_keys || [] } = {}) {
  let hidden_general = 0, hidden_excluded = 0, shown_general = 0;
  const nodes = (graph.nodes || []).filter(node => {
    const relevance = researchRelevance(node, { explicitRootKey, explicitRootKeys });
    if (relevance.state === 'excluded') { hidden_excluded++; return false; }
    if (relevance.state === 'general_platform') {
      if (relevance.default_hidden && !showGeneral) { hidden_general++; return false; }
      shown_general++;
    }
    return true;
  });
  const keys = new Set(nodes.map(node => node.account_key));
  return { ...graph, nodes, edges: (graph.edges || []).filter(edge => keys.has(edge.source_key) && keys.has(edge.target_key)),
    claims: (graph.claims || []).filter(claim => keys.has(claim.subject_key)),
    relevance_scope: { show_general: showGeneral === true, explicit_root_key: explicitRootKey, explicit_root_keys: [...explicitRootKeys],
      available_nodes: (graph.nodes || []).length, hidden_general, hidden_excluded, shown_general } };
}
export const networkEdgeKey = edge => JSON.stringify([edge.source_key, edge.target_key, edge.relationship ?? edge.relation, edge.id || edge.basis || '']);
const hopLimit = value => Number.isFinite(Number(value)) ? Math.max(1, Math.min(3, Math.floor(Number(value)))) : 1;
function observedGraph(graph, direction = 'all') {
  const visible = selectResearchGraph(graph);
  const keys = new Set((visible.nodes || []).map(node => node.account_key).filter(key => typeof key === 'string' && key));
  const adjacency = new Map([...keys].map(key => [key, []]));
  const edges = (graph.edges || []).filter(edge => edge && !Array.isArray(edge) && keys.has(edge.source_key) && keys.has(edge.target_key)
    && edge.source_key !== edge.target_key && (direction === 'all' || (edge.relationship ?? edge.relation) === 'follows')
    && ['observed', 'reported'].includes(edge.basis === undefined ? 'observed' : edge.basis)
    && typeof (edge.relationship ?? edge.relation) === 'string' && (edge.relationship ?? edge.relation)
    && publicEvidenceURLs(edge).length);
  for (const edge of edges) {
    if (direction !== 'in') adjacency.get(edge.source_key).push({ edge, to: edge.target_key, reversed: false });
    if (direction !== 'out') adjacency.get(edge.target_key).push({ edge, to: edge.source_key, reversed: true });
  }
  const compare = (a, b) => a < b ? -1 : a > b ? 1 : 0;
  for (const adjacent of adjacency.values()) adjacent.sort((a, b) => compare(a.to, b.to)
    || compare(a.edge.relationship || a.edge.relation, b.edge.relationship || b.edge.relation) || compare(networkEdgeKey(a.edge), networkEdgeKey(b.edge)));
  return { keys, adjacency, edges };
}
export function graphReachability(graph, { startKey = '', direction = 'all', maxHops = 1 } = {}) {
  direction = ['out', 'in'].includes(direction) ? direction : 'all';
  const { keys, adjacency } = observedGraph(graph, direction);
  const distances = new Map(), parents = new Map(), edges = new Set(), queue = [];
  const hops = hopLimit(maxHops);
  if (keys.has(startKey)) { distances.set(startKey, 0); queue.push(startKey); }
  for (let index = 0; index < queue.length; index++) {
    const from = queue[index], distance = distances.get(from);
    if (distance >= hops) continue;
    for (const step of adjacency.get(from)) {
      edges.add(step.edge);
      if (distances.has(step.to)) continue;
      distances.set(step.to, distance + 1); parents.set(step.to, { ...step, from }); queue.push(step.to);
    }
  }
  return { nodeKeys: new Set(distances.keys()), distances, parents, edges: [...edges], maxHops: hops, direction };
}
export function findGraphPath(graph, sourceKey, targetKey, options = {}) {
  if (!sourceKey || !targetKey || sourceKey === targetKey) return null;
  const reached = graphReachability(graph, { ...options, startKey: sourceKey });
  if (!reached.distances.has(targetKey)) return null;
  const node_keys = [targetKey], steps = [];
  let cursor = targetKey;
  while (cursor !== sourceKey) {
    const step = reached.parents.get(cursor);
    if (!step) return null;
    steps.unshift({ edge_id: step.edge.id || '', edge_key: networkEdgeKey(step.edge), edge: step.edge,
      source_key: step.edge.source_key, target_key: step.edge.target_key, relationship: step.edge.relationship ?? step.edge.relation,
      basis: step.edge.basis === undefined ? 'observed' : step.edge.basis, evidence_urls: publicEvidenceURLs(step.edge), reversed: step.reversed });
    cursor = step.from; node_keys.unshift(cursor);
  }
  return { source_key: sourceKey, target_key: targetKey, node_keys, steps, hops: steps.length };
}

// These are properties of saved visible relations, not identity or importance claims.
// Iterative articulation also handles long sparse account chains.
export function graphStructure(graph) {
  const visible = selectResearchGraph(graph);
  const rankingNodes = visible.nodes.filter(node => researchRelevance(node, { explicitRootKey: visible.relevance_scope.explicit_root_key, explicitRootKeys: visible.relevance_scope.explicit_root_keys }).state !== 'general_platform');
  const { keys, edges } = observedGraph({ ...visible, nodes: rankingNodes });
  const neighbours = new Map([...keys].map(key => [key, new Set()]));
  const incoming = new Map([...keys].map(key => [key, new Set()])), outgoing = new Map([...keys].map(key => [key, new Set()]));
  const followers = new Map([...keys].map(key => [key, new Set()])), following = new Map([...keys].map(key => [key, new Set()]));
  for (const edge of edges) {
    neighbours.get(edge.source_key).add(edge.target_key); neighbours.get(edge.target_key).add(edge.source_key);
    outgoing.get(edge.source_key).add(edge.target_key); incoming.get(edge.target_key).add(edge.source_key);
    if ((edge.relationship ?? edge.relation) === 'follows') { following.get(edge.source_key).add(edge.target_key); followers.get(edge.target_key).add(edge.source_key); }
  }
  const entered = new Map(), low = new Map(), parent = new Map(), children = new Map(), bridges = new Set();
  let order = 0;
  for (const start of keys) {
    if (entered.has(start)) continue;
    entered.set(start, ++order); low.set(start, order);
    const stack = [{ key: start, adjacent: [...neighbours.get(start)], index: 0 }];
    while (stack.length) {
      const frame = stack[stack.length - 1], key = frame.key;
      if (frame.index < frame.adjacent.length) {
        const next = frame.adjacent[frame.index++];
        if (!entered.has(next)) {
          parent.set(next, key); children.set(key, (children.get(key) || 0) + 1);
          entered.set(next, ++order); low.set(next, order); stack.push({ key: next, adjacent: [...neighbours.get(next)], index: 0 });
        } else if (next !== parent.get(key)) low.set(key, Math.min(low.get(key), entered.get(next)));
      } else {
        stack.pop();
        const previous = parent.get(key);
        if (previous !== undefined) {
          low.set(previous, Math.min(low.get(previous), low.get(key)));
          if (parent.has(previous) && low.get(key) >= entered.get(previous)) bridges.add(previous);
        } else if ((children.get(key) || 0) > 1) bridges.add(key);
      }
    }
  }
  const degrees = [...neighbours.values()].map(values => values.size).sort((a, b) => a - b);
  const threshold = Math.max(3, degrees[Math.floor(Math.max(0, keys.size - 1) * .75)] || 0);
  const types = new Map((graph.nodes || []).map(node => [node.account_key, accountType(node)]));
  const node_metrics = Object.create(null);
  for (const key of keys) {
    const degree = neighbours.get(key).size, structural_bridge = bridges.has(key);
    const tier = structural_bridge ? 'bridge' : degree >= threshold ? 'core' : 'peripheral';
    const organization_contacts = [...neighbours.get(key)].filter(next => types.get(next) === 'organization').sort();
    const reasons = [structural_bridge ? '暂移此节点会拆开当前显示的关系分支。'
      : degree >= threshold ? `当前图中有 ${degree} 个不同邻居，达到本次显示范围的重点连接阈值 ${threshold}。`
        : degree ? '当前显示结构中的外围节点；仍保留供后续核对。' : '当前显示范围没有已保存的关系边。'];
    if (organization_contacts.length >= 2) reasons.push('直接连接至少两个已标明类型的组织账号；账号可能属于同一组织，仍需逐条核对。');
    node_metrics[key] = { tier, reasons, degree, in_degree: incoming.get(key).size, out_degree: outgoing.get(key).size,
      following_count: following.get(key).size, followers_count: followers.get(key).size,
      structural_bridge, isolated: degree === 0, organization_bridge_candidate: organization_contacts.length >= 2, organization_contacts };
  }
  const saved = graph.insights?.node_metrics;
  if (saved && typeof saved === 'object' && !Array.isArray(saved) && Object.keys(saved).length === keys.size
    && [...keys].every(key => Object.hasOwn(saved, key))) {
    for (const key of keys) {
      const actual = node_metrics[key], value = saved[key];
      if (!value || !Object.hasOwn(graphTierNames, value.tier) || value.degree !== actual.degree
        || value.in_degree !== actual.in_degree || value.out_degree !== actual.out_degree
        || value.structural_bridge !== actual.structural_bridge) continue;
      const reasons = (Array.isArray(value.reasons) ? value.reasons : typeof value.reason === 'string' ? [value.reason] : [])
        .filter(reason => typeof reason === 'string' && reason.trim()).map(reason => reason.slice(0, 2000));
      if (reasons.length) Object.assign(actual, { tier: value.tier, reasons, basis: 'saved_graph_insights' });
    }
  }
  for (const node of visible.nodes) if (!Object.hasOwn(node_metrics, node.account_key)) {
    node_metrics[node.account_key] = { tier: 'peripheral', reasons: ['通用平台账号仅供核对原始关系链，不参与重点或桥接排名。'],
      degree: 0, in_degree: 0, out_degree: 0, following_count: 0, followers_count: 0, structural_bridge: false,
      isolated: false, organization_bridge_candidate: false, organization_contacts: [], ranking_excluded: true };
  }
  const displayed = visible.nodes.length;
  const total = Number.isSafeInteger(graph.total_nodes) && graph.total_nodes >= displayed ? graph.total_nodes : displayed;
  return { node_metrics, scope: { displayed_nodes: displayed, ranking_nodes: keys.size, total_nodes: total, partial: total > displayed, basis: 'displayed_saved_relations' } };
}
export function graphEmphasis(graph, { startKey = '', direction = 'all', maxHops = null, tier = 'all', endKey = '', structure } = {}) {
  const local = maxHops !== null && maxHops !== '' || ['out', 'in'].includes(direction);
  const reached = local ? graphReachability(graph, { startKey, direction, maxHops: maxHops || 1 }) : null;
  let nodeKeys = reached ? reached.nodeKeys : observedGraph(graph).keys;
  let edges = reached ? reached.edges : observedGraph(graph).edges;
  const metrics = (structure || graphStructure(graph)).node_metrics;
  if (Object.hasOwn(graphTierNames, tier)) {
    nodeKeys = new Set([...nodeKeys].filter(key => metrics[key]?.tier === tier || key === startKey));
    edges = edges.filter(edge => nodeKeys.has(edge.source_key) && nodeKeys.has(edge.target_key));
  }
  const path = endKey ? findGraphPath(graph, startKey, endKey, { direction, maxHops: maxHops || 3 }) : null;
  if (endKey) { nodeKeys = new Set(path?.node_keys || (startKey ? [startKey] : [])); edges = path?.steps.map(step => step.edge) || []; }
  return { active: local || tier !== 'all' || Boolean(endKey), nodeKeys, edgeKeys: new Set(edges.map(networkEdgeKey)),
    edges, path, distances: reached?.distances || new Map(), metrics };
}
