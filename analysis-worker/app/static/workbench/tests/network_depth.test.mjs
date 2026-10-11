import test from 'node:test';
import assert from 'node:assert/strict';
import * as model from '../network_model.js';

const node = (key, extra = {}) => ({ account_key: key, name: key, ...extra });
const edge = (a, b, relationship = 'follows', extra = {}) => ({ source_key: a, target_key: b, relationship, evidence_urls: [`https://example.org/${a}/${b}`], ...extra });
const graph = { nodes: ['a', 'b', 'c', 'd', 'x'].map(key => node(key)),
  edges: [edge('a', 'b'), edge('b', 'c'), edge('c', 'd'), edge('x', 'a'), edge('a', 'd', 'mentions')] };

test('account type never derives from verification, name or a following relation', () => {
  assert.equal(model.accountType(node('a', { account_status: 'verified', name: 'Official Association' })), 'unknown');
  assert.equal(model.accountType(node('a', { account_type: 'organization', account_status: 'candidate' })), 'organization');
  assert.equal(model.accountType(node('a', { account_type: 'individual', account_status: 'excluded' })), 'individual');
  assert.equal(model.accountType(node('a', { account_type: 'ORGANIZATION' })), 'unknown');
  assert.equal(model.accountTypeName({ account_type: 'organization' }), '组织');
  assert.equal(model.accountTypeName({ account_type: 'individual' }), '个人');
  assert.equal(model.accountTypeName({}), '未判定');
});
test('type evidence is separate, remains public and cannot grant verified status', () => {
  const value = node('a', { account_type: 'individual', type_basis: 'public_profile_statement',
    type_evidence_urls: ['https://example.org/profile', 'javascript:alert(1)', 'https://user:password@example.org/private'],
    evidence_urls: ['https://example.org/unrelated-following'], account_status: 'candidate' });
  assert.deepEqual(model.accountTypeInfo(value).evidence_urls, ['https://example.org/profile']);
  assert.equal(model.accountTypeInfo(value).basis, 'public_profile_statement');
  assert.equal(model.accountStateName(value), '候选账号');
});
test('public evidence URL selection excludes active schemes, credentials and duplicates', () => {
  assert.deepEqual(model.publicEvidenceURLs({ evidence_urls: ['https://example.org/post', 'https://example.org/post',
    'data:text/html,bad', 'javascript:alert(1)', 'https://user:password@example.org/post', null] }), ['https://example.org/post']);
});
test('outgoing emphasis includes only actual follow edges and exactly bounded hops', () => {
  const result = model.graphReachability(graph, { startKey: 'a', direction: 'out', maxHops: 2 });
  assert.deepEqual([...result.distances], [['a', 0], ['b', 1], ['c', 2]]);
  assert.equal(result.edges.length, 2);
  assert.ok(!result.nodeKeys.has('d') && !result.nodeKeys.has('x'));
});
test('incoming following preserves the original observed arrow', () => {
  const result = model.findGraphPath(graph, 'c', 'a', { direction: 'in', maxHops: 2 });
  assert.deepEqual(result.node_keys, ['c', 'b', 'a']);
  assert.deepEqual(result.steps.map(step => [step.source_key, step.target_key, step.reversed]), [['b', 'c', true], ['a', 'b', true]]);
  assert.equal(model.findGraphPath(graph, 'a', 'c', { direction: 'in', maxHops: 3 }), null);
});
test('all-relations paths can traverse reverse edges without inventing a new arrow', () => {
  const result = model.findGraphPath(graph, 'b', 'x', { maxHops: 2 });
  assert.deepEqual(result.node_keys, ['b', 'a', 'x']);
  assert.equal(result.steps[0].source_key, 'a');
  assert.equal(result.steps[0].target_key, 'b');
  assert.equal(result.steps[0].reversed, true);
});
test('shortest path is deterministic and carries only the actual step evidence', () => {
  const result = model.findGraphPath(graph, 'a', 'd', { direction: 'out', maxHops: 3 });
  assert.equal(result.hops, 3);
  assert.deepEqual(result.node_keys, ['a', 'b', 'c', 'd']);
  assert.deepEqual(result.steps.map(step => step.evidence_urls), [['https://example.org/a/b'], ['https://example.org/b/c'], ['https://example.org/c/d']]);
  assert.equal(model.findGraphPath(graph, 'a', 'd', { direction: 'out', maxHops: 2 }), null);
});
test('missing source evidence cannot become an evidence-backed path', () => {
  const data = { nodes: ['a', 'b', 'c'].map(key => node(key)), edges: [edge('a', 'b', 'follows', { evidence_urls: [] }), edge('b', 'c')] };
  assert.equal(model.findGraphPath(data, 'a', 'c', { maxHops: 3 }), null);
  assert.deepEqual([...model.graphReachability(data, { startKey: 'a', maxHops: 3 }).nodeKeys], ['a']);
});
test('dangling endpoints, self loops, cycles and parallel records do not fabricate nodes or extend a path', () => {
  const data = { nodes: ['a', 'b', 'c'].map(key => node(key)), edges: [edge('outside', 'a'), edge('a', 'a'),
    edge('a', 'b'), edge('a', 'b'), edge('b', 'c'), edge('c', 'a')] };
  const result = model.graphReachability(data, { startKey: 'a', direction: 'out', maxHops: 3 });
  assert.deepEqual([...result.nodeKeys], ['a', 'b', 'c']);
  assert.equal(result.distances.get('c'), 2);
  assert.equal(model.findGraphPath(data, 'a', 'outside', { maxHops: 3 }), null);
  assert.equal(model.findGraphPath(data, 'a', 'a', { maxHops: 3 }), null);
});
test('hops clamp to 1–3, saved discovery depth is independent and inputs stay untouched', () => {
  const data = structuredClone(graph); data.nodes.forEach(value => { value.depth = 99; });
  const before = JSON.stringify(data);
  assert.equal(model.findGraphPath(data, 'a', 'd', { direction: 'out', maxHops: 999 }).hops, 3);
  assert.equal(model.findGraphPath(data, 'a', 'c', { direction: 'out', maxHops: 0 }), null);
  model.graphStructure(data);
  assert.equal(JSON.stringify(data), before);
});
test('a visible articulation is structural bridge even when its degree is modest', () => {
  const data = { nodes: ['a', 'b', 'c', 'd'].map(key => node(key)), edges: [edge('a', 'b'), edge('b', 'c'), edge('c', 'd')] };
  const metrics = model.graphStructure(data).node_metrics;
  assert.equal(metrics.b.structural_bridge, true);
  assert.equal(metrics.b.tier, 'bridge');
  assert.equal(metrics.a.tier, 'peripheral');
  assert.equal(metrics.b.degree, 2);
  assert.ok(metrics.b.reasons.length);
});
test('a connected triangle has no bridge and remains below the focus degree threshold', () => {
  const data = { nodes: ['a', 'b', 'c', 'd'].map(key => node(key)), edges: [edge('a', 'b'), edge('b', 'c'), edge('c', 'a')] };
  const metrics = model.graphStructure(data).node_metrics;
  for (const key of ['a', 'b', 'c']) { assert.equal(metrics[key].tier, 'peripheral'); assert.equal(metrics[key].structural_bridge, false); }
  assert.equal(metrics.d.tier, 'peripheral');
});
test('inferred, missing, HTTP, credential and unusual-port support cannot enter paths, tiers or following emphasis', () => {
  const data = { nodes: ['a', 'b', 'c', 'd', 'e', 'f'].map(key => node(key)), edges: [
    edge('a', 'b', 'follows', { basis: 'inferred' }), edge('a', 'c', 'follows', { evidence_urls: [] }),
    edge('a', 'd', 'follows', { evidence_urls: ['http://example.org/post'] }),
    edge('a', 'e', 'follows', { evidence_urls: ['https://example.org:444/post'] }),
    edge('a', 'f', 'follows', { evidence_urls: ['https://user:password@example.org/post'] })] };
  assert.equal(model.graphStructure(data).node_metrics.a.degree, 0);
  assert.deepEqual([...model.graphReachability(data, { startKey: 'a', direction: 'out', maxHops: 3 }).nodeKeys], ['a']);
  for (const key of ['b', 'c', 'd', 'e', 'f']) assert.equal(model.findGraphPath(data, 'a', key), null);
  assert.deepEqual(model.publicEvidenceURLs({ evidence_urls: [' https://example.org/post', 'https://example.org/post with space',
    'https://example.org:444/post', 'http://example.org/post'] }), []);
});
test('legacy observed edges accept a singular public evidence link and reported basis stays explicit', () => {
  const data = { nodes: ['a', 'b'].map(key => node(key)), edges: [edge('a', 'b', 'mentions', { basis: 'reported', evidence_urls: [], evidence_url: 'https://example.org/statement' })] };
  const path = model.findGraphPath(data, 'a', 'b');
  assert.equal(path.steps[0].basis, 'reported');
  assert.deepEqual(path.steps[0].evidence_urls, ['https://example.org/statement']);
});
test('equal-length paths select the same ordered evidence chain after an input reorder', () => {
  const data = { nodes: ['a', 'b', 'c', 'd'].map(key => node(key)), edges: [edge('a', 'c'), edge('c', 'd'), edge('a', 'b'), edge('b', 'd')] };
  const first = model.findGraphPath(data, 'a', 'd', { direction: 'out', maxHops: 3 });
  const second = model.findGraphPath({ ...data, edges: [...data.edges].reverse() }, 'a', 'd', { direction: 'out', maxHops: 3 });
  assert.deepEqual(first.node_keys, ['a', 'b', 'd']);
  assert.deepEqual(first.steps.map(step => step.evidence_urls), second.steps.map(step => step.evidence_urls));
});
test('a four-node clique reaches the structural focus threshold without a bridge', () => {
  const keys = ['a', 'b', 'c', 'd'];
  const data = { nodes: keys.map(key => node(key)), edges: keys.flatMap((a, index) => keys.slice(index + 1).map(b => edge(a, b))) };
  for (const metric of Object.values(model.graphStructure(data).node_metrics)) { assert.equal(metric.tier, 'core'); assert.equal(metric.structural_bridge, false); }
});
test('contacts with two organization accounts remain a structural candidate, not two distinct organizations or affiliation', () => {
  const data = { nodes: [node('a'), node('b', { account_type: 'organization' }), node('c', { account_type: 'organization' })],
    edges: [edge('a', 'b'), edge('a', 'c')] };
  const metric = model.graphStructure(data).node_metrics.a;
  assert.equal(metric.organization_bridge_candidate, true);
  assert.deepEqual(metric.organization_contacts, ['b', 'c']);
  assert.ok(metric.reasons.some(reason => reason.includes('可能属于同一组织')));
  assert.ok(!('affiliation' in metric));
});
test('degree counts distinct observed neighbours and never manufactures identity or affiliation', () => {
  const data = { nodes: [node('a', { account_status: 'candidate' }), node('b')], edges: [edge('a', 'b'), edge('a', 'b', 'mentions'), edge('a', 'a'), edge('outside', 'a')] };
  const metric = model.graphStructure(data).node_metrics.a;
  assert.equal(metric.degree, 1); assert.equal(metric.out_degree, 1); assert.equal(metric.in_degree, 0);
  assert.equal(metric.structural_bridge, false);
  assert.equal(model.accountStateName(data.nodes[0]), '候选账号');
  assert.equal(model.accountType(data.nodes[0]), 'unknown');
  assert.ok(!('affiliation' in metric));
});
test('structural scope warns when displayed nodes are a subset of saved totals', () => {
  const result = model.graphStructure({ ...graph, total_nodes: 20 });
  assert.equal(result.scope.partial, true);
  assert.equal(result.scope.displayed_nodes, 5);
  assert.equal(result.scope.total_nodes, 20);
});
test('direction and tier emphasis retains full arrays and only changes highlighted sets', () => {
  const before = JSON.stringify(graph);
  const result = model.graphEmphasis(graph, { startKey: 'a', direction: 'out', maxHops: 2, tier: 'all' });
  assert.deepEqual([...result.nodeKeys], ['a', 'b', 'c']);
  assert.equal(result.active, true);
  const all = model.graphEmphasis(graph, { startKey: 'a', direction: 'all', maxHops: null, tier: 'all' });
  assert.equal(all.active, false); assert.equal(all.nodeKeys.size, graph.nodes.length);
  assert.equal(JSON.stringify(graph), before);
});
test('projected type details take precedence, preserve lock semantics and use human labels', () => {
  const info = model.accountTypeInfo({ account_type: 'individual', type_basis: 'legacy', evidence_urls: ['https://example.org/unrelated'],
    account_type_details: { basis: 'manual_review', manual_locked: true, confidence_scope: '人工登记', note: '核对说明', evidence_urls: [] } });
  assert.equal(info.basis, 'manual_review'); assert.equal(info.manual_locked, true); assert.equal(info.note, '核对说明');
  assert.deepEqual(info.evidence_urls, []);
  assert.equal(model.accountTypeBasisName(info), '人工核对');
  assert.equal(model.accountTypeBasisName({ basis: 'insufficient_source' }), '依据不足');
  assert.equal(model.accountTypeInfo({ account_type_details: { manual_locked: 'true' } }).manual_locked, false);
});
test('an unrelated saved following link is not described as type evidence when no type basis exists', () => {
  assert.deepEqual(model.accountTypeInfo({ evidence_urls: ['https://example.org/following'] }).evidence_urls, []);
  assert.equal(model.accountType({ metadata: { account_identity: { type: 'organization' } } }), 'unknown');
});
test('server structure explanations apply only to exactly matching observed structure', () => {
  const data = { nodes: ['a', 'b', 'c'].map(key => node(key)), edges: [edge('a', 'b'), edge('b', 'c'), edge('c', 'a')] };
  const local = model.graphStructure(data).node_metrics;
  data.insights = { node_metrics: Object.fromEntries(Object.entries(local).map(([key, value]) => [key, { ...value, reasons: ['保存关系结构的后端说明'] }])) };
  assert.deepEqual(model.graphStructure(data).node_metrics.a.reasons, ['保存关系结构的后端说明']);
  const filtered = { ...data, edges: [data.edges[0]] };
  assert.equal(model.graphStructure(filtered).node_metrics.a.tier, 'peripheral');
  assert.ok(!model.graphStructure(filtered).node_metrics.a.reasons.includes('保存关系结构的后端说明'));
});
test('iterative structure analysis agrees with removing each vertex across small varied graphs', () => {
  const countComponents = (keys, edges) => {
    const remaining = new Set(keys); let count = 0;
    while (remaining.size) {
      count++; const queue = [remaining.values().next().value]; remaining.delete(queue[0]);
      for (const key of queue) for (const [a, b] of edges) {
        const next = a === key ? b : b === key ? a : null;
        if (next !== null && remaining.delete(next)) queue.push(next);
      }
    }
    return count;
  };
  for (let sample = 0; sample < 30; sample++) {
    const keys = Array.from({ length: 3 + sample % 6 }, (_, i) => String(i));
    const pairs = keys.flatMap((a, i) => keys.slice(i + 1).filter(b => (Number(a) * 13 + Number(b) * 7 + sample * 3) % 7 < 3).map(b => [a, b]));
    const metrics = model.graphStructure({ nodes: keys.map(key => node(key)), edges: pairs.map(([a, b]) => edge(a, b)) }).node_metrics;
    const before = countComponents(keys, pairs);
    for (const key of keys) assert.equal(metrics[key].structural_bridge,
      countComponents(keys.filter(value => value !== key), pairs.filter(pair => !pair.includes(key))) > before, `${sample}:${key}`);
  }
});

