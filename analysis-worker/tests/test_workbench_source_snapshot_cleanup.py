"""A research run keeps one source version and current graphs honor cleanup."""
import json
import sqlite3

import pytest

from app.workbench.store import Store


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "source-consistency.sqlite3")
    yield value
    value.close()


def setup_run(store, *, content="Original @beta source", kind="social", url=None):
    accounts = [{"platform": "twitter", "handle": handle, "name": handle, "url": f"https://x.com/{handle}"}
                for handle in ("alpha", "beta")]
    material = {"type": kind, "title": "Public source", "content": content,
                "url": url or "https://x.com/alpha/status/1234567890123456789",
                "published_at": "2026-10-09T00:00:00Z", "metadata": {"content_kind": "body"}}
    if kind == "social":
        material["account_key"] = "twitter:alpha"
    job = store.create_job("research", {"keyword": "public", "start_date": "2026-10-09", "end_date": "2026-10-09"})
    store.create_research_run(job["id"], job["params"])
    nodes = [{"account_key": f"twitter:{handle}", "depth": 0, "status": "completed", "score": 1}
             for handle in ("alpha", "beta")]
    store.checkpoint_research(job["id"], accounts=accounts, nodes=nodes, items=[material],
                             phase="finished", status="succeeded")
    store.update_job(job["id"], status="succeeded")
    return job["id"], store.get_research_run(job["id"])["items"][0]


def clean(store, item_id, action):
    preview = store.cleanup_preview([item_id], action)
    return store.cleanup_apply([item_id], action, preview["preview_digest"], "Source reviewed by user",
                               confirm_purge=action == "purge")


def mention(store, run_id, material, *, extra_urls=(), claims=False):
    edge = {"source_key": "twitter:alpha", "target_key": "twitter:beta", "relationship": "mentions",
            "basis": "observed", "evidence_urls": [material["url"], *extra_urls]}
    params = {"edges": [edge]}
    if claims:
        params["claims"] = [{"subject_key": "twitter:alpha", "object_label": "Public group", "relationship": "affiliation",
                             "basis": "inferred", "statement": "Candidate from this source", "method": "configured_ai",
                             "evidence_urls": [material["url"]]}]
    store.checkpoint_research(run_id, **params)


def test_recaptured_original_freezes_prior_research_source_without_changing_report(store):
    run_id, original = setup_run(store)
    analysis = {"ai": {"complete": True, "input_evidence_count": 1}, "evidence": [{"id": "post-1", "type": "post", "url": original["url"], "excerpt": original["content"]}]}
    store.checkpoint_research(run_id, analysis=analysis)
    report = store.save_report({"title": "Frozen report", "markdown": "Original report", "snapshot": {"items": [original]}, "mode": "evidence"})
    changed = {**original, "content": "Original text after platform toggle differs", "metadata": {"content_kind": "body", "text_representation": "original_after_platform_toggle"}}
    assert store.import_items([changed])["updated"] == 1
    assert store.get_item(original["id"])["content"] == changed["content"]
    assert store.export_research(run_id)["items"][0]["content"] == original["content"]
    assert store.research_graph(run_id)["items"][0]["content"] == original["content"]
    assert store.export_research(run_id)["run"]["analysis"] == analysis
    assert store.get_report(report["id"])["snapshot"] == report["snapshot"]
    reopened = Store(store.path)
    try:
        assert reopened.export_research(run_id)["items"][0]["content"] == original["content"]
    finally:
        reopened.close()


def test_news_corrections_keep_distinct_run_versions_and_one_real_revision_per_change(store):
    run_id, original = setup_run(store, kind="news", url="https://example.org/news")
    corrected = {**original, "content": "First corrected publisher body"}
    assert store.import_items([corrected])["updated"] == 1
    newer_run, newer = setup_run(store, kind="news", content=corrected["content"], url=original["url"])
    second = {**corrected, "content": "Second publisher correction"}
    assert store.import_items([second])["updated"] == 1
    assert store.export_research(run_id)["items"][0]["content"] == original["content"]
    assert store.export_research(newer_run)["items"][0]["content"] == newer["content"]
    assert store.item_revisions(original["id"])["total"] == 2
    with store._transaction() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_sole_source_mention_exits_both_graphs_on_trash_restores_then_stays_out_after_purge(store):
    run_id, material = setup_run(store)
    mention(store, run_id, material)
    assert len(store.registry_graph()["edges"]) == 1
    clean(store, material["id"], "trash")
    assert store.registry_graph()["edges"] == []
    assert store.research_graph(run_id)["edges"] == []
    assert store.export_research(run_id)["edges"] == []
    clean(store, material["id"], "restore")
    assert len(store.registry_graph()["edges"]) == 1
    clean(store, material["id"], "trash")
    clean(store, material["id"], "purge")
    assert store.get_item(material["id"]) is None
    assert store.registry_graph()["edges"] == []
    assert store.export_research(run_id)["items"] == []
    assert store.export_research(run_id)["edges"] == []
    with store._transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM wb_research_observations").fetchone()[0] == 1


