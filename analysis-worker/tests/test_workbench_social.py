import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from app.workbench import social
from app.workbench.social import collector as c, common, selectors, session


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def isolated_social_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_SOCIAL_DIR", str(tmp_path / "social"))


def params(**changes):
    return {"source": "twitter", "mode": "keyword", "keyword": "平和", "start_date": "2026-10-06", "end_date": "2026-10-06",
            "max_records": 10, "max_accounts": 3, "max_scrolls": 5, "include_following": False, "max_following": 5, **changes}


def tweet(post_id="100", **changes):
    return {"status_link": f"/peace/status/{post_id}", "author_link": "/peace", "name": "Peace", "text": "平和の集会を開催。",
            "published_at": "2026-10-05T15:01:00Z", "likes": "1.2K", "replies": "3", "reposts": None,
            "image_alt": [], "cited_links": [], "social_context": "", **changes}


class Page:
    def __init__(self, frames=None, details=None, outcome=None):
        self.frames = frames or [[]]
        self.details = details or {}
        self.index = 0
        self.url = "https://x.com/home"
        self.visits = []
        self.outcome = outcome
        self.following = []
        self.search_users = []
        self.listeners = {}
        self.main_frame = object()

    def on(self, event, callback):
        self.listeners.setdefault(event, []).append(callback)

    def remove_listener(self, event, callback):
        self.listeners[event].remove(callback)

    async def goto(self, url, **kwargs):
        self.url = url
        self.visits.append(url)
        self.index = 0
        return type("Response", (), {"status": 200})()

    async def evaluate(self, script, *args):
        if "social_page_state" in script:
            return {"url": self.url, "text": self.outcome or "", "twitter_auth": True, "instagram_auth": True}
        if "social_twitter_search_state" in script:
            from urllib.parse import parse_qs, urlsplit
            query = parse_qs(urlsplit(self.url).query).get("q", [""])[0]
            ready = urlsplit(self.url).path == "/search" and query == args[0]
            return {"ready": ready, "reason": "ready" if ready else "search_route_mismatch", "loading": False, "result_text": self.outcome or ""}
        if "social_scroll" in script:
            self.index += 1
            return None
        if "social_twitter_posts" in script or "social_instagram_grid" in script:
            return self.frames[min(self.index, len(self.frames) - 1)]
        if "social_instagram_detail" in script:
            return self.details[self.url]
        if "social_twitter_accounts" in script:
            return self.following
        if "social_instagram_search" in script:
            return {"valid": True, "users": self.search_users}
        raise AssertionError("unhandled script")

    async def wait_for_selector(self, *args, **kwargs):
        return None

    def is_closed(self):
        return False


def mock_browser(monkeypatch, page):
    state = {"closed": False, "calls": []}

    @asynccontextmanager
    async def manager(platform, **kwargs):
        state["calls"].append({"platform": platform, **kwargs})
        try:
            yield None, page
        finally:
            state["closed"] = True

    async def no_wait(*args):
        await asyncio.sleep(0)

    monkeypatch.setattr(c, "browser_session", manager)
    monkeypatch.setattr(common, "pause", no_wait)
    monkeypatch.setattr(c, "pause", no_wait)
    return state


@pytest.mark.parametrize("change", [{"source": "x"}, {"max_records": 1000001}, {"max_scrolls": 0}, {"max_scrolls": 5001}, {"max_accounts": True},
                                    {"max_following": -1}, {"end_date": "2026-10-05"}, {"mode": "account", "handle": "https://evil.example/name"},
                                    {"mode": "hashtag", "keyword": "a/b"}, {"include_following": "true"}])
def test_social_parameters_are_bounded_before_browser_launch(change):
    with pytest.raises(social.SocialError) as exc:
        run(social.collect(params(**change)))
    assert exc.value.code == "invalid_params"


@pytest.mark.parametrize("keyword", ["平和 since:2026-01-01", "from:peace", "lang：ja 平和", "peace OR war", '"peace movement"', "peace -war"])
def test_advanced_query_operators_are_explicitly_rejected(keyword):
    with pytest.raises(social.SocialError, match="普通关键词"):
        common.Params.parse(params(keyword=keyword))


@pytest.mark.parametrize("keyword,text,matched", [
    ("peace", "peacemaker announcement", False),
    ("war", "award-winning news", False),
    ("Japan", "Japanese baseball", False),
    ("peace", "A PEACE movement", True),
    ("war", "anti-war movement", True),
    ("peace", "平和peace集会", True),
    ("ＰＥＡＣＥ", "peace movement", True),
    ("Article 9", "Ａｒｔｉｃｌｅ　９ peace discussion", True),
    ("憲法九条", "憲法第９条を守る", True),
    ("憲法9条", "憲法 九条を守る", True),
    ("九条", "第９条について", True),
    ("9条", "九条の会", True),
    ("9条", "第99条について", False),
    ("憲法9条", "憲法99条について", False),
])
def test_query_matching_normalizes_article_nine_and_respects_latin_words(keyword, text, matched):
    assert c._query_matches(common.Params.parse(params(keyword=keyword)), text) is matched


def test_v2_session_paths_do_not_discover_legacy_state(monkeypatch, tmp_path):
    monkeypatch.delenv("WORKBENCH_SOCIAL_DIR")
    monkeypatch.setenv("WORKBENCH_DATA_DIR", str(tmp_path / "user-data"))
    monkeypatch.setenv("WORKBENCH_DB_PATH", str(tmp_path / "db" / "workbench.sqlite3"))
    assert session.state_file("twitter") == tmp_path / "user-data/social/twitter/state.json"
    monkeypatch.delenv("WORKBENCH_DATA_DIR")
    assert session.state_file("instagram") == tmp_path / "db/social/instagram/state.json"


def test_capabilities_only_stat_own_session_never_read_cookie(monkeypatch):
    path = session.state_file("twitter")
    path.parent.mkdir(parents=True)
    path.write_text('{"cookies":[{"value":"never-disclose"}]}')
    monkeypatch.setattr(session, "_runtime", lambda: {"playwright_installed": True, "browser_installed": True, "ready": True})
    monkeypatch.setattr(Path, "read_text", lambda *args, **kw: pytest.fail("capabilities read session contents"))
    result = social.capabilities()
    assert result["platforms"][0]["state"] == "session_unverified"
    assert result["platforms"][1]["state"] == "login_required"
    assert "never-disclose" not in json.dumps(result)


