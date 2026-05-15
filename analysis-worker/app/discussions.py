from __future__ import annotations

import asyncio
import hashlib
import os
import re
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from urllib.parse import quote

import feedparser
import httpx
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from .db import fetch_all, fetch_one, get_conn
from .sentiment import classify_sentiment, sentiment_profile_from_counts, summarize_sentiments


REDDIT_SUBREDDITS = ["japan", "okinawa", "japanlife", "newsokur"]
REDDIT_OPTIONAL_SUBREDDITS = ["japanpolitics", "JapanPeace", "worldnews", "geopolitics"]
DEFAULT_DISCUSSION_KEYWORDS = ["憲法九条 平和", "沖縄 米軍基地", "反戦 市民運動", "Japan peace movement", "Japan antiwar", "Okinawa US base"]
REDDIT_DEFAULT_QUERIES = [
    "Japan peace movement",
    "Japan antiwar",
    "Okinawa US base",
    "Futenma",
    "Henoko",
    "Article 9 Japan",
    "Japan pacifism",
    "Japan defense policy",
    "Japan nuclear disarmament",
    "Japan hibakusha",
    "Japan US alliance",
    "Japan security treaty",
    "Japan peace education",
    "Japan war memory",
    "憲法九条",
    "沖縄基地",
    "安保法制",
    "反戦運動",
    "平和運動",
    "日米同盟",
    "原水爆禁止",
    "自衛隊派遣",
    "平和憲法",
]
REDDIT_QUERY_TRANSLATIONS = {
    "憲法九条": ["Article 9 Japan"],
    "平和憲法": ["Japan peace constitution"],
    "沖縄基地": ["Okinawa US base", "Okinawa military"],
    "反戦運動": ["Japan antiwar"],
    "平和運動": ["Japan peace movement"],
    "安保法制": ["Japan security laws"],
    "日米同盟": ["Japan US alliance"],
    "防衛費": ["Japan defense spending"],
    "自衛隊": ["Japan Self-Defense Forces"],
    "被爆者": ["hibakusha", "Japan atomic bomb survivors"],
    "核廃絶": ["Japan nuclear abolition"],
    "尖閣諸島": ["Senkaku Islands"],
    "北朝鮮": ["North Korea", "DPRK"],
    "米軍基地": ["US military base", "Okinawa base"],
    "辺野古": ["Henoko"],
    "普天間": ["Futenma"],
}
USER_AGENT = "japan-antiwar-intel/0.2 public-source research"
PUBLIC_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,application/rss+xml,application/xml,text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8,zh;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Ch-Ua": '"Chromium";v="131", "Google Chrome";v="131", "Not=A?Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}
REDDIT_HEADERS = {
    **PUBLIC_BROWSER_HEADERS,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}
DEFAULT_DISCUSSION_KEYWORDS = ["憲法九条 平和", "沖縄 米軍基地", "反戦 市民運動", "Japan peace movement", "Japan antiwar", "Okinawa US base"]
JAPAN_ANCHOR_TERMS = [
    "japan",
    "japanese",
    "okinawa",
    "沖縄",
    "憲法九条",
    "憲法9条",
    "article 9",
    "平和",
    "peace",
    "反戦",
    "antiwar",
    "pacifism",
    "自衛隊",
    "self-defense",
    "米軍基地",
    "us base",
    "military base",
    "安保",
    "security treaty",
    "日米同盟",
    "alliance",
    "被爆",
    "hibakusha",
    "核廃絶",
    "nuclear disarmament",
    "辺野古",
    "henoko",
    "普天間",
    "futenma",
    "平和運動",
    "peace movement",
    "市民団体",
    "ngo",
]
KEYWORD_STOPWORDS = {
    "and",
    "or",
    "the",
    "with",
    "japan",
    "japanese",
    "politics",
    "political",
    "government",
    "news",
}

DiscussionProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]


async def emit_discussion_progress(
    progress: DiscussionProgressCallback | None,
    event: dict[str, Any],
) -> None:
    if progress is not None:
        await progress(event)

DEFAULT_DISCUSSION_KEYWORDS = [
    "憲法九条",
    "平和憲法",
    "沖縄 基地",
    "反戦運動",
    "平和運動",
    "安保法制",
    "日米同盟",
    "Japan peace movement",
    "Japan antiwar",
    "Okinawa US base",
    "Futenma",
    "Henoko",
    "Article 9 Japan",
    "Japan pacifism",
    "Japan defense policy",
    "Japan nuclear disarmament",
    "Japan hibakusha",
    "Japan US alliance",
    "Japan security treaty",
    "Japan peace education",
    "Japan war memory",
    "自衛隊 派遣",
    "防衛費 増額",
    "核廃絶 運動",
    "被爆者 証言",
    "尖閣 諸島",
    "北朝鮮 ミサイル",
    "米軍 再編",
    "市民 抗議",
    "国会 決議",
    "NGO peace Japan",
]

JAPAN_ANCHOR_TERMS = [
    "japan",
    "japanese",
    "okinawa",
    "沖縄",
    "憲法九条",
    "憲法9条",
    "article 9",
    "平和",
    "peace",
    "反戦",
    "antiwar",
    "pacifism",
    "自衛隊",
    "self-defense",
    "米軍基地",
    "us base",
    "military base",
    "安保",
    "security treaty",
    "日米同盟",
    "alliance",
    "被爆",
    "hibakusha",
    "核廃絶",
    "nuclear disarmament",
    "辺野古",
    "henoko",
    "普天間",
    "futenma",
    "平和運動",
    "peace movement",
    "市民団体",
    "ngo",
]


def discussion_window(start: date, end: date) -> tuple[datetime, datetime]:
    start_dt = datetime.combine(start, time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return start_dt, end_dt


def hash_public_id(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()[:24]


def content_hash(*parts: str | None) -> str:
    raw = "\n".join(part or "" for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def discussion_relevant(post: dict[str, Any], keywords: list[str] | None = None) -> bool:
    haystack = f"{post.get('title', '')}\n{post.get('raw_text', '')}".lower()
    if any(term.lower() in haystack for term in JAPAN_ANCHOR_TERMS):
        return True
    tokens: list[str] = []
    for keyword in keywords or []:
        for token in re.split(r"[\s,;|()\"']+", keyword.lower()):
            token = token.strip()
            if len(token) >= 3 and token not in KEYWORD_STOPWORDS:
                tokens.append(token)
    return bool(tokens and any(token in haystack for token in tokens))


def rsshub_base_url() -> str:
    return os.getenv("RSSHUB_BASE_URL", "http://rsshub:1200").rstrip("/")



def parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        parsed = date_parser.parse(str(value), fuzzy=True)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def in_date_range(value: datetime | None, start: date, end: date) -> bool:
    if not value:
        return True
    return start <= value.date() <= end


async def async_retry_with_backoff(fn, max_retries: int = 3, base_delay: float = 1.0):
    last_exc = None
    for attempt in range(max_retries):
        try:
            return await fn()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {429, 503} and attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                await asyncio.sleep(delay)
                last_exc = exc
                continue
            raise
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError) as exc:
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                await asyncio.sleep(delay)
                last_exc = exc
                continue
            raise
    raise last_exc


REDDIT_TIMEOUT = httpx.Timeout(30.0, connect=10.0, read=30.0, write=10.0)


def ensure_discussion_tables() -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS discussion_posts (
                    id BIGSERIAL PRIMARY KEY,
                    platform TEXT NOT NULL,
                    board_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL UNIQUE,
                    author_hash TEXT,
                    published_at TIMESTAMPTZ,
                    fetched_at TIMESTAMPTZ NOT NULL,
                    language TEXT,
                    raw_text TEXT,
                    clean_text TEXT,
                    interaction_count INTEGER NOT NULL DEFAULT 0,
                    comment_count INTEGER NOT NULL DEFAULT 0,
                    content_hash TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_discussion_posts_time ON discussion_posts (published_at, fetched_at)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_discussion_posts_platform ON discussion_posts (platform, board_name)")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS discussion_comments (
                    id BIGSERIAL PRIMARY KEY,
                    post_id BIGINT NOT NULL REFERENCES discussion_posts(id) ON DELETE CASCADE,
                    platform TEXT NOT NULL,
                    author_hash TEXT,
                    comment_text TEXT NOT NULL,
                    vote_type TEXT,
                    score INTEGER NOT NULL DEFAULT 0,
                    floor INTEGER,
                    published_at TIMESTAMPTZ,
                    comment_hash TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (post_id, comment_hash)
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_discussion_comments_post ON discussion_comments (post_id)")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS discussion_sentiments (
                    id BIGSERIAL PRIMARY KEY,
                    post_id BIGINT REFERENCES discussion_posts(id) ON DELETE CASCADE,
                    comment_id BIGINT REFERENCES discussion_comments(id) ON DELETE CASCADE,
                    sentiment_label TEXT NOT NULL,
                    keywords JSONB NOT NULL DEFAULT '[]'::jsonb,
                    narrative_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
                    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                    llm_analyzed BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_discussion_sentiments_post ON discussion_sentiments (post_id)")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS discussion_fetch_runs (
                    id BIGSERIAL PRIMARY KEY,
                    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    completed_at TIMESTAMPTZ,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    platforms JSONB NOT NULL DEFAULT '[]'::jsonb,
                    keywords JSONB NOT NULL DEFAULT '[]'::jsonb,
                    status TEXT NOT NULL DEFAULT 'running',
                    diagnostics JSONB NOT NULL DEFAULT '[]'::jsonb,
                    fetched_posts INTEGER NOT NULL DEFAULT 0,
                    fetched_comments INTEGER NOT NULL DEFAULT 0
                )
                """
            )


def start_fetch_run(start_date: date, end_date: date, platforms: list[str], keywords: list[str]) -> int:
    ensure_discussion_tables()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO discussion_fetch_runs (start_date, end_date, platforms, keywords)
                VALUES (%s, %s, %s::jsonb, %s::jsonb)
                RETURNING id
                """,
                (start_date, end_date, json_dumps(platforms), json_dumps(keywords)),
            )
            return int(cur.fetchone()["id"])


def finish_fetch_run(run_id: int, status: str, diagnostics: list[dict[str, Any]], fetched_posts: int, fetched_comments: int) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE discussion_fetch_runs
                SET completed_at = NOW(), status = %s, diagnostics = %s::jsonb,
                    fetched_posts = %s, fetched_comments = %s
                WHERE id = %s
                """,
                (status, json_dumps(diagnostics), fetched_posts, fetched_comments, run_id),
            )


def json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


def normalize_discussion_item(post: dict[str, Any]) -> dict[str, Any]:
    text = (post.get("raw_text") or post.get("clean_text") or "").strip()
    post["clean_text"] = text
    post["content_hash"] = content_hash(post.get("title"), post.get("url"), text)
    post["author_hash"] = hash_public_id(post.get("author"))
    post.pop("author", None)
    return post


def store_discussion_post(post: dict[str, Any], comments: list[dict[str, Any]]) -> dict[str, Any]:
    ensure_discussion_tables()
    post = normalize_discussion_item(post)
    fetched_at = datetime.now(timezone.utc)
    inserted_post = False
    inserted_comments = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO discussion_posts
                  (platform, board_name, title, url, author_hash, published_at, fetched_at, language,
                   raw_text, clean_text, interaction_count, comment_count, content_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (url) DO UPDATE SET
                  fetched_at = EXCLUDED.fetched_at,
                  interaction_count = GREATEST(discussion_posts.interaction_count, EXCLUDED.interaction_count),
                  comment_count = GREATEST(discussion_posts.comment_count, EXCLUDED.comment_count)
                RETURNING id, (xmax = 0) AS inserted
                """,
                (
                    post["platform"],
                    post["board_name"],
                    post["title"],
                    post["url"],
                    post.get("author_hash"),
                    post.get("published_at"),
                    fetched_at,
                    post.get("language"),
                    post.get("raw_text"),
                    post.get("clean_text"),
                    int(post.get("interaction_count") or 0),
                    int(post.get("comment_count") or len(comments)),
                    post["content_hash"],
                ),
            )
            row = cur.fetchone()
            post_id = int(row["id"])
            inserted_post = bool(row["inserted"])
            if inserted_post:
                sentiment = classify_sentiment(f"{post['title']}\n{post.get('clean_text') or ''}")
                cur.execute(
                    """
                    INSERT INTO discussion_sentiments
                      (post_id, sentiment_label, keywords, narrative_tags, confidence, llm_analyzed)
                    VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, FALSE)
                    """,
                    (
                        post_id,
                        sentiment["label"],
                        json_dumps(sentiment["keywords"]),
                        json_dumps(sentiment["narrative_tags"]),
                        sentiment["confidence"],
                    ),
                )
            for index, comment in enumerate(comments, start=1):
                text = (comment.get("comment_text") or "").strip()
                if not text:
                    continue
                chash = content_hash(str(post_id), text, str(comment.get("floor") or index), comment.get("author") or "")
                cur.execute(
                    """
                    INSERT INTO discussion_comments
                      (post_id, platform, author_hash, comment_text, vote_type, score, floor, published_at, comment_hash)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (post_id, comment_hash) DO NOTHING
                    RETURNING id
                    """,
                    (
                        post_id,
                        post["platform"],
                        hash_public_id(comment.get("author")),
                        text,
                        comment.get("vote_type"),
                        int(comment.get("score") or 0),
                        comment.get("floor") or index,
                        comment.get("published_at"),
                        chash,
                    ),
                )
                comment_row = cur.fetchone()
                if not comment_row:
                    continue
                inserted_comments += 1
                sentiment = classify_sentiment(text)
                cur.execute(
                    """
                    INSERT INTO discussion_sentiments
                      (post_id, comment_id, sentiment_label, keywords, narrative_tags, confidence, llm_analyzed)
                    VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, FALSE)
                    """,
                    (
                        post_id,
                        int(comment_row["id"]),
                        sentiment["label"],
                        json_dumps(sentiment["keywords"]),
                        json_dumps(sentiment["narrative_tags"]),
                        sentiment["confidence"],
                    ),
                )
    return {"post_id": post_id, "inserted_post": inserted_post, "inserted_comments": inserted_comments}


