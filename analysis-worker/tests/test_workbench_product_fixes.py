"""Source/version/provenance regressions using only isolated evidence."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.dedupe import content_fingerprint
from app.workbench import pipeline
from app.workbench.backup import BackupService
from app.workbench.restore import prepare_restore
from app.workbench.server import create_app
from app.workbench.store import Store


def news(**changes):
    return {"type": "news", "title": "公開平和集会", "content": "日本の反戦集会が開かれた。",
            "url": "https://a.example.org/story", "source_name": "Publisher A", "published_at": "2026-10-05T00:00:00Z",
            "metadata": {"collection_method": "public_rss", "source_url": "https://feeds.example.org/public.xml",
                         "collected_at": "2026-10-06T00:00:00Z", "discovery_query": "反戦"}, **changes}


@pytest.fixture
def store(tmp_path):
    repository = Store(tmp_path / "evidence.sqlite3")
    yield repository
    repository.close()


def test_matching_news_text_keeps_both_publishers_and_idempotent_recapture(store):
    a, b = news(), news(url="https://b.example.org/reprint", source_name="Publisher B")
    result = store.import_items([a, b])
    assert result["inserted"] == 2 and result["duplicates"] == 0
    rows = [store.get_item(identity) for identity in result["ids"]]
    assert {row["url"] for row in rows} == {a["url"], b["url"]}
    assert {row["source_name"] for row in rows} == {"Publisher A", "Publisher B"}
    assert rows[0]["metadata"]["content_group_hash"] == rows[1]["metadata"]["content_group_hash"]
    repeated = store.import_items([a, b])
    assert (repeated["inserted"], repeated["updated"], repeated["duplicates"]) == (0, 0, 2)
    assert all(store.item_revisions(row["id"])["total"] == 0 for row in rows)


def test_correction_retains_prior_capture_and_immutable_report_across_backup_restore(store, tmp_path):
    original = news()
    identity = store.import_items([original])["ids"][0]
    report = asyncio.run(pipeline.build_report(store.report_items("2026-10-01", "2026-10-06"), {"mode": "evidence"}))
    saved_report = store.save_report(report)
    before = json.dumps(saved_report["snapshot"], ensure_ascii=False, sort_keys=True)
    corrected = news(content="更正：公開平和集会は翌日開催された。", metadata={**original["metadata"], "discovery_query": "訂正", "collected_at": "2026-10-07T00:00:00Z"})
    imported = store.import_items([corrected])
    assert imported["resolved_ids"] == [identity] and imported["updated"] == 1
    assert store.get_item(identity)["metadata"]["capture_version"] == 2
    history = store.item_revisions(identity)
    assert history["total"] == 1 and history["items"][0]["capture"]["content"] == original["content"]
    assert store.import_items([corrected])["updated"] == 0
    assert json.dumps(store.get_report(saved_report["id"])["snapshot"], ensure_ascii=False, sort_keys=True) == before
    response = BackupService().response(store)
    backup = tmp_path / "public-backup.sqlite3"
    try:
        backup.write_bytes(Path(response.path).read_bytes())
    finally:
        response.directory.cleanup()
    old_hash = hashlib.sha256(backup.read_bytes()).hexdigest()
    prepare_restore(backup, tmp_path / "restored")
    recovered = Store(tmp_path / "restored/workbench.sqlite3")
    try:
        assert recovered.get_item(identity)["content"] == corrected["content"]
        assert recovered.item_revisions(identity)["items"][0]["capture"]["metadata"]["discovery_query"] == "反戦"
        assert json.dumps(recovered.get_report(saved_report["id"])["snapshot"], ensure_ascii=False, sort_keys=True) == before
    finally:
        recovered.close()
    assert hashlib.sha256(backup.read_bytes()).hexdigest() == old_hash


def test_previously_stored_global_hash_keeps_identity_when_recaptured(store):
    a = news()
    identity = store.import_items([a])["ids"][0]
    with store._transaction(write=True) as connection:
        connection.execute("UPDATE wb_items SET content_hash=? WHERE id=?", (content_fingerprint(a["title"], a["content"]), identity))
    same = store.import_items([a])
    assert same["resolved_ids"] == [identity] and same["updated"] == 0
    b = store.import_items([news(url="https://b.example.org/reprint", source_name="Publisher B")])
    assert b["inserted"] == 1
    changed = store.import_items([news(content="更正の公開本文")])
    assert changed["resolved_ids"] == [identity] and changed["updated"] == 1
    assert store.item_revisions(identity)["total"] == 1


def test_parallel_recapture_does_not_invent_multiple_versions(store):
    identity = store.import_items([news()])["ids"][0]
    changed = news(content="更正された同一の本文")
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: store.import_items([changed]), range(8)))
    assert sum(row["updated"] for row in results) == 1
    assert store.item_revisions(identity)["total"] == 1


def test_public_metadata_survives_import_and_report_without_mutating_input(store):
    source = news()
    normalized = pipeline.enrich_item(source)
    assert normalized["metadata"] == source["metadata"] and normalized["metadata"] is not source["metadata"]
    identity = store.import_items([normalized])["ids"][0]
    result = asyncio.run(pipeline.build_report([store.get_item(identity)], {"mode": "evidence"}))
    assert result["snapshot"]["items"][0]["metadata"]["source_url"] == source["metadata"]["source_url"]
    result["snapshot"]["items"][0]["metadata"]["discovery_query"] = "changed-copy"
    assert store.get_item(identity)["metadata"]["discovery_query"] == source["metadata"]["discovery_query"]


@pytest.mark.parametrize("metadata", [{"nested": {"auth_token": "synthetic"}}, {"cookies": []}, {"api-key": "synthetic"}])
def test_non_public_metadata_is_rejected_before_store_or_report(store, metadata):
    with pytest.raises(pipeline.PipelineError):
        pipeline.enrich_item(news(metadata=metadata))
    result = store.import_items([news(metadata=metadata)])
    assert result["rejected"] == 1 and store.list_items()["total"] == 0


def test_revisions_api_pagination_and_account_status_come_from_saved_sources(store, monkeypatch):
    monkeypatch.delenv("WORKBENCH_SESSION_TOKEN", raising=False)
    identity = store.import_items([news()])["ids"][0]
    store.import_items([news(content="第二版")])
    store.import_items([news(content="第三版")])
    with TestClient(create_app(store, run_worker=False)) as client:
        page1 = client.get(f"/api/v2/items/{identity}/revisions?page_size=1").json()
        page2 = client.get(f"/api/v2/items/{identity}/revisions?page_size=1&page=2").json()
        assert page1["total"] == 2 and page1["items"][0]["capture"]["content"] == "第二版"
        assert page2["items"][0]["capture"]["content"] == news()["content"]
        assert client.get("/api/v2/items/missing/revisions").status_code == 404
