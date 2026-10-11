"""Exact public references, source authority and observable failure regressions.

All network and account writes use controlled fixtures or a temporary SQLite DB.
Live public-page evidence is recorded separately in the QA output directory.
"""
import asyncio

import httpx
import pytest

from app.workbench import account_verification as verification, pipeline
from app.workbench.store import Store


SOURCE = "https://organization.example.org/official/"


def candidate(platform="twitter", handle="alpha", **values):
    host = "x.com" if platform == "twitter" else "www.instagram.com"
    return {"id": "fixture-alpha", "platform": platform, "handle": handle,
            "url": f"https://{host}/{handle}", "status": "candidate", **values}


def checked(monkeypatch, body, *, account=None, final_url=SOURCE, source_url=SOURCE):
    async def fetch(url, **kwargs):
        return body.encode(), final_url, 200
    monkeypatch.setattr(pipeline, "_fetch_public_feed", fetch)
    return asyncio.run(verification.check_account(account or candidate(), source_url))


@pytest.mark.parametrize("url", [
    "https://x.com:9999/alpha", "https://x.com:invalid/alpha",
    "https://x.com/@@alpha", "https://x.com//alpha///",
    "https://www.instagram.com:9999/alpha/", "https://www.instagram.com:invalid/alpha/",
    "https://www.instagram.com/@@alpha/", "https://www.instagram.com//alpha///",
    "https://[", "https://x.com/alpha/status/123", "https://x.com/intent/follow?screen_name=alpha",
    "http://x.com/alpha", "https://x.com.evil.example/alpha", "https://user@x.com/alpha",
])
def test_platform_identity_requires_an_exact_https_homepage(url):
    assert verification.account_link_key(url) is None


@pytest.mark.parametrize("url,key", [
    ("https://x.com/ALPHA/", "twitter:alpha"),
    ("https://twitter.com:443/alpha?ref=home", "twitter:alpha"),
    ("https://www.instagram.com:443/Alpha/?igsh=tracking", "instagram:alpha"),
])
def test_canonical_public_homepages_remain_usable(url, key):
    assert verification.account_link_key(url) == key


@pytest.mark.parametrize("bad_first", [True, False])
def test_one_malformed_link_does_not_discard_valid_source_evidence(monkeypatch, bad_first):
    good = '<a href="https://x.com/alpha">Published account</a>'
    bad = '<a href="https://[">Broken unrelated reference</a>'
    result = checked(monkeypatch, bad + good if bad_first else good + bad)
    assert result["outcome"] == "public_backlink" and result["verified"] is True
    assert result["evidence_url"] == SOURCE


@pytest.mark.parametrize("final_url", [
    "http://organization.example.org/official/",
    "https://organization.example.org:8443/official/",
    "https://other.example.org/official/",
])
def test_redirect_cannot_inherit_source_authority_across_scheme_port_or_site(monkeypatch, final_url):
    result = checked(monkeypatch, '<a href="https://x.com/alpha">Account</a>', final_url=final_url)
    assert result["outcome"] == "source_redirected" and result["verified"] is False
    assert "evidence_url" not in result


@pytest.mark.parametrize("section", [
    '<div id="comments"><p class="comment-content">{link}</p></div>',
    '<ol class="comment-list"><li>{link}<img src="icon.png"></li></ol>',
    '<div class="comments-area"><div class="comment-body">{link}</div></div>',
    '<template>{link}</template>',
])
def test_visitor_comments_and_inert_templates_cannot_verify_an_account(monkeypatch, section):
    result = checked(monkeypatch, section.format(link='<a href="https://x.com/alpha">visitor link</a>'))
    assert result["outcome"] == "no_backlink" and result["verified"] is False


def test_owner_link_after_comment_section_is_still_checked(monkeypatch):
    body = '<div id="comments"><img src="icon.png"><p>Visitor</p></div><footer><a href="https://x.com/alpha">Official X</a></footer>'
    assert checked(monkeypatch, body)["verified"] is True


def test_verification_uses_exact_reference_and_html_content_negotiation(monkeypatch):
    source = SOURCE + "?utm_source=required&signature=abc%2Bdef&empty="
    requests = []
    async def fetch(url, **kwargs):
        requests.append((url, kwargs))
        return b'<a href="https://x.com/alpha">X</a>', url, 200
    monkeypatch.setattr(pipeline, "_fetch_public_feed", fetch)
    result = asyncio.run(verification.check_account(candidate(), source))
    assert result["verified"] is True
    assert result["source_url"] == result["evidence_url"] == source
    assert requests[0][0] == source
    assert "text/html" in requests[0][1]["accept"]


