import asyncio
import json
from urllib.parse import parse_qs

import httpx
import pytest

from app.workbench import google_news_resolver as resolver, news_reader, pipeline

OPAQUE = "CBMiaA" + "A" * 50
ARTICLE = "https://news.google.com/rss/articles/" + OPAQUE + "?oc=5"
ORIGINAL = "https://publisher.example/public-news"
TITLE = "憲法九条をめぐる市民の公開対話計画"
BODY = "公開対話は来年の実施を計画している。発起人は意見交換を希望するが参加人数は未確定で、開催済みではない。" * 10


def reply(url=ORIGINAL, method="Fbv4je", tag="garturlres", request_id="0"):
    frame = [["wrb.fr", method, json.dumps([tag, url]), None, None, None, request_id]]
    return (")]}'\n\n" + str(len(json.dumps(frame))) + "\n" + json.dumps(frame) + "\n25\n" + json.dumps([["di", 0]])).encode()


def test_public_rpc_response_keeps_a_single_matching_request_and_url():
    assert resolver.parse_original_url(reply()) == ORIGINAL
    assert resolver.article_id(ARTICLE) == OPAQUE
    assert not resolver.article_id("https://other.example/rss/articles/" + OPAQUE)
    assert not resolver.article_id("https://news.google.com/rss/articles/fixture")


@pytest.mark.parametrize("body", [b"<html>login</html>", reply(method="other"), reply(tag="other"), reply(request_id="7"),
                                  reply(url="file:///private"), reply(url="https://user:secret@publisher.example/"),
                                  reply(url=ARTICLE), reply() + b"\n" + reply()])
def test_ambiguous_unrecognized_or_nonpublic_address_is_not_used(body):
    with pytest.raises(pipeline.PipelineError):
        resolver.parse_original_url(body)


def test_automatic_original_capture_checks_protocol_then_publisher_title(monkeypatch):
    calls = []
    async def fetch(url, **kwargs):
        calls.append((url, kwargs))
        if len(calls) == 1:
            return b'<html><div data-n-a-sg="public-signature" data-n-a-ts="1791460000"></div></html>', url, 200
        if url == resolver.ENDPOINT:
            payload = json.loads(parse_qs(kwargs["post_data"].decode())["f.req"][0])
            inner = json.loads(payload[0][0][1])
            assert inner[0] == "garturlreq" and inner[2:] == [OPAQUE, 1791460000, "public-signature"]
            return reply(), url, 200
        assert url == ORIGINAL and "post_data" not in kwargs
        return f'<html><title>{TITLE}</title><main><p>{BODY}</p></main></html>'.encode(), url, 200
    monkeypatch.setattr(pipeline, "_fetch_public_feed", fetch)
    source = {"id": "stable", "type": "news", "title": TITLE, "content": TITLE, "url": ARTICLE,
              "source_name": "出版方", "metadata": {"content_kind": "headline_only"}}
    item, diagnostic = asyncio.run(news_reader.capture_article(source))
    assert diagnostic["ok"] and len(calls) == 3 and item["content"] == BODY
    assert item["id"] == "stable" and item["url"] == ARTICLE
    assert item["metadata"]["body_address_resolution"] == "google_news_public_article_rpc"
    assert item["metadata"]["body_source_url"] == ORIGINAL


def test_missing_public_parameters_preserves_headline_and_diagnostics(monkeypatch):
    async def fetch(url, **kwargs): return b"<html>public aggregator shell</html>", url, 200
    monkeypatch.setattr(pipeline, "_fetch_public_feed", fetch)
    source = {"type": "news", "title": TITLE, "content": TITLE, "url": ARTICLE, "metadata": {"content_kind": "headline_only"}}
    item, diagnostic = asyncio.run(news_reader.capture_article(source))
    assert item == source and not diagnostic["ok"] and "公开地址解析参数" in diagnostic["message"]


def test_public_post_is_pinned_no_credentials_and_never_follows_redirect(monkeypatch):
    calls = []
    client = httpx.AsyncClient
    async def resolve(url): return "8.8.8.8"
    def handler(request):
        calls.append(request)
        return httpx.Response(307, headers={"location": "https://unrelated.example/"})
    monkeypatch.setattr(pipeline, "_public_address", resolve)
    monkeypatch.setattr(pipeline.httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(pipeline.PipelineError, match="未转发"):
        asyncio.run(pipeline._fetch_public_feed(resolver.ENDPOINT, post_data=b"f.req=public"))
    assert len(calls) == 1 and calls[0].method == "POST" and calls[0].url.host == "8.8.8.8"
    assert calls[0].extensions["sni_hostname"] == "news.google.com" and calls[0].headers["host"] == "news.google.com"
    assert "authorization" not in calls[0].headers and "cookie" not in calls[0].headers
    with pytest.raises(pipeline.PipelineError, match="限定端点"):
        asyncio.run(pipeline._fetch_public_feed("https://unrelated.example/", post_data=b"f.req=public"))
    assert len(calls) == 1
