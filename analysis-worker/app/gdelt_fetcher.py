from __future__ import annotations

from datetime import date, datetime, timezone
import asyncio
from typing import Any
from urllib.parse import urlencode

import feedparser
import httpx
from dateutil import parser


GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"
DEFAULT_SEARCH_TOPICS = [
    {
        "topic": "憲法九条/平和主義",
        "gdelt": 'Japan AND ("Article 9" OR pacifism OR "peace constitution")',
        "queries": {
            "ja": "憲法九条 平和主義 日本国憲法",
            "en": "Japan Article 9 pacifism peace constitution",
            "zh-Hans": "日本 宪法第九条 和平主义",
        },
    },
    {
        "topic": "米軍基地/沖縄",
        "gdelt": 'Japan AND (Okinawa AND ("US base" OR "military base" OR Futenma OR Henoko))',
        "queries": {
            "ja": "沖縄 米軍基地 普天間 辺野古",
            "en": "Japan Okinawa US military base Futenma Henoko",
            "zh-Hans": "日本 冲绳 美军基地 普天间 边野古",
        },
    },
    {
        "topic": "反戦運動/市民団体",
        "gdelt": 'Japan AND (antiwar OR "peace movement" OR "peace activist" OR protest)',
        "queries": {
            "ja": "反戦運動 平和運動 市民団体 デモ",
            "en": "Japan antiwar peace movement protest activist",
            "zh-Hans": "日本 反战运动 和平运动 市民团体",
        },
    },
    {
        "topic": "自衛隊/軍事政策",
        "gdelt": 'Japan AND ("Self-Defense Forces" OR "defense policy" OR "military spending")',
        "queries": {
            "ja": "自衛隊 防衛政策 軍事費 防衛費",
            "en": "Japan Self-Defense Forces defense policy military spending",
            "zh-Hans": "日本 自卫队 防卫政策 军费",
        },
    },
    {
        "topic": "核廃絶/被爆者",
        "gdelt": 'Japan AND (nuclear AND (abolition OR disarmament OR hibakusha))',
        "queries": {
            "ja": "核廃絶 被爆者 核兵器 核軍縮",
            "en": "Japan nuclear abolition disarmament hibakusha",
            "zh-Hans": "日本 废除核武器 核裁军 被爆者",
        },
    },
    {
        "topic": "日米同盟/安保",
        "gdelt": 'Japan AND ("US-Japan alliance" OR "security treaty" OR "Article 5")',
        "queries": {
            "ja": "日米同盟 安保条約 日米安保",
            "en": "Japan US-Japan alliance security treaty Article 5",
            "zh-Hans": "日本 日美同盟 安保条约",
        },
    },
    {
        "topic": "近隣外交/東アジア",
        "gdelt": 'Japan AND ("East Asia" AND (diplomacy OR tension OR "territorial dispute"))',
        "queries": {
            "ja": "東アジア 外交 緊張 領土問題",
            "en": "Japan East Asia diplomacy tension territorial dispute",
            "zh-Hans": "日本 东亚 外交 紧张 领土争端",
        },
    },
    {
        "topic": "平和教育/歴史認識",
        "gdelt": 'Japan AND ("peace education" OR "history textbook" OR "war memory")',
        "queries": {
            "ja": "平和教育 歴史教科書 戦争の記憶",
            "en": "Japan peace education history textbook war memory",
            "zh-Hans": "日本 和平教育 历史教科书 战争记忆",
        },
    },
    {
        "topic": "国際報道/メディア",
        "gdelt": 'Japan AND (Reuters OR AP OR Bloomberg OR "Financial Times" OR BBC)',
        "queries": {
            "ja": "日本 海外メディア Reuters Bloomberg",
            "en": "Japan Reuters AP Bloomberg Financial Times BBC",
            "zh-Hans": "日本 国际媒体 路透 彭博 BBC",
        },
    },
]
DEFAULT_QUERIES = [topic["gdelt"] for topic in DEFAULT_SEARCH_TOPICS]
NEWS_SEARCH_LOCALES = [
    {"language": "日文", "code": "ja", "hl": "ja", "gl": "JP", "ceid": "JP:ja"},
    {"language": "英文", "code": "en", "hl": "en-US", "gl": "US", "ceid": "US:en"},
    {"language": "简体中文", "code": "zh-Hans", "hl": "zh-CN", "gl": "US", "ceid": "US:zh-Hans"},
]
DEFAULT_RSS_FEEDS = [
    {"name": "朝日新聞", "url": "https://www.asahi.com/rss/asahi/newsheadlines.rdf", "source_type": "major_newspaper"},
    {"name": "毎日新聞", "url": "https://mainichi.jp/rss/etc/mainichi-flash.rss", "source_type": "major_newspaper"},
    {"name": "NHK", "url": "https://www.nhk.or.jp/rss/news/cat0.xml", "source_type": "public_broadcaster"},
    {"name": "沖縄タイムス", "url": "https://www.okinawatimes.co.jp/list/feed.xml", "source_type": "local_media"},
]
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"


