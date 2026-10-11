"""Read-path regressions: all public evidence survives bounded UI reads."""
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from app.workbench.server import create_app
from app.workbench.social.common import account
from app.workbench.store import Store


def stopped_run(store):
    params = {"source": "twitter", "seed_sources": ["twitter"], "mode": "account", "handle": "readparent0",
              "start_date": "2026-10-01", "end_date": "2026-10-09", "depth": 2, "max_accounts": 2,
              "max_records": 20, "max_edges": 20, "time_budget_seconds": 60, "analysis_mode": "rules"}
    job = store.create_job("research", params)
    store.create_research_run(job["id"], params)
    nodes = [{"account_key": "twitter:readparent" + str(i), "depth": 0, "status": "visited"} for i in range(2)]
    urls = [f"https://x.com/readparent{i}/status/{1000 + i}" for i in range(2)]
    items = [{"account_key": nodes[i]["account_key"], "url": url, "title": "Source " + str(i), "content": "Public fixture source " + str(i),
              "type": "social", "published_at": "2026-10-09T00:00:00Z"} for i, url in enumerate(urls)]
    entries = []
    for index in range(12):
        key = f"twitter:readchild{index}"
        entries.append({"account_key": key, "account": account("twitter", f"readchild{index}"), "depth": 1,
            "parent_key": nodes[0]["account_key"], "discovery_reason": "following", "score": 20,
            "status": "deferred", "admission_state": "deferred", "defer_reason": "account_budget", "evidence_urls": urls,
            "discovery_paths": [{"parent_key": nodes[i]["account_key"], "reason": "following", "depth": 1, "evidence_urls": [url]}
                                for i, url in enumerate(urls)],
            "items": [{"account_key": key, "url": f"https://x.com/readchild{index}/status/{2000 + index}", "type": "social",
                       "title": "Cached source", "content": "DEFERRED_BODY_NEVER_ON_GRAPH" * 4000,
                       "published_at": "2026-10-09T00:00:00Z"}],
            "relations": [{"source_key": nodes[i]["account_key"], "target_key": key, "relation": "follows", "evidence_url": url}
                          for i, url in enumerate(urls)]})
    summary = {"outcome": "partial", "collection_outcome": "partial", "collection_complete": False,
               "stop_reason": "account_budget", "collection_stop_reason": "account_budget", "limit_hits": ["account_budget"],
               "deferred_frontier_version": 1, "pending_work_version": 1, "record_count": 2}
    store.checkpoint_research(job["id"], nodes=nodes, items=items, deferred_frontier=entries,
                             visited=[n["account_key"] for n in nodes], summary=summary, status="partial", phase="collection")
    store.update_job(job["id"], status="partial", result={"run_id": job["id"]})
    return job["id"]


def test_graph_api_reads_cached_component_projection_once_and_preserves_full_export(tmp_path, monkeypatch):
    with closing(Store(tmp_path / "owned.sqlite3")) as store:
        run_id = stopped_run(store)
        reads = []
        original = store._read_deferred
        def tracked(connection, identity, *, include_materials=True):
            reads.append(include_materials)
            return original(connection, identity, include_materials=include_materials)
        monkeypatch.setattr(store, "_read_deferred", tracked)
        with TestClient(create_app(store, run_worker=False)) as client:
            response = client.get(f"/api/v2/research/{run_id}")
        assert response.status_code == 200
        assert reads == [False]
        graph = response.json()
        assert "DEFERRED_BODY_NEVER_ON_GRAPH" not in response.text
        assert "deferred_frontier" not in graph["run"]
        assert graph["summary"]["deferred_count"] == 12
        assert graph["summary"]["deferred_record_count"] == 12
        assert graph["summary"]["deferred_relation_count"] == 24
        exported = store.export_research(run_id)
        assert len(exported["run"]["deferred_frontier"]) == 12
        assert sum(len(e["discovery_paths"]) for e in exported["run"]["deferred_frontier"]) == 24
        assert all(e["items"][0]["content"] == "DEFERRED_BODY_NEVER_ON_GRAPH" * 4000 for e in exported["run"]["deferred_frontier"])


def test_list_and_graph_counts_follow_live_exclusion_and_restore_without_stale_cache(tmp_path):
    with closing(Store(tmp_path / "owned.sqlite3")) as store:
        run_id = stopped_run(store)
        excluded = store.save_account({"name": "Read child", "handle": "readchild0", "platform": "twitter",
                                       "url": "https://x.com/readchild0", "status": "excluded"})
        source = next(i for i in store.get_research_run(run_id)["items"] if i["title"] == "Source 0")
        preview = store.cleanup_preview([source["id"]], "trash")
        store.cleanup_apply([source["id"]], "trash", preview["preview_digest"], reason="Owned fixture")
        for result in (store.list_research_runs()["items"][0], store.research_graph(run_id)["run"]):
            assert result["summary"]["deferred_count"] == 11
            assert result["summary"]["deferred_record_count"] == 11
            assert result["summary"]["deferred_relation_count"] == 11
            assert result["summary"]["continuation_deferred_accounts"] == 11
        preview = store.cleanup_preview([source["id"]], "restore")
        store.cleanup_apply([source["id"]], "restore", preview["preview_digest"], reason="Owned fixture")
        store.save_account({"status": "candidate"}, id=excluded["id"])
        for result in (store.list_research_runs()["items"][0], store.research_graph(run_id)["run"]):
            assert result["summary"]["deferred_count"] == 12
            assert result["summary"]["deferred_relation_count"] == 24
        assert sum(len(e["discovery_paths"]) for e in store.get_research_run(run_id)["deferred_frontier"]) == 24