def test_twitter_live_incremental_dates_dedupe_and_mapping(monkeypatch):
    first = tweet()
    second = tweet("101", published_at="2026-10-06T14:59:59Z", cited_links=["/other/status/900"], social_context="Someone reposted")
    page = Page([[first], [first, second], [second, tweet("102", published_at=None), tweet("103", published_at="2026-10-06T15:00:00Z")]])
    state = mock_browser(monkeypatch, page)
    result = run(social.collect(params()))
    assert [row["url"].rsplit("/", 1)[-1] for row in result["items"]] == ["100", "101"]
    diag = result["diagnostics"][0]
    assert diag["scanned"] == 4
    assert diag["duplicates"] > 0 and diag["stalls"] == 3
    assert diag["unknown_dates"] == 1 and diag["date_filtered"] == 1
    assert diag["outcome"] == "ok"
    assert "f=live" in page.visits[0]
    assert "since%3A2026-10-05" in page.visits[0] and "until%3A2026-10-08" in page.visits[0]
    assert result["items"][0]["metadata"]["engagement"] == {"likes": 1200, "replies": 3, "reposts": None}
    assert result["items"][1]["metadata"]["post_kind"] == "repost"
    assert result["items"][1]["metadata"]["cited_urls"] == ["https://x.com/other/status/900"]
    assert result["accounts"][0]["status"] == "candidate"
    assert state["closed"] and state["calls"][0]["visible"] is False


def test_twitter_author_and_query_checks_do_not_accept_quoted_or_unrelated_posts(monkeypatch):
    page = Page([[tweet(), tweet("102", text="野球の試合"), tweet("103", status_link="/other/status/103", author_link="/peace")]])
    mock_browser(monkeypatch, page)
    result = run(social.collect(params()))
    assert len(result["items"]) == 1
    assert result["diagnostics"][0]["query_mismatch"] == 1
    assert result["diagnostics"][0]["author_mismatch"] == 1


def test_twitter_account_mode_reads_profile_and_checks_real_author(monkeypatch):
    page = Page([[tweet(), tweet("200", status_link="/other/status/200", author_link="/other")]])
    mock_browser(monkeypatch, page)
    result = run(social.collect(params(mode="account", handle="@peace", keyword="")))
    assert len(result["items"]) == 1
    assert page.visits[0] == "https://x.com/peace"
    assert result["diagnostics"][0]["author_mismatch"] == 1


def test_relevance_filter_runs_before_twitter_author_and_record_budgets(monkeypatch):
    rows = [tweet(str(index), status_link=f"/author{index}/status/{index}", author_link=f"/author{index}", text="平和 generally unrelated") for index in range(1, 5)]
    rows.append(tweet("900", text="平和 憲法九条の集会"))
    page = Page([rows])
    mock_browser(monkeypatch, page)
    result = run(social.collect(params(max_accounts=1, max_records=1), accept=lambda item: {"matched": "憲法九条" in item["content"], "reasons": ["outside_research_scope"]}))
    assert len(result["items"]) == 1
    assert result["items"][0]["url"] == "https://x.com/peace/status/900"
    assert result["items"][0]["source_name"] == "X / @peace"
    assert result["diagnostics"][0]["irrelevant"] == 4
    assert result["diagnostics"][0]["account_limit_skipped"] == 0
    assert len(result["accounts"]) == 1


@pytest.mark.parametrize("notice,outcome", [("Rate limit exceeded", "rate_limited"), ("Verify you are human", "challenge"), ("This account is private", "private_account")])
def test_platform_denials_are_not_zero_success(monkeypatch, notice, outcome):
    page = Page(outcome=notice)
    state = mock_browser(monkeypatch, page)
    result = run(social.collect(params()))
    assert result["items"] == []
    assert result["diagnostics"][0]["ok"] is False
    assert result["diagnostics"][0]["outcome"] == outcome
    assert state["closed"]


def test_empty_changed_dom_is_parse_failure_not_claimed_no_results(monkeypatch):
    page = Page()
    mock_browser(monkeypatch, page)
    result = run(social.collect(params()))
    assert result["diagnostics"][0]["outcome"] == "parse_error"


def test_network_diagnostics_preserve_type_and_safe_code_without_secrets(monkeypatch, caplog):
    page = Page()
    async def failure(*args, **kwargs):
        raise RuntimeError("net::ERR_PROXY_CONNECTION_FAILED https://secret:password@example.test Authorization: do-not-expose")
    page.goto = failure
    mock_browser(monkeypatch, page)
    result = run(social.collect(params()))
    diag = result["diagnostics"][0]
    assert diag["error_type"] == "RuntimeError"
    assert diag["network_code"] == "net::ERR_PROXY_CONNECTION_FAILED"
    assert diag["stage"] == "navigate"
    assert "password" not in json.dumps(result) + caplog.text
    assert "do-not-expose" not in json.dumps(result) + caplog.text


@pytest.mark.parametrize("status,main_document,expected", [(403, True, "access_denied"), (429, True, "rate_limited"), (429, False, "http_error"), (None, True, "http_error")])
def test_navigation_http_failure_records_only_observed_main_document_status(monkeypatch, status, main_document, expected):
    page = Page()

    async def failure(*args, **kwargs):
        if status:
            request = type("Request", (), {"frame": page.main_frame if main_document else object(), "is_navigation_request": lambda self: True})()
            response = type("Response", (), {"request": request, "status": status})()
            for callback in page.listeners.get("response", []):
                callback(response)
        raise RuntimeError("net::ERR_HTTP_RESPONSE_CODE_FAILURE https://secret:password@x.com/?auth=private")

    page.goto = failure
    mock_browser(monkeypatch, page)
    result = run(social.collect(params()))
    diag = result["diagnostics"][0]
    assert diag["outcome"] == expected
    assert diag["network_code"] == "net::ERR_HTTP_RESPONSE_CODE_FAILURE"
    assert diag.get("http_status") == (status if main_document else None)
    if status is None or not main_document:
        assert "未取得可核验" in diag["message"]
    assert page.listeners["response"] == []
    assert "private" not in json.dumps(result) and "password" not in json.dumps(result)


def test_navigation_returned_http_status_does_not_require_reading_failed_document(monkeypatch):
    page = Page()
    async def response(*args, **kwargs):
        return type("Response", (), {"status": 502})()
    page.goto = response
    mock_browser(monkeypatch, page)
    result = run(social.collect(params()))
    assert result["diagnostics"][0]["outcome"] == "http_error"
    assert result["diagnostics"][0]["http_status"] == 502


@pytest.mark.parametrize("url,expected", [
    ("https://x.com/i/jf/onboarding/web?redirect_after_login=%2Fsearch%3Fq%3Dprivate&mode=login", True),
    ("https://x.com/i/jf/onboarding/web?mode=signup", False),
    ("https://x.com/i/jf/onboarding/web", False),
    ("https://example.org/i/jf/onboarding/web?mode=login", False),
])
def test_new_x_login_route_requires_actual_platform_path_and_login_mode(url, expected):
    assert common.login_url("twitter", url) is expected
    if expected:
        assert common.page_outcome("twitter", {"url": url, "text": ""}) == "login_required"


def test_failed_navigation_recognizes_observed_login_redirect_without_leaking_query(monkeypatch):
    page = Page()
    async def failure(*args, **kwargs):
        request = type("Request", (), {"frame": page.main_frame, "is_navigation_request": lambda self: True})()
        response = type("Response", (), {"request": request, "status": 200,
            "url": "https://x.com/i/jf/onboarding/web?mode=login&redirect_after_login=private-query"})()
        for callback in page.listeners.get("response", []):
            callback(response)
        raise RuntimeError("net::ERR_HTTP_RESPONSE_CODE_FAILURE")
    page.goto = failure
    mock_browser(monkeypatch, page)
    result = run(social.collect(params()))
    diag = result["diagnostics"][0]
    assert diag["outcome"] == "login_required"
    assert diag["observed_redirect_kind"] == "login"
    assert "private-query" not in json.dumps(result)


