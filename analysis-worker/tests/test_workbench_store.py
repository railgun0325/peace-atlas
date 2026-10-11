"""Repository guarantees using disposable databases only."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from app.workbench.store import Store


@pytest.fixture
def store(tmp_path):
    repository = Store(tmp_path / "workbench-test.sqlite3")
    yield repository
    repository.close()


def item(number=1, **changes):
    return {"title": f"資料 {number}", "content": f"検証可能な本文 {number}",
            "url": f"https://example.org/articles/{number}", "type": "news",
            "source_name": "公開資料", "published_at": "2026-10-06",
            "topics": ["平和"], "entities": ["公開団体"], "locations": [], **changes}


def test_import_keeps_publishers_and_prior_versions_under_stable_url_identity(store):
    first = item(url="https://example.org/article/?utm_source=test#section")
    duplicate_url = item(2, url="https://example.org/article")
    duplicate_hash = item(url="https://example.org/another")
    result = store.import_items([first, duplicate_url, duplicate_hash])
    assert result["inserted"] == 2
    assert result["duplicates"] == 1
    assert result["updated"] == 1
    assert result["rejected"] == 0
    saved = store.get_item(result["ids"][0])
    assert saved["url"] == "https://example.org/article"
    assert saved["content"] == duplicate_url["content"]
    assert store.item_revisions(saved["id"])["items"][0]["capture"]["content"] == first["content"]
    assert store.get_item(result["ids"][1])["url"] == duplicate_hash["url"]
    assert saved["topics"] == ["平和"]
    assert saved["entities"] == ["公開団体"]


def test_import_resolves_only_accepted_evidence_and_prefers_url_identity(store):
    original = store.import_items([item(1), item(2)])
    first, second = original["ids"]
    assert original["resolved_ids"] == [first, second]
    result = store.import_items([
        item(1, url="https://example.org/reprint"),
        item(1, url=""),
        item(1, url=item(2)["url"]),  # The URL wins over a different content match.
        item(2, published_at="not-a-date"),
    ])
    assert result["resolved_ids"] == [*result["ids"], second]
    assert len(result["ids"]) == 2 and result["duplicates"] == 1 and result["rejected"] == 1
    assert store.get_item(first)["url"] == item(1)["url"]
    assert store.get_item(second)["content"] == item(1)["content"]
    assert store.item_revisions(second)["items"][0]["capture"]["content"] == item(2)["content"]


def test_import_handles_invalid_records_individually_and_no_fake_publication_date(store):
    result = store.import_items([item(published_at=None), {}, item(2, url="javascript:alert(1)"),
                                 item(3, published_at="not-a-date"), item(4, topics="not-a-list")])
    assert (result["inserted"], result["rejected"]) == (1, 4)
    assert [issue["index"] for issue in result["issues"]] == [1, 2, 3, 4]
    saved = store.get_item(result["ids"][0])
    assert saved["published_at"] is None
    assert saved["publication_date_unknown"] is True
    assert store.list_items(start_date="2026-10-06")["total"] == 0


def test_date_filters_include_exact_tokyo_calendar_day(store):
    store.import_items([
        item(1, published_at="2026-10-05T14:59:59.999999Z"),
        item(2, published_at="2026-10-05T15:00:00Z"),
        item(3, published_at="2026-10-06T14:59:59.999999Z"),
        item(4, published_at="2026-10-06T15:00:00Z"),
        item(5, published_at=None), item(6, published_at="2026-10-06"),
    ])
    rows = store.list_items(start_date="2026-10-06", end_date="2026-10-06")
    assert {row["title"] for row in rows["items"]} == {"資料 2", "資料 3", "資料 6"}
    assert rows["total"] == 3
    timeline = {entry["date"]: entry["count"] for entry in store.overview()["timeline"]}
    assert timeline == {"2026-10-05": 1, "2026-10-06": 3, "2026-10-07": 1}


def test_filters_are_parameterized_and_literal_wildcards_stay_literal(store):
    store.import_items([item(1, title="Literal 100%_phrase", topics=["peace_%"]),
                        item(2, title="Literal 100XXphrase", topics=["peace_A"]),
                        item(3, type="social", source_name="Other")])
    assert store.list_items(query="100%_")["total"] == 1
    assert store.list_items(topic="peace_%")["total"] == 1
    assert store.list_items(query="' OR 1=1 --")["total"] == 0
    assert store.list_items(type="social", source="Other")["total"] == 1
    assert store.list_items(page=2, page_size=2)["total"] == 3
    assert len(store.list_items(page=2, page_size=2)["items"]) == 1
    with pytest.raises(ValueError):
        store.list_items(start_date="2026-10-07", end_date="2026-10-06")
    with pytest.raises(ValueError):
        store.list_items(page=0)


def test_import_rolls_back_entire_batch_on_infrastructure_failure(store):
    with sqlite3.connect(store.path) as connection:
        connection.execute("""CREATE TRIGGER fail_import BEFORE INSERT ON wb_items
            WHEN NEW.title = 'force-error' BEGIN SELECT RAISE(ABORT, 'simulated disk failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="simulated disk failure"):
        store.import_items([item(1), item(2, title="force-error")])
    assert store.list_items()["total"] == 0


def test_simultaneous_imports_cannot_insert_duplicates(store):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.import_items([item(1)]), range(16)))
    assert sum(result["inserted"] for result in results) == 1
    assert sum(result["duplicates"] for result in results) == 15
    assert store.list_items()["total"] == 1


