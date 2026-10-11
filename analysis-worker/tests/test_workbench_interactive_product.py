"""Behavioral checks for public trust, real graph evidence and event counts."""
import asyncio
import json

from fastapi.testclient import TestClient
import pytest

from app.workbench import account_verification as verification
from app.workbench.activity_events import PREFECTURES, extract_candidates, item_activities
from app.workbench.jobs import JobRunner
from app.workbench.server import create_app
from app.workbench.store import Store


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "atlas.sqlite3")
    yield db
    db.close()


def account(store, handle="alpha", **values):
    return store.save_account({"name": handle, "handle": handle, "platform": "twitter", "url": "https://x.com/" + handle, **values})


def article(store, content="2026年10月5日、東京都で反戦集会が開催された。", url="https://news.example.org/a", title="反戦集会"):
    identity = store.import_items([{"type": "news", "title": title, "content": content, "url": url, "published_at": "2026-10-06T00:00:00Z"}])["ids"][0]
    return store.get_item(identity)


def confirm(store, item, **values):
    candidate = extract_candidates(item)[0]
    return store.review_activity(item["id"], {"id": candidate["id"], "title": "公開平和集会", "quote": candidate["quote"], "date": "2026-10-05", "prefecture": "東京都", "status": "confirmed", **values})


@pytest.mark.parametrize("link,allowed", [
    ("https://x.com/alpha?ref=home", True), ("https://twitter.com/ALPHA/", True),
    ("https://x.com/alpha/status/123", False), ("https://x.com/alpha_fake", False),
    ("https://x.com.evil.example/alpha", False), ("https://x.com@evil.example/alpha", False),
    ("https://x.com/intent/follow?screen_name=alpha", False)])
def test_exact_public_backlink_is_required(store, monkeypatch, link, allowed):
    a = account(store)
    async def fetch(url, **kwargs):
        assert "text/html" in kwargs["accept"]
        return f'<a href="{link}">公開アカウント</a>'.encode(), url, 200
    monkeypatch.setattr(verification.pipeline, "_fetch_public_feed", fetch)
    result = asyncio.run(verification.check_account(a, "https://organization.example.org/about"))
    assert result["verified"] is allowed
    saved = store.apply_account_checks([result])
    assert saved["verified"] == int(allowed)
    current = store.get_account(a["id"])
    assert current["status"] == ("verified" if allowed else "candidate")
    assert current["metadata"]["public_verification"]["checked_at"]
    assert current["metadata"]["public_verification"]["source_basis"] == "user_supplied"


def test_website_bio_blue_badge_and_unknown_source_do_not_verify(store, monkeypatch):
    a = account(store)
    def unexpected(*args): raise AssertionError("No approved source should be fetched")
    monkeypatch.setattr(verification.pipeline, "_fetch_public_feed", unexpected)
    result = asyncio.run(verification.check_account(a))
    assert result["outcome"] == "missing_source" and not result["verified"]
    store.apply_account_checks([result])
    assert store.get_account(a["id"])["status"] == "candidate"


def test_redirect_or_manual_edit_cannot_inherit_verification(store, monkeypatch):
    a = account(store)
    async def fetch(url, **kwargs):
        assert "text/html" in kwargs["accept"]
        return b'<a href="https://x.com/alpha">X</a>', "https://other.example.org/page", 200
    monkeypatch.setattr(verification.pipeline, "_fetch_public_feed", fetch)
    result = asyncio.run(verification.check_account(a, "https://organization.example.org/"))
    assert result["outcome"] == "source_redirected" and not result["verified"]
    result.update(verified=True, outcome="public_backlink", evidence_url="https://organization.example.org/")
    store.save_account({"status": "excluded"}, id=a["id"])
    saved = store.apply_account_checks([result])
    assert saved["verified"] == 0 and saved["diagnostics"][0]["outcome"] == "changed_during_check"
    assert store.get_account(a["id"])["status"] == "excluded"


def test_graph_keeps_isolated_accounts_and_only_literal_own_post_mentions(store):
    a, b = account(store), account(store, "beta")
    assert len(store.registry_graph()["nodes"]) == 2 and store.registry_graph()["edges"] == []
    store.import_items([{"type": "social", "account_id": a["id"], "title": "post", "content": "反戦集会 @beta contact mail@beta.example", "url": "https://x.com/alpha/status/123"}])
    article(store, "alpha and beta appeared together in an article @alpha @beta")
    graph = store.registry_graph()
    assert len(graph["edges"]) == 1
    edge = graph["edges"][0]
    assert (edge["source_key"], edge["target_key"], edge["relationship"]) == ("twitter:alpha", "twitter:beta", "mentions")
    assert edge["evidence_urls"] == ["https://x.com/i/status/123"]