def test_explicit_no_results_is_an_honest_empty_sample(monkeypatch):
    page = Page(outcome="No results for this search")
    mock_browser(monkeypatch, page)
    result = run(social.collect(params()))
    assert result["diagnostics"][0]["outcome"] == "no_matches"
    assert result["diagnostics"][0]["ok"] is True


def test_instagram_waits_for_delayed_grid_and_primary_detail(monkeypatch):
    page = Page([[{"href": "/p/late/"}]], {"https://www.instagram.com/p/late/": {
        "handle": "peace", "caption": "平和", "published_at": "2026-10-06T02:00:00Z", "detail_present": True}})
    original = page.evaluate
    reads = {"grid": 0, "detail": 0}

    async def delayed(script, *args):
        if "social_instagram_grid" in script:
            reads["grid"] += 1
            if reads["grid"] <= 12:  # Six simulated seconds of SPA loading.
                return []
        if "social_instagram_detail" in script:
            reads["detail"] += 1
            if reads["detail"] <= 8:
                return {"detail_present": True, "handle": "peace", "published_at": None}
        return await original(script, *args)

    page.evaluate = delayed
    state = mock_browser(monkeypatch, page)
    result = run(social.collect(params(source="instagram", mode="account", handle="peace", max_scrolls=1)))
    assert len(result["items"]) == 1
    diag = result["diagnostics"][0]
    assert diag["initial_wait_polls"] >= 13 and diag["detail_wait_polls"] == 9
    assert len(page.visits) == 2 and state["closed"]


def test_delayed_instagram_login_redirect_is_not_parse_error(monkeypatch):
    page = Page()
    original = page.evaluate
    reads = 0

    async def delayed(script, *args):
        nonlocal reads
        if "social_instagram_grid" in script:
            reads += 1
            if reads == 12:
                page.url = "https://www.instagram.com/accounts/login/?next=private-query"
            return []
        return await original(script, *args)

    page.evaluate = delayed
    state = mock_browser(monkeypatch, page)
    result = run(social.collect(params(source="instagram", mode="hashtag")))
    diag = result["diagnostics"][0]
    assert diag["outcome"] == "login_required"
    assert diag["stage"] == "waiting_for_content" and diag["observed_redirect_kind"] == "login"
    assert len(page.visits) == 1 and state["closed"]
    assert "private-query" not in json.dumps(result)


def test_scroll_waits_for_new_ids_without_reloading(monkeypatch):
    page = Page([[tweet()], [tweet("101")]])
    original = page.evaluate
    reads = 0

    async def delayed(script, *args):
        nonlocal reads
        if "social_twitter_posts" in script and page.index == 1:
            reads += 1
            if reads <= 2:
                return [tweet()]
        return await original(script, *args)

    page.evaluate = delayed
    mock_browser(monkeypatch, page)
    result = run(social.collect(params(max_records=2)))
    assert len(result["items"]) == 2
    assert result["diagnostics"][0]["scroll_wait_polls"] == 3
    assert result["diagnostics"][0]["stalls"] == 0 and len(page.visits) == 1


def test_permanent_skeleton_has_bounded_wait_and_exactly_one_safe_navigation_retry(monkeypatch):
    page = Page()
    original = page.evaluate
    reads = 0

    async def loading(script, *args):
        nonlocal reads
        if "social_instagram_grid" in script:
            reads += 1
        value = await original(script, *args)
        return {**value, "loading": True} if "social_page_state" in script else value

    page.evaluate = loading
    mock_browser(monkeypatch, page)
    result = run(social.collect(params(source="instagram", mode="hashtag")))
    diag = result["diagnostics"][0]
    assert diag["outcome"] == "parse_error" and diag["stage"] == "waiting_for_content"
    assert reads == 50 and diag["loading_polls"] == 50
    assert diag["stalls"] == 0 and len(page.visits) == 2
    assert diag["initial_navigation_retry"] == 1


def test_slow_spa_can_recover_on_one_safe_reload_without_false_empty(monkeypatch):
    page = Page()
    original_goto = page.goto

    async def ready_after_reload(url, **kwargs):
        if page.visits:
            page.frames = [[tweet()]]
        return await original_goto(url, **kwargs)

    page.goto = ready_after_reload
    state = mock_browser(monkeypatch, page)
    result = run(social.collect(params()))
    assert len(result["items"]) == 1 and len(page.visits) == 2
    assert result["diagnostics"][0]["initial_navigation_retry"] == 1
    assert result["diagnostics"][0]["outcome"] == "ok"
    assert state["closed"]


def test_research_follower_list_preserves_true_edge_direction(monkeypatch):
    page = Page()
    page.following = [{"handle": "follower", "name": "Follower"}]
    mock_browser(monkeypatch, page)
    result = run(c.read_neighbors(page, "twitter", "peace", "followers", limit=2, max_scrolls=1))
    assert result["relations"][0]["source_key"] == "twitter:follower"
    assert result["relations"][0]["target_key"] == "twitter:peace"
    assert result["relations"][0]["discovery_direction"] == "followers"
    assert result["relations"][0]["evidence_url"] == "https://x.com/peace/followers/"


def test_research_visible_zero_list_count_does_not_invent_edges(monkeypatch):
    page = Page()
    result = run(c.read_neighbors(page, "twitter", "peace", "following", known_count=0))
    assert not page.visits and not result["relations"]
    assert result["diagnostics"][0]["outcome"] == "visible_zero_count"


def test_research_profile_selectors_keep_identity_and_bio_in_semantic_profile_header():
    if not session._runtime()["ready"]:
        pytest.skip("bundled Chromium is not installed in this test environment")

    async def fixture():
        async with session.browser_session("twitter", visible=False, require_session=False) as (ctx, page):
            x_html = '''<main><div data-testid="UserName">Peace Research</div><div data-testid="UserDescription">所属: 団体A。労働者の活動を記録。</div>
              <a href="/peace/following/">12 Following</a><a href="/peace/followers/">33 Followers</a>
              <a data-testid="UserUrl" href="https://example.org/peace">example.org/peace</a>
              <article data-testid="tweet"><div data-testid="tweetText">其他帖文不属于简介。</div></article></main>'''
            ig_html = '''<main><header><h2>peace</h2><div data-testid="profile-bio">団体B公式アカウント。</div>
              <a href="/peace/followers/">20 Followers</a><a href="/peace/following/">9 Following</a></header>
              <article><header><h2>other</h2></header><h1>其他作者的caption。</h1></article></main>'''
            async def route(req):
                await req.fulfill(body=x_html if "x.com" in req.request.url else ig_html, content_type="text/html; charset=utf-8")
            await ctx.route("**/*", route)
            await page.goto("https://x.com/peace/")
            profile = await page.evaluate(selectors.TW_PROFILE, "peace")
            assert profile["verified_in_dom"] and profile["handle"] == "peace"
            assert "所属" in profile["bio"] and "其他帖文" not in profile["bio"]
            assert profile["following_count"] == "12 Following"
            assert not (await page.evaluate(selectors.TW_PROFILE, "other"))["verified_in_dom"]
            await page.goto("https://www.instagram.com/peace/")
            profile = await page.evaluate(selectors.IG_PROFILE, "peace")
            assert profile["verified_in_dom"] and profile["bio"] == "団体B公式アカウント。"
            assert "其他作者" not in profile["bio"]
            await page.set_content('<main><article><header><h2>other</h2></header></article></main>')
            assert not (await page.evaluate(selectors.IG_PROFILE, "peace"))["verified_in_dom"]

    run(fixture())


