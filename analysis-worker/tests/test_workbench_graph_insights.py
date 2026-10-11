import copy
import random

import pytest

from app.workbench.graph_insights import graph_insights, shortest_evidence_path, explicit_root_keys


def node(key, **fields):
    return {'account_key': 'twitter:' + key, **fields}


def edge(a, b, relation='follows', **fields):
    return {'source_key': 'twitter:' + a, 'target_key': 'twitter:' + b, 'relationship': relation,
            'basis': 'observed', 'evidence_urls': ['https://x.com/' + a + '/following/'], **fields}


def test_shortest_path_preserves_every_original_relation_and_evidence_direction():
    nodes = [node(k, depth=0) for k in ['a','b','c']]
    edges = [edge('a','b'), edge('b','c','mentions', evidence_urls=['https://x.com/b/status/1'])]
    path = shortest_evidence_path(nodes, edges, 'twitter:a', 'twitter:c')
    assert path['node_keys'] == ['twitter:a','twitter:b','twitter:c'] and path['hops'] == 2
    assert [r['relationship'] for r in path['steps']] == ['follows','mentions']
    assert path['steps'][1]['evidence_urls'] == ['https://x.com/b/status/1']
    assert shortest_evidence_path(nodes, edges, 'twitter:c', 'twitter:a') is None
    reverse = shortest_evidence_path(nodes, edges, 'twitter:c', 'twitter:a', direction='incoming')
    assert all(r['traversal_reversed'] for r in reverse['steps'])
    assert reverse['steps'][0]['source_key'] == 'twitter:b'
    assert shortest_evidence_path(nodes, edges, 'twitter:a', 'twitter:c', relationship='follows') is None
    assert shortest_evidence_path(nodes, edges, 'twitter:a', 'twitter:c', max_hops=1) is None


@pytest.mark.parametrize('bad', [edge('b','c', evidence_urls=[]), edge('b','c', basis='inferred'),
    edge('b','c', evidence_urls=['http://unsafe.example/']), edge('b','c', evidence_urls=['https://user:password@example.org/'])])
def test_unreferenced_or_inferred_edges_never_create_paths_or_structural_bridges(bad):
    nodes = [node(k) for k in ['a','b','c']]
    assert shortest_evidence_path(nodes, [edge('a','b'),bad], 'twitter:a','twitter:c') is None
    info = graph_insights(nodes, [edge('a','b'),bad])
    assert not info['node_metrics']['twitter:b']['structural_bridge']


def test_parallel_records_reciprocals_and_multiple_types_do_not_inflate_neighbour_count():
    nodes = [node(k) for k in ['a','b','c','isolated']]
    edges = [edge('a','b'),edge('b','a'),edge('a','b','mentions'),edge('b','c'),edge('a','b')]
    info = graph_insights(nodes, edges)
    b = info['node_metrics']['twitter:b']
    assert b['degree'] == 2 and b['following_count'] == 2 and b['followers_count'] == 1
    assert b['structural_bridge'] and b['tier'] == 'bridge'
    assert info['node_metrics']['twitter:isolated']['isolated']
    assert info['supported_edge_count'] == 4


def test_cycle_is_not_an_articulation_and_organization_contacts_are_not_affiliation():
    nodes = [node('a',account_type='organization'),node('b',account_type='individual'),node('c',account_type='organization')]
    edges = [edge('a','b'),edge('b','c'),edge('c','a')]
    original = copy.deepcopy((nodes,edges))
    info = graph_insights(nodes, edges)
    b = info['node_metrics']['twitter:b']
    assert not b['structural_bridge'] and b['organization_bridge_candidate']
    assert b['organization_contacts'] == ['twitter:a','twitter:c']
    assert not any('affiliation' in r for r in info['node_metrics'].values())
    assert (nodes,edges) == original


