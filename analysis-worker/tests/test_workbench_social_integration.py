"""Persistence, migration and research boundaries across social components."""
import asyncio
import sqlite3
import time

from fastapi.testclient import TestClient
import pytest

from app.workbench import pipeline, relevance
from app.workbench.store import Store
from app.workbench.server import create_app


def accounts():
    return [{"platform": "twitter", "handle": handle, "name": handle,
             "account_key": f"twitter:{handle}", "status": "verified"} for handle in ("peace_a", "peace_b")]


def post(n=1, **changes):
    return {"title": "憲法九条を守る集会", "content": "憲法九条を守るために集会を開催します。",
            "url": f"https://x.com/peace_a/status/{n}", "published_at": "2026-10-06T00:00:00Z",
            "source_name": "X / peace_a", "platform": "twitter", "type": "social",
            "account_key": "twitter:peace_a", "metadata": {"content_kind": "body", "engagement": {"likes": 17}}, **changes}


def test_social_commit_metadata_author_relations_and_idempotence(tmp_path):
    store = Store(tmp_path / "social.db")
    relations = [{"source_key": "twitter:peace_a", "target_key": "twitter:peace_b", "relation": "follows",
                  "evidence_url": "https://x.com/peace_a/following", "observed_at": "2026-10-06T00:00:00Z"}]
    result = store.import_social([post(), post(2)], accounts(), relations)
    assert (result["inserted"], result["accounts_inserted"], result["relations_inserted"]) == (2, 2, 1)
    saved = store.get_item(result["ids"][0])
    assert saved["metadata"]["engagement"]["likes"] == 17
    assert saved["account_status"] == "candidate"
    assert saved["published_at"].startswith("2026-10-06")
    assert saved["account_id"]
    assert store.account_relations(saved["account_id"])["total"] == 1
    store.save_account({"status": "verified", "evidence_url": "https://example.org/about"}, saved["account_id"])
    again = store.import_social([post(), post(2)], accounts(), relations)
    assert (again["duplicates"], again["accounts_existing"], again["relations_existing"]) == (2, 2, 1)
    assert store.get_item(result["ids"][0])["account_status"] == "verified"


