"""Deferred components survive inert restore; only one prior schema migrates."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from app.workbench.backup import BackupService
from app.workbench.restore import RestoreError, prepare_restore
from app.workbench.social.common import account
from app.workbench.store import Store


def candidate(handle="fixturechild", *, items=()):
    source = "https://x.com/fixtureparent/following/"
    return {"account_key": "twitter:" + handle, "account": account("twitter", handle),
            "depth": 1, "parent_key": "twitter:fixtureparent", "discovery_reason": "following", "score": 20,
            "status": "deferred", "admission_state": "deferred", "defer_reason": "account_budget",
            "observed_at": "2026-10-09T00:00:00Z", "evidence_urls": [source],
            "discovery_paths": [{"parent_key": "twitter:fixtureparent", "reason": "following", "depth": 1,
                "evidence_urls": [source], "observed_at": "2026-10-09T00:00:00Z"}],
            "items": list(items), "relations": [{"source_key": "twitter:fixtureparent", "target_key": "twitter:" + handle,
                "relation": "follows", "evidence_url": source, "observed_at": "2026-10-09T00:00:00Z"}]}


def backup_run(tmp_path, deferred=(), *, admitted=()):
    with closing(Store(tmp_path / "fixture-live.sqlite3")) as store:
        job = store.create_job("research", {"keyword": "public fixture", "start_date": "2026-10-01", "end_date": "2026-10-09"})
        store.create_research_run(job["id"], job["params"])
        if admitted:
            store.checkpoint_research(job["id"], nodes=[{"account_key": key, "depth": 1, "score": 20, "status": "pending"}
                                                      for key in admitted])
        summary = {"outcome": "partial", "stop_reason": "account_budget", "collection_outcome": "partial",
                   "collection_stop_reason": "account_budget", "collection_complete": False,
                   "account_budget_skipped": 49}
        store.checkpoint_research(job["id"], status="partial", summary=summary, deferred_frontier=list(deferred))
        store.update_job(job["id"], status="partial", result={"run_id": job["id"], "summary": summary})
        response = BackupService().response(store)
        try:
            path = tmp_path / "independent-frontier-backup.sqlite3"
            path.write_bytes(Path(response.path).read_bytes())
        finally:
            response.directory.cleanup()
    return path, job["id"]


def mutate(source, sql, args=()):
    with closing(sqlite3.connect(source)) as connection:
        # Model SQLite-valid corruption that an offline writer can produce;
        # restore must validate data even when the schema still has its CHECK.
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(sql, args)
        connection.commit()


def assert_rejected_unchanged(source, stage, code="restore_data"):
    before = source.read_bytes()
    names = set(source.parent.iterdir())
    with pytest.raises(RestoreError) as error:
        prepare_restore(source, stage)
    assert error.value.code == code
    assert source.read_bytes() == before and not stage.exists()
    assert set(source.parent.iterdir()) == names


def test_complete_prior_backup_only_missing_deferred_table_migrates_in_stage(tmp_path):
    source, run_id = backup_run(tmp_path)
    mutate(source, "DROP TABLE wb_research_deferred")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    summary = prepare_restore(source, tmp_path / "stage")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert summary["counts"]["research_runs"] == 1
    with closing(Store(tmp_path / "stage/workbench.sqlite3")) as restored:
        run = restored.get_research_run(run_id)
        assert run["deferred_frontier"] == []
        assert run["summary"]["account_budget_skipped"] == 49
        assert run["summary"]["collection_complete"] is False
        assert restored.list_jobs()["total"] == 1
        with restored._transaction() as connection:
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("mutation", ["DROP TABLE wb_job_events", "CREATE TABLE unknown_frontier(value TEXT)",
                                      "CREATE INDEX forged_job_status ON wb_jobs(status)"])
def test_prior_backup_missing_deferred_does_not_relax_other_schema(tmp_path, mutation):
    source, _ = backup_run(tmp_path)
    mutate(source, "DROP TABLE wb_research_deferred")
    mutate(source, mutation)
    assert_rejected_unchanged(source, tmp_path / "stage", "restore_schema")


def test_current_deferred_backup_roundtrips_all_components_with_no_new_jobs(tmp_path):
    rows = [candidate("fixturechild"), candidate("fixtureother")]
    source, run_id = backup_run(tmp_path, rows)
    before = source.read_bytes()
    prepare_restore(source, tmp_path / "stage")
    assert source.read_bytes() == before
    with closing(Store(tmp_path / "stage/workbench.sqlite3")) as restored:
        assert restored.get_research_run(run_id)["deferred_frontier"] == rows
        assert restored.list_jobs()["total"] == 1


def test_normalized_frontier_larger_than_two_mib_survives_restore(tmp_path):
    items = [{"account_key": "twitter:fixturechild", "url": f"https://x.com/fixturechild/status/{index + 1}",
              "type": "social", "title": "Synthetic source", "content": "合成原文" * 10000,
              "published_at": "2026-10-09T00:00:00Z", "evidence_id": f"fixture-item-{index}"}
             for index in range(24)]
    rows = [candidate(items=items)]
    assert len(json.dumps(rows, ensure_ascii=False).encode("utf-8")) > 2097152
    source, run_id = backup_run(tmp_path, rows)
    prepare_restore(source, tmp_path / "stage")
    with closing(Store(tmp_path / "stage/workbench.sqlite3")) as restored:
        assert restored.get_research_run(run_id)["deferred_frontier"] == rows


@pytest.mark.parametrize("component,value", [("header", "not an object"), ("items", "not an object"),
                                            ("relations", []), ("discovery_paths", "not an object"),
                                            ("evidence_urls", {"url": "https://example.org"})])
def test_component_wrong_json_shapes_are_rejected_before_install(tmp_path, component, value):
    rows = [candidate(items=[{"account_key": "twitter:fixturechild", "url": "https://x.com/fixturechild/status/1",
                              "content": "Synthetic only", "title": "Synthetic source", "type": "social"}])]
    source, _ = backup_run(tmp_path, rows)
    mutate(source, "UPDATE wb_research_deferred SET record=? WHERE component=?", (json.dumps(value), component))
    assert_rejected_unchanged(source, tmp_path / "stage")


@pytest.mark.parametrize("mutation", [
    "DELETE FROM wb_research_deferred WHERE component='header'",
    "UPDATE wb_research_deferred SET ordinal=2 WHERE component='header'",
    "UPDATE wb_research_deferred SET ordinal=2 WHERE component='evidence_urls'",
    "UPDATE wb_research_deferred SET ordinal=-1 WHERE component='items'",
    "UPDATE wb_research_deferred SET component='unknown' WHERE component='relations'",
    "UPDATE wb_research_deferred SET ordinal='invalid' WHERE component='relations'",
])
def test_corrupt_component_keys_and_ordinals_are_rejected(tmp_path, mutation):
    rows = [candidate(items=[{"account_key": "twitter:fixturechild", "url": "https://x.com/fixturechild/status/1",
                              "content": "Synthetic only", "title": "Synthetic source", "type": "social"}])]
    source, _ = backup_run(tmp_path, rows)
    mutate(source, mutation)
    assert_rejected_unchanged(source, tmp_path / "stage")


def test_header_account_key_cannot_rebind_component_identity(tmp_path):
    source, _ = backup_run(tmp_path, [candidate()])
    with closing(sqlite3.connect(source)) as connection:
        value = json.loads(connection.execute("SELECT record FROM wb_research_deferred WHERE component='header'").fetchone()[0])
    value["account_key"] = "twitter:someother"
    mutate(source, "UPDATE wb_research_deferred SET record=? WHERE component='header'", (json.dumps(value),))
    assert_rejected_unchanged(source, tmp_path / "stage")


def test_duplicate_candidate_headers_rejected_even_when_primary_key_is_unique(tmp_path):
    source, _ = backup_run(tmp_path, [candidate()])
    mutate(source, "INSERT INTO wb_research_deferred SELECT run_id,account_key,component,ordinal+1,record,updated_at FROM wb_research_deferred WHERE component='header'")
    assert_rejected_unchanged(source, tmp_path / "stage")


def test_real_admitted_pending_material_survives_restore_after_discovery_cleanup(tmp_path):
    row = candidate(items=[{"account_key": "twitter:fixturechild", "url": "https://x.com/fixturechild/status/1",
                            "content": "Independent saved synthetic body", "title": "Synthetic source", "type": "social"}])
    row.update(admission_state="admitted", evidence_urls=[], discovery_paths=[], relations=[])
    source, run_id = backup_run(tmp_path, [row], admitted=[row["account_key"]])
    prepare_restore(source, tmp_path / "stage")
    with closing(Store(tmp_path / "stage/workbench.sqlite3")) as restored:
        assert restored.get_research_run(run_id)["deferred_frontier"] == [row]


def test_cache_admitted_label_cannot_replace_a_missing_real_node(tmp_path):
    source, _ = backup_run(tmp_path, [candidate(items=[{"account_key": "twitter:fixturechild", "url": "https://x.com/fixturechild/status/1",
        "content": "Independent saved synthetic body", "title": "Synthetic source", "type": "social"}])])
    mutate(source, "DELETE FROM wb_research_deferred WHERE component IN ('evidence_urls','discovery_paths','relations')")
    with closing(sqlite3.connect(source)) as connection:
        header = json.loads(connection.execute("SELECT record FROM wb_research_deferred WHERE component='header'").fetchone()[0])
    header["admission_state"] = "admitted"
    mutate(source, "UPDATE wb_research_deferred SET record=? WHERE component='header'", (json.dumps(header),))
    assert_rejected_unchanged(source, tmp_path / "stage")


@pytest.mark.parametrize("component,field", [("header", "last_observed_at"), ("discovery_paths", "observed_at"),
                                            ("items", "published_at"), ("relations", "observed_at")])
def test_component_dates_that_break_collection_or_provenance_are_rejected(tmp_path, component, field):
    source, _ = backup_run(tmp_path, [candidate(items=[{"account_key": "twitter:fixturechild", "url": "https://x.com/fixturechild/status/1",
        "content": "Saved synthetic body", "title": "Synthetic source", "type": "social"}])])
    with closing(sqlite3.connect(source)) as connection:
        record = json.loads(connection.execute("SELECT record FROM wb_research_deferred WHERE component=?", (component,)).fetchone()[0])
    record[field] = "invalid timestamp"
    mutate(source, "UPDATE wb_research_deferred SET record=? WHERE component=?", (json.dumps(record), component))
    assert_rejected_unchanged(source, tmp_path / "stage")