def test_account_verification_is_explicit_and_reflected_in_linked_items(store):
    account = store.save_account({"name": "公開団体", "handle": "peace", "platform": "x", "tags": ["平和"]})
    assert account["status"] == "candidate"
    imported = store.import_items([item(type="social", account_id=account["id"], account_status="verified",
                                        evidence_url="https://untrusted.example.org")])
    assert store.get_item(imported["ids"][0])["account_status"] == "candidate"
    with pytest.raises(ValueError, match="evidence_url"):
        store.save_account({"status": "verified"}, id=account["id"])
    store.save_account({"status": "verified", "evidence_url": "https://example.org/official"}, id=account["id"])
    linked = store.list_items(type="social")["items"][0]
    assert linked["account_status"] == "verified"
    assert linked["evidence_url"] == "https://example.org/official"
    assert store.get_account(account["id"])["tags"] == ["平和"]
    store.save_account({"status": "excluded"}, id=account["id"])
    assert store.get_item(imported["ids"][0])["account_status"] == "excluded"
    with pytest.raises(LookupError):
        store.save_account({"name": "missing"}, id="not-an-account")


def test_reports_same_period_remain_immutable_versions(store):
    payload = {"title": "初回報告", "start_date": "2026-10-01", "end_date": "2026-10-06",
               "mode": "evidence", "markdown": "# Original\n\n[Source](https://example.org)",
               "snapshot": [{"id": "source-a", "title": "Original evidence"}], "source_count": 1}
    first = store.save_report(payload)
    payload["markdown"] = "# Revision"
    payload["snapshot"][0]["title"] = "Changed evidence"
    second = store.save_report(payload)
    assert first["id"] != second["id"]
    assert store.get_report(first["id"])["markdown"].startswith("# Original")
    assert store.get_report(first["id"])["snapshot"][0]["title"] == "Original evidence"
    assert store.get_report(second["id"])["markdown"] == "# Revision"
    assert store.list_reports()["total"] == 2
    assert "snapshot" not in store.list_reports()["items"][0]
    assert store.overview()["stats"]["reports"] == 2


