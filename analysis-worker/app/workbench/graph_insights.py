"""Explainable topology of the displayed, supported account graph only.

Distances and tiers do not assert influence, affiliation or a complete network.
Each path retains original edge direction and public evidence links.
"""
from __future__ import annotations

from collections import deque
from urllib.parse import urlsplit


def explicit_root_keys(params):
    from .account_verification import account_link_key
    from .social.common import normalize_handle
    roots = set()
    if not isinstance(params, dict):
        return roots
    source = params.get('source')
    supported = isinstance(source, str) and source in {'twitter','instagram'}
    if params.get('mode') == 'account' and supported:
        try:
            roots.add(source + ':' + normalize_handle(source, params.get('handle', '')))
        except (TypeError, ValueError):
            pass
    seeds = params.get('seed_accounts') or []
    for seed in seeds if isinstance(seeds, (list,tuple)) else []:
        if not isinstance(seed, str):
            continue
        try:
            parsed = urlsplit(seed)
        except ValueError:
            continue
        if parsed.query or parsed.fragment or parsed.netloc and not parsed.scheme:
            continue
        key = account_link_key(seed)
        if key:
            roots.add(key)
        elif supported and not parsed.scheme and not parsed.netloc:
            try:
                roots.add(source + ':' + normalize_handle(source, seed))
            except (TypeError, ValueError):
                pass
    return roots


def _public_links(values):
    result = []
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            p = urlsplit(value)
            if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in {None, 443} or any(c.isspace() for c in value):
                continue
        except ValueError:
            continue
        if value not in result:
            result.append(value)
    return result


def supported_edges(nodes, edges):
    keys = {node['account_key'] for node in nodes if isinstance(node, dict) and isinstance(node.get('account_key'), str)}
    result, identities = [], {}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source, target = edge.get('source_key'), edge.get('target_key')
        relation, basis = edge.get('relationship', edge.get('relation', '')), edge.get('basis', 'observed')
        if source not in keys or target not in keys or source == target or basis not in {'observed','reported'} or not isinstance(relation, str) or not relation:
            continue
        urls = edge.get('evidence_urls') or []
        urls = urls if isinstance(urls, list) else []
        urls = _public_links([*urls, edge.get('evidence_url')])
        if not urls:
            continue
        identity = (source, target, relation, basis)
        if identity in identities:
            existing = identities[identity]
            existing['evidence_urls'] = list(dict.fromkeys([*existing['evidence_urls'], *urls]))
            continue
        value = {'edge_id': edge.get('id') or '|'.join(identity), 'source_key': source, 'target_key': target,
                 'relationship': relation, 'basis': basis, 'evidence_urls': urls}
        identities[identity] = value
        result.append(value)
    return result


def shortest_evidence_path(nodes, edges, source_key, target_key, *, direction='outgoing', max_hops=3, relationship=''):
    if direction not in {'outgoing','incoming','both'} or isinstance(max_hops, bool) or not isinstance(max_hops, int) or not 1 <= max_hops <= 6:
        raise ValueError('路径方向或层数无效。')
    keys = {n['account_key'] for n in nodes}
    if source_key not in keys or target_key not in keys:
        return None
    adjacency = {key: [] for key in keys}
    for edge in supported_edges(nodes, edges):
        if relationship and edge['relationship'] != relationship:
            continue
        if direction in {'outgoing','both'}:
            adjacency[edge['source_key']].append((edge['target_key'], edge, False))
        if direction in {'incoming','both'}:
            adjacency[edge['target_key']].append((edge['source_key'], edge, True))
    queue, parents = deque([source_key]), {source_key: None}
    distance = {source_key: 0}
    while queue:
        key = queue.popleft()
        if key == target_key:
            node_keys, steps = [key], []
            while parents[key] is not None:
                previous, edge, reversed_step = parents[key]
                steps.append({**edge, 'traversal_reversed': reversed_step})
                node_keys.append(previous)
                key = previous
            return {'source_key': source_key, 'target_key': target_key, 'node_keys': node_keys[::-1],
                    'steps': steps[::-1], 'hops': len(steps), 'direction': direction,
                    'coverage': '当前显示图中的最短证据链；反向遍历不改变原关系方向，也不产生新的端点关系。'}
        if distance[key] >= max_hops:
            continue
        for target, edge, reversed_step in sorted(adjacency[key], key=lambda value: (value[0], value[1]['relationship'], value[1]['edge_id'])):
            if target in parents:
                continue
            parents[target] = (key, edge, reversed_step)
            distance[target] = distance[key] + 1
            queue.append(target)
    return None