def test_purge_matches_original_url_alias_and_does_not_remove_independent_follow_source(store):
    run_id, material = setup_run(store)
    mention(store, run_id, {**material, "url": "https://twitter.com/alpha/status/1234567890123456789"})
    store.checkpoint_research(run_id, edges=[{"source_key": "twitter:alpha", "target_key": "twitter:beta", "relationship": "follows", "basis": "observed", "evidence_urls": ["https://x.com/alpha/following/"], "observed_at": "2026-10-09T00:00:00Z"}])
    clean(store, material["id"], "trash")
    clean(store, material["id"], "purge")
    assert [edge["relationship"] for edge in store.registry_graph()["edges"]] == ["follows"]
    assert [edge["relationship"] for edge in store.export_research(run_id)["edges"]] == ["follows"]
    assert store.get_research_run(run_id)["relations"][0]["evidence_url"] == "https://x.com/alpha/following"


def test_deleted_candidate_source_is_hidden_but_independent_manual_confirmation_survives(store):
    run_id, material = setup_run(store)
    mention(store, run_id, material, claims=True)
    claim = store.export_research(run_id)["claims"][0]
    store.review_research_claim(run_id, claim["id"], "confirmed", "Independent public file supports it", "https://example.org/group/official")
    clean(store, material["id"], "trash")
    clean(store, material["id"], "purge")
    saved = store.export_research(run_id)["claims"][0]
    assert saved["status"] == "confirmed"
    assert saved["review_evidence_url"] == "https://example.org/group/official"
    assert material["url"] not in saved["evidence_urls"]
    assert saved["review_evidence_url"] in saved["evidence_urls"]


def test_deleted_candidate_and_source_excerpt_do_not_resurrect_in_current_export(store):
    run_id, material = setup_run(store)
    mention(store, run_id, material, claims=True)
    analysis = {"ai": {"requested": True, "complete": True, "input_evidence_count": 1, "available_evidence_count": 1, "pending_evidence_count": 0},
                "evidence": [{"id": "post-1", "type": "post", "url": material["url"], "excerpt": material["content"]}],
                "affiliations": [{"account_key": "twitter:alpha", "organization": "Public group", "claim": "Candidate from this source", "evidence_ids": ["post-1"], "evidence_urls": [material["url"]]}]}
    store.checkpoint_research(run_id, analysis=analysis)
    clean(store, material["id"], "trash")
    clean(store, material["id"], "purge")
    current = store.export_research(run_id)
    assert current["claims"] == []
    assert current["run"]["analysis"]["evidence"] == []
    assert current["run"]["analysis"]["affiliations"] == []
    with store._transaction() as connection:
        assert json.loads(connection.execute("SELECT analysis FROM wb_research_runs WHERE id=?", (run_id,)).fetchone()[0]) == analysis


def test_independent_remaining_edge_evidence_is_retained_when_one_source_is_purged(store):
    run_id, material = setup_run(store)
    mention(store, run_id, material, extra_urls=["https://example.org/independent-public-record"])
    clean(store, material["id"], "trash")
    clean(store, material["id"], "purge")
    edges = store.export_research(run_id)["edges"]
    assert len(edges) == 1
    assert edges[0]["evidence_urls"] == ["https://example.org/independent-public-record"]


def test_old_checkpoint_cannot_reimport_purged_material_or_restore_its_edge(store):
    run_id, material = setup_run(store)
    mention(store, run_id, material)
    clean(store, material["id"], "trash")
    clean(store, material["id"], "purge")
    saved = store.checkpoint_research(run_id, items=[material], edges=[{"source_key": "twitter:alpha", "target_key": "twitter:beta", "relationship": "mentions", "basis": "observed", "evidence_urls": [material["url"]]}])
    assert saved["inserted"] == 0
    assert store.list_items()["total"] == 0
    assert store.export_research(run_id)["items"] == []
    assert store.registry_graph()["edges"] == []


def test_source_freeze_and_purge_markers_survive_complete_table_backup_restore(store, tmp_path):
    from app.workbench.restore import prepare_restore
    run_id, material = setup_run(store, kind="news", url="https://example.org/news")
    mention(store, run_id, material)
    store.import_items([{**material, "content": "Corrected news body"}])
    with sqlite3.connect(store.path) as original, sqlite3.connect(tmp_path / "before-cleanup.sqlite3") as backup:
        original.backup(backup)
        assert original.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name LIKE 'wb_%'").fetchone()[0] == 23
    restored_dir = tmp_path / "restored"
    prepare_restore(tmp_path / "before-cleanup.sqlite3", restored_dir)
    restored = Store(restored_dir / "workbench.sqlite3")
    try:
        assert restored.export_research(run_id)["items"][0]["content"] == material["content"]
        assert restored.get_item(material["id"])["content"] == "Corrected news body"
        clean(restored, material["id"], "trash")
        clean(restored, material["id"], "purge")
        assert restored.registry_graph()["edges"] == []
        assert restored.export_research(run_id)["items"] == []
    finally:
        restored.close()