test('legacy following aliases keep direction and counts without changing stored arrows', () => {
  const data = { nodes: ['a', 'b'].map(key => node(key)), edges: [edge('a', 'b', undefined, { relationship: undefined, relation: 'follows' })] };
  assert.deepEqual([...model.graphReachability(data, { startKey: 'a', direction: 'out' }).nodeKeys], ['a', 'b']);
  assert.equal(model.graphStructure(data).node_metrics.a.following_count, 1);
});
const generalNode = (key, extra = {}) => node(key, { platform: key.split(':')[0], handle: key.split(':')[1], ...extra });
const generalGraph = { nodes: [generalNode('twitter:a'), generalNode('twitter:youtube'), generalNode('twitter:d'), generalNode('twitter:youtube_japan'),
  generalNode('twitter:excluded', { account_status: 'excluded' })],
  edges: [edge('twitter:a', 'twitter:youtube'), edge('twitter:youtube', 'twitter:d'), edge('twitter:a', 'twitter:excluded')] };
test('only exact public platform handles in the agreed namespace are common accounts', () => {
  for (const key of ['twitter:youtube', 'twitter:teamyoutube', 'twitter:google', 'twitter:instagram', 'twitter:x', 'instagram:instagram']) {
    assert.equal(model.researchRelevance(generalNode(key)).state, 'general_platform', key);
  }
  for (const key of ['twitter:youtube_japan', 'instagram:youtube', 'twitter:popular_news']) {
    assert.equal(model.researchRelevance(generalNode(key, { followers_count: 10000000, name: 'Official YouTube' })).state, 'unknown', key);
  }
  assert.equal(model.researchRelevance(generalNode('twitter:youtube', { platform: 'instagram' })).state, 'unknown');
});
test('default relevance hides common and manually excluded nodes without mutating saved data', () => {
  const before = JSON.stringify(generalGraph), visible = model.selectResearchGraph(generalGraph);
  assert.deepEqual(visible.nodes.map(value => value.account_key), ['twitter:a', 'twitter:d', 'twitter:youtube_japan']);
  assert.equal(visible.edges.length, 0);
  assert.equal(visible.relevance_scope.hidden_general, 1); assert.equal(visible.relevance_scope.hidden_excluded, 1);
  assert.equal(model.findGraphPath(generalGraph, 'twitter:a', 'twitter:d', { direction: 'out', maxHops: 2 }), null);
  assert.equal(JSON.stringify(generalGraph), before);
});
test('show common accounts restores per-step public paths but never restores manual exclusions', () => {
  const visible = model.selectResearchGraph(generalGraph, { showGeneral: true });
  assert.deepEqual(visible.nodes.map(value => value.account_key), ['twitter:a', 'twitter:youtube', 'twitter:d', 'twitter:youtube_japan']);
  assert.equal(visible.relevance_scope.shown_general, 1);
  assert.deepEqual(model.findGraphPath(visible, 'twitter:a', 'twitter:d', { direction: 'out', maxHops: 2 }).node_keys,
    ['twitter:a', 'twitter:youtube', 'twitter:d']);
  const metric = model.graphStructure(visible).node_metrics['twitter:youtube'];
  assert.equal(metric.tier, 'peripheral'); assert.equal(metric.structural_bridge, false);
  assert.ok(metric.reasons.some(reason => reason.includes('通用平台')));
  assert.equal(model.graphStructure(visible).node_metrics['twitter:a'].degree, 0);
});
test('trusted relevance projection permits manual inclusion but account exclusion remains decisive', () => {
  const included = generalNode('twitter:youtube', { research_relevance: { state: 'included', default_hidden: false, skip_expansion: false, reason: '本次人工纳入', basis: 'manual_review' } });
  assert.equal(model.researchRelevance(included).state, 'included');
  assert.equal(model.selectResearchGraph({ nodes: [included], edges: [] }).nodes.length, 1);
  assert.equal(model.selectResearchGraph({ nodes: [{ ...included, account_status: 'excluded' }], edges: [] }, { showGeneral: true }).nodes.length, 0);
  const irrelevant = generalNode('twitter:a', { research_relevance: { state: 'excluded', default_hidden: true, skip_expansion: true, basis: 'manual_review' } });
  assert.equal(model.selectResearchGraph({ nodes: [irrelevant], edges: [] }, { showGeneral: true }).nodes.length, 0);
  assert.equal(model.researchRelevance(generalNode('twitter:youtube', { metadata: { research_relevance_override: { state: 'included' }, explicit_root: true } })).state, 'general_platform');
});
test('only an explicit research root can exempt the common-account default', () => {
  assert.equal(model.selectResearchGraph(generalGraph, { explicitRootKey: 'twitter:youtube' }).nodes.some(value => value.account_key === 'twitter:youtube'), true);
  assert.equal(model.selectResearchGraph(generalGraph, { explicitRootKey: 'twitter:excluded', showGeneral: true }).nodes.some(value => value.account_key === 'twitter:excluded'), false);
  assert.equal(model.researchRelevance(generalNode('twitter:youtube', { account_type: 'organization', account_status: 'verified' })).default_hidden, true);
});
test('legacy relevance identity checks agree with strict canonical namespace and complete fields', () => {
  assert.equal(model.researchRelevance({ platform: 'twitter', handle: '@YouTube' }).state, 'general_platform');
  for (const value of [{ account_key: 'TWITTER:youtube' }, { account_key: 'twitter:youtube', platform: 'twitter' },
    { account_key: 'twitter:youtube', handle: 'youtube' }, { account_key: 'twitter:@youtube' }]) {
    assert.equal(model.researchRelevance(value).state, 'unknown');
  }
  assert.equal(model.researchRelevance({ account_key: 'twitter:YouTube' }).state, 'general_platform');
  assert.equal(model.researchRelevance(generalNode('twitter:a', { account_status: 'candidate', status: 'excluded' })).state, 'excluded');
});
test('proven explicit root is included for both legacy and projected common account defaults', () => {
  const value = generalNode('twitter:youtube');
  assert.equal(model.researchRelevance(value, { explicitRootKey: value.account_key }).state, 'included');
  assert.equal(model.researchRelevance({ ...value, research_relevance: { state: 'general_platform', default_hidden: true, skip_expansion: true } },
    { explicitRootKey: value.account_key }).state, 'included');
  assert.equal(model.researchRelevance({ ...value, research_relevance: { state: 'excluded', default_hidden: true, basis: 'manual_review' } },
    { explicitRootKey: value.account_key }).state, 'excluded');
});
test('saved explicit root inputs require account mode or an explicit seed, not a stale handle or selected field', () => {
  assert.deepEqual([...model.explicitResearchRoots({ source: 'twitter', handle: 'youtube', mode: 'keyword', selected: 'twitter:youtube' })], []);
  assert.deepEqual([...model.explicitResearchRoots({ source: 'twitter', handle: 'youtube' })], []);
  assert.deepEqual([...model.explicitResearchRoots({ source: 'twitter', handle: '@YouTube', mode: 'account' })], ['twitter:youtube']);
  assert.deepEqual([...model.explicitResearchRoots({ source: 'google_news', seed_accounts: ['https://x.com/youtube', 'https://www.instagram.com/instagram/'] })], ['twitter:youtube', 'instagram:instagram']);
  assert.deepEqual([...model.explicitResearchRoots({ source: 'twitter', seed_accounts: ['@teamyoutube', 'https://x.com/youtube/status/1', 'https://evil.example/youtube'] })], ['twitter:teamyoutube']);
  const visible = model.selectResearchGraph(generalGraph, { explicitRootKeys: model.explicitResearchRoots({ source: 'twitter', seed_accounts: ['youtube'] }) });
  assert.ok(visible.nodes.some(value => value.account_key === 'twitter:youtube'));
  assert.equal(model.graphStructure(visible).node_metrics['twitter:youtube'].structural_bridge, true);
});
