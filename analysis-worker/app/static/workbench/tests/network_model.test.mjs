import test from 'node:test';
import assert from 'node:assert/strict';
import { selectGraph, discoveryForest, graphPositions, accountStateName } from '../network_model.js';

const node = (key, parent_key = null, depth = 0) => ({ account_key: key, parent_key, depth, score: 40, platform: key.split(':')[0], handle: key.split(':')[1] });
test('account identity states remain distinct from exploration and use the saved registry status first', () => {
  assert.equal(accountStateName({ account_status: 'excluded', status: 'visited' }), '已排除');
  assert.equal(accountStateName({ account_status: 'verified', status: 'queued' }), '已核验');
  assert.equal(accountStateName({ account_status: 'candidate', status: 'verified' }), '候选账号');
  assert.equal(accountStateName({ status: 'excluded' }), '已排除');
  assert.equal(accountStateName({ status: 'verified' }), '已核验');
  for (const status of ['visited', 'complete', 'queued', 'skipped', 'unrecognized']) assert.equal(accountStateName({ status }), '候选账号');
  assert.equal(accountStateName({}), '候选账号');
});
test('discovery tree tolerates relationship cycles and shows each filtered account once', () => {
  const nodes = [node('twitter:a', 'twitter:b', 1), node('twitter:b', 'twitter:a', 2), node('twitter:c', 'twitter:a', 3)];
  const roots = discoveryForest(nodes);
  const flatten = list => list.flatMap(value => [value.account_key, ...flatten(value.children)]);
  assert.deepEqual(new Set(flatten(roots)), new Set(nodes.map(value => value.account_key)));
  assert.equal(flatten(roots).length, 3);
});
test('filtered missing ancestors appear as explicit roots without fabricated relations', () => {
  const forest = discoveryForest([node('instagram:child', 'instagram:outside', 2)]);
  assert.equal(forest[0].parentOutsideView, true);
  assert.equal(forest[0].children.length, 0);
});
test('platform, relationship, search and depth filters retain only actual endpoints', () => {
  const graph = { nodes: [node('twitter:a'), node('twitter:b', 'twitter:a', 1), node('instagram:c', null, 2)], edges: [{ source_key: 'twitter:a', target_key: 'twitter:b', relationship: 'follows' }, { source_key: 'twitter:a', target_key: 'instagram:c', relationship: 'mentions' }], claims: [{ subject_key: 'instagram:c' }] };
  const visible = selectGraph(graph, { platform: 'twitter', relationship: 'follows', maxDepth: 1 });
  assert.equal(visible.nodes.length, 2); assert.equal(visible.edges.length, 1); assert.equal(visible.claims.length, 0);
  assert.equal(selectGraph(graph, { query: 'b' }).nodes.length, 1);
});
test('layout positions stay bounded and deterministic through empty and multi-level inputs', () => {
  assert.equal(graphPositions([]).positions.size, 0);
  const layout = graphPositions([node('twitter:a'), node('twitter:b', 'twitter:a', 1)]);
  assert.ok(layout.positions.get('twitter:b').x > layout.positions.get('twitter:a').x);
  assert.ok(layout.width > layout.positions.get('twitter:b').x + 192);
});
