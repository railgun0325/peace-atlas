"""Budget extensions supersede only their own older capped research ancestors."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient

from app.workbench.job_retries import RETRY_METADATA, RetryConflict
from app.workbench.server import create_app
from app.workbench.store import Store
from test_workbench_research_continuation import saved_run


def extended_run(store, *, reason="account_budget", limits=None, status="interrupted", analysis_parent=False):
    original, _ = saved_run(store, reason=reason, pending=True)
    if analysis_parent:
        with store._transaction(write=True) as connection:
            parent_params = {**original["params"], "_analysis_only": True}
            connection.execute("UPDATE wb_jobs SET params=? WHERE id=?", (json.dumps(parent_params), original["id"]))
            connection.execute("UPDATE wb_research_runs SET params=? WHERE id=?", (json.dumps(parent_params), original["id"]))
    limits = limits or {"max_accounts": 2}
    child = store.continue_research_job(original["id"], limits)
    store.update_job(child["id"], status=status)
    return original, store.get_job(child["id"])


@pytest.mark.parametrize("reason,limits", [("account_budget", {"max_accounts": 2}),
                                          ("edge_budget", {"max_edges": 6}),
                                          ("record_budget", {"max_records": 40})])
def test_normal_retry_uses_legally_extended_current_job_not_old_cap(tmp_path, reason, limits):
    store = Store(tmp_path / "extended.sqlite3")
    try:
        original, current = extended_run(store, reason=reason, limits=limits)
        before = deepcopy(store.get_research_run(original["id"]))
        old_jobs = {job["id"]: job for job in store.list_jobs()["items"]}
        retried = store.retry_job(current["id"])
        after = store.get_research_run(original["id"])
        assert retried["params"][RETRY_METADATA] == {"parent_job_id": current["id"], "root_job_id": original["id"]}
        for key, value in limits.items():
            assert retried["params"][key] == value
        assert retried["params"]["resume_run_id"] == original["id"]
        assert not retried["params"].get("_analysis_only")
        assert after["current_job_id"] == retried["id"]
        for key in ("analysis", "accounts", "items", "edges", "nodes", "deferred_frontier", "frontier", "visited", "summary"):
            assert after[key] == before[key]
        for identity, job in old_jobs.items():
            assert store.get_job(identity) == job
        assert store.list_jobs()["total"] == 3
    finally:
        store.close()


def test_http_retry_after_analysis_only_cap_ancestor_returns_current_collection_params(tmp_path):
    store = Store(tmp_path / "http.sqlite3")
    try:
        original, current = extended_run(store, analysis_parent=True, limits={"max_accounts": 2, "max_edges": 8,
                                                                           "max_records": 40, "time_budget_seconds": 60})
        with TestClient(create_app(store, run_worker=False)) as client:
            response = client.post(f"/api/v2/jobs/{current['id']}/retry")
        assert response.status_code == 202, response.text
        params = response.json()["params"]
        assert params[RETRY_METADATA] == {"parent_job_id": current["id"], "root_job_id": original["id"]}
        assert params["max_accounts"] == 2 and params["max_records"] == 40
        assert not params.get("_analysis_only")
    finally:
        store.close()


def test_event_only_legacy_extended_lineage_survives_reopen(tmp_path):
    store = Store(tmp_path / "legacy.sqlite3")
    try:
        original, current = extended_run(store)
        with store._transaction(write=True) as connection:
            parameters = dict(current["params"])
            parameters.pop(RETRY_METADATA)
            connection.execute("UPDATE wb_jobs SET params=? WHERE id=?", (json.dumps(parameters), current["id"]))
        reopened = Store(store.path)
        try:
            retried = reopened.retry_job(current["id"])
            assert retried["params"][RETRY_METADATA] == {"parent_job_id": current["id"], "root_job_id": original["id"]}
            assert retried["params"]["max_accounts"] == 2
        finally:
            reopened.close()
    finally:
        store.close()


@pytest.mark.parametrize("status", ["queued", "running", "succeeded", "partial"])
def test_parallel_active_completed_or_hard_capped_sibling_still_blocks_retry(tmp_path, status):
    store = Store(tmp_path / "sibling.sqlite3")
    try:
        original, current = extended_run(store)
        sibling = store.create_job("research", {**current["params"], RETRY_METADATA: {
            "parent_job_id": original["id"], "root_job_id": original["id"]}})
        if status != "queued":
            store.update_job(sibling["id"], status=status, result={"summary": {"stop_reason": "account_budget"}} if status == "partial" else None)
        before = deepcopy(store.get_research_run(original["id"]))
        with pytest.raises(RetryConflict if status in {"queued", "running"} else ValueError):
            store.retry_job(current["id"])
        assert store.list_jobs()["total"] == 3
        assert store.get_research_run(original["id"]) == before
    finally:
        store.close()


def test_latest_new_budget_cap_is_not_superseded_and_no_job_is_created(tmp_path):
    store = Store(tmp_path / "latest-cap.sqlite3")
    try:
        original, current = extended_run(store, status="queued")
        store.update_job(current["id"], status="partial", result={"summary": {"stop_reason": "account_budget"}})
        before = deepcopy(store.get_research_run(original["id"]))
        with pytest.raises(ValueError, match="预算"):
            store.retry_job(current["id"])
        with pytest.raises(ValueError, match="预算"):
            store.retry_job(original["id"])
        assert store.list_jobs()["total"] == 2
        assert store.get_research_run(original["id"]) == before
    finally:
        store.close()


@pytest.mark.parametrize("changes", [{"research_goal": "different scope"}, {"max_edges": 3}, {"max_accounts": True}])
def test_changed_scope_regressed_budget_or_invalid_params_cannot_bypass_old_cap(tmp_path, changes):
    store = Store(tmp_path / "invalid-extension.sqlite3")
    try:
        original, current = extended_run(store)
        with store._transaction(write=True) as connection:
            params = {**current["params"], **changes}
            connection.execute("UPDATE wb_jobs SET params=? WHERE id=?", (json.dumps(params), current["id"]))
            connection.execute("UPDATE wb_research_runs SET params=? WHERE id=?", (json.dumps(params), original["id"]))
        before = deepcopy(store.get_research_run(original["id"]))
        with pytest.raises(ValueError, match="预算"):
            store.retry_job(current["id"])
        assert store.list_jobs()["total"] == 2
        assert store.get_research_run(original["id"]) == before
    finally:
        store.close()


def test_canonical_params_mismatch_cannot_claim_valid_budget_extension(tmp_path):
    store = Store(tmp_path / "mismatch.sqlite3")
    try:
        original, current = extended_run(store)
        with store._transaction(write=True) as connection:
            params = {**current["params"], "max_accounts": 3}
            connection.execute("UPDATE wb_jobs SET params=? WHERE id=?", (json.dumps(params), current["id"]))
        with pytest.raises(ValueError, match="预算"):
            store.retry_job(current["id"])
        assert store.list_jobs()["total"] == 2
    finally:
        store.close()


def test_mixed_http_retry_and_continue_race_reserves_only_one_current_child(tmp_path):
    store = Store(tmp_path / "mixed.sqlite3")
    try:
        original, current = extended_run(store)
        with TestClient(create_app(store, run_worker=False)) as client:
            def submit(index):
                return client.post(f"/api/v2/jobs/{current['id']}/retry") if index % 2 else client.post(f"/api/v2/research/{original['id']}/continue", json={})
            with ThreadPoolExecutor(max_workers=8) as pool:
                responses = list(pool.map(submit, range(8)))
        assert sum(row.status_code == 202 for row in responses) == 1
        assert all(row.status_code in {202, 409} for row in responses), [row.text for row in responses]
        assert store.list_jobs()["total"] == 3
        child = store.get_job(store.get_research_run(original["id"])["current_job_id"])
        assert child["params"][RETRY_METADATA] == {"parent_job_id": current["id"], "root_job_id": original["id"]}
        assert child["params"]["max_accounts"] == 2
    finally:
        store.close()
