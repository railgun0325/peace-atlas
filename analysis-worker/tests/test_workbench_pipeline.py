import asyncio
import json
import socket

import httpx
import pytest

from app.workbench import pipeline as p


def article(**overrides):
    return {"id": "news-1", "title": "日本 沖縄の反戦運動", "url": "https://example.org/story",
            "content": "日本平和委員会が沖縄で平和運動について声明を発表。", "source_name": "公開新聞",
            "published_at": "2026-10-05T15:30:00Z", "type": "news", **overrides}


def run(coro):
    return asyncio.run(coro)


def report_params(**overrides):
    return {"start_date": "2026-10-06", "end_date": "2026-10-06", "mode": "evidence", **overrides}


def test_enrichment_uses_japanese_taxonomy_and_real_entities():
    result = p.enrich_item(article(content="<p>日本平和委員会と九条の会が沖縄で反戦運動。</p><script>alert(1)</script>",
                                  url="https://example.org/story?utm_source=x"))
    assert result["url"] == "https://example.org/story"
    assert "<script>" not in result["content"] and "alert" not in result["content"]
    assert "米軍基地/沖縄" in result["topics"]
    assert "日本平和委員会" in result["entities"]
    assert "公開新聞" not in result["entities"]
    assert any(loc["name"] == "沖縄県" and loc["basis"] == "text_mention" for loc in result["locations"])
    assert result["publication_day"] == "2026-10-06"


def test_domestic_article_nine_does_not_need_literal_japan_marker():
    title = "9条を守れ 平和の鐘つき 川崎・登戸"
    assert p._relevant_japan_item(title, "") is True
    assert "宪法九条/平和主義" in p.enrich_item(article(title=title, content=title))["topics"]
    assert p._relevant_japan_item("解釈改憲に異議 市民の熟議", "") is True
    assert p._relevant_japan_item("Japan baseball championships", "sports results") is False


@pytest.mark.parametrize("publisher", ["東京新聞", "Tokyo Shimbun"])
def test_publisher_suffix_does_not_create_locations_or_change_original_material(publisher):
    title = f"川崎・登戸の長念寺で鐘つき - {publisher}"
    content = f"川崎・登戸の長念寺で鐘つき {publisher}"
    result = p.enrich_item(article(title=title, content=content, source_name=publisher))
    assert not any(loc["name"] == "東京都" for loc in result["locations"])
    assert result["title"] == title
    assert result["content"] == content
    assert result["source_name"] == publisher
    assert result["content_hash"] == p.content_fingerprint(title, content)


@pytest.mark.parametrize("publisher,body", [
    ("東京新聞", "東京で集会。東京新聞が経緯を取材した。"),
    ("Tokyo Shimbun", "A gathering in Tokyo. Tokyo Shimbun reported on the event."),
])
def test_publisher_suffix_removal_preserves_real_body_mentions(publisher, body):
    content = f"{body} {publisher}"
    result = p.enrich_item(article(title=f"集会の報告 - {publisher}", content=content, source_name=publisher))
    assert any(loc["name"] == "東京都" for loc in result["locations"])
    assert p._without_publisher_suffix(content, publisher) == body


def test_publisher_suffix_is_removed_from_entity_and_topic_analysis_only():
    publisher = "日本平和委員会 九条の会"
    result = p.enrich_item(article(title=f"資料の掲載 - {publisher}",
                                  content=f"資料を掲載しました {publisher}", source_name=publisher))
    assert result["entities"] == []
    assert result["topics"] == ["未分类/待研判"]
    assert publisher in result["content"]
    assert p._without_publisher_suffix("東京新聞が報じた記事", "東京新聞") == "東京新聞が報じた記事"
    assert p._without_publisher_suffix("東京新聞", "別の新聞") == "東京新聞"


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///c:/private", "/relative", "https://user:secret@example.org/a", "https://example.org/a\n"])
def test_invalid_import_urls_are_rejected(url):
    # Leading/trailing spaces are normalized; embedded control characters are not.
    if url.endswith("\n"):
        url = "https://example.org/a\nb"
    with pytest.raises(p.PipelineError):
        p.enrich_item(article(url=url))


def test_unknown_dates_stay_unknown_and_date_only_keeps_tokyo_day():
    unknown = p.enrich_item(article(published_at=None))
    assert unknown["published_at"] is None
    assert unknown["publication_date_unknown"] is True
    day = p.enrich_item(article(published_at="2026-10-06"))
    assert day["publication_day"] == "2026-10-06"
    assert day["published_at"] == "2026-10-05T15:00:00+00:00"
    with pytest.raises(p.PipelineError, match="带时区"):
        p.enrich_item(article(published_at="2026-10-06T10:00:00"))