def test_cancellation_during_first_content_wait_closes_browser(monkeypatch):
    page = Page()
    original = page.evaluate
    reads = 0

    async def cancelled(script, *args):
        nonlocal reads
        if "social_instagram_grid" in script:
            reads += 1
            if reads == 3:
                raise asyncio.CancelledError()
        return await original(script, *args)

    page.evaluate = cancelled
    state = mock_browser(monkeypatch, page)
    with pytest.raises(asyncio.CancelledError):
        run(social.collect(params(source="instagram", mode="hashtag")))
    assert state["closed"] and len(page.visits) == 1


def test_instagram_reads_each_detail_and_never_relabels_alt_as_caption(monkeypatch):
    links = [{"href": "/p/one/", "image_alt": "Image may contain a person"}, {"href": "/p/two/", "image_alt": "Japan peace art"}, {"href": "/p/unknown/", "image_alt": "unknown date"}]
    detail = {"handle": "peace", "name": "Peace", "published_at": "2026-10-06T02:00:00Z", "likes": "12 likes", "detail_present": True}
    page = Page([links], {"https://www.instagram.com/p/one/": {**detail, "caption": "平和への声明。"},
                          "https://www.instagram.com/p/two/": {**detail, "caption": ""},
                          "https://www.instagram.com/p/unknown/": {**detail, "caption": "平和", "published_at": None}})
    mock_browser(monkeypatch, page)
    result = run(social.collect(params(source="instagram", mode="account", handle="peace")))
    assert len(result["items"]) == 2
    assert result["items"][0]["content"] == "平和への声明。"
    assert result["items"][1]["content"] == ""
    assert result["items"][1]["metadata"]["content_kind"] == "image_alt"
    assert result["items"][1]["metadata"]["image_alt"] == ["Japan peace art"]
    assert result["diagnostics"][0]["unknown_dates"] == 1
    assert all(row["account_key"] == "instagram:peace" for row in result["items"])


def test_instagram_partial_detail_failure_is_preserved_in_diagnostics(monkeypatch):
    page = Page([[{"href": "/p/bad/"}, {"href": "/p/good/"}]], {"https://www.instagram.com/p/bad/": {},
        "https://www.instagram.com/p/good/": {"handle": "peace", "caption": "平和", "published_at": "2026-10-06T02:00:00Z", "detail_present": True}})
    mock_browser(monkeypatch, page)
    result = run(social.collect(params(source="instagram", mode="account", handle="peace")))
    assert len(result["items"]) == 1
    assert result["diagnostics"][0]["outcome"] == "partial"
    assert any(row.get("url", "").endswith("/bad/") and row["outcome"] == "parse_error" for row in result["diagnostics"])


def test_instagram_prefixed_permalink_and_missing_author_are_parse_issues(monkeypatch):
    assert c._post_link("instagram", "/yuji.n262/reel/DWgBM51oLAe/") == ("https://www.instagram.com/reel/DWgBM51oLAe/", "DWgBM51oLAe", "yuji.n262")
    assert c._post_link("instagram", "/yuji.n262/p/DWgBM51oLAe/c/123/") is None
    page = Page([[{"href": "/p/one/"}]], {"https://www.instagram.com/p/one/": {"caption": "#平和", "published_at": "2026-10-06T02:00:00Z", "detail_present": True}})
    mock_browser(monkeypatch, page)
    result = run(social.collect(params(source="instagram", mode="hashtag")))
    assert result["items"] == []
    assert result["diagnostics"][1]["outcome"] == "parse_error"
    assert result["diagnostics"][1]["field"] == "author"


def test_following_relations_are_observed_bounded_and_candidate_only(monkeypatch):
    page = Page([[tweet()]])
    page.following = [{"handle": "friend", "name": "Friend"}, {"handle": "another", "name": "Another"}]
    mock_browser(monkeypatch, page)
    result = run(social.collect(params(mode="account", handle="peace", include_following=True, max_following=1)))
    assert len(result["relations"]) == 1
    assert result["relations"][0]["source_key"] == "twitter:peace"
    assert result["relations"][0]["target_key"] == "twitter:friend"
    assert result["relations"][0]["evidence_url"] == "https://x.com/peace/following/"
    assert all(row["status"] == "candidate" for row in result["accounts"])


def test_collect_cancellation_closes_browser_and_does_not_become_network_error(monkeypatch):
    page = Page([[tweet()]])
    state = mock_browser(monkeypatch, page)

    async def cancel_progress(event):
        if event["stage"] == "collecting":
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        run(social.collect(params(), progress=cancel_progress))
    assert state["closed"]


@pytest.mark.parametrize("reuse_page", [False, True])
def test_internal_deadline_returns_already_read_posts_before_outer_guard(monkeypatch, reuse_page):
    page = Page([[tweet()]])
    state = mock_browser(monkeypatch, page)

    async def slow_after_read(event):
        if event["stage"] == "collecting":
            await asyncio.sleep(1)

    async def collect_with_outer_guard():
        kwargs = {"progress": slow_after_read, "time_budget_seconds": 0.05}
        operation = (c.collect_on_page(page, params(max_scrolls=120), **kwargs) if reuse_page
                     else social.collect(params(max_scrolls=120), **kwargs))
        return await asyncio.wait_for(operation, timeout=0.5)

    result = run(collect_with_outer_guard())
    diag = result["diagnostics"][0]
    assert len(result["items"]) == 1
    assert diag["outcome"] == "timeout" and diag["partial"] is True
    assert diag["post_count"] == 1 and diag["scan_stop_reason"] == "time_budget"
    assert diag["time_budget_seconds"] == 0.05
    assert state["closed"] is (not reuse_page)


@pytest.mark.parametrize("budget", [0, -1, True, float("nan"), float("inf"), 86401, "60"])
def test_internal_deadline_is_validated_before_browser_launch(monkeypatch, budget):
    page = Page([[tweet()]])
    state = mock_browser(monkeypatch, page)
    with pytest.raises(social.SocialError, match="内部采集时间预算"):
        run(social.collect(params(), time_budget_seconds=budget))
    assert state["calls"] == []


def test_large_research_viewport_budget_still_stops_after_three_unchanged_frames(monkeypatch):
    page = Page([[tweet()]])
    mock_browser(monkeypatch, page)
    result = run(c.collect_on_page(page, params(max_scrolls=200)))
    diag = result["diagnostics"][0]
    assert len(result["items"]) == 1 and diag["max_scrolls"] == 200
    assert diag["scrolls_completed"] == 3 and diag["stalls"] == 3
    assert diag["scan_stop_reason"] == "no_new_content"


