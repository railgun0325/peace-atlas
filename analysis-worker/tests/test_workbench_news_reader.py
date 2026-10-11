import asyncio
import copy
import json

from fastapi.testclient import TestClient
import pytest

from app.workbench import news_reader as reader, pipeline
from app.workbench.jobs import JobRunner
from app.workbench.server import create_app
from app.workbench.store import Store


TITLE = "憲法九条をめぐる公開対話プロジェクト"
BODY = "発起人は公開対話を計画している。参加人数と議案は未確定で、実施済みとの記載はない。" * 10


def page(title=TITLE, body=BODY):
    return (f'<html><head><title>{title}</title></head><body><nav>无关导航</nav><main><h1>{title}</h1><p>{body}</p></main></body></html>').encode()


def source(**changes):
    return {"title": TITLE, "content": TITLE, "url": "https://public.example/story", "type": "news",
            "source_name": "公开出版方", "published_at": "2026-10-05T00:00:00Z", "metadata": {"content_kind": "rss_summary"}, **changes}


def test_public_body_is_contextual_and_navigation_is_not_source_content():
    text, title, method = reader.extract_public_body(page(), TITLE)
    assert text == BODY and title == TITLE and "导航" not in text


def test_article_preview_cannot_be_padded_with_unrelated_recommendations():
    html = f'<html><title>{TITLE}</title><main><article><div class="article-body"><p>报道明确表示计划还未完成，但这里只呈现预览。</p></div></article><div><p>{"无关推荐新闻"*90}</p></div></main></html>'
    with pytest.raises(pipeline.PipelineError, match="短预览"):
        reader.extract_public_body(html.encode(), TITLE)