def test_report_scope_excludes_disabled_type_unknown_dates_and_candidates_from_main():
    candidate = article(id="social-1", type="social", title="候选账号专属标题", content="候选材料专属内容",
                        url="https://example.org/post", account_status="candidate")
    unknown = article(id="unknown", url="https://example.org/unknown", published_at=None)
    report = run(p.build_report([article(), candidate, unknown], report_params()))
    assert report["source_count"] == 2
    main, appendix = report["markdown"].split("## 待核验社媒附录")
    assert "候选账号专属标题" not in main
    assert "候选材料专属内容" not in report["markdown"]
    assert "候选账号专属标题" in appendix
    assert report["snapshot"]["exclusions"]["unknown_date"] == 1
    assert report["snapshot"]["scope"]["timezone"] == "Asia/Tokyo"
    assert "未调用 AI" in report["markdown"]
    social_only = run(p.build_report([article(), candidate], report_params(include_news=False)))
    assert social_only["source_count"] == 1
    assert "无法形成主要结论" in social_only["markdown"]


def test_verified_social_needs_explicit_evidence_and_snapshot_is_detached():
    supplied = [article(type="social", account_id="verified-account", account_status="verified", evidence_url="https://example.org/verification")]
    report = run(p.build_report(supplied, report_params()))
    assert report["snapshot"]["items"][0]["use_in_conclusions"] is True
    supplied[0]["content"] = "changed after generating"
    assert "changed after" not in report["snapshot"]["items"][0]["content"]
    missing = run(p.build_report([article(type="social", account_status="verified")], report_params()))
    assert missing["snapshot"]["items"][0]["use_in_conclusions"] is False


def test_unlinked_import_cannot_verify_its_own_social_identity():
    result = p.enrich_item(article(type="social", account_status="verified", evidence_url="https://example.org/claim"))
    assert result["account_status"] == "candidate"


def test_source_url_is_safe_for_markdown_and_encoded_citations():
    result = p.enrich_item(article(url="https://example.org/story(1)"))
    assert result["url"] == "https://example.org/story%281%29"
    with pytest.raises(p.PipelineError):
        p.enrich_item(article(url="https://example.org/<script>"))


def test_empty_or_invalid_scope_is_an_error():
    with pytest.raises(p.PipelineError, match="没有可用证据"):
        run(p.build_report([], report_params()))
    with pytest.raises(p.PipelineError, match="开始日期"):
        run(p.build_report([article()], report_params(start_date="2026-10-07")))
    with pytest.raises(p.PipelineError, match="至少选择"):
        run(p.build_report([article()], report_params(include_news=False, include_social=False)))


RSS = b'''<?xml version="1.0"?><rss version="2.0"><channel><title>Japan peace</title>
<item><title>Japan antiwar peace movement</title><link>https://example.org/one</link><pubDate>Mon, 05 Oct 2026 15:30:00 GMT</pubDate><description>Japan peace movement in Okinawa</description></item>
<item><title>Japan antiwar unknown date</title><link>https://example.org/two</link><description>Japan peace movement</description></item>
<item><title>Japan antiwar old news</title><link>https://example.org/three</link><pubDate>Mon, 05 Oct 2026 10:00:00 GMT</pubDate><description>Japan peace movement</description></item>
</channel></rss>'''


def test_collection_uses_tokyo_dates_preserves_diagnostics_and_enforces_limits(monkeypatch):
    calls = []

    async def fetch(url):
        calls.append(url)
        return RSS, url, 200

    monkeypatch.setattr(p, "_fetch_public_feed", fetch)
    result = run(p.collect({"source": "google_news", "keyword": "日本 平和", "start_date": "2026-10-06",
                           "end_date": "2026-10-06", "max_records": 10}))
    assert len(result["items"]) == 1
    assert result["items"][0]["url"] == "https://example.org/one"
    assert result["diagnostics"][0]["unknown_dates"] == 1
    assert result["diagnostics"][0]["filtered"] == 1
    assert "after%3A2026-10-05" in calls[0] and "before%3A2026-10-08" in calls[0]


def test_collection_reports_bad_html_failure_instead_of_zero_success(monkeypatch):
    async def fetch(url):
        return b"<html><h1>Please login</h1></html>", url, 200

    monkeypatch.setattr(p, "_fetch_public_feed", fetch)
    result = run(p.collect({"source": "rss", "feed_url": "https://example.org/rss", **report_params()}))
    assert result["items"] == []
    assert result["diagnostics"][0]["ok"] is False
    assert "RSS/Atom" in result["diagnostics"][0]["message"]