@pytest.mark.parametrize("source", [
    "https://mobile.twitter.com/organization", "https://api.instagram.com/organization",
    "https://x.com/alpha", "http://organization.example.org/", "https://organization.example.org:invalid/",
])
def test_social_platforms_and_invalid_sources_are_not_independent_authorities(source):
    with pytest.raises(ValueError):
        verification.verification_source(candidate(), source)


@pytest.mark.parametrize("exception,reason", [
    (httpx.ReadTimeout("private raw request details"), "timeout"),
    (httpx.ConnectError("private raw request details"), "network_error"),
    (pipeline.PipelineError("采集地址 DNS 解析失败或超时。"), "dns_error"),
    (pipeline.PipelineError("订阅源重定向次数过多。"), "redirect_limit"),
])
def test_failed_source_reads_have_safe_actionable_diagnostics(monkeypatch, exception, reason):
    async def fetch(url, **kwargs):
        raise exception
    monkeypatch.setattr(pipeline, "_fetch_public_feed", fetch)
    result = asyncio.run(verification.check_account(candidate(), SOURCE))
    assert result["outcome"] == "source_unavailable" and result["verified"] is False
    assert result["failure_reason"] == reason
    assert result["error_type"] == type(exception).__name__
    assert "private raw request details" not in str(result)


def test_http_failure_records_status_without_exposing_raw_exception(monkeypatch):
    async def fetch(url, **kwargs):
        raise httpx.HTTPStatusError("private details", request=httpx.Request("GET", url), response=httpx.Response(403))
    monkeypatch.setattr(pipeline, "_fetch_public_feed", fetch)
    result = asyncio.run(verification.check_account(candidate(), SOURCE))
    assert result["outcome"] == "source_unavailable" and result["http_status"] == 403
    assert result["failure_reason"] == "http_error" and "private details" not in str(result)


def test_client_dns_diagnostics_are_saved_with_candidate_result(monkeypatch, tmp_path):
    async def fetch(url, **kwargs):
        pipeline._DNS_DIAGNOSTICS.get().append({"hostname": "organization.example.org", "method": "https_doh_fallback"})
        raise httpx.ReadTimeout("ignored")
    monkeypatch.setattr(pipeline, "_fetch_public_feed", fetch)
    db = Store(tmp_path / "isolated.sqlite3")
    try:
        a = db.save_account({"name": "Alpha", "handle": "alpha", "platform": "twitter", "url": "https://x.com/alpha"})
        result = asyncio.run(verification.check_account(a, SOURCE))
        applied = db.apply_account_checks([result])
        assert applied["verified"] == 0
        saved = db.get_account(a["id"])
        assert saved["status"] == "candidate"
        assert saved["metadata"]["public_verification"]["dns_diagnostics"][0]["method"] == "https_doh_fallback"
    finally:
        db.close()


@pytest.mark.parametrize("url", [
    "https://organization.example.org/official/",
    "https://organization.example.org/official/?utm_source=required&signature=abc%2Bdef&empty=",
    "https://organization.example.org/p;x=1/?b=2&a=1&a=3",
])
def test_public_transport_preserves_route_and_query(url):
    assert pipeline._url(url) == url


def test_canonical_slash_redirect_is_followed_to_the_actual_resource(monkeypatch):
    seen = []
    async def resolve(url):
        return "93.184.216.34"
    def request(req):
        seen.append((req.url.path, str(req.url.query)))
        if req.url.path == "/official":
            return httpx.Response(301, headers={"location": SOURCE})
        assert req.url.path == "/official/"
        return httpx.Response(200, content=b'<a href="https://x.com/alpha">Official</a>')
    original = httpx.AsyncClient
    def client(**kwargs):
        return original(transport=httpx.MockTransport(request), **kwargs)
    monkeypatch.setattr(pipeline, "_public_address", resolve)
    monkeypatch.setattr(pipeline.httpx, "AsyncClient", client)
    body, final_url, status = asyncio.run(pipeline._fetch_public_feed(SOURCE.rstrip("/")))
    assert final_url == SOURCE and status == 200 and b"alpha" in body
    assert [row[0] for row in seen] == ["/official", "/official/"]


@pytest.mark.parametrize("url", [
    "https://organization.example.org:invalid/", "https://organization.example.org:99999/",
    "https://user:password@organization.example.org/", "file:///private", "javascript:alert(1)",
    "https://organization.example.org/a\nb", "https://organization.example.org/a\\b", "https://[",
])
def test_reference_preservation_keeps_invalid_address_rejection(url):
    with pytest.raises(pipeline.PipelineError):
        pipeline._url(url)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/private", "http://198.18.0.70/private", "https://localhost/private", "https://10.0.0.1/private",
])
def test_reference_preservation_does_not_bypass_public_address_checks(url):
    with pytest.raises(pipeline.PipelineError):
        asyncio.run(pipeline._public_address(url))