def get_gdelt_queries(queries: list[str] | None = None) -> list[str]:
    selected = [query.strip() for query in queries or [] if query and query.strip()]
    return selected or DEFAULT_QUERIES


def get_news_search_queries(queries: list[str] | None = None) -> list[str]:
    return [task["query"] for task in get_news_search_tasks(queries)]


def get_news_search_tasks(queries: list[str] | None = None) -> list[dict[str, str]]:
    selected = [query.strip() for query in queries or [] if query and query.strip()]
    tasks: list[dict[str, str]] = []
    for locale in NEWS_SEARCH_LOCALES:
        locale_queries = selected or [topic["queries"][locale["code"]] for topic in DEFAULT_SEARCH_TOPICS]
        for query in locale_queries:
            tasks.append({**locale, "query": query})
    return tasks


def _dt_start(day: date) -> str:
    return datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).strftime("%Y%m%d%H%M%S")


def _dt_end(day: date) -> str:
    return datetime.combine(day, datetime.max.time(), tzinfo=timezone.utc).strftime("%Y%m%d%H%M%S")


def _normalize_item(item: dict[str, Any], query: str) -> dict[str, Any] | None:
    url = item.get("url")
    title = item.get("title")
    if not url or not title:
        return None
    source_name = item.get("domain") or item.get("sourcecountry") or "GDELT"
    raw_text = " ".join(
        str(value)
        for value in [title, item.get("description"), item.get("snippet")]
        if value
    )
    published_at = _parse_gdelt_date(item.get("seendate"))
    return {
        "title": title,
        "url": url,
        "source_name": source_name,
        "source_type": "international_media",
        "published_at": published_at,
        "raw_text": raw_text or title,
        "language": item.get("language"),
        "gdelt_query": query,
        "source_country": item.get("sourcecountry"),
    }


def _parse_gdelt_date(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)
    try:
        if text.isdigit() and len(text) == 14:
            return datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).isoformat()
        return parser.parse(text).isoformat()
    except Exception:
        return None


def _date_in_range(value: str | None, start_date: date, end_date: date) -> bool:
    if not value:
        return True
    try:
        parsed = parser.parse(value)
        return start_date <= parsed.date() <= end_date
    except Exception:
        return True


def is_relevant_japan_affairs(title: str, text: str = "") -> bool:
    haystack = f"{title}\n{text}".lower()
    japan_markers = [
        "japan",
        "japanese",
        "日本",
        "にほん",
        "nippon",
        "okinawa",
        "沖縄",
    ]
    peace_markers = [
        "antiwar",
        "反戦",
        "pacifism",
        "pacifist",
        "平和",
        "peace",
        "article 9",
        "九条",
        "憲法",
        "constitution",
        "military",
        "defense",
        "自衛隊",
        "sdf",
        "基地",
        "base",
        "futenma",
        "henoko",
        "普天間",
        "辺野古",
        "nuclear",
        "核",
        "hibakusha",
        "被爆",
        "disarmament",
        "廃絶",
        "us-japan",
        "日米",
        "安保",
        "security treaty",
        "alliance",
        "protest",
        "デモ",
        "activist",
        "運動",
        "vetnam",
        "iraq",
        "war",
    ]
    noisy_markers = ["movie", "film", "trailer", "box office", "celebrity", "horoscope", "sports betting"]
    if any(marker in haystack for marker in noisy_markers) and not any(marker in haystack for marker in peace_markers):
        return False
    return any(marker in haystack for marker in japan_markers) and any(
        marker in haystack for marker in peace_markers
    )


async def fetch_gdelt_articles(
    start_date: date,
    end_date: date,
    queries: list[str] | None = None,
    max_records_per_query: int = 25,
) -> list[dict[str, Any]]:
    articles, _ = await fetch_gdelt_articles_detailed(
        start_date=start_date,
        end_date=end_date,
        queries=queries,
        max_records_per_query=max_records_per_query,
    )
    return articles


