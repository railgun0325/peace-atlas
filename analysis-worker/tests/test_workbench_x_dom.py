"""Offline regressions for real X permalink/quote shapes and bounded scrolling.

Fixtures contain synthetic public post data only. No browser profile, saved
session, credentials, live platform request, or captured page body is used.
"""
import asyncio
from contextlib import asynccontextmanager
from html import escape
from urllib.parse import parse_qs, quote, urlsplit

import pytest

from app.workbench.social import collector, selectors, session


def post_html(*, text="憲法九条を守る集会", extra="", author="peace", post_id="100"):
    return f'''<article data-testid="tweet"><div data-testid="User-Name">
        <a role="link" href="/{author}">Peace</a>
        <a role="link" href="/{author}/status/{post_id}"><time datetime="2026-10-06T02:00:00Z">1h</time></a>
        </div>{'<div data-testid="tweetText">' + text + '</div>' if text else ''}{extra}
        <button data-testid="like">12</button></article>'''


def extract(html):
    if not session._runtime()["ready"]:
        pytest.skip("bundled Chromium is not installed in this test environment")

    async def scenario():
        async with session.browser_session("twitter", visible=False, require_session=False) as (context, page):
            query = "憲法九条 since:2026-10-05 until:2026-10-08"
            target = "https://x.com/search?q=" + quote(query) + "&src=typed_query&f=live"
            fixture = f'''<main><div data-testid="primaryColumn">
                <input data-testid="SearchBox_Search_Input" value="{escape(query)}">
                <div role="tablist"><a role="tab" aria-selected="true" href="{escape(target)}">任意の翻訳済み最新タブ</a></div>
                <section role="region" aria-label="検索結果">{html}</section></div></main>'''
            await context.route("**/*", lambda route: route.fulfill(body=fixture, content_type="text/html; charset=utf-8"))
            await page.goto(target, wait_until="domcontentloaded")
            assert (await page.evaluate(selectors.TW_SEARCH_STATE, query))["ready"]
            return await page.evaluate(selectors.TW_POSTS)

    return asyncio.run(scenario())


@pytest.mark.parametrize("quote_author", ["other", "peace"])
def test_primary_role_link_is_not_quote_and_nested_anchor_cannot_hide_quote(quote_author):
    quote = f'''<div role="link"><div data-testid="User-Name"><a role="link" href="/{quote_author}">Quoted</a></div>
        <a role="link" href="/{quote_author}/status/999"><time datetime="2020-01-01T00:00:00Z">old</time></a>
        <div data-testid="tweetText">無関係な引用の本文</div><div data-testid="tweetPhoto"><img alt="引用画像"></div>
        <button data-testid="like">999</button></div>'''
    row = extract('<div role="link">' + post_html(extra=quote) + '</div>')[0]
    assert row["status_link"] == "/peace/status/100"
    assert row["author_link"] == "/peace"
    assert row["published_at"] == "2026-10-06T02:00:00Z"
    assert row["text"] == "憲法九条を守る集会"
    assert row["cited_links"] == [f"/{quote_author}/status/999"]
    assert row["image_alt"] == [] and row["likes"] == "12"


def test_quoted_only_text_and_time_are_never_promoted_to_primary_post():
    html = '''<article data-testid="tweet"><div data-testid="User-Name"><a role="link" href="/peace">Peace</a></div>
        <div data-testid="quoteTweet"><a role="link" href="/peace/status/999"><time datetime="2020-01-01T00:00:00Z">old</time></a>
        <div data-testid="tweetText">憲法九条は引用だけ</div><div data-testid="tweetPhoto"><img alt="引用だけの画像"></div></div></article>'''
    row = extract(html)[0]
    assert row["status_link"] == "" and row["published_at"] is None
    assert row["text"] == "" and row["image_alt"] == []


def test_article_card_has_explicit_skip_reason_and_never_uses_summary_or_alt():
    card = '<div data-testid="article-cover-image"><img alt="憲法九条という画像説明"></div><h2>記事の見出し</h2><p>記事の要約</p>'
    rows = extract(post_html(text="", extra=card) + post_html(text="憲法九条の原帖正文", extra=card, post_id="101"))
    assert rows[0]["status_link"] == "/peace/status/100"
    assert rows[0]["text"] == "" and rows[0]["image_alt"] == []
    assert rows[0]["skip_reason"] == "article_card_without_body"
    assert rows[1]["text"] == "憲法九条の原帖正文" and rows[1]["skip_reason"] is None


def test_primary_author_must_match_permalink_even_when_quote_has_matching_author():
    html = post_html().replace('/peace/status/100', '/different/status/100')
    row = extract(html)[0]
    assert row["author_link"] == "/peace" and row["status_link"] == ""


def params(**changes):
    return {"source": "twitter", "mode": "keyword", "keyword": "憲法九条", "start_date": "2026-10-06", "end_date": "2026-10-06",
            "max_records": 2, "max_accounts": 3, "max_scrolls": 5, "include_following": False, **changes}


