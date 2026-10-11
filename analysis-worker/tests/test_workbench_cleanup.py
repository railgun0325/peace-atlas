"""Cleanup affects live selection, preserves frozen reports and can recover."""
import asyncio
import hashlib
import json
import sqlite3

from fastapi.testclient import TestClient
import pytest

from app.workbench import pipeline
from app.workbench.activity_events import extract_candidates
from app.workbench.restore import prepare_restore
from app.workbench.server import create_app
from app.workbench.store import Store


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "cleanup.sqlite3")
    yield db
    db.close()


def item(db, **changes):
    source = {"type": "news", "title": "公开活动", "content": "2026年10月5日、東京都で平和集会が開催された。", "url": "https://example.org/public", "published_at": "2026-10-05T00:00:00Z", **changes}
    return db.get_item(db.import_items([source])["resolved_ids"][0])


def clean(db, ids, action, **changes):
    preview = db.cleanup_preview(ids, action)
    return db.cleanup_apply(ids, action, preview["preview_digest"], "人工核对的清理决定", **changes)


def test_trash_excludes_material_new_report_activity_and_overview_then_restores_same_id(store):
    record = item(store)
    candidate = extract_candidates(record)[0]
    store.review_activity(record["id"], {**{k: candidate[k] for k in ("id", "title", "quote", "date", "prefecture")}, "status": "confirmed"})
    report = store.save_report(asyncio.run(pipeline.build_report(store.report_items("2026-10-01", "2026-10-06"), {"mode": "evidence"})))
    snapshot = json.dumps(report["snapshot"], sort_keys=True)
    clean(store, [record["id"]], "trash")
    assert store.list_items()["total"] == 0 and store.list_items(trash=True)["total"] == 1
    assert store.overview()["stats"]["items"] == 0 and store.overview()["recent_items"] == []
    assert store.report_items("2026-10-01", "2026-10-06") == [] and store.activities()["denominator"] == 0
    assert json.dumps(store.get_report(report["id"])["snapshot"], sort_keys=True) == snapshot
    with pytest.raises(ValueError): store.review_activity(record["id"], {**{k: candidate[k] for k in ("id", "title", "quote", "date", "prefecture")}, "status": "confirmed"})
    clean(store, [record["id"]], "restore")
    assert store.list_items()["items"][0]["id"] == record["id"] and store.activities()["denominator"] == 1


def test_preview_cannot_apply_after_source_change_or_to_extra_ids(store):
    record = item(store)
    preview = store.cleanup_preview([record["id"]], "trash")
    item(store, content="更正的正文")
    with pytest.raises(ValueError, match="预览后发生变化"):
        store.cleanup_apply([record["id"]], "trash", preview["preview_digest"], "不相关")
    assert store.list_items()["total"] == 1
    with pytest.raises(LookupError): store.cleanup_preview([record["id"], "missing"], "trash")


def test_purge_requires_trashed_record_explicit_confirmation_and_releases_research_fk(store):
    record = item(store)
    job = store.create_job("research", {"keyword": "反戦"})
    store.create_research_run(job["id"], job["params"])
    store.checkpoint_research(job["id"], items=[record])
    store.update_job(job["id"], status="succeeded")
    item(store, content="修正后的公开材料")
    assert store.item_revisions(record["id"])["total"] == 1
    with pytest.raises(ValueError): store.cleanup_preview([record["id"]], "purge")
    clean(store, [record["id"]], "trash")
    with pytest.raises(ValueError): clean(store, [record["id"]], "purge")
    clean(store, [record["id"]], "purge", confirm_purge=True)
    assert store.get_item(record["id"]) is None
    assert store.list_items(trash=True)["total"] == 0
    with store._transaction() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_ongoing_capture_blocks_cleanup_and_source_recapture_does_not_restore_it(store):
    record = item(store)
    job = store.create_job("collect", {"source": "rss"})
    with pytest.raises(ValueError, match="正在执行"): clean(store, [record["id"]], "trash")
    store.update_job(job["id"], status="succeeded")
    clean(store, [record["id"]], "trash")
    item(store, content="再次采集更新的原文")
    assert store.list_items()["total"] == 0 and store.list_items(trash=True)["total"] == 1
    with pytest.raises(ValueError): store.cleanup_preview([record["id"]], "trash")


def test_cleanup_survives_validated_backup_restore_without_mutating_source(store, tmp_path):
    record = item(store)
    clean(store, [record["id"]], "trash")
    source = tmp_path / "backup.sqlite3"
    with sqlite3.connect(store.path) as original, sqlite3.connect(source) as dest: original.backup(dest)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    prepared = tmp_path / "prepared.sqlite3"
    prepare_restore(source, prepared)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    restored = Store(prepared / "workbench.sqlite3")
    try:
        assert restored.list_items()["total"] == 0 and restored.list_items(trash=True)["total"] == 1
        clean(restored, [record["id"]], "restore")
        assert restored.list_items()["total"] == 1
    finally: restored.close()


def test_cleanup_api_preview_apply_and_input_cannot_fake_bin_decision(store, monkeypatch):
    monkeypatch.delenv("WORKBENCH_SESSION_TOKEN", raising=False)
    record = item(store, metadata={"atlas_cleanup": {"status": "trashed"}})
    assert store.list_items()["total"] == 1
    with TestClient(create_app(store, run_worker=False)) as client:
        payload = {"ids": [record["id"]], "action": "trash"}
        preview = client.post("/api/v2/items/cleanup/preview", json=payload)
        assert preview.status_code == 200
        response = client.post("/api/v2/items/cleanup/apply", json={**payload, "preview_digest": preview.json()["preview_digest"], "reason": "明确不相关"})
        assert response.status_code == 200
        assert client.get("/api/v2/items").json()["total"] == 0
        assert client.get("/api/v2/items?trash=true").json()["total"] == 1