def graph_insights(nodes, edges, *, explicit_roots=()):
    from .account_relevance import describe_relevance
    valid = [n for n in nodes if isinstance(n, dict) and isinstance(n.get('account_key'), str)]
    retained = [node for node in valid[:300] if not describe_relevance(node, node['account_key'] in explicit_roots)['research_relevance']['default_hidden']]
    values = {node['account_key']: node for node in retained}
    rows = list(values.values())
    links = supported_edges(rows, edges)
    neighbours = {key: set() for key in values}
    inbound, outbound = {key: set() for key in values}, {key: set() for key in values}
    follows_in, follows_out = {key: set() for key in values}, {key: set() for key in values}
    for edge in links:
        a, b = edge['source_key'], edge['target_key']
        neighbours[a].add(b); neighbours[b].add(a)
        outbound[a].add(b); inbound[b].add(a)
        if edge['relationship'] == 'follows':
            follows_out[a].add(b); follows_in[b].add(a)
    # Tarjan articulation points; parallel sources and reciprocal edges do
    # not multiply neighbours or create artificial bridge scores.
    discovered, low, parents, articulation = {}, {}, {}, set()
    clock = 0
    def visit(key):
        nonlocal clock
        clock += 1
        discovered[key] = low[key] = clock
        children = 0
        for target in sorted(neighbours[key]):
            if target not in discovered:
                parents[target] = key; children += 1; visit(target)
                low[key] = min(low[key], low[target])
                if key in parents and low[target] >= discovered[key]:
                    articulation.add(key)
            elif parents.get(key) != target:
                low[key] = min(low[key], discovered[target])
        if key not in parents and children > 1:
            articulation.add(key)
    for key in sorted(values):
        if key not in discovered:
            visit(key)
    threshold = max(3, sorted(len(n) for n in neighbours.values())[int(max(0,len(values)-1)*.75)] if values else 3)
    metrics = {}
    for key in sorted(values):
        degree = len(neighbours[key])
        organization_contacts = sorted(k for k in neighbours[key] if values[k].get('account_type') == 'organization')
        is_bridge = key in articulation
        reasons = []
        if is_bridge:
            reasons.append('在当前显示图中，移除此节点会分开原本连通的支路。')
        if len(organization_contacts) >= 2:
            reasons.append('直接连接至少两个已标明类型的组织账号；账号可能属于同一组织，仍需逐条核对。')
        tier = 'bridge' if is_bridge else 'core' if degree >= threshold else 'peripheral'
        if tier == 'core':
            reasons.append(f'当前图中有 {degree} 个不同邻居，达到本次显示范围的重点连接阈值 {threshold}。')
        if tier == 'peripheral':
            reasons.append('当前已采图中的连接较少；不代表真实网络中不重要。')
        metrics[key] = {'tier': tier, 'degree': degree, 'in_degree': len(inbound[key]), 'out_degree': len(outbound[key]),
            'following_count': len(follows_out[key]), 'followers_count': len(follows_in[key]),
            'structural_bridge': is_bridge, 'organization_bridge_candidate': len(organization_contacts) >= 2,
            'organization_contacts': organization_contacts, 'isolated': degree == 0, 'reasons': reasons}
    return {'version': 1, 'node_metrics': metrics, 'paths': [], 'supported_edge_count': len(links),
            'scope': 'displayed_supported_graph', 'direction': 'original_edges', 'bridge_method': 'undirected_articulation',
            'limited': len(valid) > 300, 'hidden_node_count': len(valid[:300])-len(retained),
            'coverage': '层级是当前已采图的结构提示；通用平台及人工排除节点不参与默认层级。指标不表示完整关注量、影响力、组织隶属或政治立场。'}