def post(**changes):
    return {"status_link": "/peace/status/100", "author_link": "/peace", "text": "憲法九条の原帖正文",
            "published_at": "2026-10-06T02:00:00Z", **changes}


def article(**changes):
    return post(text="", skip_reason="article_card_without_body", **changes)


class Page:
    def __init__(self, frames):
        self.frames = frames
        self.index = 0
        self.reads = 0
        self.visits = []
        self.url = "https://x.com/home"
        self.input_query = ""
        self.selected_tab_url = ""
        self.result_loading = False

    async def evaluate(self, script, *args):
        if script == selectors.SCROLL:
            self.index += 1
            return None
        if script == selectors.TW_SEARCH_STATE:
            assert len(args) == 1
            expected = args[0]
            parsed = urlsplit(self.url)
            query = parse_qs(parsed.query)
            tab = urlsplit(self.selected_tab_url)
            tab_query = parse_qs(tab.query)
            route = parsed.path == "/search" and query.get("q") == [expected] and query.get("f") == ["live"]
            input_matches = self.input_query == expected
            latest = tab.path == "/search" and tab_query.get("q") == [expected] and tab_query.get("f") == ["live"]
            ready = route and input_matches and latest and not self.result_loading
            return {"ready": ready, "route_matches": route, "input_matches": input_matches, "latest_selected": latest,
                    "loading": self.result_loading, "result_text": "",
                    "reason": "ready" if ready else "search_results_loading" if self.result_loading else "search_context_pending"}
        assert script == selectors.TW_POSTS
        self.reads += 1
        return self.frames[min(self.index, len(self.frames) - 1)]


def collect(monkeypatch, page, *, accept=None, **changes):
    @asynccontextmanager
    async def browser(*args, **kwargs):
        yield None, page

    async def inspect(*args):
        return {"text": "", "twitter_auth": True}

    async def navigate(page, platform, url):
        page.url = url
        page.input_query = parse_qs(urlsplit(url).query).get("q", [""])[0]
        page.selected_tab_url = url if urlsplit(url).path == "/search" else ""
        page.visits.append(url)
        return await inspect()

    async def no_wait(*args):
        await asyncio.sleep(0)

    monkeypatch.setattr(collector, "browser_session", browser)
    monkeypatch.setattr(collector, "navigate", navigate)
    monkeypatch.setattr(collector, "inspect_page", inspect)
    monkeypatch.setattr(collector, "pause", no_wait)
    return asyncio.run(collector.collect(params(**changes), accept=accept))


@pytest.mark.parametrize("card_link", ["/peace/status/1", ""])
def test_first_viewport_article_card_scrolls_to_real_post(monkeypatch, card_link):
    page = Page([[article(status_link=card_link)], [post()]])
    result = collect(monkeypatch, page, max_records=1)
    assert len(result["items"]) == 1
    diag = result["diagnostics"][0]
    assert diag["outcome"] == "ok" and diag["initial_wait_polls"] == 1
    assert diag["skipped_article_cards"] == 1 and diag["scanned"] == 1
    assert page.index == 1


def test_article_only_results_wait_for_scroll_then_report_unreadable_not_empty_success(monkeypatch):
    page = Page([[article()]])
    result = collect(monkeypatch, page)
    diag = result["diagnostics"][0]
    assert result["items"] == [] and diag["ok"] is False
    assert diag["outcome"] == "parse_error" and diag["stage"] == "article_cards_only"
    assert diag["skipped_article_cards"] == 1 and diag["scanned"] == 0
    assert diag["stalls"] == 3 and page.index == 3
    assert diag["initial_wait_polls"] == 1 and diag["scroll_wait_polls"] == 21


@pytest.mark.parametrize("frames", [[[]], [[{"status_link": "", "text": "unrecognized structure"}]]])
def test_unrecognized_or_loading_dom_still_errors_before_scrolling(monkeypatch, frames):
    page = Page(frames)
    result = collect(monkeypatch, page)
    diag = result["diagnostics"][0]
    assert diag["outcome"] == "parse_error" and diag["stage"] == "waiting_for_content"
    assert diag["initial_wait_polls"] == 25 and page.index == 0
    assert diag["search_context_verified"]
    if frames == [[]]:
        assert diag["retry_wait_polls"] == 25 and page.visits == [page.visits[0]] * 2
    else:
        assert len(page.visits) == 1


def test_article_skip_does_not_bypass_query_date_author_or_relevance_checks(monkeypatch):
    page = Page([[article(status_link="/peace/status/1")], [
        post(status_link="/peace/status/2", text="野球の原帖", image_alt=["憲法九条"]),
        post(status_link="/peace/status/3", published_at="2020-01-01T00:00:00Z"),
        post(status_link="/other/status/4"),
        post(status_link="/peace/status/5"),
    ]])
    result = collect(monkeypatch, page, accept=lambda candidate: {"matched": False, "reasons": ["outside project scope"]})
    diag = result["diagnostics"][0]
    assert result["items"] == []
    assert diag["skipped_article_cards"] == 1 and diag["scanned"] == 4
    assert diag["query_mismatch"] == 1 and diag["date_filtered"] == 1
    assert diag["author_mismatch"] == 1 and diag["irrelevant"] == 1


