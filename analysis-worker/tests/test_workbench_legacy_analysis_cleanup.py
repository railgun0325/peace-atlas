"""Older unattributed analysis remains readable without becoming sourced claims."""
import copy
import json
import sqlite3
from pathlib import Path
import shutil

import pytest
from fastapi.testclient import TestClient

from app.workbench.backup import BackupService
from app.workbench.research_export import research_markdown
from app.workbench.research_reports import prepare_research_report
from app.workbench.restore import prepare_restore
from app.workbench.server import create_app
from app.workbench.store import Store


TEXT = "旧版历史文字 <待核对> | 尚无逐条来源"


def clean(store, item_id, action):
    preview = store.cleanup_preview([item_id], action)
    return store.cleanup_apply([item_id], action, preview["preview_digest"], "核对后清理无关材料",
                               confirm_purge=action == "purge")


def saved_analysis(store, run_id):
    with sqlite3.connect(store.path) as connection:
        return json.loads(connection.execute("SELECT analysis FROM wb_research_runs WHERE id=?", (run_id,)).fetchone()[0])


def seeded(store, field, version):
    items = [{"type": "news", "title": f"公开材料 {index}", "url": f"https://example.org/legacy/{index}",
              "content": f"2026年10月9日、公開資料 {index}。", "published_at": "2026-10-09T00:00:00Z"}
             for index in (1, 2)]
    job = store.create_job("research", {"analysis_mode": "rules", "research_goal": "公开材料研究"})
    store.create_research_run(job["id"], job["params"])
    analysis = {field: [TEXT], "evidence": [{"id": f"post-{index}", "type": "post", "url": item["url"],
                 "excerpt": item["content"]} for index, item in enumerate(items, 1)]}
    if version is not None:
        analysis["ai"] = {"review_version": version}
    store.checkpoint_research(job["id"], items=items, analysis=copy.deepcopy(analysis), status="succeeded")
    store.update_job(job["id"], status="succeeded")
    report = store.save_report({"title": "独立历史报告", "mode": "evidence", "markdown": TEXT,
                               "snapshot": {"items": copy.deepcopy(items)}})
    item_id = next(row["id"] for row in store.get_research_run(job["id"])["items"]
                   if row["url"] == items[0]["url"])
    return job["id"], item_id, analysis, report


def assert_readers(store, run_id, field, *, blocked):
    value = store.get_research_run(run_id)
    export = store.export_research(run_id)
    listed = next(row for row in store.list_research_runs()["items"] if row["id"] == run_id)
    # The normal lightweight list deliberately omits full analysis payloads.
    assert listed["id"] == run_id and "analysis" not in listed
    for analysis in (value["analysis"], export["run"]["analysis"]):
        assert analysis[field] == [TEXT]
        if blocked:
            assert analysis["cleanup_projection"]["legacy_unattributed_count"] == 1
            assert analysis["cleanup_projection"]["legacy_text_scope"] == "historical_without_source_support"
            assert len(analysis["evidence"]) == 1
        else:
            assert len(analysis["evidence"]) == 2
    assert value["claims"] == value["edges"] == export["claims"] == export["edges"] == []
    markdown = research_markdown(export)
    assert "历史文字（未记录可核对引用）" in markdown
    assert "旧版历史文字 &lt;待核对&gt; \\| 尚无逐条来源" in markdown
    assert "这些文字仅保留为历史记录，不作为当前关系、隶属或报告引用的依据。" in markdown
    material, context = prepare_research_report(export)
    assert TEXT not in json.dumps(material, ensure_ascii=False)
    assert context["relationship_claims"] == context["relationships"] == []
    assert len(material) == (1 if blocked else 2)
    with store._transaction() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


@pytest.mark.parametrize("field", ["findings", "affiliations"])
@pytest.mark.parametrize("version", [None, 1])
def test_accepted_legacy_checkpoint_does_not_break_readers_on_source_cleanup(tmp_path, field, version):
    store = Store(tmp_path / "reader.sqlite3")
    try:
        run_id, item_id, _, _ = seeded(store, field, version)
        assert store.get_research_run(run_id)["analysis"][field] == [TEXT]
        assert store.export_research(run_id)["run"]["analysis"][field] == [TEXT]
        assert store.list_research_runs()["total"] == 1
        clean(store, item_id, "trash")
        assert store.get_research_run(run_id)["analysis"][field] == [TEXT]
        assert store.export_research(run_id)["run"]["analysis"][field] == [TEXT]
        assert store.list_research_runs()["total"] == 1
    finally:
        store.close()