def test_dangling_edges_self_loops_and_legacy_unknown_types_are_not_invented():
    nodes = [node('a'),node('b')]
    info = graph_insights(nodes, [edge('a','missing'),edge('a','a'),edge('a','b')])
    assert info['supported_edge_count'] == 1
    assert not info['node_metrics']['twitter:b']['organization_bridge_candidate']
    assert shortest_evidence_path(nodes, [], 'twitter:a','twitter:a')['hops'] == 0


def test_articulation_matches_independent_removal_definition_in_disconnected_graphs():
    rng = random.Random(711)
    for _ in range(25):
        names = [str(i) for i in range(9)]
        edges = [edge(a,b) for i,a in enumerate(names) for b in names[i+1:] if rng.random() < .2]
        neighbours = {name: set() for name in names}
        for row in edges:
            a,b = row['source_key'][8:],row['target_key'][8:]
            neighbours[a].add(b); neighbours[b].add(a)
        def components(excluded=None):
            remaining, count = set(names)-{excluded},0
            while remaining:
                pending = [remaining.pop()]; count += 1
                while pending:
                    for other in neighbours[pending.pop()] & remaining:
                        remaining.remove(other); pending.append(other)
            return count
        baseline = components()
        actual = graph_insights([node(k) for k in names], edges)['node_metrics']
        assert {key[8:] for key,m in actual.items() if m['structural_bridge']} == {k for k in names if components(k) > baseline}


def test_large_metrics_explicitly_mark_the_bounded_display_scope():
    info = graph_insights([node(str(i)) for i in range(301)], [])
    assert info['limited'] and len(info['node_metrics']) == 300


@pytest.mark.parametrize('opts', [{'direction':'wrong'}, {'max_hops':True}, {'max_hops':7}])
def test_invalid_path_options_are_rejected(opts):
    with pytest.raises(ValueError):
        shortest_evidence_path([node('a')], [], 'twitter:a','twitter:a', **opts)


@pytest.mark.parametrize('params', [None, {'source':[],'mode':'account'},
    {'source':'twitter','handle':'youtube','mode':'keyword'},
    {'source':'twitter','seed_accounts':['https://x.com/youtube?fake=1','//x.com/youtube','https://[invalid']}])
def test_unproven_or_malformed_roots_never_exempt_generic_accounts(params):
    assert explicit_root_keys(params) == set()


def test_generic_noise_cannot_make_organizations_a_bridge_but_explicit_seed_can_be_studied():
    nodes = [node('org_a',account_type='organization'),node('youtube'),node('org_b',account_type='organization')]
    edges = [edge('org_a','youtube'),edge('youtube','org_b')]
    baseline = graph_insights(nodes, edges)
    assert 'twitter:youtube' not in baseline['node_metrics'] and baseline['supported_edge_count'] == 0
    roots = explicit_root_keys({'mode':'account','source':'twitter','handle':'youtube'})
    explicit = graph_insights(nodes, edges, explicit_roots=roots)
    assert explicit['node_metrics']['twitter:youtube']['structural_bridge']


def test_research_api_returns_noise_projection_and_keeps_raw_sources(tmp_path):
    from app.workbench.store import Store
    store = Store(tmp_path/'owned.sqlite3')
    org_a = store.save_account({'name':'Org A','platform':'twitter','handle':'org_a','url':'https://x.com/org_a'})
    youtube = store.save_account({'name':'YouTube','platform':'twitter','handle':'youtube','url':'https://x.com/youtube'})
    store.import_social([], [], [{'source_key':'twitter:org_a','target_key':'twitter:youtube','relation':'follows',
        'evidence_url':'https://x.com/org_a/following/','observed_at':'2026-10-09T07:00:00Z'}])
    graph = store.registry_graph()
    assert next(n for n in graph['nodes'] if n['handle']=='youtube')['research_relevance']['default_hidden']
    assert len(graph['nodes']) == 2 and len(graph['edges']) == 1
    assert 'twitter:youtube' not in graph['insights']['node_metrics']
    assert len(store.list_accounts()) == 2
    store.close()