def test_research_timeline_can_pass_legacy_five_hundred_candidate_cap(monkeypatch):
    frames = [[tweet(str(index * 5 + offset)) for offset in range(5)] for index in range(120)]
    page = Page(frames)
    mock_browser(monkeypatch, page)
    result = run(c.collect_on_page(page, params(mode="account", handle="peace", max_records=600, max_scrolls=120)))
    diag = result["diagnostics"][0]
    assert len(result["items"]) == 600
    assert diag["candidate_scan_limit"] == 3000 and diag["scanned"] == 600
    assert diag["scan_stop_reason"] == "record_budget" and diag["scrolls_completed"] == 119


def test_research_neighbors_use_requested_scroll_budget_beyond_five(monkeypatch):
    page = Page()
    original = page.evaluate
    mock_browser(monkeypatch, page)

    async def next_neighbor(script, *args):
        if "social_twitter_accounts" in script:
            return [{"handle": f"friend_{page.index}", "name": "Friend"}]
        return await original(script, *args)

    page.evaluate = next_neighbor
    result = run(c.read_neighbors(page, "twitter", "peace", "followers", limit=8, max_scrolls=40))
    diag = result["diagnostics"][0]
    assert len(result["relations"]) == 8 and diag["scrolls_completed"] == 7
    assert diag["max_scrolls"] == 40 and diag["candidate_scan_limit"] == 8
    assert diag["scan_stop_reason"] == "candidate_budget"


def test_neighbor_deadline_returns_already_observed_relations(monkeypatch):
    page = Page()
    page.following = [{"handle": "friend", "name": "Friend"}]
    original = page.evaluate
    mock_browser(monkeypatch, page)

    async def slow_scroll(script, *args):
        if "social_scroll" in script:
            await asyncio.sleep(1)
        return await original(script, *args)

    page.evaluate = slow_scroll
    result = run(c.read_neighbors(page, "twitter", "peace", "following", limit=8, max_scrolls=200, time_budget_seconds=0.05))
    diag = result["diagnostics"][0]
    assert len(result["relations"]) == 1 and len(result["accounts"]) == 1
    assert diag["outcome"] == "timeout" and diag["partial"] is True
    assert result["relations"][0]["source_key"] == "twitter:peace"


def test_keyword_group_deadline_keeps_the_active_branch_material(monkeypatch):
    page = Page([[tweet()]])
    state = mock_browser(monkeypatch, page)

    async def slow_after_read(event):
        if event["stage"] == "collecting":
            await asyncio.sleep(1)

    result = run(c.collect_queries(params(keyword="平和,護憲"), progress=slow_after_read, time_budget_seconds=0.1))
    assert len(result["items"]) == 1 and state["closed"], result
    assert result["items"][0]["metadata"]["query_group"] == "平和"
    assert any(row.get("outcome") == "timeout" and row.get("partial") is True for row in result["diagnostics"])
    assert len(page.visits) == 1


def test_neighbor_user_cancellation_is_not_reported_as_budget_timeout(monkeypatch):
    page = Page()
    page.following = [{"handle": "friend", "name": "Friend"}]
    original = page.evaluate
    mock_browser(monkeypatch, page)

    async def cancel_scroll(script, *args):
        if "social_scroll" in script:
            raise asyncio.CancelledError()
        return await original(script, *args)

    page.evaluate = cancel_scroll
    with pytest.raises(asyncio.CancelledError):
        run(c.read_neighbors(page, "twitter", "peace", "following", limit=8, max_scrolls=200, time_budget_seconds=30))


@pytest.mark.parametrize("scrolls,records,expected", [(20, 100, 150), (40, 100, 300), (120, 500, 1500), (200, 10000, 2850)])
def test_instagram_candidate_budget_is_visible_and_scales_with_research_budget(scrolls, records, expected):
    options = common.Params.parse(params(source="instagram", max_scrolls=scrolls, max_records=records))
    diag = c._base_diag(options)
    assert diag["candidate_scan_limit"] == expected
    assert diag["max_scrolls"] == scrolls


def test_visible_login_saves_only_platform_state_without_disclosing_secret(monkeypatch):
    page = Page()
    calls = []
    path = session.state_file("twitter")
    path.parent.mkdir(parents=True)
    path.write_text('{"cookies": [{"name": "expired", "value": "old-test-state"}]}', encoding="utf-8")
    original_goto = page.goto

    async def completed_login(url, **kwargs):
        await original_goto(url, **kwargs)
        page.url = "https://x.com/home"

    page.goto = completed_login

    class Context:
        async def cookies(self, url):
            return [{"name": "auth_token", "value": "test-secret-never-return"}]

        async def storage_state(self, path):
            Path(path).write_text('{"cookies": []}', encoding="utf-8")

    @asynccontextmanager
    async def browser(platform, **kwargs):
        calls.append(kwargs)
        yield Context(), page

    monkeypatch.setattr(session, "browser_session", browser)
    result = run(social.login("twitter"))
    assert result["ok"] is True
    assert calls == [{"visible": True, "require_session": False}]
    assert page.visits == ["https://x.com/i/flow/login"]
    assert session.state_file("twitter").is_file()
    assert json.loads(path.read_text(encoding="utf-8")) == {"cookies": []}
    assert not session.state_file("instagram").exists()
    assert "test-secret" not in json.dumps(result)


def fake_playwright(monkeypatch, page):
    """Exercise browser_session context options without launching a browser."""
    from types import SimpleNamespace
    import playwright.async_api

    calls = {"launch": [], "context": [], "closed": []}

    class Context:
        async def new_page(self):
            return page

        async def close(self):
            calls["closed"].append("context")

    class Browser:
        async def new_context(self, **options):
            calls["context"].append(options)
            return Context()

        async def close(self):
            calls["closed"].append("browser")

    class Runtime:
        async def launch(self, **options):
            calls["launch"].append(options)
            return Browser()

        async def stop(self):
            calls["closed"].append("runtime")

    runtime = Runtime()
    runtime.chromium = runtime

    async def start():
        return runtime

    monkeypatch.setattr(playwright.async_api, "async_playwright", lambda: SimpleNamespace(start=start))
    monkeypatch.setattr(session, "_runtime", lambda: {"ready": True})
    monkeypatch.setattr(session, "_browser_path", lambda: Path("test-chromium"))
    return calls


@pytest.mark.parametrize("visible,require_session", [(True, False), (False, True)])
def test_manual_context_is_fresh_chinese_and_collection_loads_japanese_session(monkeypatch, visible, require_session):
    path = session.state_file("twitter")
    path.parent.mkdir(parents=True)
    original = b'{"cookies": [{"name": "test-old", "value": "old-test-state"}]}'
    path.write_bytes(original)
    calls = fake_playwright(monkeypatch, Page())

    async def scenario():
        async with session.browser_session("twitter", visible=visible, require_session=require_session):
            pass

    run(scenario())
    assert len(calls["context"]) == 1
    options = calls["context"][0]
    assert options["locale"] == ("zh-CN" if visible else "ja-JP")
    assert calls["launch"][0]["headless"] is (not visible)
    if require_session:
        assert options["storage_state"] == str(path)
    else:
        assert "storage_state" not in options
    assert path.read_bytes() == original
    assert calls["closed"] == ["context", "browser", "runtime"]


