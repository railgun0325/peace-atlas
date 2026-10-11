"""Permanent source tombstones survive older producer/analysis checkpoints."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

import pytest

from app.workbench.research_repository import source_url_key
from app.workbench.store import Store


DELETED = "synthetic_deleted_syntax_batch_fragment"
RETAINED = "synthetic_retained_batch_fragment"


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "cleanup-checkpoints.sqlite3")
    yield value
    value.close()


def material(store, number):
    item_id = store.import_items([{"type": "news", "title": f"Synthetic public source {number}",
        "url": f"https://example.org/source/{number}", "content": f"Synthetic distinct public body {number}",
        "published_at": "2026-10-09T00:00:00Z", "metadata": {"content_kind": "body"}}])["resolved_ids"][0]
    return store.get_item(item_id)


def run(store, analysis):
    job = store.create_job("research", {"analysis_mode": "rules"})
    value = store.create_research_run(job["id"], job["params"])
    store.checkpoint_research(value["id"], analysis=analysis, status="partial")
    store.update_job(job["id"], status="partial")
    return value["id"]


def clean(store, items, action):
    ids = [item["id"] for item in items]
    preview = store.cleanup_preview(ids, action)
    return store.cleanup_apply(ids, action, preview["preview_digest"], "Synthetic source cleanup reviewed",
        confirm_purge=action == "purge")


def durable(store, run_id):
    with sqlite3.connect(store.path) as connection:
        value = connection.execute("SELECT analysis,metadata,revision FROM wb_research_runs WHERE id=?", (run_id,)).fetchone()
        return json.loads(value[0]), json.loads(value[1]), value[2]


def pending_analysis(items, *, review_version=None, omit_catalogue=False, raw_format="json_text"):
    evidence = [{"id": f"post-{number}", "type": "post", "url": item["url"], "excerpt": item["content"]}
                for number, item in enumerate(items)]
    raw = {"affiliation_candidates": [], "extra": f"{DELETED} {RETAINED}"}
    if raw_format == "json_text":
        raw = json.dumps(raw, ensure_ascii=False)[:-1] + ",}"
    encoded = raw.encode("utf-8") if isinstance(raw, str) else json.dumps(raw, sort_keys=True, separators=(",", ":")).encode("utf-8")
    pending = {"batch_id": "b" * 64, "context_fingerprint": "c" * 64,
        "evidence_hashes": {row["id"]: "e" * 64 for row in evidence},
        "evidence_source_keys": {row["id"]: source_url_key(row["url"]) for row in evidence},
        "repair_attempts": 0, "requested_max_tokens": 32768, "status": "validation_failed",
        "validation_issues": [{"code": "json_syntax" if raw_format == "json_text" else "unknown_fields"}],
        "raw_response": raw, "raw_response_format": raw_format, "raw_response_sha256": hashlib.sha256(encoded).hexdigest(),
        "raw_response_bytes": len(encoded), "raw_response_omitted": False}
    analysis = {"evidence": [] if omit_catalogue else evidence, "findings": [], "affiliations": [],
        "ai": {"requested": True, "complete": False, "review_work_version": 1, "pending_review": pending,
               "review_batches": [], "input_evidence_count": 0, "available_evidence_count": len(evidence),
               "pending_evidence_count": len(evidence)}}
    if review_version is not None:
        analysis["ai"]["review_version"] = review_version
    return analysis


@pytest.mark.parametrize("review_version", [None, 2])
@pytest.mark.parametrize("omit_catalogue", [False, True])
@pytest.mark.parametrize("raw_format", ["json_text", "parsed_json"])
@pytest.mark.parametrize("pending_status", ["validation_failed", "response_received"])
def test_purged_partial_batch_raw_cannot_return_through_stale_checkpoint(store, review_version, omit_catalogue, raw_format, pending_status):
    selected, kept = material(store, 1), material(store, 2)
    stale = pending_analysis([selected, kept], review_version=review_version, omit_catalogue=omit_catalogue, raw_format=raw_format)
    stale["ai"]["pending_review"]["status"] = pending_status
    run_id = run(store, stale)
    unrelated = pending_analysis([kept], review_version=review_version, omit_catalogue=omit_catalogue, raw_format=raw_format)
    unrelated["ai"]["pending_review"]["raw_response"] = {"extra": "Independent pending paid response"}
    unrelated_id = run(store, unrelated)
    unrelated_before = durable(store, unrelated_id)
    clean(store, [selected], "trash")
    assert durable(store, run_id)[0]["ai"]["pending_review"] == stale["ai"]["pending_review"]
    assert store.get_research_run(run_id)["analysis"]["ai"]["pending_review"] is None
    # A producer checkpoint while the source is recoverable must retain the paid response.
    store.checkpoint_research(run_id, analysis=copy.deepcopy(stale), metadata={"purged_source_keys": []})
    clean(store, [selected], "restore")
    assert store.get_research_run(run_id)["analysis"]["ai"]["pending_review"] == stale["ai"]["pending_review"]
    clean(store, [selected], "trash")
    clean(store, [selected], "purge")
    assert durable(store, run_id)[0]["ai"]["pending_review"] is None
    for status in ("cancelled", "failed", "partial"):
        store.checkpoint_research(run_id, analysis=copy.deepcopy(stale), metadata={"purged_source_keys": []}, status=status)
        saved, metadata, _ = durable(store, run_id)
        assert saved["ai"]["pending_review"] is None
        assert DELETED not in json.dumps(saved) and RETAINED not in json.dumps(saved)
        assert saved["ai"]["pending_review_invalidation"]["removed_evidence_count"] == 1
        assert source_url_key(selected["url"]) in metadata["purged_source_keys"]
    reopened = Store(store.path)
    try:
        assert durable(reopened, run_id)[0]["ai"]["pending_review"] is None
        assert DELETED not in json.dumps(durable(reopened, run_id)[0])
    finally:
        reopened.close()
    assert store.get_item(kept["id"]) == kept
    assert durable(store, unrelated_id) == unrelated_before
    with store._transaction() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("field", ["evidence", "findings", "affiliations", "identity_variants", "source_url"])
def test_completed_analysis_only_dependencies_get_tombstones_for_two_sources(store, field):
    first, second, independent = material(store, 11), material(store, 12), material(store, 13)
    keys = {source_url_key(item["url"]) for item in (first, second)}
    if field == "evidence":
        analysis = {"evidence": [{"id": f"post-{number}", "url": item["url"], "excerpt": item["content"]}
                                 for number, item in enumerate((first, second, independent))]}
    elif field == "identity_variants":
        analysis = {"affiliations": [{"account_key": "twitter:alpha", "organization": "Public group",
            "evidence_urls": [independent["url"]], "identity_variants": [{"evidence_urls": [item["url"]]} for item in (first, second)]}]}
    elif field == "source_url":
        analysis = {"findings": [{"source_url": item["url"], "claim": item["content"]} for item in (first, second, independent)]}
    else:
        analysis = {field: [{"evidence_urls": [item["url"]], "claim": item["content"]} for item in (first, second, independent)]}
    analysis["ai"] = {"complete": True, "pending_review": None}
    run_ids = [run(store, copy.deepcopy(analysis)) for _ in range(2)]
    unrelated_analysis = {"evidence": [{"id": "independent", "url": independent["url"]}],
        "findings": [{"claim": "Literal prose URL alone is not source provenance: " + first["url"]}],
        "ai": {"complete": True, "pending_review": None}}
    unrelated_id = run(store, unrelated_analysis)
    unrelated_before = durable(store, unrelated_id)
    report = store.save_report({"title": "Independent report snapshot", "mode": "evidence", "markdown": "Synthetic report",
        "snapshot": {"items": [first, second, independent], "analysis": copy.deepcopy(analysis)}})
    report_before = store.get_report(report["id"])
    clean(store, [first, second], "trash")
    clean(store, [first, second], "purge")
    for run_id in run_ids:
        saved, metadata, _ = durable(store, run_id)
        assert saved == analysis, "Completed analysis history stays durable; current view is projected"
        assert keys <= set(metadata["purged_source_keys"])
        current = store.get_research_run(run_id)["analysis"]
        encoded = json.dumps(current)
        assert first["url"] not in encoded and second["url"] not in encoded
        assert independent["url"] in encoded
        store.checkpoint_research(run_id, analysis=copy.deepcopy(analysis), metadata={"purged_source_keys": []})
        replayed, metadata, _ = durable(store, run_id)
        assert keys <= set(metadata["purged_source_keys"])
        assert first["url"] not in json.dumps(replayed) and second["url"] not in json.dumps(replayed)
        assert independent["url"] in json.dumps(replayed)
    assert durable(store, unrelated_id) == unrelated_before
    assert store.get_report(report["id"]) == report_before
    assert store.get_item(independent["id"]) == independent
    with store._transaction() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_purge_dependency_update_and_source_delete_roll_back_together(store, monkeypatch):
    selected = material(store, 21)
    run_id = run(store, pending_analysis([selected]))
    store.checkpoint_research(run_id, items=[selected])
    store.import_items([{**selected, "content": "Corrected publisher source retains its frozen research version"}])
    clean(store, [selected], "trash")
    before = durable(store, run_id)
    with store._transaction() as connection:
        dependencies_before = {table: [tuple(row) for row in connection.execute("SELECT * FROM " + table + " ORDER BY 1,2")]
                               for table in ("wb_research_items", "wb_item_revisions")}
    assert len(dependencies_before["wb_research_items"]) == len(dependencies_before["wb_item_revisions"]) == 1
    preview = store.cleanup_preview([selected["id"]], "purge")
    execute = store._execute
    def fail_delete(connection, sql, params=()):
        if sql.startswith("DELETE FROM wb_items"):
            raise RuntimeError("Synthetic purge failure after dependency updates")
        return execute(connection, sql, params)
    monkeypatch.setattr(store, "_execute", fail_delete)
    with pytest.raises(RuntimeError, match="Synthetic purge failure"):
        store.cleanup_apply([selected["id"]], "purge", preview["preview_digest"], "Rollback regression", confirm_purge=True)
    assert durable(store, run_id) == before
    assert store.get_item(selected["id"])["metadata"]["atlas_cleanup"]["status"] == "trashed"
    with store._transaction() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert dependencies_before == {table: [tuple(row) for row in connection.execute("SELECT * FROM " + table + " ORDER BY 1,2")]
                                       for table in dependencies_before}


@pytest.mark.parametrize("review_status", ["confirmed", "rejected"])
def test_v2_stale_analysis_reconciles_only_retained_candidates_and_keeps_manual_review(store, review_status):
    selected, independent = material(store, 31), material(store, 32)
    run_id = run(store, {})
    accounts = [{"platform": "twitter", "handle": handle, "name": handle, "url": "https://x.com/" + handle}
                for handle in ("alpha", "beta")]
    nodes = [{"account_key": "twitter:" + handle, "depth": 0, "status": "completed", "score": 1}
             for handle in ("alpha", "beta")]
    claims = [{"subject_key": "twitter:alpha", "object_label": label, "relationship": "affiliation", "basis": "inferred",
               "method": "configured_ai", "statement": "Synthetic candidate", "evidence_urls": [item["url"]]}
              for label, item in (("Deleted organization", selected), ("Manually reviewed organization", independent))]
    stale = {"evidence": [{"id": "post-selected", "url": selected["url"]}, {"id": "post-independent", "url": independent["url"]}], "findings": [],
        "affiliations": [{"account_key": "twitter:alpha", "organization": "Deleted organization", "status": "candidate",
                          "evidence_ids": ["post-selected"], "evidence_urls": [selected["url"]]},
                         {"account_key": "twitter:alpha", "organization": "Manually reviewed organization", "status": "candidate",
                          "evidence_ids": ["post-independent"], "evidence_urls": [independent["url"]]}],
        "ai": {"requested": True, "complete": True, "review_version": 2, "pending_review": None}}
    store.checkpoint_research(run_id, accounts=accounts, nodes=nodes, claims=claims, analysis=stale)
    manual = next(row for row in store.export_research(run_id)["claims"] if row["object_label"] == "Manually reviewed organization")
    store.review_research_claim(run_id, manual["id"], review_status, "Independent public confirmation", independent["url"])
    clean(store, [selected], "trash")
    clean(store, [selected], "purge")
    assert durable(store, run_id)[0] == stale
    store.checkpoint_research(run_id, analysis=copy.deepcopy(stale), metadata={"purged_source_keys": []})
    with store._transaction() as connection:
        saved = {row["object_label"]: json.loads(row["record"]) for row in
                 connection.execute("SELECT object_label,record FROM wb_research_claims WHERE run_id=?", (run_id,))}
    assert saved["Deleted organization"]["analysis_current"] is False
    assert [row["organization"] for row in durable(store, run_id)[0]["affiliations"]] == ["Manually reviewed organization"]
    retained = store.export_research(run_id)["claims"]
    assert len(retained) == 1 and retained[0]["id"] == manual["id"]
    assert retained[0]["status"] == review_status and retained[0]["review_evidence_url"] == independent["url"]


def test_catalogue_only_legacy_pending_and_social_url_alias_get_permanent_tombstone(store):
    item_id = store.import_items([{"type": "social", "title": "Public original source", "content": "Synthetic original post",
        "url": "https://x.com/alpha/status/1234567890123456789", "published_at": "2026-10-09T00:00:00Z"}])["resolved_ids"][0]
    selected = store.get_item(item_id)
    alias = {**selected, "url": "https://twitter.com/alpha/status/1234567890123456789/"}
    stale = pending_analysis([alias])
    stale["ai"]["pending_review"].pop("evidence_source_keys")
    run_id = run(store, stale)
    clean(store, [selected], "trash")
    clean(store, [selected], "purge")
    store.checkpoint_research(run_id, analysis=copy.deepcopy(stale))
    saved, metadata, _ = durable(store, run_id)
    assert saved["ai"]["pending_review"] is None
    assert DELETED not in json.dumps(saved)
    assert source_url_key(alias["url"]) in metadata["purged_source_keys"]


def test_source_url_cleanup_retains_mixed_independent_support(store):
    selected, independent = material(store, 41), material(store, 42)
    analysis = {"findings": [{"source_url": selected["url"], "evidence_urls": [independent["url"]],
                              "claim": "Supported by an independent retained public file"}],
                "ai": {"complete": True, "pending_review": None}}
    run_id = run(store, analysis)
    clean(store, [selected], "trash")
    clean(store, [selected], "purge")
    assert durable(store, run_id)[0] == analysis
    current = store.get_research_run(run_id)["analysis"]["findings"]
    assert len(current) == 1 and current[0]["claim"] == analysis["findings"][0]["claim"]
    assert current[0]["evidence_urls"] == [independent["url"]] and "source_url" not in current[0]


def test_full_backup_restore_preserves_purge_and_unrelated_response_checkpoints(store, tmp_path, monkeypatch):
    from app.workbench import backup
    from app.workbench.backup import BackupService
    from app.workbench.restore import prepare_restore

    selected, independent = material(store, 51), material(store, 52)
    stale = pending_analysis([selected], omit_catalogue=True)
    stale["ai"]["pending_review"]["status"] = "response_received"
    run_id = run(store, stale)
    unrelated = pending_analysis([independent])
    unrelated_id = run(store, unrelated)
    completed = {"evidence": [{"id": "completed-post", "url": selected["url"], "excerpt": selected["content"]}],
                 "ai": {"complete": True, "pending_review": None}}
    completed_id = run(store, completed)
    report = store.save_report({"title": "Frozen report preserved by restore", "mode": "evidence", "markdown": "Synthetic report",
                               "snapshot": {"items": [selected], "analysis": copy.deepcopy(completed)}})
    clean(store, [selected], "trash")
    clean(store, [selected], "purge")
    store.checkpoint_research(run_id, analysis=copy.deepcopy(stale), metadata={"purged_source_keys": []})
    monkeypatch.setattr(backup, "TemporaryDirectory", lambda **kwargs: TemporaryDirectory(dir=tmp_path, **kwargs))
    response = BackupService().response(store)
    source = tmp_path / "full-backup.sqlite3"
    try:
        source.write_bytes(Path(response.path).read_bytes())
    finally:
        response.directory.cleanup()
    before_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    stage = tmp_path / "prepared-restore"
    prepare_restore(source, stage)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before_hash
    restored = Store(stage / "workbench.sqlite3")
    try:
        saved, metadata, _ = durable(restored, run_id)
        assert saved["ai"]["pending_review"] is None and DELETED not in json.dumps(saved)
        assert source_url_key(selected["url"]) in metadata["purged_source_keys"]
        restored.checkpoint_research(run_id, analysis=copy.deepcopy(stale), metadata={"purged_source_keys": []})
        assert durable(restored, run_id)[0]["ai"]["pending_review"] is None
        assert durable(restored, unrelated_id)[0]["ai"]["pending_review"] == unrelated["ai"]["pending_review"]
        assert durable(restored, completed_id)[0] == completed
        assert restored.get_research_run(completed_id)["analysis"]["evidence"] == []
        assert restored.get_report(report["id"])["snapshot"] == report["snapshot"]
        with restored._transaction() as connection:
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        restored.close()