@pytest.mark.parametrize("field", ["findings", "affiliations"])
@pytest.mark.parametrize("version", [None, 1])
def test_legacy_text_survives_trash_restore_purge_and_stale_checkpoint(tmp_path, field, version):
    store = Store(tmp_path / "legacy.sqlite3")
    try:
        run_id, item_id, analysis, report = seeded(store, field, version)
        assert_readers(store, run_id, field, blocked=False)
        clean(store, item_id, "trash")
        assert_readers(store, run_id, field, blocked=True)
        assert saved_analysis(store, run_id) == analysis
        clean(store, item_id, "restore")
        assert_readers(store, run_id, field, blocked=False)
        clean(store, item_id, "trash")
        clean(store, item_id, "purge")
        assert_readers(store, run_id, field, blocked=True)
        assert saved_analysis(store, run_id) == analysis
        store.checkpoint_research(run_id, analysis=copy.deepcopy(analysis), status="partial")
        assert_readers(store, run_id, field, blocked=True)
        assert saved_analysis(store, run_id)[field] == [TEXT]
        assert len(saved_analysis(store, run_id)["evidence"]) == 1
        assert store.get_report(report["id"])["snapshot"] == report["snapshot"]
        assert store.get_report(report["id"])["markdown"] == report["markdown"]
    finally:
        store.close()
    reopened = Store(tmp_path / "legacy.sqlite3")
    try:
        assert_readers(reopened, run_id, field, blocked=True)
    finally:
        reopened.close()


@pytest.mark.parametrize("phase", ["trash", "purge"])
@pytest.mark.parametrize("field", ["findings", "affiliations"])
@pytest.mark.parametrize("version", [None, 1])
def test_legacy_text_backup_restore_remains_readable(tmp_path, field, version, phase):
    store = Store(tmp_path / "legacy.sqlite3")
    try:
        run_id, item_id, analysis, report = seeded(store, field, version)
        clean(store, item_id, "trash")
        if phase == "purge":
            clean(store, item_id, "purge")
        response = BackupService().response(store)
        original = tmp_path / "download.sqlite3"
        try:
            shutil.copyfile(Path(response.path), original)
        finally:
            response.directory.cleanup()
        prepare_restore(original, tmp_path / "prepared")
        restored = Store(tmp_path / "prepared" / "workbench.sqlite3")
        try:
            assert saved_analysis(restored, run_id) == analysis
            assert_readers(restored, run_id, field, blocked=True)
            assert restored.get_report(report["id"])["snapshot"] == report["snapshot"]
            if phase == "trash":
                clean(restored, item_id, "restore")
                assert_readers(restored, run_id, field, blocked=False)
        finally:
            restored.close()
    finally:
        store.close()


def test_mixed_legacy_text_keeps_order_and_filters_only_sourced_findings(tmp_path):
    store = Store(tmp_path / "mixed.sqlite3")
    try:
        run_id, item_id, analysis, _ = seeded(store, "findings", None)
        analysis["findings"] = [TEXT, {"summary": "removed", "evidence_ids": ["post-1"],
            "source_url": "https://example.org/legacy/1"}, "第二段历史文字",
            {"summary": "retained", "evidence_ids": ["post-2"], "source_url": "https://example.org/legacy/2"}]
        store.checkpoint_research(run_id, analysis=analysis)
        clean(store, item_id, "trash")
        value = store.get_research_run(run_id)["analysis"]
        assert [row if isinstance(row, str) else row["summary"] for row in value["findings"]] == [TEXT, "第二段历史文字", "retained"]
        assert value["cleanup_projection"]["excluded_evidence_count"] == 1
        assert value["cleanup_projection"]["legacy_unattributed_count"] == 2
        assert saved_analysis(store, run_id)["findings"] == analysis["findings"]
    finally:
        store.close()


@pytest.mark.parametrize("phase", ["trash", "purge"])
@pytest.mark.parametrize("field", ["findings", "affiliations"])
def test_legacy_graph_and_download_endpoints_after_cleanup(tmp_path, monkeypatch, field, phase):
    monkeypatch.delenv("WORKBENCH_SESSION_TOKEN", raising=False)
    store = Store(tmp_path / "endpoint.sqlite3")
    try:
        run_id, item_id, _, _ = seeded(store, field, 1)
        clean(store, item_id, "trash")
        if phase == "purge":
            clean(store, item_id, "purge")
        with TestClient(create_app(store, run_worker=False)) as client:
            listing = client.get("/api/v2/research")
            graph = client.get(f"/api/v2/research/{run_id}")
            download = client.get(f"/api/v2/research/{run_id}/export")
            markdown = client.get(f"/api/v2/research/{run_id}/report")
        assert listing.status_code == graph.status_code == download.status_code == markdown.status_code == 200
        assert listing.json()["total"] == 1
        for response in (graph, download):
            assert response.json()["run"]["analysis"][field] == [TEXT]
            assert response.json()["claims"] == response.json()["edges"] == []
        assert "历史文字（未记录可核对引用）" in markdown.text
        assert "尚无逐条来源" in markdown.text
        assert "attachment" in download.headers["Content-Disposition"]
    finally:
        store.close()