@pytest.mark.parametrize("failure", ["closed", "navigation"])
def test_failed_fresh_login_never_changes_previous_session(monkeypatch, failure):
    path = session.state_file("twitter")
    path.parent.mkdir(parents=True)
    original = b'{"cookies": [{"name": "test-old", "value": "preserve-test-state"}]}'
    path.write_bytes(original)
    page = Page()
    if failure == "closed":
        page.is_closed = lambda: True
    else:
        async def fail_navigation(*args, **kwargs):
            raise RuntimeError("test navigation failure")
        page.goto = fail_navigation
    calls = fake_playwright(monkeypatch, page)
    with pytest.raises(social.SocialError) as exc:
        run(social.login("twitter"))
    assert exc.value.code == ("login_cancelled" if failure == "closed" else "login_failed")
    assert "storage_state" not in calls["context"][0]
    assert calls["context"][0]["locale"] == "zh-CN"
    assert path.read_bytes() == original
    assert not path.with_suffix(".tmp").exists()
    assert calls["closed"] == ["context", "browser", "runtime"]


@pytest.mark.parametrize("notice,text,expected", [
    ("我们已临时限制你的登录。请稍后重试。", "", "rate_limited"),
    ("", "用户帖子引用：我们已临时限制你的登录。请稍后重试。", "ok"),
    ("请稍后重试。", "", "ok"),
])
def test_chinese_login_rate_limit_is_specific_to_notice(notice, text, expected):
    assert common.page_outcome("twitter", {"url": "https://x.com/home", "notice": notice, "text": text}) == expected


def test_manual_login_rate_limit_stops_immediately_without_replacing_state(monkeypatch):
    path = session.state_file("twitter")
    path.parent.mkdir(parents=True)
    original = b'{"cookies": [{"name": "test-old", "value": "preserve-test-state"}]}'
    path.write_bytes(original)
    page = Page(outcome="我们已临时限制你的登录。请稍后重试。")
    calls = fake_playwright(monkeypatch, page)

    async def no_wait_after_limit(*args):
        pytest.fail("A confirmed login rate limit must not keep waiting")

    monkeypatch.setattr(session, "pause", no_wait_after_limit)
    with pytest.raises(social.SocialError) as exc:
        run(social.login("twitter"))
    assert exc.value.code == "rate_limited"
    assert "复用 Firefox 会话" in str(exc.value)
    assert path.read_bytes() == original and not path.with_suffix(".tmp").exists()
    assert calls["closed"] == ["context", "browser", "runtime"]
    assert len(page.visits) == 1


def test_manual_challenge_remains_open_for_user(monkeypatch):
    page = Page(outcome="Verify you are human")
    calls = fake_playwright(monkeypatch, page)
    pauses = []

    async def user_closes_after_wait(seconds):
        pauses.append(seconds)
        page.is_closed = lambda: True

    monkeypatch.setattr(session, "pause", user_closes_after_wait)
    with pytest.raises(social.SocialError) as exc:
        run(social.login("twitter"))
    assert exc.value.code == "login_cancelled" and pauses == [1.5]
    assert calls["closed"] == ["context", "browser", "runtime"]


def test_dom_primary_tweet_and_instagram_caption_offline():
    """Exercise the real selectors using only in-memory HTML, never the network."""
    if not session._runtime()["ready"]:
        pytest.skip("bundled Chromium is not installed in this test environment")

    async def fixture():
        async with session.browser_session("twitter", visible=False, require_session=False) as (ctx, page):
            await ctx.route("**/*", lambda route: route.abort())
            await page.set_content('''<main data-testid="primaryColumn"><div role="link"><article data-testid="tweet"><div data-testid="User-Name"><a href="/peace">Peace</a></div>
            <a href="/peace/status/100"><time datetime="2026-10-06T00:00:00Z"></time></a><div data-testid="tweetText">平和の主帖</div>
            <div role="link"><div data-testid="User-Name"><a href="/other">Other</a></div><a href="/other/status/999"><time datetime="2020-01-01T00:00:00Z"></time></a>
            <div data-testid="tweetText">引用内容を主帖に混ぜない</div></div><button data-testid="like" aria-label="4 Likes">4</button></article></div></main>''')
            tweets = await page.evaluate(selectors.TW_POSTS)
            assert tweets[0]["status_link"] == "/peace/status/100"
            assert tweets[0]["text"] == "平和の主帖"
            assert tweets[0]["published_at"] == "2026-10-06T00:00:00Z"
            await page.set_content('''<main><article><header><a href="/peace/">peace</a></header><h1>原帖の正文</h1>
            <a href="/p/source/"><time datetime="2026-10-06T01:00:00Z"></time></a><img alt="Image may contain text"><ul><li><span dir="auto">他人のコメント</span></li></ul></article></main>''')
            detail = await page.evaluate(selectors.IG_DETAIL, "https://www.instagram.com/p/source/")
            assert detail["caption"] == "原帖の正文" and detail["handle"] == "peace"
            assert detail["published_at"] == "2026-10-06T01:00:00Z"
            await page.set_content('''<main><article><header><a href="/peace/">peace</a></header><time datetime="2026-10-06T01:00:00Z"></time>
            <img alt="Image may contain text"><ul><li><span dir="auto">他人のコメント</span></li></ul></article></main>''')
            assert (await page.evaluate(selectors.IG_DETAIL))["caption"] == ""
            await page.set_content('''<main><article><header><a href="/peace/">peace</a></header>
            <ul><li><h2><a href="/peace/">peace</a></h2><span dir="auto">作者の原帖正文</span></li>
            <li><h2><a href="/other/">other</a></h2><span dir="auto">他人のコメント</span><time datetime="2026-10-07T01:00:00Z"></time></li></ul>
            <a href="/p/source/"><time datetime="2026-10-06T01:00:00Z"></time></a></article></main>''')
            detail = await page.evaluate(selectors.IG_DETAIL, "https://www.instagram.com/p/source/")
            assert detail["caption"] == "作者の原帖正文"
            assert detail["published_at"] == "2026-10-06T01:00:00Z"
            await page.set_content('''<main><article><header><a href="/peace/">peace</a></header>
            <ul><li><h2><a href="/other/">other</a></h2><span dir="auto">コメントだけ</span><time datetime="2026-10-07T01:00:00Z"></time></li></ul></article></main>''')
            detail = await page.evaluate(selectors.IG_DETAIL, "https://www.instagram.com/p/source/")
            assert detail["caption"] == "" and detail["published_at"] is None
            # Sanitized structure observed on the public 2026 Instagram page:
            # no article/header/h1/h2/ul, edit timestamp before publication.
            await page.set_content('''<div role="main"><span dir="auto"><div><div>
            <a href="/yuji.n262/"><div><div><span dir="auto">yuji.n262</span></div></div></a>
            <span>已编辑</span><span>•</span><span><time datetime="2026-03-30T08:29:33Z">27周</time></span></div>
            <span><a href="/explore/tags/nowar/">#nowar</a><br><a href="/explore/tags/反戦/">#反戦</a><br><a href="/nowarfromjapan/">@nowarfromjapan</a></span>
            </div></span><div><div><a href="/yuji.n262/">yuji.n262</a><a href="/p/DWgBM51oLAe/c/123/"><time datetime="2026-04-01T09:00:00Z">comment</time></a></div><span>作者后来的评论不能当caption</span></div>
            <a href="/yuji.n262/reel/DWgBM51oLAe/"><time datetime="2026-03-30T07:32:45Z">27周前</time></a></div>''')
            detail = await page.evaluate(selectors.IG_DETAIL, "https://www.instagram.com/p/DWgBM51oLAe/")
            assert detail["handle"] == "yuji.n262"
            assert detail["published_at"] == "2026-03-30T07:32:45Z"
            assert "#nowar" in detail["caption"] and "#反戦" in detail["caption"]
            assert "已编辑" not in detail["caption"] and "作者后来的评论" not in detail["caption"]
            await page.set_content('''<main><a href="/yuji.n262/">yuji.n262</a><span>已编辑<time datetime="2026-03-30T08:29:33Z"></time></span>
            <a href="/p/DWgBM51oLAe/c/123/"><time datetime="2026-04-01T09:00:00Z"></time></a></main>''')
            assert (await page.evaluate(selectors.IG_DETAIL, "https://www.instagram.com/p/DWgBM51oLAe/"))["published_at"] is None
            await page.set_content('''<div role="main"><a href="/yuji.n262/reel/DWgBM51oLAe/"><img alt="grid image"></a></div>''')
            assert (await page.evaluate(selectors.IG_GRID))[0]["href"] == "/yuji.n262/reel/DWgBM51oLAe/"

    run(fixture())


