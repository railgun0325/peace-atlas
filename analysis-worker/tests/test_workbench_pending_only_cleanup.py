"""Pending profile reviews depend on sources without research-item links."""
import asyncio
import copy
import hashlib
import json

import pytest

from app.workbench import relationship_analysis as relationships
from app.workbench.pipeline import PipelineError
from app.workbench.research_repository import source_url_key
from app.workbench.store import Store


def pending_analysis(monkeypatch, handle):
    account = {"account_key": "twitter:" + handle, "name": handle, "status": "candidate",
        "profile": {"verified_in_dom": True, "url": f"https://x.com/{handle}/", "bio": "所属：公开团体A。",
            "observed_at": "2026-10-09T00:00:00Z"}}
    malformed = {"affiliation_candidates": [], "extra": handle + "_pending_only_raw_marker"}
    calls = []

    async def model(prompt, data, **options):
        calls.append(copy.deepcopy(data))
        if len(calls) == 1:
            return copy.deepcopy(malformed)
        raise PipelineError("HTTP 503 offline fixture")

    monkeypatch.setattr(relationships, "configured_ai_json", model)
    analysis = asyncio.run(relationships.analyze_relationships_with_ai([account], [], [], use_ai=True))
    assert analysis["ai"]["pending_review"]["raw_response"] == malformed
    assert analysis["ai"]["pending_review"]["evidence_source_keys"]
    assert analysis["ai"]["complete"] is False and len(calls) == 2
    return account, analysis


def save_profile_run(store, account, analysis):
    job = store.create_job("research", {})
    run = store.create_research_run(job["id"], {})
    handle = account["account_key"].split(":", 1)[1]
    store.checkpoint_research(run["id"], accounts=[{"platform": "twitter", "handle": handle,
        "url": account["profile"]["url"], "name": handle, "status": "candidate"}],
        nodes=[{"account_key": account["account_key"]}], analysis=analysis, phase="finished", status="partial")
    store.update_job(job["id"], status="partial")
    with store._transaction() as db:
        for table in ("wb_research_items", "wb_research_edges", "wb_research_claims", "wb_research_deferred"):
            assert db.execute("SELECT COUNT(*) FROM " + table + " WHERE run_id=?", (run["id"],)).fetchone()[0] == 0
    return run["id"]


def clean(store, item_id, action):
    preview = store.cleanup_preview([item_id], action)
    return store.cleanup_apply([item_id], action, preview["preview_digest"], "Offline pending dependency regression",
        confirm_purge=action == "purge")


def saved_analysis(store, run_id):
    with store._transaction() as db:
        row = db.execute("SELECT analysis,metadata FROM wb_research_runs WHERE id=?", (run_id,)).fetchone()
        return json.loads(row["analysis"]), json.loads(row["metadata"])


@pytest.mark.parametrize("omit_catalogue", [False, True])
def test_pending_only_source_trash_restore_purge_survives_reopen_and_preserves_unrelated(monkeypatch, tmp_path, omit_catalogue):
    account, analysis = pending_analysis(monkeypatch, "alpha")
    unrelated_account, unrelated_analysis = pending_analysis(monkeypatch, "beta")
    if omit_catalogue:
        analysis["evidence"] = []
        unrelated_analysis["evidence"] = []
    path = tmp_path / "pending-only.sqlite3"
    store = Store(path)
    try:
        run_id = save_profile_run(store, account, analysis)
        unrelated_id = save_profile_run(store, unrelated_account, unrelated_analysis)
        unrelated_saved = saved_analysis(store, unrelated_id)
        unrelated_revision = store.get_research_run(unrelated_id)["revision"]
        material = {"type": "news", "title": "Separately saved public profile",
            "url": account["profile"]["url"], "source_name": "public fixture", "content": account["profile"]["bio"],
            "published_at": "2026-10-09T00:00:00Z", "metadata": {"content_kind": "body"}}
        item_id = store.import_items([material])["resolved_ids"][0]
        report = store.save_report({"title": "Independent saved snapshot", "markdown": "Retained report",
            "mode": "evidence", "snapshot": {"items": [material], "analysis": analysis}})
        pending = analysis["ai"]["pending_review"]
        marker = pending["raw_response"]["extra"]

        clean(store, item_id, "trash")
        projected = store.get_research_run(run_id)["analysis"]
        assert projected["ai"]["pending_review"] is None
        assert marker not in json.dumps(projected)
        assert saved_analysis(store, run_id)[0]["ai"]["pending_review"] == pending
        clean(store, item_id, "restore")
        assert store.get_research_run(run_id)["analysis"]["ai"]["pending_review"] == pending
        clean(store, item_id, "trash")
        clean(store, item_id, "purge")

        durable, metadata = saved_analysis(store, run_id)
        assert durable["ai"]["pending_review"] is None
        assert marker not in json.dumps(durable)
        invalidation = durable["ai"]["pending_review_invalidation"]
        assert invalidation == {"reason": "source_purged", "removed_evidence_count": len(pending["evidence_hashes"]),
            "batch_id_sha256": hashlib.sha256(pending["batch_id"].encode("utf-8")).hexdigest()}
        assert source_url_key(material["url"]) in metadata["purged_source_keys"]
        assert store.get_item(item_id) is None
        assert saved_analysis(store, unrelated_id) == unrelated_saved
        assert store.get_research_run(unrelated_id)["revision"] == unrelated_revision
        assert store.get_report(report["id"])["snapshot"] == report["snapshot"]
        with store._transaction() as db:
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        store.close()

    reopened = Store(path)
    try:
        assert reopened.get_research_run(run_id)["analysis"]["ai"]["pending_review"] is None
        assert saved_analysis(reopened, run_id)[0]["ai"]["pending_review"] is None
        assert saved_analysis(reopened, unrelated_id) == unrelated_saved
        assert reopened.get_research_run(unrelated_id)["analysis"]["ai"]["pending_review"] == unrelated_analysis["ai"]["pending_review"]
        assert reopened.get_report(report["id"])["snapshot"] == report["snapshot"]
    finally:
        reopened.close()


def test_legacy_pending_only_catalogue_discovers_dependency_without_source_key_mapping(monkeypatch, tmp_path):
    account, analysis = pending_analysis(monkeypatch, "legacy")
    analysis["ai"]["pending_review"].pop("evidence_source_keys")
    store = Store(tmp_path / "legacy-pending-only.sqlite3")
    try:
        run_id = save_profile_run(store, account, analysis)
        item_id = store.import_items([{"type": "news", "title": "Legacy profile source",
            "url": account["profile"]["url"], "content": account["profile"]["bio"]}])["resolved_ids"][0]
        clean(store, item_id, "trash")
        assert store.get_research_run(run_id)["analysis"]["ai"]["pending_review"] is None
        clean(store, item_id, "purge")
        durable, metadata = saved_analysis(store, run_id)
        assert durable["ai"]["pending_review"] is None
        assert source_url_key(account["profile"]["url"]) in metadata["purged_source_keys"]
    finally:
        store.close()