def test_account_mode_reads_only_profile_with_accurate_source_metadata(monkeypatch):
    page = Page([[post()]])
    result = collect(monkeypatch, page, mode="account", handle="@PEACE", keyword="", max_records=1)
    assert page.visits == ["https://x.com/peace"]
    assert len(result["items"]) == 1
    for row in (result["diagnostics"][0], result["items"][0]["metadata"]):
        assert row["collection_method"] == "x_browser_profile_timeline"
        assert row["source_url"] == "https://x.com/peace" and row["query"] is None
    assert "不代表平台完整历史" in result["diagnostics"][0]["coverage"]


@pytest.mark.parametrize("frames", [[[]], [[post(published_at="2020-01-01T00:00:00Z")]]])
def test_account_mode_never_implicitly_falls_back_to_search(monkeypatch, frames):
    page = Page(frames)
    result = collect(monkeypatch, page, mode="account", handle="peace", keyword="")
    assert result["items"] == []
    # An empty viewport may safely reload the same profile once; it must never
    # change the collection mode or fall back to a keyword search.
    assert page.visits == ["https://x.com/peace"] * (2 if frames == [[]] else 1)
    assert result["diagnostics"][0]["source_url"] == "https://x.com/peace"
    assert result["diagnostics"][0]["query"] is None


@pytest.mark.parametrize("repost_label", ["Peace reposted", "Peace 已转帖", "Peaceさんがリポスト", "Peace 已轉發"])
def test_profile_excludes_other_authors_reposts_and_old_pins_without_early_date_stop(monkeypatch, repost_label):
    page = Page([[
        post(status_link="/peace/status/1", published_at="2020-01-01T00:00:00Z", social_context="Pinned"),
        post(status_link="/other/status/2", author_link="/other", social_context="Peace reposted"),
        post(status_link="/other/status/3", author_link="/peace"),
        post(status_link="/peace/status/4", social_context=repost_label),
        post(status_link="/peace/status/5", published_at=None),
    ], [post(status_link="/peace/status/6")]])
    result = collect(monkeypatch, page, mode="account", handle="peace", keyword="", max_records=1,
                     accept=lambda candidate: {"matched": "憲法九条" in candidate["content"]})
    assert [row["url"] for row in result["items"]] == ["https://x.com/peace/status/6"]
    diag = result["diagnostics"][0]
    assert diag["date_filtered"] == 1 and diag["unknown_dates"] == 1
    assert diag["author_mismatch"] == 2 and diag["reposts_filtered"] == 1
    assert page.index == 1 and page.visits == ["https://x.com/peace"]
    assert [row["handle"] for row in result["accounts"]] == ["peace"]


@pytest.mark.parametrize("body,accepted", [("憲法九条の原帖正文", True), ("野球の原帖正文", False)])
def test_profile_relevance_uses_primary_text_and_preserves_quote_separation(monkeypatch, body, accepted):
    quote = '''<div role="link"><div data-testid="User-Name"><a role="link" href="/other">Other</a></div>
        <a role="link" href="/other/status/999"><time datetime="2020-01-01T00:00:00Z">old</time></a>
        <div data-testid="tweetText">憲法九条の引用本文</div></div>'''
    rows = extract(post_html(text=body, extra=quote))
    result = collect(monkeypatch, Page([rows]), mode="account", handle="peace", keyword="", max_records=1,
                     accept=lambda candidate: {"matched": "憲法九条" in candidate["content"]})
    assert len(result["items"]) == int(accepted)
    if accepted:
        item = result["items"][0]
        assert item["content"] == body and item["account_key"] == "twitter:peace"
        assert item["published_at"] == "2026-10-06T02:00:00+00:00"
        assert item["metadata"]["post_kind"] == "quote"
        assert item["metadata"]["cited_urls"] == ["https://x.com/other/status/999"]
    else:
        assert result["diagnostics"][0]["irrelevant"] == 1


@pytest.mark.parametrize("mode", ["keyword", "hashtag"])
def test_keyword_and_hashtag_keep_live_search_path_and_date_query(monkeypatch, mode):
    page = Page([[post(text="#平和の集会 #平和")]])
    result = collect(monkeypatch, page, mode=mode, keyword="平和", max_records=1)
    assert len(result["items"]) == 1 and len(page.visits) == 1
    parsed = urlsplit(page.visits[0])
    query = parse_qs(parsed.query)
    assert parsed.path == "/search" and query["f"] == ["live"]
    expected = ("#平和" if mode == "hashtag" else "平和") + " since:2026-10-05 until:2026-10-08"
    assert query["q"] == [expected]
    for row in (result["diagnostics"][0], result["items"][0]["metadata"]):
        assert row["collection_method"] == "x_browser_live_search"
        assert row["source_url"] == page.visits[0] and row["query"] == expected