def test_x_search_context_never_treats_old_or_sidebar_empty_state_as_results(monkeypatch):
    page = Page([[tweet("old")]], outcome="No results found in sidebar")
    original = page.evaluate

    async def pending(script, *args):
        if "social_twitter_search_state" in script:
            return {"ready": False, "reason": "search_input_pending", "loading": True, "result_text": ""}
        return await original(script, *args)

    page.evaluate = pending
    state = mock_browser(monkeypatch, page)
    result = run(social.collect(params(max_scrolls=1)))
    diag = result["diagnostics"][0]
    assert not result["items"] and diag["outcome"] == "parse_error"
    assert diag["stage"] == "waiting_for_search" and diag["scanned"] == 0
    assert diag["search_context_reason"] == "search_input_pending"
    assert len(page.visits) == 2 and page.visits[0] == page.visits[1] and state["closed"]


def test_dom_x_search_waits_for_target_hydration_and_ignores_sidebar_offline():
    """A real Chromium document retains old cards until search hydration."""
    if not session._runtime()["ready"]:
        pytest.skip("bundled Chromium is not installed in this test environment")

    from html import escape
    from urllib.parse import quote
    options = common.Params.parse(params(max_scrolls=1))
    query = c._query(options)
    target = "https://x.com/search?q=" + quote(query) + "&src=typed_query&f=live"

    def card(serial, text):
        return f'<article data-testid="tweet"><div data-testid="User-Name"><a href="/peace">Peace</a></div><a href="/peace/status/{serial}"><time datetime="2026-10-06T00:00:00Z"></time></a><div data-testid="tweetText">{text}</div></article>'

    html = f'''<main><div data-testid="primaryColumn"><input data-testid="SearchBox_Search_Input" value="{escape(query)}">
    <div role="tablist"><a role="tab" aria-selected="true" href="{escape(target)}">最も新しい結果</a></div>
    <section id="results" role="region" aria-label="任意の翻訳済み結果">{card("100", "旧ホームの野球")}
    <div id="result-loading" role="progressbar" style="width:20px;height:20px">Loading</div></section></div>
    <aside>{card("999", "推薦欄の平和")}<div role="progressbar" style="width:20px;height:20px">Sidebar loading</div>No results found in sidebar</aside></main>'''

    async def fixture():
        async with session.browser_session("twitter", visible=False, require_session=False) as (ctx, page):
            await ctx.route("**/*", lambda route: route.fulfill(body=html, content_type="text/html; charset=utf-8"))
            await page.goto(target, wait_until="domcontentloaded")
            state = await page.evaluate(selectors.TW_SEARCH_STATE, query)
            assert not state["ready"] and state["reason"] == "search_results_loading"
            assert state["route_matches"] and state["input_matches"] and state["latest_selected"]
            committed = {"query": query, "document_committed": True}
            loaded = await page.evaluate(selectors.TW_SEARCH_STATE, committed)
            assert loaded["ready"] and loaded["pagination_loading"] and not loaded["loading"]
            await page.evaluate("document.getElementById('results').setAttribute('aria-busy','true')")
            assert not (await page.evaluate(selectors.TW_SEARCH_STATE, committed))["ready"]
            await page.evaluate("document.getElementById('results').removeAttribute('aria-busy'); document.getElementById('results').prepend(document.getElementById('result-loading'))")
            assert not (await page.evaluate(selectors.TW_SEARCH_STATE, committed))["ready"]
            await page.evaluate("document.getElementById('results').append(document.getElementById('result-loading'))")
            assert [row["status_link"] for row in await page.evaluate(selectors.TW_POSTS)] == ["/peace/status/100"]
            await page.evaluate("document.querySelector('input').value='old query'")
            assert (await page.evaluate(selectors.TW_SEARCH_STATE, query))["reason"] == "search_input_pending"
            await page.evaluate("value => document.querySelector('input').value=value", query)
            await page.evaluate("document.querySelector('[role=tab]').href='/search?q=wrong&f=top'")
            assert (await page.evaluate(selectors.TW_SEARCH_STATE, query))["reason"] == "latest_tab_pending"
            await page.evaluate("value => document.querySelector('[role=tab]').href=value", target)
            await page.evaluate("document.getElementById('result-loading').style.display='none'")
            assert (await page.evaluate(selectors.TW_SEARCH_STATE, query))["ready"]
            await page.evaluate("document.getElementById('result-loading').style.display='block'")
            await page.evaluate("body => setTimeout(() => document.getElementById('results').innerHTML=body, 350)", card("101", "平和の新しい検索結果"))
            diag = {**c._base_diag(options), "query": query, "source_url": target}
            rows = [row async for row in c._frames(page, "twitter", selectors.TW_POSTS, 1, diag, lambda row: row.get("status_link"), limit=1)]
            assert [row["status_link"] for row in rows] == ["/peace/status/101"]
            assert diag["scanned"] == 1 and diag["search_context_verified"]
            assert diag["initial_wait_polls"] >= 2 and not diag.get("initial_navigation_retry")

    run(fixture())