def test_relations_pagination_reaches_all_rows_with_stable_order_and_validates_bounds(tmp_path):
    with closing(Store(tmp_path / "owned.sqlite3")) as store:
        rows = [account("twitter", "readerroot"), *[account("twitter", f"readerleaf{i}") for i in range(1001)]]
        result = store.import_social([], rows, [{"source_key": "twitter:readerroot", "target_key": f"twitter:readerleaf{i}",
            "relation": "follows", "evidence_url": "https://x.com/readerroot/following/", "observed_at": "2026-10-09T00:00:00Z"}
            for i in range(1001)])
        assert result["relations_inserted"] == 1001
        root_id = result["account_ids"]["twitter:readerroot"]
        with TestClient(create_app(store, run_worker=False)) as client:
            first = client.get(f"/api/v2/accounts/{root_id}/relations?page=1&page_size=1000").json()
            second = client.get(f"/api/v2/accounts/{root_id}/relations?page=2&page_size=1000").json()
            again = client.get(f"/api/v2/accounts/{root_id}/relations?page=1&page_size=1000").json()
            assert first["total"] == second["total"] == 1001
            assert len(first["items"]) == 1000 and len(second["items"]) == 1
            assert first["items"] == again["items"]
            assert len({row["id"] for row in first["items"] + second["items"]}) == 1001
            assert client.get(f"/api/v2/accounts/{root_id}/relations?page=0").status_code == 422
            assert client.get(f"/api/v2/accounts/{root_id}/relations?page_size=1001").status_code == 422
        for page, page_size in ((True, 1), (1, False), (0, 1), (1, 1001)):
            with pytest.raises(ValueError):
                store.account_relations(root_id, page=page, page_size=page_size)


def test_registry_bounded_scan_marks_unique_total_as_lower_bound_and_reports_exact_source_rows(tmp_path):
    with closing(Store(tmp_path / "owned.sqlite3")) as store:
        rows = [account("twitter", f"bounded{i}") for i in range(72)]
        relations = [{"source_key": f"twitter:bounded{i}", "target_key": f"twitter:bounded{j}", "relation": "follows",
                      "evidence_url": f"https://x.com/bounded{i}/following/", "observed_at": "2026-10-09T00:00:00Z"}
                     for i in range(72) for j in range(72) if i != j]
        assert len(relations) == 5112
        store.import_social([], rows, relations)
        graph = store.registry_graph()
        assert graph["total_edges"] == 5000
        assert graph["total_edges_exact"] is False
        assert graph["coverage_metadata"]["edge_count_lower_bound"] == 5000
        source = graph["coverage_metadata"]["sources"]["account_relations"]
        assert source == {"total_rows": 5112, "scanned_rows": 5000, "omitted_rows": 112, "limit": 5000}
        assert graph["limited"] is True


def test_registry_complete_source_scan_retains_exact_unique_total(tmp_path):
    with closing(Store(tmp_path / "owned.sqlite3")) as store:
        store.import_social([], [account("twitter", "one"), account("twitter", "two")],
            [{"source_key": "twitter:one", "target_key": "twitter:two", "relation": "follows",
              "evidence_url": "https://x.com/one/following/", "observed_at": "2026-10-09T00:00:00Z"}])
        graph = store.registry_graph()
        assert graph["total_edges"] == len(graph["edges"]) == 1
        assert graph["total_edges_exact"] is True
        assert graph["coverage_metadata"]["complete"] is True


def test_light_identifier_resolver_retains_attached_and_legacy_job_links(tmp_path):
    with closing(Store(tmp_path / "owned.sqlite3")) as store:
        run_id = stopped_run(store)
        attached = store.create_job("research", {"resume_run_id": run_id})
        store.update_job(attached["id"], status="failed")
        store.attach_research_job(run_id, attached["id"])
        legacy = store.create_job("research", {})
        store.update_job(legacy["id"], status="failed", result={"run_id": run_id})
        by_params = store.create_job("research", {"resume_run_id": run_id})
        store.update_job(by_params["id"], status="failed")
        assert all(store.resolve_research_id(value) == run_id for value in (run_id, attached["id"], legacy["id"], by_params["id"]))
        assert store.resolve_research_id("missing-owned-fixture") is None