def test_atomic_social_commit_rolls_back_accounts_on_storage_error(tmp_path):
    store = Store(tmp_path / "rollback.db")
    with sqlite3.connect(store.path) as con:
        con.execute("CREATE TRIGGER fail_social BEFORE INSERT ON wb_items BEGIN SELECT RAISE(ABORT, 'disk failed'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.import_social([post()], accounts(), [])
    assert store.list_accounts() == []
    assert store.list_items()["total"] == 0


def test_old_job_schema_upgrade_preserves_jobs_events_and_fk(tmp_path):
    path = tmp_path / "upgrade.db"
    original = Store(path)
    job = original.create_job("collect", {"source": "google_news"})
    original.add_job_event(job["id"], "Original evidence")
    with sqlite3.connect(path) as con:
        # Build the exact earlier schema, exercising a real upgrade on reopen.
        schema = con.execute("SELECT sql FROM sqlite_master WHERE name='wb_jobs'").fetchone()[0]
        con.execute(schema.replace("wb_jobs", "old_jobs", 1).replace(",\u0027login\u0027", ""))
        con.execute("INSERT INTO old_jobs SELECT * FROM wb_jobs")
        con.execute("DROP TABLE wb_jobs")
        con.execute("ALTER TABLE old_jobs RENAME TO wb_jobs")
    upgraded = Store(path)
    assert upgraded.get_job(job["id"])["params"]["source"] == "google_news"
    assert upgraded.job_events(job["id"])[0]["message"] == "Original evidence"
    assert upgraded.create_job("login", {"platform": "twitter"})["kind"] == "login"
    with sqlite3.connect(path) as con:
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    Store(path)  # Repeated startup is idempotent.


@pytest.mark.parametrize("body,accepted", [
    ("憲法9条を守る集会に参加しましょう", True),
    ("東京で戦争反対の集会を行います", True),
    ("戦争反対。今週も声をあげます", True),
    ("Japan nuclear disarmament campaign", True),
    ("Okinawa military base debate", True),
    ("Japan baseball game today", False),
    ("沖縄の海で平和な休日を過ごす", False),
    ("Peaceful night in Tokyo", False),
    ("https://example.org/japan/antiwar 今日の朝ごはん", False),
    ("A peace march in Berlin", False),
    ("自衛隊のラーメンが美味しかったです。", False),
    ("今日は自衛隊の野球チームを応援しました。", False),
    ("自衛隊の防衛費について考えます。", True),
])
def test_focused_relevance_reads_body_not_broad_keywords_or_urls(body, accepted):
    result = relevance.assess(post(content=body))
    assert result["matched"] is accepted
    assert result["reasons"]


def test_broad_explicitly_preserves_query_results_and_alt_is_not_evidence():
    raw = post(content="", metadata={"content_kind": "image_alt", "image_alt": "憲法九条"})
    assert relevance.assess(raw)["matched"] is False
    assert relevance.assess(raw, "broad")["matched"] is True
    raw.update(account_id="verified", account_status="verified", evidence_url="https://example.org/about")
    report = asyncio.run(pipeline.build_report([raw], {"start_date": "2026-10-06", "end_date": "2026-10-06", "mode": "evidence"}))
    snapshot = report["snapshot"]["items"][0]
    assert snapshot["metadata"]["image_alt"] == "憲法九条"
    assert snapshot["use_in_conclusions"] is False
    assert "帖子正文尚未核对" in report["markdown"]


def wait_job(client, id):
    for _ in range(200):
        job = client.get(f"/api/v2/jobs/{id}").json()
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(.02)
    raise AssertionError("Worker did not finish")


def test_api_social_job_validation_and_candidate_report(tmp_path, monkeypatch):
    from app.workbench import social
    async def collect(params, progress=None, accept=None):
        assert params["max_records"] == 2
        assert accept(post())["matched"] is True
        if progress:
            await progress({"message": "Read incremental post batch"})
        return {"items": [post(), post(2, content="沖縄の海で平和な休日")], "accounts": accounts(), "relations": [],
                "diagnostics": [{"ok": True, "outcome": "ok", "scanned": 2}]}
    monkeypatch.setattr(social, "collect", collect)
    with TestClient(create_app(Store(tmp_path / "jobs.db"))) as client:
        params = {"source": "twitter", "mode": "keyword", "keyword": "憲法九条", "max_records": 2,
                  "start_date": "2026-10-06", "end_date": "2026-10-06"}
        submitted = client.post("/api/v2/jobs", json={"kind": "collect", "params": params})
        assert submitted.status_code == 202, submitted.text
        job = wait_job(client, submitted.json()["id"])
        assert job["status"] == "succeeded", job
        assert job["result"]["inserted"] == 1
        assert job["result"]["accounts_inserted"] == 2
        assert job["result"]["diagnostics"][-1]["irrelevant"] == 1
        item = client.get("/api/v2/items").json()["items"][0]
        assert item["metadata"]["relevance"]["matched"]
        assert item["account_status"] == "candidate"
        assert client.get(f"/api/v2/accounts/{item['account_id']}/relations").status_code == 200
        for updates in ({"mode": "account", "handle": "https://x.com/a"}, {"keyword": ""}, {"max_scrolls": 21}):
            assert client.post("/api/v2/jobs", json={"kind": "collect", "params": {**params, **updates}}).status_code == 422


def test_login_job_cancel_awaits_cleanup_and_retry_is_separate(tmp_path, monkeypatch):
    from app.workbench import social
    entered, closed = [], []
    async def login(platform):
        try:
            entered.append(platform)
            await asyncio.sleep(30)
        finally:
            closed.append(platform)
    monkeypatch.setattr(social, "login", login)
    with TestClient(create_app(Store(tmp_path / "login.db"))) as client:
        pending = client.post("/api/v2/jobs", json={"kind": "login", "params": {"platform": "twitter", "method": "manual"}})
        assert pending.status_code == 202, pending.text
        jid = pending.json()["id"]
        for _ in range(100):
            if entered:
                break
            time.sleep(.02)
        assert entered == ["twitter"]
        client.post(f"/api/v2/jobs/{jid}/cancel")
        assert wait_job(client, jid)["status"] == "cancelled"
        assert closed == ["twitter"]
        retry = client.post(f"/api/v2/jobs/{jid}/retry").json()
        assert retry["id"] != jid and retry["kind"] == "login"


def test_recapture_fills_legacy_gaps_with_same_id_and_preserves_snapshot(tmp_path):
    store = Store(tmp_path / "recapture.db")
    old = post(url="https://x.com/i/status/1", content="An old image description", account_key=None, published_at=None,
               metadata={"content_kind": "legacy_unverified", "import_source": "legacy_collectors"})
    inserted = store.import_social([old], [], [])
    old_item = store.get_item(inserted["ids"][0])
    frozen = store.save_report({"title": "Earlier archive", "start_date": "2026-10-06", "end_date": "2026-10-06",
        "mode": "evidence", "markdown": "Previous capture", "snapshot": {"items": [old_item]}, "source_count": 1})
    result = store.import_social([post()], accounts(), [])
    assert (result["inserted"], result["duplicates"], result["updated"]) == (0, 1, 1)
    complete = store.get_item(old_item["id"])
    assert complete["content"] == post()["content"]
    assert complete["published_at"] and complete["account_id"]
    assert complete["metadata"]["content_kind"] == "body"
    assert complete["account_status"] == "candidate"
    assert store.get_report(frozen["id"])["snapshot"]["items"][0] == old_item
    assert store.import_social([post()], accounts(), [])["updated"] == 0
    with sqlite3.connect(store.path) as con:
        assert con.execute("SELECT COUNT(*) FROM wb_item_revisions").fetchone()[0] == 1


def test_account_identity_edit_cannot_reassign_existing_evidence(tmp_path):
    store = Store(tmp_path / "identity.db")
    result = store.import_social([post()], accounts(), [])
    account_id = store.get_item(result["ids"][0])["account_id"]
    store.save_account({"status": "verified", "evidence_url": "https://example.org/about"}, account_id)
    with pytest.raises(ValueError, match="不能改"):
        store.save_account({"platform": "instagram", "handle": "new_identity", "url": "https://instagram.com/new_identity"}, account_id)
    assert store.get_account(account_id)["platform"] == "twitter"
    updated = store.save_account({"url": "https://x.com/other"}, account_id)
    assert updated["status"] == "candidate" and not updated["evidence_url"]
    assert store.get_item(result["ids"][0])["account_status"] == "candidate"


def test_existing_account_keys_backfilled_and_duplicate_case_rejected(tmp_path):
    store = Store(tmp_path / "keys.db")
    account = store.save_account({"name": "Existing", "platform": "twitter", "handle": "PEACE"})
    with sqlite3.connect(store.path) as con:
        con.execute("DELETE FROM wb_account_keys")
    upgraded = Store(store.path)
    with pytest.raises(ValueError, match="已存在"):
        upgraded.save_account({"name": "Duplicate", "platform": "twitter", "handle": "peace"})
    assert upgraded.import_social([], [{"platform": "twitter", "handle": "peace"}], [])["accounts_existing"] == 1
    assert upgraded.list_accounts()[0]["id"] == account["id"]


def test_legacy_times_use_utc_without_faking_post_date():
    from app.workbench.legacy_import import _legacy_utc
    assert _legacy_utc("2026-05-18 07:24:32") == "2026-05-18T07:24:32Z"
    assert _legacy_utc("2026-05-18T16:24:32+09:00") == "2026-05-18T07:24:32Z"
    assert _legacy_utc(None) is None


def test_recaptured_relation_fills_evidence_and_preserves_first_observation(tmp_path):
    store = Store(tmp_path / "relations.db")
    old = {"source_key": "twitter:peace_a", "target_key": "twitter:peace_b", "relation": "follows",
           "evidence_url": "", "observed_at": "2026-05-18T07:00:00Z"}
    store.import_social([], accounts(), [old])
    new = {**old, "evidence_url": "https://x.com/peace_a/following", "observed_at": "2026-10-06T00:00:00Z"}
    assert store.import_social([], accounts(), [new])["relations_updated"] == 1
    row = store.account_relations(store.list_accounts()[0]["id"])["items"][0]
    assert row["observed_at"].startswith("2026-05-18")
    assert row["last_observed_at"].startswith("2026-10-06")
    assert row["evidence_url"] == new["evidence_url"]
    assert store.import_social([], accounts(), [old])["relations_updated"] == 0


def test_legacy_recapture_normalizes_only_documented_utc_capture_time(tmp_path):
    store = Store(tmp_path / "capture-time.db")
    raw = post(published_at=None, metadata={"import_source": "legacy_collectors", "collected_at": "2026-05-18 07:24:32"})
    id = store.import_social([raw], accounts(), [])["ids"][0]
    corrected = {**raw, "metadata": {**raw["metadata"], "collected_at": "2026-05-18T07:24:32Z"}}
    assert store.import_social([corrected], accounts(), [])["updated"] == 1
    assert store.get_item(id)["metadata"]["collected_at"] == "2026-05-18T07:24:32Z"
    assert store.get_item(id)["published_at"] is None


@pytest.mark.parametrize("old,new", [
    ("https://x.com/i/status/123", "https://twitter.com/A/status/123"),
    ("https://x.com/i/web/status/123", "https://x.com/B/status/123/photo/1"),
    ("https://www.instagram.com/p/CODE1/", "https://www.instagram.com/owner/reel/CODE1/"),
])
def test_social_post_identity_deduplicates_equivalent_permalink_forms(tmp_path, old, new):
    store = Store(tmp_path / "aliases.db")
    assert store.import_social([post(url=old)], accounts(), [])["inserted"] == 1
    result = store.import_social([post(url=new)], accounts(), [])
    assert result["inserted"] == 0 and result["duplicates"] == 1