def test_search_document_receipt_requires_new_main_navigation_and_is_invalidated(monkeypatch):
    page = Page([[tweet()]])
    mock_browser(monkeypatch, page)
    query = c._query(common.Params.parse(params()))
    from urllib.parse import quote
    target = "https://x.com/search?q=" + quote(query) + "&f=live"
    original = page.goto

    async def main_document(url, **kwargs):
        await original(url, **kwargs)
        request = type("Request", (), {"frame": page.main_frame, "is_navigation_request": lambda self: True})()
        return type("Response", (), {"status": 200, "request": request})()

    page.goto = main_document
    run(common.navigate(page, "twitter", target))
    assert common.search_state_argument(page, query) == {"query": query, "document_committed": True}
    assert common.search_state_argument(page, "other query") == "other query"
    page.goto = original
    run(common.navigate(page, "twitter", target))
    assert common.search_state_argument(page, query) == query


@pytest.mark.parametrize("restores", [True, False])
def test_platform_translation_is_restored_or_skipped_never_saved_as_original(restores):
    if not session._runtime()["ready"]:
        pytest.skip("bundled Chromium is not installed in this test environment")
    onclick = "document.getElementById('own-body').textContent='平和の集会を計画、参加人数は未確定。';document.getElementById('own-body').lang='ja';this.textContent='查看翻译';" if restores else ""
    html = f'''<main><div data-testid="primaryColumn"><article data-testid="tweet">
      <div data-testid="User-Name"><a href="/peace">Peace</a></div>
      <a href="/peace/status/111"><time datetime="2026-10-06T00:00:00Z"></time></a>
      <div data-testid="tweetText" id="own-body" lang="zh">平台译文：计划召开和平集会。</div>
      <span role="button" onclick="{onclick}">显示原文</span>
      <div data-testid="quoteTweet"><div data-testid="tweetText">被引用的其他作者</div><span role="button">显示原文</span></div>
      </article></div></main>'''
    async def fixture():
        async with session.browser_session("twitter", visible=False, require_session=False) as (ctx,page):
            await ctx.route("**/*", lambda route: route.fulfill(body=html, content_type="text/html; charset=utf-8"))
            await page.goto("https://x.com/peace", wait_until="domcontentloaded")
            before = await page.evaluate(selectors.TW_POSTS)
            assert before[0]["translation_visible"] and before[0]["skip_reason"] == "translated_body_not_restored"
            options = common.Params.parse(params(mode="account", handle="peace", max_scrolls=1))
            diag = c._base_diag(options)
            rows = [row async for row in c._frames(page,"twitter",selectors.TW_POSTS,0,diag,lambda row: row["status_link"],
                skip_row=lambda row: row.get("skip_reason") == "translated_body_not_restored")]
            assert diag["original_text_toggle_attempts"] == 1
            if restores:
                assert len(rows) == 1 and rows[0]["text"] == "平和の集会を計画、参加人数は未確定。"
                assert rows[0]["body_language"] == "ja" and rows[0]["original_ui_restored"] and not rows[0]["translation_visible"]
                assert not diag["skipped_translated_bodies"]
            else:
                assert rows == [] and diag["skipped_translated_bodies"] == 1
    run(fixture())


def test_corrected_original_replaces_old_dom_translation_once_and_archives_it(tmp_path):
    from app.workbench.store import Store
    store = Store(tmp_path / "original-correction.sqlite3")
    translated = {"type": "social", "title": "平台译文", "content": "旧平台译文：计划和平集会。", "source_name": "X / @peace",
                  "url": "https://x.com/peace/status/111", "published_at": "2026-10-06T00:00:00Z", "metadata": {"content_kind": "body"}}
    identity = store.import_items([translated])["resolved_ids"][0]
    original = {**translated, "title": "平和の集会を計画。", "content": "平和の集会を計画。参加人数は未確定。",
                "metadata": {"content_kind": "body", "body_language": "ja", "text_representation": "original_after_platform_toggle"}}
    assert store.import_items([original])["updated"] == 1
    assert store.get_item(identity)["content"] == original["content"]
    assert store.item_revisions(identity)["items"][0]["capture"]["content"] == translated["content"]
    assert store.import_items([translated])["updated"] == 0
    assert store.import_items([original])["updated"] == 0
    assert store.get_item(identity)["content"] == original["content"] and store.item_revisions(identity)["total"] == 1


@pytest.mark.parametrize("media,expected", [
    ('<img alt="Unscoped image has no reliable ownership">', []),
    ('<a href="/sunset_in_tokyo/p/DWdIHYMj76A/"><img alt="4/19 反戦デモの案内"></a>', ["4/19 反戦デモの案内"]),
])
def test_instagram_alt_excludes_recommendations_in_current_main(media, expected):
    if not session._runtime()["ready"]:
        pytest.skip("bundled Chromium is not installed in this test environment")

    async def fixture():
        async with session.browser_session("instagram", visible=False, require_session=False) as (ctx, page):
            await ctx.route("**/*", lambda route: route.abort())
            # Sanitized shape of the real post: no article, with unrelated
            # recommended post thumbnails below the current caption and time.
            await page.set_content('''<main><a href="/sunset_in_tokyo/"><img alt="sunset_in_tokyo profile picture"></a>
                <h1>4/19 反戦デモに参加します。</h1>''' + media + '''
                <a href="/sunset_in_tokyo/p/DWdIHYMj76A/"><time datetime="2026-03-29T02:00:00Z"></time></a>
                <div><a href="/baseball/p/recommended1/"><img alt="大谷翔平、9月の野球ニュース"></a>
                <a href="/p/recommended2/"><img alt="無関係な旅行写真"></a></div></main>''')
            detail = await page.evaluate(selectors.IG_DETAIL, "https://www.instagram.com/p/DWdIHYMj76A/")
            assert detail["image_alt"] == expected
            assert detail["caption"] == "4/19 反戦デモに参加します。"
            assert detail["handle"] == "sunset_in_tokyo"

    run(fixture())


def test_instagram_alt_requires_a_single_post_article():
    if not session._runtime()["ready"]:
        pytest.skip("bundled Chromium is not installed in this test environment")

    async def fixture():
        async with session.browser_session("instagram", visible=False, require_session=False) as (ctx, page):
            await ctx.route("**/*", lambda route: route.abort())
            article = '''<article><header><a href="/peace/"><img alt="Avatar"></a></header>
                <h1>平和の声明</h1><img alt="原帖の図版"><a href="/peace/p/current/">
                <time datetime="2026-10-06T00:00:00Z"></time></a>{}</article>'''
            recommendation = '<a href="/p/other/"><img alt="無関係な推薦投稿"></a>'
            await page.set_content('<main>' + article.format('') + recommendation + '</main>')
            assert (await page.evaluate(selectors.IG_DETAIL, "https://www.instagram.com/p/current/"))["image_alt"] == ["原帖の図版"]
            await page.set_content('<main>' + article.format(recommendation) + '</main>')
            assert (await page.evaluate(selectors.IG_DETAIL, "https://www.instagram.com/p/current/"))["image_alt"] == []

    run(fixture())