def test_visible_same_article_public_full_link_is_read_once(monkeypatch):
    calls = []
    async def fetch(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            preview = f'<html><title>{TITLE}</title><div class="article-body"><p>仅公开预览</p><a href="/story?display=1">続きを読む</a></div></html>'
            return preview.encode(), url, 200
        return page(), url, 200
    monkeypatch.setattr(pipeline, "_fetch_public_feed", fetch)
    item, diagnostic = asyncio.run(reader.capture_article(source(url="https://public.example/story")))
    assert diagnostic["ok"] and item["content"] == BODY
    assert calls == ["https://public.example/story", "https://public.example/story?display=1"]
    assert item["metadata"]["body_public_continuation_from"] == calls[0]


@pytest.mark.parametrize("href", ["https://other.example/story?display=1", "/another-story?display=1", "file:///private?display=1", "/story?login=1"])
def test_other_story_login_or_external_read_more_never_inherits_source_identity(href):
    html = f'<html><title>{TITLE}</title><a href="{href}">続きを読む</a></html>'
    assert reader.public_full_article_link(html.encode(), "https://public.example/story", TITLE) == ""


def test_one_source_dependency_cancellation_does_not_cancel_report_capture(monkeypatch):
    async def capture(item, original_url=""):
        if item["id"] == "cancelled-source":
            raise asyncio.CancelledError()
        captured = copy.deepcopy(item)
        captured["content"] = BODY
        captured["metadata"]["content_kind"] = "public_article_body"
        return captured, {"source": "public_article", "item_id": item["id"], "ok": True}
    monkeypatch.setattr(reader, "capture_article", capture)
    items, diagnostics = asyncio.run(reader.capture_sources([source(id="cancelled-source"), source(id="good-source")]))
    assert items[0]["content"] == TITLE and items[1]["content"] == BODY
    assert any(row.get("outcome") == "source_cancelled" for row in diagnostics)
    assert any(row["ok"] for row in diagnostics)


def test_user_or_shutdown_cancellation_still_closes_capture_tasks(monkeypatch):
    active, closed = asyncio.Event(), []
    async def capture(item, original_url=""):
        active.set()
        try:
            await asyncio.sleep(10)
        finally:
            closed.append(item["id"])
    monkeypatch.setattr(reader, "capture_article", capture)
    async def exercise():
        task = asyncio.create_task(reader.capture_sources([source(id="owned")]))
        await active.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(exercise())
    assert closed == ["owned"]


@pytest.mark.parametrize("response", [page(title="完全无关的商业金融新闻标题"), page(body="太短"), page(body=BODY + "この続きを読むには購読が必要です")])
def test_wrong_page_restricted_content_or_too_short_capture_rejected(response):
    with pytest.raises(pipeline.PipelineError): reader.extract_public_body(response, TITLE)


def test_jsonld_article_body_and_title_are_read_without_executing_scripts():
    body = (f'<html><head><meta property="og:title" content="{TITLE}"></head><body><script type="application/ld+json">'
            + json.dumps({"@graph": [{"@type": "NewsArticle", "articleBody": BODY}]}, ensure_ascii=False) + '</script></body></html>').encode()
    assert reader.extract_public_body(body, TITLE)[0] == BODY


def test_publisher_date_is_recorded_separately_without_overwriting_feed_date(monkeypatch):
    body = (f'<html><head><title>{TITLE}</title></head><body><script type="application/ld+json">'
            + json.dumps({"@type": "NewsArticle", "articleBody": BODY, "datePublished": "2026-10-05T19:00:00+09:00"}, ensure_ascii=False) + '</script></body></html>').encode()
    async def fetch(*args, **kwargs): return body, "https://public.example/story", 200
    monkeypatch.setattr(pipeline, "_fetch_public_feed", fetch)
    captured, diagnostic = asyncio.run(reader.capture_article(source()))
    assert diagnostic["ok"] and captured["published_at"] == "2026-10-05T00:00:00+00:00"
    assert captured["metadata"]["body_published_at"] == "2026-10-05T10:00:00+00:00"
    assert captured["metadata"]["body_publication_differs_from_feed"] is True


def test_aggregator_is_not_called_or_presented_as_a_publisher_body(monkeypatch):
    async def forbidden(*args, **kwargs): raise AssertionError("aggregator request should not happen")
    monkeypatch.setattr(pipeline, "_fetch_public_feed", forbidden)
    original = source(url="https://news.google.com/rss/articles/example")
    captured, diagnostic = asyncio.run(reader.capture_article(original))
    assert not diagnostic["ok"] and diagnostic["outcome"] == "aggregator_only"
    assert captured == original


def test_public_body_capture_keeps_identity_source_date_and_provenance(monkeypatch):
    calls = []
    async def fetch(url, **kwargs):
        calls.append((url, kwargs)); return page(), "https://publisher.example/original", 200
    monkeypatch.setattr(pipeline, "_fetch_public_feed", fetch)
    original = source(id="saved-id")
    original_copy = copy.deepcopy(original)
    captured, diagnostic = asyncio.run(reader.capture_article(original, "https://publisher.example/original"))
    assert diagnostic["ok"] and captured["content"] == BODY
    assert captured["id"] == "saved-id" and captured["url"] == original["url"] and captured["published_at"] == original["published_at"].replace("Z", "+00:00")
    assert captured["metadata"]["body_source_url"] == "https://publisher.example/original"
    assert captured["metadata"]["body_document_sha256"] and original == original_copy


def test_body_survives_summary_recapture_but_changed_headline_is_a_revision(tmp_path):
    store = Store(tmp_path / "news.sqlite3")
    summary = source()
    item_id = store.import_items([summary])["resolved_ids"][0]
    complete = source(content=BODY, metadata={"content_kind": "public_article_body", "body_source_url": "https://publisher.example/original"})
    assert store.import_items([complete])["updated"] == 1
    assert store.import_items([summary])["duplicates"] == 1
    assert store.get_item(item_id)["content"] == BODY and store.item_revisions(item_id)["total"] == 1
    assert store.import_items([source(title=TITLE + "：更正")])["updated"] == 1
    assert store.item_revisions(item_id)["total"] == 2


def test_read_route_deduplicates_queue_and_rejects_bad_range(tmp_path, monkeypatch):
    monkeypatch.delenv("WORKBENCH_SESSION_TOKEN", raising=False)
    store = Store(tmp_path / "api.sqlite3")
    item_id = store.import_items([source()])["resolved_ids"][0]
    with TestClient(create_app(store, run_worker=False)) as client:
        path = f"/api/v2/items/{item_id}/read-original"
        first = client.post(path, json={"original_url": "https://publisher.example/original"})
        second = client.post(path, json={})
        assert first.status_code == second.status_code == 202 and first.json()["id"] == second.json()["id"]
        assert second.json()["reused"] and store.list_jobs()["total"] == 1
        assert client.post(path, json={"original_url": "file:///private"}).status_code == 422
        assert client.post(path, json={"original_url": "https://user:password@example.org/"}).status_code == 422


def test_source_read_failure_is_partial_preserves_material_and_does_not_invent_body(tmp_path, monkeypatch):
    store = Store(tmp_path / "worker.sqlite3")
    item_id = store.import_items([source(url="https://news.google.com/rss/articles/example")])["resolved_ids"][0]
    async def accept():
        job = store.create_job("collect", {"_news_capture": True, "item_id": item_id, "original_url": ""})
        claimed = store.claim_job()
        await JobRunner(store)._execute(claimed)
        return store.get_job(job["id"])
    job = asyncio.run(accept())
    assert job["status"] == "partial" and job["result"]["diagnostics"][0]["outcome"] == "aggregator_only"
    assert store.get_item(item_id)["content"] == TITLE and store.item_revisions(item_id)["total"] == 0


def test_page_read_does_not_overwrite_concurrently_corrected_source(tmp_path, monkeypatch):
    store = Store(tmp_path / "concurrent.sqlite3")
    item_id = store.import_items([source()])["resolved_ids"][0]
    async def fetch(*args, **kwargs):
        store.import_items([source(title=TITLE + "：已更正", content="更正的当前资料正文。")])
        return page(), "https://public.example/story", 200
    monkeypatch.setattr(pipeline, "_fetch_public_feed", fetch)
    async def accept():
        job = store.create_job("collect", {"_news_capture": True, "item_id": item_id})
        await JobRunner(store)._execute(store.claim_job())
        return store.get_job(job["id"])
    job = asyncio.run(accept())
    assert job["status"] == "partial" and job["result"]["diagnostics"][0]["outcome"] == "source_changed"
    assert store.get_item(item_id)["content"] == "更正的当前资料正文。"


def test_report_pipeline_hydrates_body_saves_prior_version_and_cites_publisher(tmp_path, monkeypatch):
    store = Store(tmp_path / "report.sqlite3")
    item_id = store.import_items([source()])["resolved_ids"][0]
    async def fetch(url, **kwargs): return page(), "https://publisher.example/original", 200
    monkeypatch.setattr(pipeline, "_fetch_public_feed", fetch)
    async def accept():
        job = store.create_job("report", {"mode": "evidence", "start_date": "2026-10-01", "end_date": "2026-10-08", "read_original": True})
        await JobRunner(store)._execute(store.claim_job())
        return store.get_job(job["id"])
    job = asyncio.run(accept())
    assert job["status"] == "succeeded"
    report = store.get_report(job["result"]["report_id"])
    assert BODY[:80] in report["markdown"] and "https://publisher.example/original" in report["markdown"]
    assert report["snapshot"]["items"][0]["id"] == item_id and report["snapshot"]["public_body_capture"][0]["ok"]
    assert store.item_revisions(item_id)["items"][0]["capture"]["content"] == TITLE