async def fetch_gdelt_articles_detailed(
    start_date: date,
    end_date: date,
    queries: list[str] | None = None,
    max_records_per_query: int = 25,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch public news article metadata from GDELT DOC 2.0.

    GDELT is an enrichment source. A failed query returns no rows and does not
    affect ingestion, analysis, or report generation.
    """
    selected_queries = get_gdelt_queries(queries)
    results: dict[str, dict[str, Any]] = {}
    diagnostics: list[dict[str, Any]] = []
    for index, query in enumerate(selected_queries):
        if index:
            await asyncio.sleep(5.5)
        batch, diagnostic = await fetch_gdelt_query(query, start_date, end_date, max_records_per_query)
        diagnostics.append(diagnostic)
        for article in batch:
            results.setdefault(article["url"], article)
    return list(results.values()), diagnostics


async def fetch_gdelt_query(
    query: str,
    start_date: date,
    end_date: date,
    max_records: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": max_records,
        "sort": "datedesc",
        "startdatetime": _dt_start(start_date),
        "enddatetime": _dt_end(end_date),
    }
    diagnostic: dict[str, Any] = {"query": query, "ok": False, "status_code": None, "article_count": 0}
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            response = await client.get(f"{GDELT_DOC_API}?{urlencode(params)}")
            diagnostic["status_code"] = response.status_code
            if response.status_code == 429:
                diagnostic["message"] = "GDELT public API rate limit hit; retried once after waiting."
                await asyncio.sleep(6.0)
                response = await client.get(f"{GDELT_DOC_API}?{urlencode(params)}")
                diagnostic["retry_status_code"] = response.status_code
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        diagnostic["message"] = str(exc)
        return [], diagnostic
    articles = []
    for item in data.get("articles", []):
        normalized = _normalize_item(item, query)
        if normalized and is_relevant_japan_affairs(normalized["title"], normalized.get("raw_text", "")):
            articles.append(normalized)
    diagnostic["ok"] = True
    diagnostic["article_count"] = len(articles)
    if not articles:
        diagnostic["message"] = "GDELT returned no articles for this date range and query."
    return articles, diagnostic


async def fetch_google_news_query(
    query: str,
    start_date: date,
    end_date: date,
    max_records: int,
    locale: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    locale = locale or NEWS_SEARCH_LOCALES[0]
    params = {
        "q": query,
        "hl": locale["hl"],
        "gl": locale["gl"],
        "ceid": locale["ceid"],
    }
    diagnostic: dict[str, Any] = {
        "source": "google_news_rss",
        "query": query,
        "language": locale["language"],
        "language_code": locale["code"],
        "ok": False,
        "status_code": None,
        "article_count": 0,
    }
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            response = await client.get(
                f"{GOOGLE_NEWS_RSS}?{urlencode(params)}",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            diagnostic["status_code"] = response.status_code
            response.raise_for_status()
    except Exception as exc:
        diagnostic["message"] = str(exc)
        return [], diagnostic

    parsed = feedparser.parse(response.content)
    articles: list[dict[str, Any]] = []
    for entry in parsed.entries:
        published_at = _parse_gdelt_date(entry.get("published") or entry.get("updated"))
        if not _date_in_range(published_at, start_date, end_date):
            continue
        title = str(entry.get("title") or "").strip()
        url = str(entry.get("link") or "").strip()
        if not title or not url:
            continue
        summary = str(entry.get("summary") or "")
        if not is_relevant_japan_affairs(title, summary):
            continue
        source = entry.get("source") or {}
        source_name = source.get("title") if isinstance(source, dict) else None
        articles.append(
            {
                "title": title,
                "url": url,
                "source_name": source_name or "Google News",
                "source_type": "international_media",
                "published_at": published_at,
                "raw_text": " ".join(str(v) for v in [title, summary] if v),
                "language": locale["code"],
                "gdelt_query": f"google_news:{locale['code']}:{query}",
                "source_country": None,
            }
        )
        if len(articles) >= max_records:
            break
    diagnostic["ok"] = True
    diagnostic["article_count"] = len(articles)
    if not articles:
        diagnostic["message"] = "Google News RSS returned no articles for this date range and query."
    return articles, diagnostic


async def fetch_rss_feed(
    feed: dict[str, str],
    start_date: date,
    end_date: date,
    max_records: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    diagnostic: dict[str, Any] = {
        "source": "rss",
        "feed_name": feed["name"],
        "url": feed["url"],
        "ok": False,
        "status_code": None,
        "article_count": 0,
    }
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            response = await client.get(feed["url"], headers={"User-Agent": "Mozilla/5.0"})
            diagnostic["status_code"] = response.status_code
            response.raise_for_status()
    except Exception as exc:
        diagnostic["message"] = str(exc)
        return [], diagnostic

    parsed = feedparser.parse(response.content)
    articles: list[dict[str, Any]] = []
    for entry in parsed.entries:
        published_at = _parse_gdelt_date(entry.get("published") or entry.get("updated"))
        if not _date_in_range(published_at, start_date, end_date):
            continue
        title = str(entry.get("title") or "").strip()
        url = str(entry.get("link") or "").strip()
        summary = str(entry.get("summary") or "")
        if not title or not url or not is_relevant_japan_affairs(title, summary):
            continue
        articles.append(
            {
                "title": title,
                "url": url,
                "source_name": feed["name"],
                "source_type": feed.get("source_type", "local_media"),
                "published_at": published_at,
                "raw_text": " ".join(str(v) for v in [title, summary] if v),
                "language": feed.get("language"),
                "gdelt_query": f"rss:{feed['name']}",
                "source_country": None,
            }
        )
        if len(articles) >= max_records:
            break
    diagnostic["ok"] = True
    diagnostic["article_count"] = len(articles)
    if not articles:
        diagnostic["message"] = "RSS feed returned no relevant articles for this date range."
    return articles, diagnostic