def test_claims_are_oldest_first_and_atomic_between_workers(store):
    first = store.create_job("collect", {"keyword": "平和"})
    second = store.create_job("report", {"mode": "evidence"})
    assert store.claim_job()["id"] == first["id"]
    assert store.claim_job()["id"] == second["id"]
    assert store.claim_job() is None
    created = [store.create_job("collect", {"number": index}) for index in range(16)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed = list(pool.map(lambda _: store.claim_job(), range(24)))
    ids = [job["id"] for job in claimed if job]
    assert len(ids) == len(set(ids)) == 16
    assert set(ids) == {job["id"] for job in created}
    assert all(job["started_at"] and job["status"] == "running" for job in claimed if job)


def test_cancel_queued_job_never_claimed_and_running_job_requests_stop(store):
    queued = store.create_job("collect", {})
    cancelled = store.request_cancel(queued["id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["finished_at"] and cancelled["cancel_requested"]
    assert store.claim_job() is None
    running = store.create_job("report", {})
    store.claim_job()
    stopping = store.request_cancel(running["id"])
    assert stopping["status"] == "running" and stopping["cancel_requested"]
    store.request_cancel(running["id"])
    assert len(store.job_events(running["id"])) == 1
    done = store.update_job(running["id"], status="cancelled", progress=30)
    assert done["finished_at"] and done["progress"] == 30
    assert store.request_cancel("missing") is None
    with pytest.raises(ValueError, match="终态"):
        store.update_job(running["id"], status="queued")


def test_recovery_preserves_queued_and_finished_jobs_and_records_interruption(store):
    completed = store.create_job("collect", {})
    store.claim_job()
    store.update_job(completed["id"], status="succeeded", result={"inserted": 2, "diagnostics": []})
    running = store.create_job("report", {"mode": "evidence"})
    store.claim_job()
    queued = store.create_job("collect", {"keyword": "pending"})
    assert store.recover_jobs() == 1
    assert store.get_job(running["id"])["status"] == "interrupted"
    assert store.get_job(running["id"])["finished_at"]
    assert store.get_job(queued["id"])["status"] == "queued"
    assert store.get_job(completed["id"])["result"] == {"inserted": 2, "diagnostics": []}
    assert store.get_job(completed["id"])["progress"] == 100
    assert store.job_events(running["id"])[0]["level"] == "warning"
    assert store.recover_jobs() == 0


def test_overview_contains_only_saved_evidence_and_valid_geography(store):
    assert store.overview()["stats"] == {"items": 0, "news": 0, "social": 0, "reports": 0, "active_jobs": 0}
    tokyo = {"name": "東京", "lat": 35.68, "lon": 139.69}
    imported = store.import_items([item(1, topics=["平和", "平和"], locations=[tokyo, tokyo]),
                                  item(2, type="social", locations=[{"name": "大阪"}, {"name": "Invalid", "lat": 999, "lon": 0}])])
    store.create_job("collect", {})
    overview = store.overview()
    assert overview["stats"] == {"items": 2, "news": 1, "social": 1, "reports": 0, "active_jobs": 1}
    assert overview["topics"] == [{"name": "平和", "count": 2}]
    assert overview["locations"] == [
        {"name": "大阪", "count": 1, "item_ids": [imported["ids"][1]]},
        {"name": tokyo["name"], "count": 1, "item_ids": [imported["ids"][0]]},
    ]
    assert len(overview["recent_items"]) == 2


def test_sqlite_file_connections_enable_wal_busy_timeout_and_survive_reopen(tmp_path):
    path = tmp_path / "durable.sqlite3"
    store = Store(path)
    saved = store.create_job("collect", {"keyword": "persistent"})
    with store._transaction() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 15000
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    reopened = Store(path)
    assert reopened.get_job(saved["id"])["params"] == {"keyword": "persistent"}


def test_memory_store_is_usable_across_short_lived_connections():
    store = Store(":memory:")
    try:
        assert store.import_items([item()])["inserted"] == 1
        assert store.list_items()["total"] == 1
    finally:
        store.close()
