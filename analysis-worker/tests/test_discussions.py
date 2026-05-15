from app.discussions import (
    DEFAULT_DISCUSSION_KEYWORDS,
    _dcard_cookies,
    discussion_relevant,
    parse_dcard_rsshub_json,
    parse_ptt_article,
    parse_ptt_index,
    parse_reddit_comments,
    parse_reddit_listing,
    parse_reddit_rss_feed,
    reddit_query_terms,
    reddit_subreddits,
    set_dcard_cookies,
)
import app.discussions as discussions


def test_parse_ptt_index_public_board_row():
    html = """
    <div class="r-ent">
      <div class="nrec"><span class="hl f2">12</span></div>
      <div class="title"><a href="/bbs/HatePolitics/M.1.html">[新聞] 台海議題升溫</a></div>
      <div class="meta"><div class="author">tester</div><div class="date">5/11</div></div>
    </div>
    """
    posts = parse_ptt_index(html, "HatePolitics")
    assert posts[0]["title"] == "[新聞] 台海議題升溫"
    assert posts[0]["author"] == "tester"
    assert posts[0]["interaction_count"] == 12
    assert posts[0]["url"].endswith("/bbs/HatePolitics/M.1.html")


def test_parse_ptt_article_body_and_push_comments():
    html = """
    <div id="main-content">
      <div class="article-metaline"><span class="article-meta-tag">作者</span><span class="article-meta-value">tester</span></div>
      <div class="article-metaline"><span class="article-meta-tag">看板</span><span class="article-meta-value">HatePolitics</span></div>
      <div class="article-metaline"><span class="article-meta-tag">標題</span><span class="article-meta-value">[討論] 國防預算</span></div>
      <div class="article-metaline"><span class="article-meta-tag">時間</span><span class="article-meta-value">Mon May 11 10:00:00 2026</span></div>
      正文第一段
      <div class="push"><span class="push-tag">推</span><span class="push-userid">u1</span><span class="push-content">: 支持增加國防</span><span class="push-ipdatetime">05/11 10:01</span></div>
      <div class="push"><span class="push-tag">噓</span><span class="push-userid">u2</span><span class="push-content">: 黑箱令人擔心</span><span class="push-ipdatetime">05/11 10:02</span></div>
    </div>
    """
    post, comments = parse_ptt_article(html, "https://www.ptt.cc/bbs/HatePolitics/M.1.html", "HatePolitics")
    assert post["title"] == "[討論] 國防預算"
    assert "正文第一段" in post["raw_text"]
    assert len(comments) == 2
    assert comments[0]["score"] == 1
    assert comments[1]["score"] == -1


def test_parse_reddit_listing_and_comments():
    listing = {
        "data": {
            "children": [
                {
                    "data": {
                        "title": "Taiwan politics thread",
                        "permalink": "/r/taiwan/comments/abc/thread/",
                        "author": "public_user",
                        "created_utc": 1778500000,
                        "selftext": "Discussion about Taiwan Strait.",
                        "score": 15,
                        "num_comments": 2,
                        "id": "abc",
                    }
                }
            ]
        }
    }
    comments_data = [
        {},
        {
            "data": {
                "children": [
                    {"kind": "t1", "data": {"body": "I worry about the risk.", "score": 3, "author": "c1", "created_utc": 1778500001}}
                ]
            }
        },
    ]
    posts = parse_reddit_listing(listing, "taiwan")
    comments = parse_reddit_comments(comments_data, max_comments=10)
    assert posts[0]["title"] == "Taiwan politics thread"
    assert posts[0]["comment_count"] == 2
    assert comments[0]["comment_text"] == "I worry about the risk."
    assert comments[0]["score"] == 3


def test_parse_reddit_rss_feed():
    xml = """
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Taiwan Strait discussion</title>
        <link href="https://www.reddit.com/r/taiwan/comments/abc/taiwan_strait_discussion/" />
        <author><name>public_user</name></author>
        <updated>2026-05-11T10:00:00+00:00</updated>
        <content type="html">Public Reddit RSS summary</content>
      </entry>
    </feed>
    """
    posts = parse_reddit_rss_feed(xml, "taiwan")
    assert posts[0]["title"] == "Taiwan Strait discussion"
    assert posts[0]["reddit_id"] == "abc"
    assert posts[0]["board_name"] == "r/taiwan"


def test_reddit_query_terms_are_capped_for_default_keywords():
    terms = reddit_query_terms(None)
    assert terms
    assert terms[0] == "Taiwan politics"
    assert len(terms) <= len(DEFAULT_DISCUSSION_KEYWORDS)
    assert "Taiwan Strait" in terms


def test_reddit_query_terms_translate_common_chinese_keywords():
    terms = reddit_query_terms(["台海", "國防預算"])
    assert "Taiwan Strait" in terms
    assert "Taiwan defense budget" in terms


def test_reddit_default_subreddits_are_conservative():
    assert reddit_subreddits(None) == ["taiwan"]


def test_parse_dcard_rsshub_json_feed():
    data = {
        "items": [
            {
                "title": "台海議題討論",
                "url": "https://www.dcard.tw/f/trending/p/123",
                "content_text": "公開文章摘要",
                "date_published": "2026-05-11T10:00:00Z",
            }
        ]
    }
    posts = parse_dcard_rsshub_json(data, "trending")
    assert posts[0]["platform"] == "dcard"
    assert posts[0]["title"] == "台海議題討論"
    assert posts[0]["url"].endswith("/123")



def test_default_discussion_keywords_are_expanded():
    assert len(DEFAULT_DISCUSSION_KEYWORDS) >= 25
    assert "Taiwan Strait" in DEFAULT_DISCUSSION_KEYWORDS
    assert "民進黨" in DEFAULT_DISCUSSION_KEYWORDS


def test_discussion_relevance_requires_taiwan_anchor_or_specific_keyword():
    assert discussion_relevant({"title": "Taiwan Strait military activity", "raw_text": ""}, ["Taiwan Strait"])
    assert discussion_relevant({"title": "國防預算爭議", "raw_text": "立法院討論"}, ["國防預算"])
    assert not discussion_relevant({"title": "Generic China economy discussion", "raw_text": ""}, ["Taiwan politics"])


def test_dcard_cookies_accept_oauth_without_cf_clearance(monkeypatch):
    monkeypatch.setattr(discussions, "_runtime_dcard_cookies", None)
    monkeypatch.delenv("DCARD_CF_CLEARANCE", raising=False)
    monkeypatch.delenv("DCARD_CF_BM", raising=False)
    monkeypatch.setenv("DCARD_OAUTH_TOKEN", "token-value")
    assert _dcard_cookies() == {"dcard-web-oauth-tk": "token-value"}


def test_set_dcard_cookies_ignores_empty_values(monkeypatch):
    monkeypatch.setattr(discussions, "_runtime_dcard_cookies", None)
    monkeypatch.delenv("DCARD_CF_CLEARANCE", raising=False)
    monkeypatch.delenv("DCARD_CF_BM", raising=False)
    monkeypatch.setenv("DCARD_OAUTH_TOKEN", "env-token")
    set_dcard_cookies({"cf_clearance": "", "dcard-web-oauth-tk": ""})
    assert _dcard_cookies() == {"dcard-web-oauth-tk": "env-token"}