def test_collection_invalid_rows_report_partial_failure(monkeypatch):
    async def fetch(url):
        return RSS.replace(b"Mon, 05 Oct 2026 10:00:00 GMT", b"not-a-date"), url, 200

    monkeypatch.setattr(p, "_fetch_public_feed", fetch)
    result = run(p.collect({"source": "rss", "feed_url": "https://example.org/rss", **report_params()}))
    assert len(result["items"]) == 1
    assert result["diagnostics"][0]["ok"] is False
    assert result["diagnostics"][0]["rejected"] == 1


def test_collection_bounded_concurrency_and_partial_failures(monkeypatch):
    running = peak = 0

    async def fetch(url):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.001)
        running -= 1
        if "nhk" in url:
            raise p.PipelineError("test outage")
        return RSS, url, 200

    monkeypatch.setattr(p, "_fetch_public_feed", fetch)
    result = run(p.collect({"source": "rss", "max_records": 1, **report_params()}))
    assert peak <= 3
    assert len(result["items"]) == 1
    assert any(not diag["ok"] for diag in result["diagnostics"])
    assert any(diag["ok"] for diag in result["diagnostics"])


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "192.168.1.1"])
def test_dns_private_addresses_are_rejected(monkeypatch, address):
    async def scenario():
        async def resolve(*args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
        with pytest.raises(p.PipelineError, match="地址"):
            await p._public_address("https://example.org/rss")
    run(scenario())


def test_all_fake_ip_answers_enable_trusted_doh(monkeypatch):
    calls = []
    async def doh(hostname):
        calls.append(hostname)
        return ["8.8.4.4"], "cloudflare-dns.com"

    async def scenario():
        async def resolve(*args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.70", 443)),
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.19.0.71", 443))]
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
        monkeypatch.setattr(p, "_resolve_fake_ip", doh)
        notes = []
        token = p._DNS_DIAGNOSTICS.set(notes)
        try:
            assert await p._public_address("https://news.google.com/rss") == "8.8.4.4"
            assert notes[0]["method"] == "https_doh_fallback"
            assert notes[0]["resolver"] == "cloudflare-dns.com"
        finally:
            p._DNS_DIAGNOSTICS.reset(token)
    run(scenario())
    assert calls == ["news.google.com"]


@pytest.mark.parametrize("other", ["8.8.8.8", "2404:6800:4012:6::200e"])
def test_fake_ip_mixed_only_with_public_addresses_uses_trusted_doh(monkeypatch, other):
    calls = []

    async def doh(hostname):
        calls.append(hostname)
        return ["8.8.4.4", "2606:4700:4700::1111"], "cloudflare-dns.com"

    async def scenario():
        async def resolve(*args, **kwargs):
            return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))
                    for ip in (other, "198.18.0.70")]
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
        monkeypatch.setattr(p, "_resolve_fake_ip", doh)
        assert await p._public_address("https://news.google.com/rss") == "8.8.4.4"
    run(scenario())
    assert calls == ["news.google.com"]


@pytest.mark.parametrize("other", ["127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.1",
                                    "169.254.169.254", "fc00::1", "fe80::1", "::1", "198.51.100.1"])
def test_mixed_fake_ip_answers_do_not_allow_doh_bypass(monkeypatch, other):
    async def scenario():
        async def resolve(*args, **kwargs):
            return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))
                    for ip in ("198.18.0.1", "8.8.8.8", other)]
        async def forbidden(hostname):
            raise AssertionError("DoH must not be called")
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
        monkeypatch.setattr(p, "_resolve_fake_ip", forbidden)
        with pytest.raises(p.PipelineError, match="保留地址"):
            await p._public_address("https://example.org/rss")
    run(scenario())


def test_fake_ip_mixed_with_public_still_rejects_private_doh_result(monkeypatch):
    async def scenario():
        async def resolve(*args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))
                    for ip in ("198.18.0.70", "8.8.8.8")]
        async def doh(hostname):
            return ["8.8.4.4", "fc00::1"], "cloudflare-dns.com"
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
        monkeypatch.setattr(p, "_resolve_fake_ip", doh)
        with pytest.raises(p.PipelineError, match="保留地址"):
            await p._public_address("https://news.google.com/rss")
    run(scenario())


def test_fake_ip_literal_and_internal_names_never_use_doh():
    with pytest.raises(p.PipelineError, match="保留 IP"):
        run(p._public_address("http://198.18.0.70/private"))
    for hostname in ("localhost", "printer.lan", "router.home.arpa"):
        with pytest.raises(p.PipelineError, match="公共域名"):
            run(p._resolve_fake_ip(hostname))