def test_missing_or_wrong_source_revision_never_silently_replaces_snapshot_with_live_body(store):
    run_id, material = setup_run(store, kind="news", url="https://example.org/news")
    store.import_items([{**material, "content": "Changed news body"}])
    with store._transaction(write=True) as connection:
        row = connection.execute("SELECT metadata FROM wb_research_runs WHERE id=?", (run_id,)).fetchone()
        metadata = json.loads(row[0])
        metadata["research_source_revisions"][material["id"]] = "missing-revision"
        connection.execute("UPDATE wb_research_runs SET metadata=? WHERE id=?", (json.dumps(metadata), run_id))
    with pytest.raises(ValueError, match="来源版本缺失或不匹配"):
        store.export_research(run_id)


def test_frozen_source_after_recapture_in_bin_restores_live_cleanup_status(store):
    run_id, material = setup_run(store, kind="news", url="https://example.org/news")
    clean(store, material["id"], "trash")
    store.import_items([{**material, "content": "New publisher text while source is in bin"}])
    assert store.export_research(run_id)["items"] == []
    clean(store, material["id"], "restore")
    restored = store.export_research(run_id)["items"][0]
    assert restored["content"] == material["content"]
    assert restored["metadata"]["atlas_cleanup"]["status"] == "active"


def test_live_edge_filter_applies_before_display_cap_and_preserves_real_total(store):
    run_id, material = setup_run(store)
    kinds = ["mentions", "reposts", "replies", "quotes", "shared_url", "co_event", "co_occurrence", "shared_link", "shared_event"]
    edges = [{"source_key": "twitter:alpha", "target_key": "twitter:beta", "relationship": kind,
              "basis": "observed", "evidence_urls": [material["url"] if index < 8 else "https://example.org/independent"]}
             for index, kind in enumerate(kinds)]
    store.checkpoint_research(run_id, edges=edges)
    before = store.research_graph(run_id, limit=2)
    assert before["total_edges"] == 9 and len(before["edges"]) == 8 and before["truncated"]
    clean(store, material["id"], "trash")
    after = store.research_graph(run_id, limit=2)
    assert after["total_edges"] == len(after["edges"]) == 1
    assert after["edges"][0]["relationship"] == "shared_event"


@pytest.mark.parametrize("status", ["confirmed", "rejected"])
def test_consolidated_candidate_metadata_and_more_than_one_hundred_urls_roundtrip_with_manual_review(store, status):
    run_id, _ = setup_run(store)
    urls = [f"https://example.org/source/{number}" for number in range(125)]
    claim = {"subject_key": "twitter:alpha", "object_label": "Public group", "relationship": "affiliation", "basis": "inferred",
             "statement": "Consolidated candidate", "method": "configured_ai", "evidence_urls": urls,
             "identity_version": 1, "identity_variants": [{"organization": "Public group", "claim": "Raw claim",
                                                          "raw_candidate_id": "raw-1", "evidence_ids": ["post-1"],
                                                          "evidence_urls": urls[:6], "review_batch_ids": ["batch-1"]}],
             "identity_qualifiers": ["Regional qualifier"],
             "identity_merge_audit": {"method": "same_subject_source_alias", "representative_id": "raw-1", "variant_ids": ["raw-1"]}}
    store.checkpoint_research(run_id, claims=[claim])
    saved = store.export_research(run_id)["claims"][0]
    assert saved["evidence_urls"] == urls
    for field in ("identity_version", "identity_variants", "identity_qualifiers", "identity_merge_audit"):
        assert saved[field] == claim[field]
    store.review_research_claim(run_id, saved["id"], status, "Review applies to this precise content", "https://example.org/review")
    store.checkpoint_research(run_id, claims=[{**claim, "statement": "New automatic wording", "identity_variants": []}])
    reviewed = store.export_research(run_id)["claims"][0]
    assert reviewed["id"] == saved["id"] and reviewed["status"] == status
    assert reviewed["statement"] == "Consolidated candidate"
    assert reviewed["identity_variants"] == claim["identity_variants"]


def test_oversized_identity_audit_fails_explicitly_instead_of_omitting_provenance(store):
    run_id, material = setup_run(store)
    claim = {"subject_key": "twitter:alpha", "object_label": "Public group", "relationship": "affiliation", "basis": "inferred",
             "statement": "Candidate", "evidence_urls": [material["url"]], "identity_variants": [{"claim": "x" * 300000}]}
    with pytest.raises(ValueError, match="保存内容超过大小限制"):
        store.checkpoint_research(run_id, claims=[claim])
    assert store.export_research(run_id)["claims"] == []


def test_older_analysis_without_catalog_cannot_keep_deleted_source_claim_in_current_context(store):
    run_id, material = setup_run(store)
    analysis = {"version": 1, "affiliations": [{"account_key": "twitter:alpha", "organization": "Public group", "claim": "Old candidate source text", "evidence_urls": [material["url"]]}]}
    store.checkpoint_research(run_id, analysis=analysis)
    clean(store, material["id"], "trash")
    clean(store, material["id"], "purge")
    assert store.export_research(run_id)["run"]["analysis"]["affiliations"] == []