def test_map_counts_activities_not_mentions_and_requires_review(store):
    assert len(PREFECTURES) == 47
    article(store, "東京の政治と大阪の歴史に関する記事。", title="政治記事")
    activity = article(store, url="https://news.example.org/activity")
    before = store.activities(status="candidate")
    assert before["denominator"] == 0 and all(row["percent"] is None for row in before["prefectures"])
    assert before["pending"] >= 1
    confirm(store, activity)
    data = store.activities()
    assert data["denominator"] == 1 and data["confirmed"] == 1
    tokyo = next(row for row in data["prefectures"] if row["name"] == "東京都")
    assert tokyo["count"] == 1 and tokyo["percent"] == 100


def test_activity_duplicate_reports_merge_and_distinct_venues_remain_separate(store):
    a = article(store)
    b = article(store, url="https://second.example.org/reprint")
    first = confirm(store, a)
    confirm(store, b)
    data = store.activities()
    assert data["denominator"] == 1 and len(data["items"][0]["sources"]) == 2
    c = article(store, "2026年10月5日、東京都新宿で反戦集会を開催した。", url="https://other.example.org/venue")
    confirm(store, c)
    assert store.activities()["denominator"] == 2, "Same broad prefecture/date/name does not prove identical venue"
    confirm(store, c, merge_with=first["event_key"])
    assert store.activities()["denominator"] == 1


def test_date_filters_and_unlocated_activity_are_explicit(store):
    a = article(store)
    confirm(store, a)
    b = article(store, "2026年10月6日、反戦集会を開催した。", url="https://news.example.org/unlocated")
    confirm(store, b, date="2026-10-06", prefecture="")
    data = store.activities("2026-10-06", "2026-10-06")
    assert (data["confirmed"], data["unlocated"], data["denominator"]) == (1, 1, 0)
    assert store.activities("2026-10-05", "2026-10-05")["denominator"] == 1
    with pytest.raises(ValueError): store.activities("2026-10-07", "2026-10-05")


def test_source_correction_invalidates_review_and_public_import_cannot_grant_it(store):
    a = article(store)
    review = confirm(store, a)
    store.import_items([{**a, "content": "訂正：2026年10月7日、京都府で反戦集会を開催。", "metadata": {"atlas_activity_reviews": {review["id"]: review}}}])
    data = store.activities()
    assert data["denominator"] == 0 and data["pending"] >= 1
    current = store.get_item(a["id"])
    assert any(row.get("stale") for row in item_activities(current))
    forged = article(store, url="https://forged.example.org/post")
    store.import_items([{**forged, "metadata": {"atlas_activity_reviews": {review["id"]: {**review, "item_id": forged["id"]}}}}])
    assert store.get_item(forged["id"])["metadata"].get("atlas_activity_reviews") is None


def test_activity_review_requires_current_quote_date_and_source(store):
    a = article(store)
    with pytest.raises(ValueError): confirm(store, a, quote="invented quote")
    with pytest.raises(ValueError): confirm(store, a, date="")
    with pytest.raises(ValueError): confirm(store, a, prefecture="Tokyo invented")
    assert store.activities()["denominator"] == 0


def test_new_api_check_guard_job_execution_and_map_roundtrip(tmp_path, monkeypatch):
    async def fetch(url, **kwargs):
        assert "text/html" in kwargs["accept"]
        return b'<a href="https://x.com/alpha">X</a>', url, 200
    monkeypatch.setattr(verification.pipeline, "_fetch_public_feed", fetch)
    with TestClient(create_app(store=Store(tmp_path / "api.sqlite3"), run_worker=False)) as client:
        a = client.post("/api/v2/accounts", json={"name": "Alpha", "handle": "alpha", "platform": "twitter", "url": "https://x.com/alpha"}).json()
        payload = {"account_ids": [a["id"]], "source_url": "https://organization.example.org/"}
        response = client.post("/api/v2/accounts/check-public-sources", json=payload)
        assert response.status_code == 202
        assert client.post("/api/v2/accounts/check-public-sources", json=payload).status_code == 409
        db = client.app.state.store
        runner = JobRunner(db)
        job = db.claim_job()
        asyncio.run(runner._execute(job))
        result = db.get_job(job["id"])["result"]
        assert result["verified"] == 1 and db.get_account(a["id"])["status"] == "verified"
        assert client.get("/api/v2/accounts/network").json()["total_nodes"] == 1
        assert client.get("/api/v2/activities").json()["denominator"] == 0
        item = article(db)
        row = extract_candidates(item)[0]
        response = client.post(f"/api/v2/items/{item['id']}/activities", json={"id": row["id"], "title": "集会", "quote": row["quote"], "date": "2026-10-05", "prefecture": "東京都"})
        assert response.status_code == 200 and client.get("/api/v2/activities").json()["denominator"] == 1