def mock_doh(monkeypatch, *, private_aaaa=False, redirect=False):
    original_client = httpx.AsyncClient
    calls = []
    def handler(request):
        calls.append(request)
        if redirect:
            return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
        kind = int(request.url.params["type"])
        address = "8.8.4.4" if kind == 1 else "::1" if private_aaaa else "2606:4700:4700::1111"
        return httpx.Response(200, json={"Status": 0, "Question": [{"name": "news.google.com.", "type": kind}],
                              "Answer": [{"type": kind, "data": address}]})
    def client(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return original_client(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(p.httpx, "AsyncClient", client)
    return calls


def test_doh_bootstraps_fixed_ip_with_resolver_tls_name_and_checks_both_families(monkeypatch):
    calls = mock_doh(monkeypatch)
    addresses, resolver = run(p._resolve_fake_ip("news.google.com"))
    assert set(addresses) == {"8.8.4.4", "2606:4700:4700::1111"}
    assert resolver == "cloudflare-dns.com" and len(calls) == 2
    assert all(request.url.host == "1.1.1.1" and request.headers["host"] == resolver and
               request.extensions["sni_hostname"] == resolver for request in calls)


def test_doh_public_a_cannot_hide_private_aaaa(monkeypatch):
    calls = mock_doh(monkeypatch, private_aaaa=True)
    with pytest.raises(p.PipelineError, match="非公网"):
        run(p._resolve_fake_ip("news.google.com"))
    assert all(request.url.host == "1.1.1.1" for request in calls)


def test_doh_never_follows_redirects(monkeypatch):
    calls = mock_doh(monkeypatch, redirect=True)
    with pytest.raises(p.PipelineError, match="未成功"):
        run(p._resolve_fake_ip("news.google.com"))
    assert {request.url.host for request in calls} == {"1.1.1.1", "8.8.8.8"}


def test_fetch_pins_public_ip_and_blocks_private_redirect(monkeypatch):
    requested = []
    original_client = httpx.AsyncClient

    async def resolve(url):
        if "127.0.0.1" in url:
            raise p.PipelineError("private redirect blocked")
        return "8.8.8.8"

    def handler(request):
        requested.append(request)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    def client(**kwargs):
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(p, "_public_address", resolve)
    monkeypatch.setattr(p.httpx, "AsyncClient", client)
    with pytest.raises(p.PipelineError, match="private redirect"):
        run(p._fetch_public_feed("https://example.org/feed"))
    assert len(requested) == 1
    assert requested[0].url.host == "8.8.8.8"
    assert requested[0].headers["host"] == "example.org"
    assert requested[0].extensions["sni_hostname"] == "example.org"


def mock_ai(monkeypatch, markdown=None, status=200, finish_reason="stop"):
    original_client = httpx.AsyncClient
    captured = []

    def handler(request):
        captured.append(request)
        payload = json.loads(request.content)
        content = markdown
        data = json.loads(payload["messages"][1]["content"])
        if "candidate_report" in data:
            assert data["source_reviews"] and "[S001]" in data["candidate_report"]
            content = json.dumps({"accepted": True, "issues": []})
        elif payload.get("response_format"):
            sources = data["sources"]
            # A saved body must be read; a title cannot stand in for a contrary
            # body. Only headline-only/empty material can quote the title.
            def source_quote(row):
                if row.get("metadata", {}).get("content_kind") != "headline_only":
                    body = next((part["text"] for part in row["content_fragments"] if part["text"].strip()), "")
                    if body:
                        return body[:40]
                return row["title"][:40]
            content = json.dumps({"source_reviews": [{"citation_id": row["citation_id"], "quote": source_quote(row),
                      "reading": "记录了来源所述内容。", "limitations": "单一材料未完成独立核验。"} for row in sources]}, ensure_ascii=False)
        elif isinstance(content, str):
            # Report synthesis now cites IDs; the local renderer owns URLs.
            content = content.replace("[公開新聞](https://example.org/story)", "[S001]").replace("[来源](https://example.org/story)", "[S001]")
        return httpx.Response(status, json={"choices": [{"finish_reason": finish_reason, "message": {"content": content}}]})

    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-test-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.invalid")
    monkeypatch.setattr(p.httpx, "AsyncClient", lambda **kw: original_client(transport=httpx.MockTransport(handler), **kw))
    return captured


def test_ai_receives_only_eligible_material_and_preserves_sources(monkeypatch):
    calls = mock_ai(monkeypatch, "# 研究报告\n\n材料提及反战活动。[公開新聞](https://example.org/story)")
    candidate = article(id="candidate", type="social", content="candidate secret marker", url="https://example.org/candidate")
    report = run(p.build_report([article(), candidate], report_params(mode="ai")))
    assert len(calls) == 3
    assert [json.loads(call.content)["max_tokens"] for call in calls] == [16384, 32768, 32768]
    assert all(json.loads(call.content)["reasoning_effort"] == "high" for call in calls)
    instruction = json.loads(calls[1].content)["messages"][0]["content"]
    assert "topics、entities、locations 是工作台自动关键词规则标注，并非来源元数据" in instruction
    assert "不能把规则误匹配表述为原始报道矛盾" in instruction
    assert all(b"candidate secret marker" not in call.content for call in calls)
    assert report["mode"] == "ai"
    assert "引用存在不等于" in report["markdown"]
    assert len(report["snapshot"]["items"]) == 2
    assert len(report["snapshot"]["analysis_review"]["source_reviews"]) == 1
    assert report["snapshot"]["report_quality_review"]["requested"] is True
    review = json.loads(json.loads(calls[-1].content)["messages"][1]["content"])
    assert review["candidate_report"] == "# 研究报告\n\n材料提及反战活动。[S001]"


@pytest.mark.parametrize("reason", ["length", "content_filter", "aborted", None])
def test_incomplete_ai_output_is_not_saved(monkeypatch, reason):
    mock_ai(monkeypatch, "未完成的分析 [来源](https://example.org/story)", finish_reason=reason)
    with pytest.raises(p.PipelineError, match="未完整结束"):
        run(p.build_report([article()], report_params(mode="ai")))


@pytest.mark.parametrize("markdown", [
    "编造 [引用](https://unprovided.example/story)",
    "无链接的结论", "[已知](https://example.org/story) [恶意](javascript:alert)",
    "[已知](https://example.org/story) <script>alert(1)</script>",
    "[已知](https://example.org/story) https://other.example/extra",
    "[已知](https://example.org/story) ![远程图](https://example.org/story)",
])
def test_ai_fabricated_or_unsafe_citations_fail_without_fallback(monkeypatch, markdown):
    mock_ai(monkeypatch, markdown)
    with pytest.raises(p.PipelineError):
        run(p.build_report([article()], report_params(mode="ai")))


def test_ai_api_failure_and_missing_config_are_explicit(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(p.PipelineError, match="未配置"):
        run(p.build_report([article()], report_params(mode="ai")))
    mock_ai(monkeypatch, status=429)
    with pytest.raises(p.PipelineError, match="HTTP 429"):
        run(p.build_report([article()], report_params(mode="ai")))


@pytest.mark.parametrize("markdown", [
    "报道提及活动[来源](https://example.org/story)。这一点仍需核验。",
    "报道提及活动（[来源](https://example.org/story)），其规模未核实。",
    "[来源](https://example.org/story)后续中文没有空格。",
    "[来源](https://example.org/story)；[同一来源](https://example.org/story)，补充说明。",
    "（[来源](https://example.org/story)）。",
    "[来源（原文）](https://example.org/story)（发布日以原文为准）",
    "[来源](https://example.org/story)。原始地址：https://example.org/story。",
])
def test_ai_citation_boundary_accepts_adjacent_chinese_prose(markdown):
    p._validate_ai_citations(markdown, [article()])


@pytest.mark.parametrize("markdown", [
    "[来源](https://example.org/story)。[伪造](https://unknown.example/story)，不允许。",
    "[来源](https://example.org/story)。另见https://unknown.example/story。",
    "[来源](https://example.org/story)。[伪造](https://example.org/story/extra)。",
    "[来源](https://example.org/story)。[参考][ref]\n\n[ref]: https://example.org/story",
    "[来源](https://example.org/story)。[参考][missing]",
    "[来源](https://example.org/story)。[坏链接](javascript:alert(1))",
    "[来源](https://example.org/story)。<b>HTML 仍禁止</b>",
])
def test_ai_citation_span_parsing_does_not_allow_unknown_or_reference_links(markdown):
    with pytest.raises(p.PipelineError):
        p._validate_ai_citations(markdown, [article()])


def test_capabilities_do_not_claim_social_readiness_or_expose_key(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-sensitive-test-value")
    monkeypatch.setenv("WORKBENCH_SOCIAL_DIR", str(tmp_path / "empty-social"))
    result = p.capabilities()
    assert result["llm"]["configured"] is True
    assert all(row["state"] in {"login_required", "runtime_unavailable"} for row in result["collectors"] if row["id"] in {"twitter", "instagram"})
    assert "fake-sensitive" not in str(result)