def parse_ptt_index(html: str, board: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one("form[action*='over18']"):
        return []
    posts = []
    for row in soup.select(".r-ent"):
        title_node = row.select_one(".title a")
        if not title_node:
            continue
        href = title_node.get("href") or ""
        nrec = row.select_one(".nrec")
        author = row.select_one(".author")
        date_node = row.select_one(".date")
        posts.append(
            {
                "platform": "ptt",
                "board_name": board,
                "title": title_node.get_text(strip=True),
                "url": f"https://www.ptt.cc{href}" if href.startswith("/") else href,
                "author": author.get_text(strip=True) if author else None,
                "date_hint": date_node.get_text(strip=True) if date_node else None,
                "interaction_count": parse_ptt_score(nrec.get_text(strip=True) if nrec else ""),
            }
        )
    return posts


def parse_ptt_score(value: str) -> int:
    value = (value or "").strip()
    if value == "爆":
        return 100
    if value.startswith("X"):
        return -10
    try:
        return int(value)
    except Exception:
        return 0


def parse_ptt_article(html: str, url: str, board: str, title_fallback: str = "") -> tuple[dict[str, Any], list[dict[str, Any]]]:
    soup = BeautifulSoup(html, "html.parser")
    main = soup.select_one("#main-content")
    meta_values = [node.get_text(strip=True) for node in soup.select(".article-meta-value")]
    title = meta_values[2] if len(meta_values) >= 3 else title_fallback
    author = meta_values[0] if meta_values else None
    published_at = parse_datetime(meta_values[3]) if len(meta_values) >= 4 else None
    comments = []
    for floor, push in enumerate(soup.select(".push"), start=1):
        tag = push.select_one(".push-tag")
        user = push.select_one(".push-userid")
        content = push.select_one(".push-content")
        ipdatetime = push.select_one(".push-ipdatetime")
        text = content.get_text(" ", strip=True).lstrip(":").strip() if content else ""
        if text:
            comments.append(
                {
                    "author": user.get_text(strip=True) if user else None,
                    "comment_text": text,
                    "vote_type": tag.get_text(strip=True) if tag else None,
                    "score": ptt_vote_score(tag.get_text(strip=True) if tag else ""),
                    "floor": floor,
                    "published_at": parse_datetime(ipdatetime.get_text(strip=True)) if ipdatetime else None,
                }
            )
        push.decompose()
    for node in soup.select(".article-metaline, .article-metaline-right"):
        node.decompose()
    body = main.get_text("\n", strip=True) if main else ""
    post = {
        "platform": "ptt",
        "board_name": board,
        "title": title or title_fallback,
        "url": url,
        "author": author,
        "published_at": published_at,
        "language": "zh-Hant",
        "raw_text": body,
        "interaction_count": sum(comment.get("score", 0) for comment in comments),
        "comment_count": len(comments),
    }
    return post, comments


def ptt_vote_score(value: str) -> int:
    if "推" in value:
        return 1
    if "噓" in value or "嘘" in value:
        return -1
    return 0


def parse_old_reddit_search_html(html: str, subreddit: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    posts = []
    for thing in soup.select(".thing.link"):
        title_el = thing.select_one("a.search-title") or thing.select_one("a.title")
        if not title_el:
            continue
        title = title_el.get_text(" ", strip=True)
        href = title_el.get("href") or ""
        if href.startswith("/"):
            href = f"https://old.reddit.com{href}"
        tagline = thing.select_one(".tagline") or thing.select_one(".search-result-meta")
        author_el = tagline.select_one("a.author") if tagline else None
        author = author_el.get_text(strip=True) if author_el else None
        time_el = tagline.select_one("time") if tagline else None
        published_at = parse_datetime(time_el.get("datetime") if time_el else None)
        score_el = thing.select_one(".score") or thing.select_one(".search-score")
        score_text = score_el.get_text(strip=True) if score_el else "0"
        try:
            score = int(re.sub(r"[^0-9]", "", score_text) or "0")
        except ValueError:
            score = 0
        comments_el = thing.select_one("a.comments") or thing.select_one(".search-comments")
        comments_text = comments_el.get_text(strip=True) if comments_el else "0"
        try:
            num_comments = int(re.sub(r"[^0-9]", "", comments_text) or "0")
        except ValueError:
            num_comments = 0
        if not title or "reddit.com" not in href:
            continue
        match = re.search(r"/comments/([^/]+)/", href)
        posts.append({
            "platform": "reddit",
            "board_name": f"r/{subreddit}",
            "title": title,
            "url": href,
            "author": author,
            "published_at": published_at,
            "language": "en",
            "raw_text": title,
            "interaction_count": score,
            "comment_count": num_comments,
            "reddit_id": match.group(1) if match else None,
        })
    return posts


def parse_reddit_new_html(html: str, subreddit: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    posts = []
    for thing in soup.select(".thing.link"):
        title_el = thing.select_one("a.title")
        if not title_el:
            continue
        title = title_el.get_text(" ", strip=True)
        href = title_el.get("href") or ""
        if href.startswith("/"):
            href = f"https://old.reddit.com{href}"
        tagline = thing.select_one(".tagline")
        author_el = tagline.select_one("a.author") if tagline else None
        author = author_el.get_text(strip=True) if author_el else None
        time_el = tagline.select_one("time") if tagline else None
        published_at = parse_datetime(time_el.get("datetime") if time_el else None)
        score_el = thing.select_one(".score.unvoted")
        score_text = score_el.get("title") or score_el.get_text(strip=True) if score_el else "0"
        try:
            score = int(re.sub(r"[^0-9]", "", score_text) or "0")
        except ValueError:
            score = 0
        comments_el = thing.select_one("a.comments")
        comments_text = comments_el.get_text(strip=True) if comments_el else "0"
        try:
            num_comments = int(re.sub(r"[^0-9]", "", comments_text) or "0")
        except ValueError:
            num_comments = 0
        if not title:
            continue
        match = re.search(r"/comments/([^/]+)/", href)
        posts.append({
            "platform": "reddit",
            "board_name": f"r/{subreddit}",
            "title": title,
            "url": href,
            "author": author,
            "published_at": published_at,
            "language": "en",
            "raw_text": title,
            "interaction_count": score,
            "comment_count": num_comments,
            "reddit_id": match.group(1) if match else None,
        })
    return posts


def parse_reddit_listing(data: dict[str, Any], subreddit: str) -> list[dict[str, Any]]:
    posts = []
    for child in data.get("data", {}).get("children", []):
        item = child.get("data", {})
        permalink = item.get("permalink") or ""
        posts.append(
            {
                "platform": "reddit",
                "board_name": f"r/{subreddit}",
                "title": item.get("title") or "",
                "url": f"https://www.reddit.com{permalink}" if permalink.startswith("/") else item.get("url") or "",
                "author": item.get("author"),
                "published_at": parse_datetime(item.get("created_utc")),
                "language": "en",
                "raw_text": item.get("selftext") or item.get("title") or "",
                "interaction_count": int(item.get("score") or 0),
                "comment_count": int(item.get("num_comments") or 0),
                "reddit_id": item.get("id"),
            }
        )
    return [post for post in posts if post["title"] and post["url"]]


def parse_reddit_rss_feed(xml_text: str, subreddit: str) -> list[dict[str, Any]]:
    feed = feedparser.parse(xml_text)
    posts = []
    for item in feed.entries:
        title = BeautifulSoup(item.get("title") or "", "html.parser").get_text(" ", strip=True)
        url = item.get("link") or ""
        summary = item.get("summary") or item.get("description") or ""
        if item.get("content"):
            first_content = item.get("content")[0] if isinstance(item.get("content"), list) else {}
            summary = first_content.get("value") or summary
        text = BeautifulSoup(summary, "html.parser").get_text(" ", strip=True)
        if not title or "reddit.com/" not in url:
            continue
        match = re.search(r"/comments/([^/]+)/", url)
        posts.append(
            {
                "platform": "reddit",
                "board_name": f"r/{subreddit}",
                "title": title,
                "url": url,
                "author": item.get("author") or "reddit_public",
                "published_at": parse_datetime(item.get("published") or item.get("updated")),
                "language": "en",
                "raw_text": text or title,
                "interaction_count": 0,
                "comment_count": 0,
                "reddit_id": match.group(1) if match else None,
            }
        )
    return posts


async def fetch_reddit_via_rss(
    client: httpx.AsyncClient,
    subreddit: str,
    start_date: date,
    end_date: date,
    max_posts: int,
    retry: int = 2,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    diagnostic = {
        "platform": "reddit",
        "board": f"r/{subreddit}",
        "source": "reddit_rss",
        "ok": False,
        "post_count": 0,
    }
    for attempt in range(retry):
        try:
            response = await client.get(
                f"https://www.reddit.com/r/{subreddit}/new/.rss",
                headers={**PUBLIC_BROWSER_HEADERS, "Accept": "application/rss+xml,application/xml,text/xml,*/*"},
            )
            diagnostic["status_code"] = response.status_code
            if response.status_code in {403, 429}:
                if attempt < retry - 1:
                    await asyncio.sleep(2.0 * (attempt + 1))
                    continue
                diagnostic["message"] = "Reddit RSS 被拒绝或限流。"
                return [], diagnostic
            response.raise_for_status()
            posts = filter_reddit_fallback_posts(
                parse_reddit_rss_feed(response.text, subreddit),
                subreddit, start_date, end_date, max_posts,
            )
            diagnostic["ok"] = True
            diagnostic["post_count"] = len(posts)
            return posts, diagnostic
        except Exception as exc:
            if attempt < retry - 1:
                await asyncio.sleep(2.0 * (attempt + 1))
                continue
            diagnostic["message"] = str(exc)
            return [], diagnostic
    return [], diagnostic


async def fetch_reddit_via_old_search(
    client: httpx.AsyncClient,
    subreddit: str,
    query: str,
    start_date: date,
    end_date: date,
    max_posts: int,
    global_search: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if global_search:
        diagnostic = {
            "platform": "reddit",
            "board": "reddit-global",
            "keyword": query,
            "source": "old_reddit_global_search",
            "ok": False,
            "post_count": 0,
        }
        params = f"q={quote(query)}&restrict_sr=on&sort=new&limit={max_posts}"
        response = await client.get(
            f"https://old.reddit.com/r/all/search?{params}",
            headers=REDDIT_HEADERS,
        )
        diagnostic["status_code"] = response.status_code
        if response.status_code in {403, 429}:
            diagnostic["message"] = "old.reddit.com 全局搜索被拒绝或限流。"
            return [], diagnostic
        response.raise_for_status()
        posts = [
            post for post in parse_old_reddit_search_html(response.text, subreddit)
            if in_date_range(post.get("published_at"), start_date, end_date)
            and discussion_relevant(post, [query])
        ][:max_posts]
        # Fallback: try JSON endpoint if HTML parsing returns 0 results
        if not posts:
            json_resp = await client.get(
                f"https://old.reddit.com/r/all/search.json?q={quote(query)}&sort=new&limit={max_posts}",
                headers={**PUBLIC_BROWSER_HEADERS, "Accept": "application/json"},
            )
            if json_resp.status_code == 200:
                data = json_resp.json()
                posts = [
                    post for post in parse_reddit_listing(data, "all")
                    if in_date_range(post.get("published_at"), start_date, end_date)
                    and discussion_relevant(post, [query])
                ][:max_posts]
        for post in posts:
            post["board_name"] = "reddit-global"
        diagnostic["ok"] = True
        diagnostic["post_count"] = len(posts)
        return posts, diagnostic
    # original (subreddit-restricted search)
    diagnostic = {
        "platform": "reddit",
        "board": f"r/{subreddit}",
        "keyword": query,
        "source": "old_reddit_search_html",
        "ok": False,
        "post_count": 0,
    }
    params = f"q={quote(query)}&restrict_sr=on&sort=new&limit={max_posts}"
    response = await client.get(
        f"https://old.reddit.com/r/{subreddit}/search?{params}",
        headers=REDDIT_HEADERS,
    )
    diagnostic["status_code"] = response.status_code
    if response.status_code in {403, 429}:
        diagnostic["message"] = "old.reddit.com 搜索被拒绝或限流。"
        return [], diagnostic
    response.raise_for_status()
    posts = [
        post for post in parse_old_reddit_search_html(response.text, subreddit)
        if in_date_range(post.get("published_at"), start_date, end_date)
        and discussion_relevant(post, [query])
    ][:max_posts]
    diagnostic["ok"] = True
    diagnostic["post_count"] = len(posts)
    return posts, diagnostic


async def fetch_reddit_via_old_new(
    client: httpx.AsyncClient,
    subreddit: str,
    start_date: date,
    end_date: date,
    max_posts: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    diagnostic = {
        "platform": "reddit",
        "board": f"r/{subreddit}",
        "source": "old_reddit_new_html",
        "ok": False,
        "post_count": 0,
    }
    response = await client.get(
        f"https://old.reddit.com/r/{subreddit}/new/",
        headers=REDDIT_HEADERS,
    )
    diagnostic["status_code"] = response.status_code
    if response.status_code in {403, 429}:
        diagnostic["message"] = "old.reddit.com 最新帖子被拒绝或限流。"
        return [], diagnostic
    response.raise_for_status()
    posts = [
        post for post in parse_reddit_new_html(response.text, subreddit)
        if in_date_range(post.get("published_at"), start_date, end_date)
    ]
    allow_broad = os.getenv("REDDIT_ACCEPT_JAPAN_LATEST_FALLBACK", "true").strip().lower() in {"1", "true", "yes", "on"}
    if not allow_broad:
        posts = [p for p in posts if discussion_relevant(p, REDDIT_DEFAULT_QUERIES)]
    posts = posts[:max_posts]
    diagnostic["ok"] = True
    diagnostic["post_count"] = len(posts)
    return posts, diagnostic


async def fetch_reddit_via_old_comments(
    client: httpx.AsyncClient,
    reddit_id: str,
    max_comments: int,
) -> list[dict[str, Any]]:
    response = await client.get(
        f"https://old.reddit.com/comments/{reddit_id}/.json",
        headers={**PUBLIC_BROWSER_HEADERS, "Accept": "application/json"},
    )
    if response.status_code != 200:
        return []
    return parse_reddit_comments(response.json(), max_comments)


def _reddit_oauth_enabled() -> bool:
    cid = os.getenv("REDDIT_CLIENT_ID", "").strip()
    csc = os.getenv("REDDIT_CLIENT_SECRET", "").strip()
    return bool(cid and csc)


async def _reddit_oauth_token(client: httpx.AsyncClient) -> str | None:
    cid = os.getenv("REDDIT_CLIENT_ID", "").strip()
    csc = os.getenv("REDDIT_CLIENT_SECRET", "").strip()
    ruser = os.getenv("REDDIT_USERNAME", "").strip()
    rpass = os.getenv("REDDIT_PASSWORD", "").strip()
    if not cid or not csc:
        return None
    import base64
    auth = base64.b64encode(f"{cid}:{csc}".encode()).decode()
    oauth_headers = {
        "Authorization": f"Basic {auth}",
        "User-Agent": PUBLIC_BROWSER_HEADERS["User-Agent"],
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if ruser and rpass:
        response = await client.post(
            "https://www.reddit.com/api/v1/access_token",
            data={"grant_type": "password", "username": ruser, "password": rpass},
            headers=oauth_headers,
        )
        if response.status_code == 200:
            return response.json().get("access_token")
    response = await client.post(
        "https://www.reddit.com/api/v1/access_token",
        data={"grant_type": "client_credentials"},
        headers=oauth_headers,
    )
    if response.status_code != 200:
        return None
    return response.json().get("access_token")


async def fetch_reddit_via_oauth(
    client: httpx.AsyncClient,
    subreddit: str,
    query: str,
    start_date: date,
    end_date: date,
    max_posts: int,
    max_comments: int,
    token: str,
    global_search: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if global_search:
        diagnostic = {
            "platform": "reddit",
            "board": "reddit-global",
            "keyword": query,
            "source": "reddit_oauth_global",
            "ok": False,
            "post_count": 0,
        }
        oauth_headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": PUBLIC_BROWSER_HEADERS["User-Agent"],
            "Accept": "application/json",
        }
        params = f"q={quote(query)}&restrict_sr=true&sort=new&limit={max_posts}"
        response = await client.get(
            f"https://oauth.reddit.com/r/all/search?{params}",
            headers=oauth_headers,
        )
        diagnostic["status_code"] = response.status_code
        if response.status_code != 200:
            diagnostic["message"] = f"Reddit OAuth API 全局搜索返回 {response.status_code}。"
            return [], diagnostic
        data = response.json()
        posts = [
            post for post in parse_reddit_listing(data, subreddit)
            if in_date_range(post.get("published_at"), start_date, end_date)
            and discussion_relevant(post, [query])
        ][:max_posts]
        for post in posts:
            post["board_name"] = "reddit-global"
        results = []
        for post in posts:
            comments: list[dict[str, Any]] = []
            reddit_id = post.pop("reddit_id", None)
            if reddit_id and max_comments > 0:
                try:
                    comment_resp = await client.get(
                        f"https://oauth.reddit.com/comments/{reddit_id}?limit={max_comments}",
                        headers=oauth_headers,
                    )
                    if comment_resp.status_code == 200:
                        comments = parse_reddit_comments(comment_resp.json(), max_comments)
                except Exception:
                    pass
            results.append({"post": post, "comments": comments})
        diagnostic["ok"] = True
        diagnostic["post_count"] = len(results)
        return results, diagnostic
    # original (subreddit-restricted search)
    diagnostic = {
        "platform": "reddit",
        "board": f"r/{subreddit}",
        "keyword": query,
        "source": "reddit_oauth_api",
        "ok": False,
        "post_count": 0,
    }
    oauth_headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": PUBLIC_BROWSER_HEADERS["User-Agent"],
        "Accept": "application/json",
    }
    params = f"q={quote(query)}&restrict_sr=1&sort=new&limit={max_posts}"
    response = await client.get(
        f"https://oauth.reddit.com/r/{subreddit}/search?{params}",
        headers=oauth_headers,
    )
    diagnostic["status_code"] = response.status_code
    if response.status_code != 200:
        diagnostic["message"] = f"Reddit OAuth API 返回 {response.status_code}。"
        return [], diagnostic
    data = response.json()
    posts = [
        post for post in parse_reddit_listing(data, subreddit)
        if in_date_range(post.get("published_at"), start_date, end_date)
        and discussion_relevant(post, [query])
    ][:max_posts]
    results = []
    for post in posts:
        comments: list[dict[str, Any]] = []
        reddit_id = post.pop("reddit_id", None)
        if reddit_id and max_comments > 0:
            try:
                comment_resp = await client.get(
                    f"https://oauth.reddit.com/comments/{reddit_id}?limit={max_comments}",
                    headers=oauth_headers,
                )
                if comment_resp.status_code == 200:
                    comments = parse_reddit_comments(comment_resp.json(), max_comments)
            except Exception:
                pass
        results.append({"post": post, "comments": comments})
    diagnostic["ok"] = True
    diagnostic["post_count"] = len(results)
    return results, diagnostic


async def fetch_reddit_listing_fallback(
    client: httpx.AsyncClient,
    subreddit: str,
    start_date: date,
    end_date: date,
    max_posts: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    diagnostic = {
        "platform": "reddit",
        "board": f"r/{subreddit}",
        "keyword": "latest_public_posts",
        "source": "old_reddit_new_html",
        "ok": False,
        "post_count": 0,
    }
    posts, html_diag = await fetch_reddit_via_old_new(client, subreddit, start_date, end_date, max_posts)
    if posts:
        diagnostic.update(html_diag)
        diagnostic["ok"] = True
        diagnostic["post_count"] = len(posts)
        return list(posts), diagnostic
    rss_posts, rss_diag = await fetch_reddit_via_rss(client, subreddit, start_date, end_date, max_posts)
    diagnostic["rss_fallback"] = rss_diag
    if rss_posts:
        diagnostic["ok"] = True
        diagnostic["source"] = "reddit_rss"
        diagnostic["post_count"] = len(rss_posts)
    return list(rss_posts), diagnostic


def filter_reddit_fallback_posts(
    posts: list[dict[str, Any]],
    subreddit: str,
    start_date: date,
    end_date: date,
    max_posts: int,
) -> list[dict[str, Any]]:
    allow_broad_japan_fallback = (
        subreddit.lower() in ("japan", "okinawa")
        and os.getenv("REDDIT_ACCEPT_JAPAN_LATEST_FALLBACK", "true").strip().lower() in {"1", "true", "yes", "on"}
    )
    return [
        post
        for post in posts
        if in_date_range(post.get("published_at"), start_date, end_date)
        and (allow_broad_japan_fallback or discussion_relevant(post, REDDIT_DEFAULT_QUERIES))
    ][:max_posts]


async def fetch_reddit_rss_fallback(
    client: httpx.AsyncClient,
    subreddit: str,
    start_date: date,
    end_date: date,
    max_posts: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    diagnostic = {
        "platform": "reddit",
        "board": f"r/{subreddit}",
        "keyword": "latest_public_posts",
        "source": "reddit_new_rss",
        "ok": False,
        "post_count": 0,
    }
    response = await client.get(
        f"https://www.reddit.com/r/{subreddit}/new/.rss",
        headers={**PUBLIC_BROWSER_HEADERS, "Accept": "application/rss+xml,application/xml,text/xml,*/*"},
    )
    diagnostic["status_code"] = response.status_code
    if response.status_code in {403, 429}:
        diagnostic["message"] = "Reddit 公开 RSS 也被拒绝或限流；不会使用登录、cookie 或绕过方式。"
        return [], diagnostic
    response.raise_for_status()
    posts = filter_reddit_fallback_posts(parse_reddit_rss_feed(response.text, subreddit), subreddit, start_date, end_date, max_posts)
    diagnostic["ok"] = True
    diagnostic["post_count"] = len(posts)
    return posts, diagnostic


def parse_reddit_comments(data: Any, max_comments: int) -> list[dict[str, Any]]:
    comments = []
    if not isinstance(data, list) or len(data) < 2:
        return comments

    def walk(children: list[dict[str, Any]]) -> None:
        for child in children:
            if len(comments) >= max_comments or child.get("kind") != "t1":
                continue
            item = child.get("data", {})
            body = item.get("body")
            if body:
                comments.append(
                    {
                        "author": item.get("author"),
                        "comment_text": body,
                        "vote_type": "score",
                        "score": int(item.get("score") or 0),
                        "floor": len(comments) + 1,
                        "published_at": parse_datetime(item.get("created_utc")),
                    }
                )
            replies = item.get("replies")
            if isinstance(replies, dict):
                walk(replies.get("data", {}).get("children", []))

    walk(data[1].get("data", {}).get("children", []))
    return comments


def reddit_query_terms(keywords: list[str] | None) -> list[str]:
    terms = []
    source_keywords = keywords if keywords is not None else REDDIT_DEFAULT_QUERIES
    for keyword in source_keywords:
        keyword = (keyword or "").strip()
        if keyword and keyword not in terms:
            terms.append(keyword)
        for translated in REDDIT_QUERY_TRANSLATIONS.get(keyword, []):
            if translated not in terms:
                terms.append(translated)
    max_terms = int(os.getenv("REDDIT_MAX_QUERY_TERMS", "15"))
    return terms[:max_terms] if max_terms > 0 else terms


def reddit_subreddits(keywords: list[str] | None) -> list[str]:
    configured = os.getenv("REDDIT_SUBREDDITS", "").strip()
    if configured:
        return [item.strip().lstrip("r/") for item in configured.split(",") if item.strip()]
    if keywords is None:
        return REDDIT_SUBREDDITS
    include_optional = os.getenv("REDDIT_INCLUDE_OPTIONAL_SUBREDDITS", "false").strip().lower() in {"1", "true", "yes", "on"}
    return REDDIT_SUBREDDITS + REDDIT_OPTIONAL_SUBREDDITS if include_optional else REDDIT_SUBREDDITS


def reddit_request_delay_seconds() -> float:
    return float(os.getenv("REDDIT_REQUEST_DELAY_SECONDS", "2.5"))


def reddit_fetch_comments_enabled() -> bool:
    return os.getenv("REDDIT_FETCH_COMMENTS", "false").strip().lower() in {"1", "true", "yes", "on"}


def reddit_global_search_enabled() -> bool:
    return os.getenv("REDDIT_GLOBAL_SEARCH", "false").strip().lower() in {"1", "true", "yes", "on"}


def reddit_stop_after_first_default_hit() -> bool:
    return os.getenv("REDDIT_STOP_AFTER_FIRST_DEFAULT_HIT", "true").strip().lower() in {"1", "true", "yes", "on"}


async def fetch_reddit_discussions(
    start_date: date,
    end_date: date,
    keywords: list[str] | None,
    max_posts: int,
    max_comments: int,
    progress: DiscussionProgressCallback | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    diagnostics = []
    collected_by_url: dict[str, dict[str, Any]] = {}
    query_terms = reddit_query_terms(keywords)
    subreddits = reddit_subreddits(keywords)
    is_default_mode = keywords is None
    request_delay = reddit_request_delay_seconds()
    fetch_comments = reddit_fetch_comments_enabled() and max_comments > 0
    stop_after_default_hit = is_default_mode and reddit_stop_after_first_default_hit()

    # GLOBAL SEARCH MODE: search ALL of Reddit (no subreddit restriction)
    if reddit_global_search_enabled():
        diagnostics.append({
            "platform": "reddit",
            "ok": True,
            "message": f"Reddit 全局搜索模式：使用 {len(query_terms)} 个关键词搜索整个 Reddit（不限子版），每词间延迟 {request_delay} 秒。",
            "post_count": 0,
        })
        async with httpx.AsyncClient(timeout=REDDIT_TIMEOUT, follow_redirects=True, headers=PUBLIC_BROWSER_HEADERS) as client:
            oauth_token: str | None = None
            if _reddit_oauth_enabled():
                try:
                    oauth_token = await _reddit_oauth_token(client)
                    if oauth_token:
                        diagnostics.append({"platform": "reddit", "ok": True, "message": "Reddit OAuth 已配置，将使用 API 进行全局搜索。", "post_count": 0})
                except Exception:
                    pass
            total_queries = len(query_terms)
            for current_query_index, query in enumerate(query_terms, start=1):
                await emit_discussion_progress(progress, {
                    "type": "progress",
                    "platform": "reddit",
                    "stage": "query_start",
                    "board": "reddit-global",
                    "keyword": query,
                    "current_query_index": current_query_index,
                    "total_queries": total_queries,
                    "found_so_far": len(collected_by_url),
                })
                if request_delay > 0:
                    await asyncio.sleep(request_delay)
                query_results: list[dict[str, Any]] = []
                comments_by_url: dict[str, list[dict[str, Any]]] = {}
                try:
                    if oauth_token:
                        oauth_results, oauth_diag = await fetch_reddit_via_oauth(
                            client, "", query, start_date, end_date, max_posts, max_comments, oauth_token, global_search=True,
                        )
                        oauth_diag["total_queries"] = total_queries
                        oauth_diag["current_query_index"] = current_query_index
                        diagnostics.append(oauth_diag)
                        for item in oauth_results:
                            url = item["post"]["url"]
                            query_results.append(item["post"])
                            comments_by_url[url] = item.get("comments", [])
                    if not query_results:
                        html_posts, html_diag = await fetch_reddit_via_old_search(
                            client, "", query, start_date, end_date, max_posts, global_search=True,
                        )
                        html_diag["total_queries"] = total_queries
                        html_diag["current_query_index"] = current_query_index
                        diagnostics.append(html_diag)
                        query_results = html_posts
                        if query_results and fetch_comments:
                            for post in query_results:
                                reddit_id = post.get("reddit_id")
                                if reddit_id and request_delay > 0:
                                    await asyncio.sleep(request_delay * 0.5)
                                if reddit_id:
                                    try:
                                        comments_by_url[post["url"]] = await fetch_reddit_via_old_comments(
                                            client, reddit_id, max_comments,
                                        )
                                    except Exception:
                                        pass
                    added = 0
                    for post in query_results:
                        url = post["url"]
                        post.pop("reddit_id", None)
                        if url not in collected_by_url:
                            added += 1
                        collected_by_url[url] = {"post": post, "comments": comments_by_url.get(url, [])}
                    if added:
                        diagnostics.append({
                            "platform": "reddit", "board": "reddit-global", "keyword": query,
                            "ok": True, "post_count": len(query_results), "new_unique_posts": added,
                            "source": "old_reddit_global_search",
                            "total_queries": total_queries,
                            "current_query_index": current_query_index,
                        })
                    await emit_discussion_progress(progress, {
                        "type": "progress",
                        "platform": "reddit",
                        "stage": "query_done",
                        "board": "reddit-global",
                        "keyword": query,
                        "current_query_index": current_query_index,
                        "total_queries": total_queries,
                        "post_count": len(query_results),
                        "new_unique_posts": added,
                        "found_so_far": len(collected_by_url),
                    })
                except Exception as exc:
                    diagnostics.append({
                        "platform": "reddit", "board": "reddit-global", "keyword": query,
                        "ok": False, "message": str(exc), "post_count": 0,
                        "total_queries": total_queries,
                        "current_query_index": current_query_index,
                    })
                    await emit_discussion_progress(progress, {
                        "type": "progress",
                        "platform": "reddit",
                        "stage": "query_error",
                        "board": "reddit-global",
                        "keyword": query,
                        "current_query_index": current_query_index,
                        "total_queries": total_queries,
                        "message": str(exc),
                        "found_so_far": len(collected_by_url),
                    })
        return list(collected_by_url.values()), diagnostics

    if is_default_mode:
        diagnostics.append({
            "platform": "reddit",
            "ok": True,
            "message": f"Reddit 默认模式：使用 {len(query_terms)} 个中/英/日关键词逐词搜索 {', '.join('r/' + s for s in subreddits)}，每词间延迟 {request_delay} 秒。",
            "post_count": 0,
        })
    oauth_available = _reddit_oauth_enabled()
    async with httpx.AsyncClient(timeout=REDDIT_TIMEOUT, follow_redirects=True, headers=PUBLIC_BROWSER_HEADERS) as client:
        oauth_token: str | None = None
        if oauth_available:
            try:
                oauth_token = await _reddit_oauth_token(client)
                if oauth_token:
                    diagnostics.append({"platform": "reddit", "ok": True, "message": "Reddit OAuth 已配置，将使用 API 获取数据。", "post_count": 0})
            except Exception:
                pass
        first_request = True
        total_queries = len(subreddits) * len(query_terms)
        current_query_index = 0
        for subreddit in subreddits:
            rate_limited = 0
            for query in query_terms:
                if rate_limited >= 2:
                    diagnostics.append({
                        "platform": "reddit", "board": f"r/{subreddit}",
                        "ok": False,
                        "message": "Reddit 连续限流，本社区本轮停止继续请求。",
                        "post_count": 0,
                        "total_queries": total_queries,
                        "current_query_index": current_query_index,
                    })
                    break
                current_query_index += 1
                await emit_discussion_progress(progress, {
                    "type": "progress",
                    "platform": "reddit",
                    "stage": "query_start",
                    "board": f"r/{subreddit}",
                    "keyword": query,
                    "current_query_index": current_query_index,
                    "total_queries": total_queries,
                    "found_so_far": len(collected_by_url),
                })
                if not first_request and request_delay > 0:
                    await asyncio.sleep(request_delay)
                first_request = False
                query_results: list[dict[str, Any]] = []
                comments_by_url: dict[str, list[dict[str, Any]]] = {}
                try:
                    if oauth_token:
                        oauth_results, oauth_diag = await fetch_reddit_via_oauth(
                            client, subreddit, query, start_date, end_date, max_posts, max_comments, oauth_token,
                        )
                        oauth_diag["total_queries"] = total_queries
                        oauth_diag["current_query_index"] = current_query_index
                        diagnostics.append(oauth_diag)
                        for item in oauth_results:
                            url = item["post"]["url"]
                            query_results.append(item["post"])
                            comments_by_url[url] = item.get("comments", [])
                    if not query_results:
                        html_posts, html_diag = await fetch_reddit_via_old_search(
                            client, subreddit, query, start_date, end_date, max_posts,
                        )
                        html_diag["total_queries"] = total_queries
                        html_diag["current_query_index"] = current_query_index
                        diagnostics.append(html_diag)
                        if html_diag["status_code"] in {403, 429}:
                            rate_limited += 1
                        query_results = html_posts
                        if query_results and fetch_comments:
                            for post in query_results:
                                reddit_id = post.get("reddit_id")
                                if reddit_id and request_delay > 0:
                                    await asyncio.sleep(request_delay * 0.5)
                                if reddit_id:
                                    try:
                                        comments_by_url[post["url"]] = await fetch_reddit_via_old_comments(
                                            client, reddit_id, max_comments,
                                        )
                                    except Exception:
                                        pass
                    added = 0
                    for post in query_results:
                        url = post["url"]
                        post.pop("reddit_id", None)
                        if url not in collected_by_url:
                            added += 1
                        collected_by_url[url] = {"post": post, "comments": comments_by_url.get(url, [])}
                    if added:
                        diag_entry = {
                            "platform": "reddit", "board": f"r/{subreddit}", "keyword": query,
                            "ok": True, "post_count": len(query_results), "new_unique_posts": added,
                            "source": "old_reddit_search",
                            "total_queries": total_queries,
                            "current_query_index": current_query_index,
                        }
                        diagnostics.append(diag_entry)
                    if stop_after_default_hit and collected_by_url:
                        diagnostics.append({
                            "platform": "reddit", "board": f"r/{subreddit}",
                            "ok": True,
                            "message": "默认模式已取得 Reddit 公开样本，本轮停止继续请求以降低限流风险。",
                            "post_count": 0,
                        })
                        await emit_discussion_progress(progress, {
                            "type": "progress",
                            "platform": "reddit",
                            "stage": "stopped_after_hit",
                            "board": f"r/{subreddit}",
                            "current_query_index": current_query_index,
                            "total_queries": total_queries,
                            "found_so_far": len(collected_by_url),
                        })
                        return list(collected_by_url.values()), diagnostics
                    await emit_discussion_progress(progress, {
                        "type": "progress",
                        "platform": "reddit",
                        "stage": "query_done",
                        "board": f"r/{subreddit}",
                        "keyword": query,
                        "current_query_index": current_query_index,
                        "total_queries": total_queries,
                        "post_count": len(query_results),
                        "new_unique_posts": added,
                        "found_so_far": len(collected_by_url),
                    })
                except Exception as exc:
                    diagnostics.append({
                        "platform": "reddit", "board": f"r/{subreddit}", "keyword": query,
                        "ok": False, "message": str(exc), "post_count": 0,
                        "total_queries": total_queries,
                        "current_query_index": current_query_index,
                    })
                    await emit_discussion_progress(progress, {
                        "type": "progress",
                        "platform": "reddit",
                        "stage": "query_error",
                        "board": f"r/{subreddit}",
                        "keyword": query,
                        "current_query_index": current_query_index,
                        "total_queries": total_queries,
                        "message": str(exc),
                        "found_so_far": len(collected_by_url),
                    })
            if not collected_by_url and subreddit in ("japan", "okinawa"):
                try:
                    await emit_discussion_progress(progress, {
                        "type": "progress",
                        "platform": "reddit",
                        "stage": "fallback_start",
                        "board": f"r/{subreddit}",
                        "current_query_index": total_queries,
                        "total_queries": total_queries,
                        "found_so_far": len(collected_by_url),
                    })
                    if request_delay > 0:
                        await asyncio.sleep(request_delay)
                    fallback_posts, fallback_diag = await fetch_reddit_listing_fallback(
                        client, subreddit, start_date, end_date, max_posts,
                    )
                    diagnostics.append(fallback_diag)
                    added = 0
                    for post in fallback_posts:
                        post.pop("reddit_id", None)
                        if post["url"] not in collected_by_url:
                            added += 1
                        collected_by_url[post["url"]] = {"post": post, "comments": []}
                    if stop_after_default_hit and collected_by_url:
                        diagnostics.append({
                            "platform": "reddit", "board": f"r/{subreddit}",
                            "ok": True,
                            "message": "默认模式已从最新公开帖子列表取得样本，本轮停止继续请求。",
                            "post_count": 0,
                        })
                        await emit_discussion_progress(progress, {
                            "type": "progress",
                            "platform": "reddit",
                            "stage": "fallback_done",
                            "board": f"r/{subreddit}",
                            "current_query_index": total_queries,
                            "total_queries": total_queries,
                            "new_unique_posts": added,
                            "found_so_far": len(collected_by_url),
                        })
                        return list(collected_by_url.values()), diagnostics
                    await emit_discussion_progress(progress, {
                        "type": "progress",
                        "platform": "reddit",
                        "stage": "fallback_done",
                        "board": f"r/{subreddit}",
                        "current_query_index": total_queries,
                        "total_queries": total_queries,
                        "new_unique_posts": added,
                        "found_so_far": len(collected_by_url),
                    })
                except Exception as exc:
                    diagnostics.append({
                        "platform": "reddit", "board": f"r/{subreddit}",
                        "source": "fallback", "ok": False, "message": str(exc), "post_count": 0,
                    })
                    await emit_discussion_progress(progress, {
                        "type": "progress",
                        "platform": "reddit",
                        "stage": "fallback_error",
                        "board": f"r/{subreddit}",
                        "current_query_index": total_queries,
                        "total_queries": total_queries,
                        "message": str(exc),
                        "found_so_far": len(collected_by_url),
                    })
    return list(collected_by_url.values()), diagnostics


def parse_dcard_next_data(html: str, forum: str) -> list[dict[str, Any]]:
    posts = []
    match = re.search(r'<script\s+id="__NEXT_DATA__"[^>]*>\s*({.*?})\s*</script>', html, re.DOTALL)
    if not match:
        match = re.search(r'window\.__NEXT_DATA__\s*=\s*({.*?});', html, re.DOTALL)
    if not match:
        return posts
    import json
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return posts
    props = data.get("props", {}).get("pageProps", {})
    articles = props.get("posts") or props.get("articles") or []
    if not articles and isinstance(props, dict):
        for key in props:
            val = props[key]
            if isinstance(val, list) and val and isinstance(val[0], dict) and "title" in val[0]:
                articles = val
                break
    for item in articles:
        post_id = item.get("id") or item.get("postId")
        title = item.get("title") or ""
        excerpt = item.get("excerpt") or item.get("summary") or ""
        url = f"https://www.dcard.tw/f/{forum}/p/{post_id}" if post_id else ""
        if not title or not url:
            continue
        posts.append({
            "platform": "dcard",
            "board_name": forum,
            "title": BeautifulSoup(title, "html.parser").get_text(" ", strip=True),
            "url": url,
            "author": str(item.get("school") or item.get("author") or "dcard_public"),
            "published_at": parse_datetime(item.get("createdAt") or item.get("created_at") or item.get("date")),
            "language": "zh-Hant",
            "raw_text": f"{title}\n{excerpt}",
            "interaction_count": int(item.get("likeCount") or item.get("likes") or 0),
            "comment_count": int(item.get("commentCount") or item.get("comments") or 0),
            "dcard_id": post_id,
        })
    return posts


def parse_dcard_html_fallback(html: str, forum: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    posts = []
    for article in soup.select("article"):
        title_el = article.select_one("h2 a, h3 a, .title a")
        if not title_el:
            continue
        title = title_el.get_text(" ", strip=True)
        href = title_el.get("href") or ""
        if href.startswith("/"):
            href = f"https://www.dcard.tw{href}"
        excerpt_el = article.select_one(".excerpt, .summary, p")
        excerpt = excerpt_el.get_text(" ", strip=True) if excerpt_el else ""
        time_el = article.select_one("time")
        published_at = parse_datetime(time_el.get("datetime") if time_el else None)
        posts.append({
            "platform": "dcard",
            "board_name": forum,
            "title": title,
            "url": href,
            "author": "dcard_public",
            "published_at": published_at,
            "language": "zh-Hant",
            "raw_text": f"{title}\n{excerpt}",
            "interaction_count": 0,
            "comment_count": 0,
        })
    return posts


async def fetch_dcard_via_html(
    client: httpx.AsyncClient,
    forum: str,
    start_date: date,
    end_date: date,
    max_posts: int,
    keywords: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    diagnostics = []
    collected_by_url: dict[str, dict[str, Any]] = {}
    relevance_keywords = dcard_relevance_keywords(keywords)
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        diagnostics.append({
            "platform": "dcard", "source": "html_scrape", "board": forum,
            "ok": False, "message": "curl_cffi 未安装，无法绕过 Cloudflare TLS 检测。", "post_count": 0,
        })
        return [], diagnostics
    urls = [
        f"https://www.dcard.tw/f/{forum}?latest=true",
        f"https://www.dcard.tw/f/{forum}",
    ]
    proxy_arg = _get_proxy()
    proxies = {"http": proxy_arg, "https": proxy_arg} if proxy_arg else None
    async with AsyncSession(impersonate="chrome131", timeout=30.0, proxies=proxies) as cffi_client:
        for url in urls:
            try:
                response = await cffi_client.get(url, headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
                })
                diagnostic = {
                    "platform": "dcard", "source": "html_scrape_curl_cffi", "board": forum,
                    "status_code": response.status_code, "ok": False, "post_count": 0,
                }
                if response.status_code >= 400:
                    diagnostic["message"] = f"Dcard 页面返回 {response.status_code}。"
                    diagnostics.append(diagnostic)
                    continue
                posts = parse_dcard_next_data(response.text, forum)
                if not posts:
                    posts = parse_dcard_html_fallback(response.text, forum)
                posts = [
                    p for p in posts
                    if in_date_range(p.get("published_at"), start_date, end_date)
                    and discussion_relevant(p, relevance_keywords)
                ][:max_posts]
                for post in posts:
                    collected_by_url[post["url"]] = {"post": post, "comments": []}
                diagnostic["ok"] = len(posts) > 0
                diagnostic["post_count"] = len(posts)
                diagnostics.append(diagnostic)
                if collected_by_url:
                    break
            except Exception as exc:
                diagnostics.append({
                    "platform": "dcard", "source": "html_scrape_curl_cffi", "board": forum,
                    "ok": False, "message": str(exc), "post_count": 0,
                })
    return list(collected_by_url.values()), diagnostics


async def fetch_dcard_via_graphql(
    client: httpx.AsyncClient,
    forum: str,
    start_date: date,
    end_date: date,
    max_posts: int,
    max_comments: int,
    keywords: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    diagnostics = []
    collected: list[dict[str, Any]] = []
    relevance_keywords = dcard_relevance_keywords(keywords)
    gql_urls = [
        "https://www.dcard.tw/service/api/v2/graphql",
        "https://www.dcard.tw/_next/data/abc123/trending.json",
    ]
    for gql_url in gql_urls:
        try:
            if "graphql" in gql_url:
                query = {
                    "operationName": "ForumPosts",
                    "variables": {"forumAlias": forum, "popular": False, "limit": max_posts},
                    "query": "query ForumPosts($forumAlias: String!, $popular: Boolean, $limit: Int) { forum(alias: $forumAlias) { posts(popular: $popular, limit: $limit) { id title excerpt createdAt likeCount commentCount school } } }",
                }
                response = await client.post(gql_url, json=query, headers={**PUBLIC_BROWSER_HEADERS, "Content-Type": "application/json"})
            else:
                response = await client.get(gql_url, headers=PUBLIC_BROWSER_HEADERS)
            diagnostic = {
                "platform": "dcard", "source": "graphql", "board": forum,
                "status_code": response.status_code, "ok": False, "post_count": 0,
            }
            if response.status_code >= 400:
                diagnostics.append(diagnostic)
                continue
            try:
                data = response.json()
            except Exception:
                diagnostics.append({**diagnostic, "message": "GraphQL 响应非 JSON。"})
                continue
            posts_list: list[dict[str, Any]] = []
            if "data" in data:
                posts_list = data.get("data", {}).get("forum", {}).get("posts", [])
            elif "pageProps" in data:
                posts_list = data.get("pageProps", {}).get("posts", [])
            posts = [
                p for p in parse_dcard_posts(posts_list, forum)
                if in_date_range(p.get("published_at"), start_date, end_date)
                and discussion_relevant(p, relevance_keywords)
            ][:max_posts]
            for post in posts:
                comments = []
                dcard_id = post.pop("dcard_id", None)
                if dcard_id and max_comments > 0:
                    try:
                        c_resp = await client.get(
                            f"https://www.dcard.tw/service/api/v2/posts/{dcard_id}/comments?limit={max_comments}",
                        )
                        if c_resp.status_code == 200:
                            for floor, item in enumerate(c_resp.json(), start=1):
                                text = item.get("content") or ""
                                if text:
                                    comments.append({
                                        "author": str(item.get("school") or "dcard_public"),
                                        "comment_text": text,
                                        "vote_type": "like",
                                        "score": int(item.get("likeCount") or 0),
                                        "floor": floor,
                                        "published_at": parse_datetime(item.get("createdAt")),
                                    })
                    except Exception:
                        pass
                collected.append({"post": post, "comments": comments[:max_comments]})
            diagnostic["ok"] = True
            diagnostic["post_count"] = len(posts)
            diagnostics.append(diagnostic)
            if collected:
                break
        except Exception as exc:
            diagnostics.append({
                "platform": "dcard", "source": "graphql", "board": forum,
                "ok": False, "message": str(exc), "post_count": 0,
            })
    return collected, diagnostics


def parse_dcard_posts(data: list[dict[str, Any]], forum: str) -> list[dict[str, Any]]:
    posts = []
    for item in data:
        post_id = item.get("id")
        title = item.get("title") or ""
        excerpt = item.get("excerpt") or ""
        posts.append(
            {
                "platform": "dcard",
                "board_name": forum,
                "title": title,
                "url": f"https://www.dcard.tw/f/{forum}/p/{post_id}" if post_id else "",
                "author": str(item.get("school") or item.get("department") or "dcard_public"),
                "published_at": parse_datetime(item.get("createdAt")),
                "language": "zh-Hant",
                "raw_text": f"{title}\n{excerpt}",
                "interaction_count": int(item.get("likeCount") or 0),
                "comment_count": int(item.get("commentCount") or 0),
                "dcard_id": post_id,
            }
        )
    return [post for post in posts if post["title"] and post["url"]]


def parse_dcard_rsshub_json(data: dict[str, Any], forum: str) -> list[dict[str, Any]]:
    posts = []
    for item in data.get("items") or []:
        title = item.get("title") or ""
        url = item.get("url") or item.get("external_url") or ""
        text = item.get("content_text") or item.get("summary") or item.get("content_html") or title
        if not title or not url:
            continue
        posts.append(
            {
                "platform": "dcard",
                "board_name": forum,
                "title": BeautifulSoup(title, "html.parser").get_text(" ", strip=True),
                "url": url,
                "author": str(item.get("author", {}).get("name") if isinstance(item.get("author"), dict) else item.get("author") or "dcard_public"),
                "published_at": parse_datetime(item.get("date_published") or item.get("pubDate")),
                "language": "zh-Hant",
                "raw_text": BeautifulSoup(text, "html.parser").get_text(" ", strip=True),
                "interaction_count": 0,
                "comment_count": 0,
            }
        )
    return posts



async def fetch_dcard_via_rsshub(
    client: httpx.AsyncClient,
    forum: str,
    start_date: date,
    end_date: date,
    max_posts: int,
    keywords: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    diagnostics = []
    collected_by_url: dict[str, dict[str, Any]] = {}
    base_url = rsshub_base_url()
    relevance_keywords = dcard_relevance_keywords(keywords)
    for feed_type in DCARD_RSSHUB_TYPES:
        url = f"{base_url}/dcard/{forum}/{feed_type}?format=json"
        try:
            response = await client.get(url)
            diagnostic = {
                "platform": "dcard",
                "source": "rsshub",
                "board": forum,
                "feed_type": feed_type,
                "status_code": response.status_code,
                "ok": False,
                "post_count": 0,
            }
            if response.status_code >= 400:
                diagnostic["message"] = "RSSHub 的 Dcard 开源路由当前不可用；继续尝试其他公开入口。"
                diagnostics.append(diagnostic)
                continue
            posts = [
                post
                for post in parse_dcard_rsshub_json(response.json(), forum)
                if in_date_range(post.get("published_at"), start_date, end_date)
                and discussion_relevant(post, relevance_keywords)
            ]
            for post in posts[:max_posts]:
                collected_by_url[post["url"]] = {"post": post, "comments": []}
            diagnostic["ok"] = True
            diagnostic["post_count"] = len(posts[:max_posts])
            diagnostics.append(diagnostic)
        except Exception as exc:
            diagnostics.append(
                {
                    "platform": "dcard",
                    "source": "rsshub",
                    "board": forum,
                    "feed_type": feed_type,
                    "ok": False,
                    "message": str(exc),
                    "post_count": 0,
                }
            )
    return list(collected_by_url.values()), diagnostics


async def fetch_dcard_via_api(
    client: httpx.AsyncClient,
    forum: str,
    start_date: date,
    end_date: date,
    max_posts: int,
    max_comments: int,
    keywords: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    diagnostics = []
    collected = []
    relevance_keywords = dcard_relevance_keywords(keywords)
    api_headers = {
        "Accept": "application/json",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://www.dcard.tw",
        "Referer": "https://www.dcard.tw/",
    }
    try:
        from curl_cffi.requests import AsyncSession
        proxy_arg = _get_proxy()
        proxies = {"http": proxy_arg, "https": proxy_arg} if proxy_arg else None
        async with AsyncSession(impersonate="chrome131", timeout=30.0, proxies=proxies) as cffi_client:
            runtime_cookies = _dcard_cookies()
            if runtime_cookies:
                for ck, cv in runtime_cookies.items():
                    cffi_client.cookies.set(ck, cv)
            response = await cffi_client.get(
                f"https://www.dcard.tw/service/api/v2/forums/{forum}/posts?popular=false&limit={max_posts}",
                headers=api_headers,
            )
            diagnostic = {"platform": "dcard", "source": "public_api_cffi", "board": forum, "status_code": response.status_code, "ok": False, "post_count": 0}
            if response.status_code in {403, 429}:
                diagnostic["message"] = "Dcard 公开 API 当前拒绝程序请求；不会使用登录、cookie、验证码绕过或私密接口。"
                diagnostics.append(diagnostic)
                return collected, diagnostics
            response.raise_for_status()
            data = response.json()
            posts = [
                post
                for post in parse_dcard_posts(data, forum)
                if in_date_range(post.get("published_at"), start_date, end_date)
                and discussion_relevant(post, relevance_keywords)
            ]
            for post in posts[:max_posts]:
                comments = []
                dcard_id = post.pop("dcard_id", None)
                if dcard_id:
                    try:
                        comment_response = await cffi_client.get(
                            f"https://www.dcard.tw/service/api/v2/posts/{dcard_id}/comments?limit={max_comments}",
                            headers=api_headers,
                        )
                        if comment_response.status_code == 200:
                            for floor, item in enumerate(comment_response.json(), start=1):
                                text = item.get("content") or ""
                                if text:
                                    comments.append({
                                        "author": str(item.get("school") or "dcard_public"),
                                        "comment_text": text,
                                        "vote_type": "like",
                                        "score": int(item.get("likeCount") or 0),
                                        "floor": floor,
                                        "published_at": parse_datetime(item.get("createdAt")),
                                    })
                    except Exception:
                        comments = []
                collected.append({"post": post, "comments": comments[:max_comments]})
            diagnostic["ok"] = True
            diagnostic["post_count"] = len(posts[:max_posts])
            diagnostics.append(diagnostic)
    except ImportError:
        diagnostics.append({
            "platform": "dcard", "source": "public_api", "board": forum,
            "ok": False, "message": "curl_cffi 未安装，无法绕过 Cloudflare。", "post_count": 0,
        })
    except Exception as exc:
        diagnostics.append({
            "platform": "dcard", "source": "public_api", "board": forum,
            "ok": False, "message": f"Dcard 当前不可用：{type(exc).__name__}: {exc}", "post_count": 0,
        })
    return collected, diagnostics



DCARD_COOKIE_KEYS = {"cf_clearance", "__cf_bm", "dcard-web-oauth-tk"}
_runtime_dcard_cookies: dict[str, str] | None = None


def set_dcard_cookies(cookies: dict[str, str]) -> None:
    global _runtime_dcard_cookies
    cleaned = {}
    for k, v in cookies.items():
        if k not in DCARD_COOKIE_KEYS or not v:
            continue
        if v:
            v = v.replace('\\"', '"')
        cleaned[k] = v
    _runtime_dcard_cookies = cleaned or None


def _dcard_cf_clearance() -> str | None:
    if _runtime_dcard_cookies and _runtime_dcard_cookies.get("cf_clearance"):
        return _runtime_dcard_cookies["cf_clearance"]
    return os.getenv("DCARD_CF_CLEARANCE", "").strip() or None


def _get_proxy() -> str | None:
    proxy = os.getenv("HTTP_PROXY", "") or os.getenv("HTTPS_PROXY", "") or os.getenv("OUTBOUND_HTTP_PROXY", "")
    return proxy.strip() or None


def _dcard_cookies() -> dict[str, str] | None:
    cookies: dict[str, str] = {}
    cf = _dcard_cf_clearance()
    if cf:
        cookies["cf_clearance"] = cf
    cf_bm = os.getenv("DCARD_CF_BM", "").strip()
    if cf_bm:
        cookies["__cf_bm"] = cf_bm
    oauth = os.getenv("DCARD_OAUTH_TOKEN", "").strip()
    if oauth:
        oauth = oauth.replace('\\"', '"')
        cookies["dcard-web-oauth-tk"] = oauth
    if _runtime_dcard_cookies:
        cookies.update(_runtime_dcard_cookies)
    for key in ("DCARD_CF_BM", "DCARD_OAUTH_TOKEN"):
        pass
    return cookies or None


def dcard_relevance_keywords(keywords: list[str] | None) -> list[str]:
    return [item for item in (keywords or DEFAULT_DISCUSSION_KEYWORDS) if item]


async def fetch_dcard_via_cookie(
    forum: str,
    start_date: date,
    end_date: date,
    max_posts: int,
    keywords: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    diagnostics = []
    collected_by_url: dict[str, dict[str, Any]] = {}
    relevance_keywords = dcard_relevance_keywords(keywords)
    cookies = _dcard_cookies()
    if not cookies:
        diagnostics.append({
            "platform": "dcard", "source": "cookie", "board": forum,
            "ok": False, "message": "Dcard Cookie/OAuth token is not configured; skipping cookie-based fetch.", "post_count": 0,
        })
        return [], diagnostics
    try:
        from curl_cffi.requests import AsyncSession

        proxy_arg = _get_proxy()
        proxies = {"http": proxy_arg, "https": proxy_arg} if proxy_arg else None

        oauth_token = cookies.get("dcard-web-oauth-tk", "").strip()

        if oauth_token:
            import json as _json
            try:
                oauth_data = _json.loads(oauth_token)
                if isinstance(oauth_data, dict) and "accessToken" in oauth_data:
                    oauth_token = oauth_data["accessToken"]
            except (_json.JSONDecodeError, TypeError):
                pass

        if oauth_token:
            async with AsyncSession(impersonate="chrome131", timeout=30.0, proxies=proxies) as cffi_client:
                for ck, cv in cookies.items():
                    cffi_client.cookies.set(ck, cv)
                api_headers = {
                    "Accept": "application/json",
                    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
                    "Authorization": f"Bearer {oauth_token}",
                    "Origin": "https://www.dcard.tw",
                    "Referer": "https://www.dcard.tw/",
                }
                try:
                    api_resp = await cffi_client.get(
                        f"https://www.dcard.tw/service/api/v2/forums/{forum}/posts?popular=false&limit={max_posts}",
                        headers=api_headers,
                    )
                    api_diag = {
                        "platform": "dcard", "source": "cookie_oauth_api", "board": forum,
                        "status_code": api_resp.status_code, "ok": False, "post_count": 0,
                    }
                    if api_resp.status_code == 200:
                        data = api_resp.json()
                        posts = [
                            p for p in parse_dcard_posts(data, forum)
                            if in_date_range(p.get("published_at"), start_date, end_date)
                            and discussion_relevant(p, relevance_keywords)
                        ][:max_posts]
                        for post in posts:
                            collected_by_url[post["url"]] = {"post": post, "comments": []}
                        api_diag["ok"] = len(posts) > 0
                        api_diag["post_count"] = len(posts)
                        api_diag["message"] = f"OAuth API 成功获取 {len(posts)} 篇帖子"
                    else:
                        api_diag["message"] = f"OAuth API 返回 {api_resp.status_code}，回退到 HTML 抓取"
                    diagnostics.append(api_diag)
                except Exception as exc:
                    diagnostics.append({
                        "platform": "dcard", "source": "cookie_oauth_api", "board": forum,
                        "ok": False, "message": f"OAuth API 失败: {exc}", "post_count": 0,
                    })

        if not collected_by_url:
            urls = [
                f"https://www.dcard.tw/f/{forum}?latest=true",
                f"https://www.dcard.tw/f/{forum}",
            ]
            async with AsyncSession(impersonate="chrome131", timeout=30.0, proxies=proxies) as cffi_client:
                for ck, cv in cookies.items():
                    cffi_client.cookies.set(ck, cv)
                for url in urls:
                    try:
                        response = await cffi_client.get(url, headers={
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
                        })
                        diagnostic = {
                            "platform": "dcard", "source": "cookie_html", "board": forum,
                            "status_code": response.status_code, "ok": False, "post_count": 0,
                        }
                        if response.status_code >= 400:
                            diagnostic["message"] = f"Dcard 页面返回 {response.status_code}（Cookie 可能已过期）。"
                            diagnostics.append(diagnostic)
                            continue
                        posts = parse_dcard_next_data(response.text, forum)
                        if not posts:
                            posts = parse_dcard_html_fallback(response.text, forum)
                        posts = [
                            p for p in posts
                            if in_date_range(p.get("published_at"), start_date, end_date)
                            and discussion_relevant(p, relevance_keywords)
                        ][:max_posts]
                        for post in posts:
                            collected_by_url[post["url"]] = {"post": post, "comments": []}
                        diagnostic["ok"] = len(posts) > 0
                        diagnostic["post_count"] = len(posts)
                        diagnostics.append(diagnostic)
                        if collected_by_url:
                            break
                    except Exception as exc:
                        diagnostics.append({
                            "platform": "dcard", "source": "cookie_html", "board": forum,
                            "ok": False, "message": str(exc), "post_count": 0,
                        })
    except ImportError:
        diagnostics.append({
            "platform": "dcard", "source": "cookie", "board": forum,
            "ok": False, "message": "curl_cffi 未安装。", "post_count": 0,
        })
    return list(collected_by_url.values()), diagnostics


async def fetch_dcard_via_playwright_service(
    forum: str,
    start_date: date,
    end_date: date,
    max_posts: int,
    keywords: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Call Windows cookie_service /fetch endpoint which uses Playwright directly."""
    host_ip = os.getenv("WINDOWS_HOST_IP", "172.19.240.1")
    cookie_service_port = os.getenv("COOKIE_SERVICE_PORT", "8096")
    url = f"http://{host_ip}:{cookie_service_port}/fetch"
    diagnostics = []
    collected = []
    relevance_keywords = dcard_relevance_keywords(keywords)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0), trust_env=False) as client:
            resp = await client.post(url, json={"forum": forum, "limit": max_posts})
            if resp.status_code != 200:
                diagnostics.append({
                    "platform": "dcard", "source": "playwright_service", "board": forum,
                    "ok": False, "message": f"Windows Playwright 服务返回 {resp.status_code}", "post_count": 0,
                })
                return collected, diagnostics
            data = resp.json()
            if not data.get("ok"):
                diagnostics.append({
                    "platform": "dcard", "source": "playwright_service", "board": forum,
                    "ok": False, "message": data.get("message", "未知错误"), "post_count": 0,
                })
                return collected, diagnostics
            for post_data in data.get("posts", []):
                post = {
                    "platform": "dcard",
                    "board_name": post_data.get("board_name", forum),
                    "title": post_data.get("title", ""),
                    "url": post_data.get("url", ""),
                    "author": post_data.get("author", "dcard_public"),
                    "published_at": parse_datetime(post_data.get("published_at")),
                    "language": post_data.get("language", "zh-Hant"),
                    "raw_text": post_data.get("raw_text", ""),
                    "interaction_count": post_data.get("like_count", 0),
                    "comment_count": post_data.get("comment_count", 0),
                }
                if in_date_range(post.get("published_at"), start_date, end_date) and discussion_relevant(post, relevance_keywords):
                    collected.append({"post": post, "comments": []})
            diagnostics.append({
                "platform": "dcard", "source": "playwright_service", "board": forum,
                "ok": len(collected) > 0, "post_count": len(collected),
                "message": f"Windows Playwright 抓取成功 {len(collected)} 篇帖子",
            })
    except httpx.ConnectError:
        diagnostics.append({
            "platform": "dcard", "source": "playwright_service", "board": forum,
            "ok": False, "message": f"无法连接 Windows Playwright 服务 ({url})", "post_count": 0,
        })
    except Exception as exc:
        diagnostics.append({
            "platform": "dcard", "source": "playwright_service", "board": forum,
            "ok": False, "message": f"Playwright 服务错误: {exc}", "post_count": 0,
        })
    return collected, diagnostics


async def fetch_dcard_discussions(
    start_date: date,
    end_date: date,
    max_posts: int,
    max_comments: int,
    keywords: list[str] | None = None,
    progress: DiscussionProgressCallback | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    diagnostics = []
    collected_by_url: dict[str, dict[str, Any]] = {}
    forums = os.getenv("DCARD_FORUMS", "").strip()
    forum_list = [f.strip() for f in forums.split(",") if f.strip()] if forums else DCARD_FORUMS
    async with httpx.AsyncClient(timeout=DCARD_TIMEOUT, follow_redirects=True, headers=PUBLIC_BROWSER_HEADERS) as client:
        total_steps = len(forum_list) * 5
        current_step = 0
        for forum in forum_list:
            layers: list[tuple[str, Any]] = [
                ("playwright_service", lambda f=forum: fetch_dcard_via_playwright_service(f, start_date, end_date, max_posts, keywords)),
                ("cookie", lambda f=forum: fetch_dcard_via_cookie(f, start_date, end_date, max_posts, keywords)),
                ("html", lambda f=forum: fetch_dcard_via_html(client, f, start_date, end_date, max_posts, keywords)),
                ("rsshub", lambda f=forum: fetch_dcard_via_rsshub(client, f, start_date, end_date, max_posts, keywords)),
                ("public_api", lambda f=forum: fetch_dcard_via_api(client, f, start_date, end_date, max_posts, max_comments, keywords)),
            ]
            forum_collected = False
            for layer_name, layer in layers:
                current_step += 1
                await emit_discussion_progress(progress, {
                    "type": "progress",
                    "platform": "dcard",
                    "stage": "layer_start",
                    "board": forum,
                    "source": layer_name,
                    "current_step": current_step,
                    "total_steps": total_steps,
                    "found_so_far": len(collected_by_url),
                })
                items, layer_diagnostics = await layer()
                diagnostics.extend(layer_diagnostics)
                for item in items:
                    collected_by_url[item["post"]["url"]] = item
                await emit_discussion_progress(progress, {
                    "type": "progress",
                    "platform": "dcard",
                    "stage": "layer_done",
                    "board": forum,
                    "source": layer_name,
                    "current_step": current_step,
                    "total_steps": total_steps,
                    "post_count": len(items),
                    "found_so_far": len(collected_by_url),
                    "diagnostics": layer_diagnostics,
                })
                if not forum_collected and any(
                    item["post"]["url"] in collected_by_url for item in items
                ):
                    forum_collected = True
                if forum_collected:
                    break
    if not collected_by_url:
        diagnostics.append({
            "platform": "dcard",
            "ok": False,
            "message": "Dcard 本轮未取得公开样本。已尝试 Cookie 绕过、curl_cffi HTML 抓取、RSSHub 开源路由和公开 API。",
            "post_count": 0,
        })
    return list(collected_by_url.values()), diagnostics


async def fetch_discussions_by_platform(
    platform: str,
    start_date: date,
    end_date: date,
    keywords: list[str] | None,
    max_posts: int,
    max_comments: int,
    progress: DiscussionProgressCallback | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if platform == "reddit":
        return await fetch_reddit_discussions(start_date, end_date, keywords, max_posts, max_comments, progress)
    if platform == "discord":
        return [], [
            {
                "platform": "discord",
                "ok": False,
                "message": "Discord usually requires login or server membership. This project only fetches public sources, so Discord scraping is disabled.",
                "post_count": 0,
            }
        ]
    return [], [{"platform": platform, "ok": False, "message": "Unsupported platform", "post_count": 0}]


def list_discussions(
    start_date: date | None = None,
    end_date: date | None = None,
    platform: str | None = None,
    keyword: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    ensure_discussion_tables()
    clauses = []
    params: list[Any] = []
    if start_date:
        clauses.append("COALESCE(published_at, fetched_at) >= %s")
        params.append(datetime.combine(start_date, time.min, tzinfo=timezone.utc))
    if end_date:
        clauses.append("COALESCE(published_at, fetched_at) < %s")
        params.append(datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone.utc))
    if platform:
        clauses.append("platform = %s")
        params.append(platform)
    if keyword:
        clauses.append("(title ILIKE %s OR clean_text ILIKE %s)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(limit)
    return fetch_all(
        f"""
        SELECT p.*,
               COALESCE(c.comment_rows, 0) AS stored_comment_count,
               COALESCE(s.sentiment_counts, '{{}}'::jsonb) AS sentiment_counts
        FROM discussion_posts p
        LEFT JOIN (
            SELECT post_id, COUNT(*) AS comment_rows
            FROM discussion_comments GROUP BY post_id
        ) c ON c.post_id = p.id
        LEFT JOIN (
            SELECT post_id, jsonb_object_agg(sentiment_label, count) AS sentiment_counts
            FROM (
                SELECT post_id, sentiment_label, COUNT(*) AS count
                FROM discussion_sentiments GROUP BY post_id, sentiment_label
            ) x GROUP BY post_id
        ) s ON s.post_id = p.id
        {where}
        ORDER BY COALESCE(p.published_at, p.fetched_at) DESC, p.id DESC
        LIMIT %s
        """,
        tuple(params),
    )


def discussion_detail(post_id: int) -> dict[str, Any] | None:
    ensure_discussion_tables()
    post = fetch_one("SELECT * FROM discussion_posts WHERE id = %s", (post_id,))
    if not post:
        return None
    comments = fetch_all(
        """
        SELECT id, comment_text, vote_type, score, floor, published_at
        FROM discussion_comments
        WHERE post_id = %s
        ORDER BY COALESCE(floor, id)
        LIMIT 300
        """,
        (post_id,),
    )
    sentiments = fetch_all(
        """
        SELECT sentiment_label, keywords, narrative_tags, confidence, llm_analyzed, comment_id
        FROM discussion_sentiments
        WHERE post_id = %s
        ORDER BY id
        """,
        (post_id,),
    )
    return {
        "post": post,
        "comments": comments,
        "sentiments": sentiments,
        "summary": summarize_sentiments(sentiments),
    }


def _empty_discussion_window_summary(start: date, end: date, label: str) -> dict[str, Any]:
    profile = sentiment_profile_from_counts({})
    return {
        "label": label,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "post_count": 0,
        "comment_count": 0,
        "platform_counts": {},
        "sentiment_counts": {},
        "sentiment_percentages": {},
        "dominant_sentiment": profile["dominant_label"],
        "dominant_sentiment_name": profile["dominant_name"],
        "overall_attitude": profile["attitude_label"],
        "overall_attitude_icon": profile["icon"],
        "overall_attitude_color": profile["color"],
        "sentiment_sample_size": 0,
        "narrative_counts": {},
        "top_narrative_tags": [],
        "top_posts": [],
        "sample_comments": [],
    }


def _discussion_window_summary(
    start: date,
    end: date,
    *,
    limit: int,
    label: str,
    include_samples: bool,
) -> dict[str, Any]:
    posts = list_discussions(start, end, limit=limit)
    if not posts:
        return _empty_discussion_window_summary(start, end, label)

    post_ids = [post["id"] for post in posts]
    sentiments = fetch_all(
        "SELECT sentiment_label, narrative_tags FROM discussion_sentiments WHERE post_id = ANY(%s)",
        (post_ids,),
    )
    comments: list[dict[str, Any]] = []
    if include_samples:
        comments = fetch_all(
            """
            SELECT p.platform, p.board_name, p.title, c.comment_text, c.score
            FROM discussion_comments c
            JOIN discussion_posts p ON p.id = c.post_id
            WHERE c.post_id = ANY(%s)
            ORDER BY ABS(c.score) DESC, c.id DESC
            LIMIT 30
            """,
            (post_ids,),
        )
    from collections import Counter

    platform_counts = Counter(post["platform"] for post in posts)
    comment_count = sum(int(post.get("stored_comment_count") or 0) for post in posts)
    summary = summarize_sentiments(sentiments)
    narrative_counts = {item["tag"]: item["count"] for item in summary["top_narrative_tags"]}
    top_posts = sorted(posts, key=lambda post: (post.get("stored_comment_count") or 0, post.get("interaction_count") or 0), reverse=True)[:12]
    return {
        "label": label,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "post_count": len(posts),
        "comment_count": comment_count,
        "platform_counts": dict(platform_counts),
        "sentiment_counts": summary["sentiment_counts"],
        "sentiment_percentages": summary["sentiment_percentages"],
        "dominant_sentiment": summary["dominant_sentiment"],
        "dominant_sentiment_name": summary["dominant_sentiment_name"],
        "overall_attitude": summary["overall_attitude"],
        "overall_attitude_icon": summary["overall_attitude_icon"],
        "overall_attitude_color": sentiment_profile_from_counts(summary["sentiment_counts"])["color"],
        "sentiment_sample_size": summary["sample_size"],
        "narrative_counts": narrative_counts,
        "top_narrative_tags": summary["top_narrative_tags"],
        "top_posts": [
            {
                "platform": post["platform"],
                "board_name": post["board_name"],
                "title": post["title"],
                "url": post["url"],
                "comment_count": post.get("stored_comment_count") or post.get("comment_count") or 0,
                "interaction_count": post.get("interaction_count") or 0,
            }
            for post in top_posts
        ],
        "sample_comments": comments,
    }


def _compact_sentiment_window(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": summary.get("label"),
        "window": summary.get("window", {}),
        "post_count": summary.get("post_count", 0),
        "comment_count": summary.get("comment_count", 0),
        "sentiment_sample_size": summary.get("sentiment_sample_size", 0),
        "sentiment_counts": summary.get("sentiment_counts", {}),
        "sentiment_percentages": summary.get("sentiment_percentages", {}),
        "dominant_sentiment": summary.get("dominant_sentiment", "neutral"),
        "dominant_sentiment_name": summary.get("dominant_sentiment_name", "中性/信息不足"),
        "overall_attitude": summary.get("overall_attitude", "样本不足"),
        "overall_attitude_icon": summary.get("overall_attitude_icon", "○"),
        "overall_attitude_color": summary.get("overall_attitude_color", "#64748b"),
        "platform_counts": summary.get("platform_counts", {}),
    }


def _sentiment_comparison(recent: dict[str, Any], long_term: dict[str, Any]) -> dict[str, Any]:
    recent_size = int(recent.get("sentiment_sample_size") or 0)
    long_size = int(long_term.get("sentiment_sample_size") or 0)
    if recent_size < 5 or long_size < 10:
        return {
            "label": "样本不足",
            "basis": f"近期情绪样本 {recent_size} 条，长期情绪样本 {long_size} 条；不足以判断稳定变化。",
        }
    recent_label = recent.get("dominant_sentiment")
    long_label = long_term.get("dominant_sentiment")
    if recent_label == long_label:
        return {
            "label": "近期与长期一致",
            "basis": f"近期与长期主导情绪均为 {recent.get('dominant_sentiment_name')}。",
        }
    return {
        "label": "近期情绪方向变化",
        "basis": f"长期主导情绪为 {long_term.get('dominant_sentiment_name')}，近期主导情绪转为 {recent.get('dominant_sentiment_name')}；该判断仅代表公开讨论样本。",
    }


def load_discussion_signals(start: date, end: date, limit: int = 80) -> dict[str, Any]:
    ensure_discussion_tables()
    recent = _discussion_window_summary(start, end, limit=limit, label="近期窗口", include_samples=True)
    long_start = end - timedelta(days=55)
    long_term = _discussion_window_summary(
        long_start,
        end,
        limit=max(limit * 4, 320),
        label="长期窗口（最近 8 周）",
        include_samples=False,
    )
    comparison = _sentiment_comparison(recent, long_term)
    methodology_note = "公众讨论只代表公开论坛样本的舆论/叙事/情绪信号，不能作为事实判断依据。"
    if not recent.get("post_count"):
        methodology_note = "近期公众讨论层无样本；不足以判断本周民众讨论趋势。长期窗口仅作背景参考。"
    return {
        **recent,
        "recent_sentiment": _compact_sentiment_window(recent),
        "long_term_sentiment": _compact_sentiment_window(long_term),
        "sentiment_comparison": comparison,
        "long_term_window": long_term.get("window", {}),
        "methodology_note": methodology_note,
    }
