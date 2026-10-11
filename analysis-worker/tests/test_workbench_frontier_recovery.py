"""Durable work remains separate from admitted evidence and respects cleanup."""
import copy
import json

import pytest
from fastapi.testclient import TestClient
from app.workbench.store import Store
from app.workbench.social.common import account


def parameters(**changes):
    return {"source": "twitter", "seed_sources": ["twitter"], "mode": "account", "handle": "alpha",
        "seed_accounts": [], "keyword": "", "keyword_mode": "any", "research_goal": "核对公开账号之间的关系",
        "start_date": "2026-10-01", "end_date": "2026-10-09", "depth": 2, "max_accounts": 1,
        "max_edges": 20, "max_records": 20, "max_scrolls": 2, "time_budget_seconds": 60,
        "expand_relations": ["following"], "analysis_mode": "rules", "relevance": "focused", **changes}


def begin(store):
    job = store.create_job("research", parameters())
    store.create_research_run(job["id"], job["params"])
    store.update_job(job["id"], status="partial", result={"run_id": job["id"], "summary": {"stop_reason": "account_budget"}})
    return job["id"]


def candidate(handle="beta", **changes):
    url = "https://x.com/alpha/following"
    return {"account_key": f"twitter:{handle}", "account": account("twitter", handle),
        "depth": 1, "parent_key": "twitter:alpha", "discovery_reason": "following", "score": 55,
        "status": "deferred", "admission_state": "deferred", "defer_reason": "account_budget",
        "evidence_urls": [url], "discovery_paths": [{"parent_key": "twitter:alpha", "reason": "following",
            "depth": 1, "evidence_urls": [url]}], "items": [], "relations": [{"source_key": "twitter:alpha",
            "target_key": f"twitter:{handle}", "relation": "follows", "evidence_url": url,
            "observed_at": "2026-10-09T00:00:00Z"}], **changes}


def test_deferred_work_roundtrips_restart_export_without_creating_graph_or_library_rows(tmp_path):
    store = Store(tmp_path / "frontier.sqlite3")
    run_id = begin(store)
    work = [candidate(), candidate("gamma")]
    store.checkpoint_research(run_id, deferred_frontier=work, status="partial", summary={"deferred_count": 2})
    assert store.get_research_run(run_id)["deferred_frontier"] == work
    assert store.export_research(run_id)["run"]["deferred_frontier"] == work
    assert store.research_graph(run_id)["nodes"] == []
    assert store.list_accounts() == [] and store.list_items()["total"] == 0
    store.close()
    reopened = Store(tmp_path / "frontier.sqlite3")
    assert reopened.get_research_run(run_id)["deferred_frontier"] == work
    reopened.checkpoint_research(run_id, summary={"deferred_count": 2})
    assert reopened.get_research_run(run_id)["deferred_frontier"] == work
    reopened.checkpoint_research(run_id, deferred_frontier=[])
    assert reopened.get_research_run(run_id)["deferred_frontier"] == []
    reopened.close()


def test_many_pending_materials_have_no_aggregate_two_megabyte_loss(tmp_path):
    store = Store(tmp_path / "large-frontier.sqlite3")
    run_id = begin(store)
    work = candidate(items=[{"url": f"https://x.com/beta/status/{index}", "content": "公开正文" * 60000,
                            "account_key": "twitter:beta", "type": "social"}
        for index in range(4)])
    assert len(json.dumps(work, ensure_ascii=False).encode()) > 2097152
    store.checkpoint_research(run_id, deferred_frontier=[work])
    assert store.get_research_run(run_id)["deferred_frontier"] == [work]
    assert store.list_items()["total"] == 0
    graph = store.research_graph(run_id)
    assert graph['summary']['deferred_record_count'] == 4
    assert '公开正文' not in json.dumps(graph, ensure_ascii=False)
    assert 'deferred_frontier' not in graph['run']
    store.close()


def test_maximum_sparse_collector_frontier_preserves_every_candidate_and_path(tmp_path):
    store = Store(tmp_path / 'sparse-frontier.sqlite3')
    run_id = begin(store)
    work = [candidate(f'account{index}', discovery_paths=[{'parent_key': f'twitter:parent{parent}',
        'reason': 'following', 'depth': 1, 'evidence_urls': [f'https://x.com/parent{parent}/following'],
        'observed_at': '2026-10-09T00:00:00Z'} for parent in range(10)]) for index in range(2400)]
    assert len(json.dumps(work, ensure_ascii=False).encode()) > 2097152
    store.checkpoint_research(run_id, deferred_frontier=work)
    assert store.get_research_run(run_id)['deferred_frontier'] == work
    assert store.research_graph(run_id)['summary']['deferred_count'] == 2400
    assert store.list_accounts() == [] and store.list_items()['total'] == 0
    store.close()


def test_deferred_bad_secret_rolls_back_new_evidence_and_preserves_prior_work(tmp_path):
    store = Store(tmp_path / "public-frontier.sqlite3")
    run_id = begin(store)
    saved = [candidate()]
    store.checkpoint_research(run_id, deferred_frontier=saved)
    invalid = candidate("gamma")
    invalid["account"]["metadata"] = {"auth_token": "forbidden-fixture"}
    with pytest.raises(ValueError, match="密钥|会话"):
        store.checkpoint_research(run_id, deferred_frontier=[invalid], nodes=[{"account_key": "twitter:new", "depth": 0}])
    assert store.get_research_run(run_id)["deferred_frontier"] == saved
    assert store.list_accounts() == []
    store.close()


def test_legal_timeline_support_and_multiple_node_paths_survive_checkpoints(tmp_path):
    store = Store(tmp_path / 'node-provenance.sqlite3')
    run_id = begin(store)
    urls = [f'https://x.com/alpha/status/{index}' for index in range(200)]
    first = {'account_key': 'twitter:alpha', 'depth': 1, 'score': 50, 'parent_key': 'twitter:beta',
        'evidence_urls': urls, 'discovery_paths': [{'parent_key': 'twitter:beta', 'reason': 'mentions',
        'depth': 1, 'evidence_urls': urls, 'observed_at': '2026-10-09T00:00:00Z'}]}
    store.checkpoint_research(run_id, nodes=[first])
    second = {**first, 'parent_key': 'twitter:gamma', 'evidence_urls': ['https://x.com/gamma/following'],
        'discovery_paths': [{'parent_key': 'twitter:gamma', 'reason': 'following', 'depth': 1,
            'evidence_urls': ['https://x.com/gamma/following'], 'observed_at': '2026-10-09T01:00:00Z'}]}
    store.checkpoint_research(run_id, nodes=[second])
    node = store.get_research_run(run_id)['nodes'][0]
    assert len(node['evidence_urls']) == 201
    assert {path['parent_key'] for path in node['discovery_paths']} == {'twitter:beta', 'twitter:gamma'}
    assert node['discovery_paths'][0]['evidence_urls'] == urls
    store.close()


def test_continuation_interface_is_explicit_and_rejects_scope_rewrite(tmp_path):
    from app.workbench.server import create_app
    store = Store(tmp_path / "continue.sqlite3")
    run_id = begin(store)
    with TestClient(create_app(store)) as client:
        response = client.post(f"/api/v2/research/{run_id}/continue", json={"max_accounts": 2, "keyword": "changed"})
        assert response.status_code == 422, response.text
        assert store.list_jobs()["total"] == 1
