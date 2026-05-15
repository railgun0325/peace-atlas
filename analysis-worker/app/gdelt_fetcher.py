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
        "topic": "总统府/行政院",
        "gdelt": 'Taiwan AND ("President Lai" OR "Executive Yuan" OR cabinet OR government)',
        "queries": {
            "zh-Hant": "賴清德 總統府 行政院",
            "zh-Hans": "赖清德 总统府 行政院 台湾",
            "ja": "台湾 頼清徳 行政院",
            "en": "Taiwan President Lai Executive Yuan",
        },
    },
    {
        "topic": "立法院",
        "gdelt": 'Taiwan AND ("Legislative Yuan" OR legislature OR parliament OR budget)',
        "queries": {
            "zh-Hant": "台灣 立法院 預算 法案",
            "zh-Hans": "台湾 立法院 预算 法案",
            "ja": "台湾 立法院 予算",
            "en": "Taiwan legislature budget bill",
        },
    },
    {
        "topic": "两岸/台海",
        "gdelt": 'Taiwan AND ("Taiwan Strait" OR "cross-strait" OR China OR Beijing)',
        "queries": {
            "zh-Hant": "台海 兩岸 陸委會",
            "zh-Hans": "台海 两岸 陆委会",
            "ja": "台湾海峡 中台関係",
            "en": "Taiwan Strait cross-strait China",
        },
    },
    {
        "topic": "国防/军事",
        "gdelt": 'Taiwan AND (defense OR military OR PLA OR "Ministry of National Defense")',
        "queries": {
            "zh-Hant": "台灣 國防 軍事 國防部",
            "zh-Hans": "台湾 国防 军事 国防部",
            "ja": "台湾 防衛 軍事",
            "en": "Taiwan defense military PLA",
        },
    },
    {
        "topic": "外交/国际",
        "gdelt": 'Taiwan AND (diplomacy OR "United States" OR Japan OR "Ministry of Foreign Affairs")',
        "queries": {
            "zh-Hant": "台灣 外交 美國 日本 外交部",
            "zh-Hans": "台湾 外交 美国 日本 外交部",
            "ja": "台湾 外交 米国 日本",
            "en": "Taiwan diplomacy United States Japan",
        },
    },
    {
        "topic": "政党攻防",
        "gdelt": 'Taiwan AND (DPP OR KMT OR Kuomintang OR "Taiwan People\'s Party")',
        "queries": {
            "zh-Hant": "民進黨 國民黨 民眾黨",
            "zh-Hans": "民进党 国民党 民众党 台湾",
            "ja": "台湾 民進党 国民党 民衆党",
            "en": "Taiwan DPP KMT Taiwan People's Party",
        },
    },
    {
        "topic": "选举/民调",
        "gdelt": 'Taiwan AND (election OR poll OR polling OR recall)',
        "queries": {
            "zh-Hant": "台灣 選舉 民調 罷免",
            "zh-Hans": "台湾 选举 民调 罢免",
            "ja": "台湾 選挙 世論調査",
            "en": "Taiwan election poll recall",
        },
    },
    {
        "topic": "司法/社会",
        "gdelt": 'Taiwan AND (judicial OR prosecution OR court OR protest OR corruption)',
        "queries": {
            "zh-Hant": "台灣 司法 檢調 社會",
            "zh-Hans": "台湾 司法 检调 社会",
            "ja": "台湾 司法 社会 抗議",
            "en": "Taiwan judicial prosecution protest",
        },
    },
    {
        "topic": "经济/产业",
        "gdelt": 'Taiwan AND (TSMC OR semiconductor OR economy OR trade OR industry)',
        "queries": {
            "zh-Hant": "台灣 經濟 產業 半導體",
            "zh-Hans": "台湾 经济 产业 半导体",
            "ja": "台湾 経済 半導体",
            "en": "Taiwan economy semiconductor industry",
        },
    },
    {
        "topic": "能源/核电",
        "gdelt": 'Taiwan AND (energy OR nuclear OR electricity OR power)',
        "queries": {
            "zh-Hant": "台灣 能源 核電 電價",
            "zh-Hans": "台湾 能源 核电 电价",
            "ja": "台湾 エネルギー 原発",
            "en": "Taiwan energy nuclear power",
        },
    },
    {
        "topic": "国际媒体视角",
        "gdelt": 'Taiwan AND (Reuters OR AP OR Bloomberg OR "Financial Times" OR BBC)',
        "queries": {
            "zh-Hant": "國際媒體 台灣 Reuters Bloomberg",
            "zh-Hans": "国际媒体 台湾 路透 彭博",
            "ja": "海外メディア 台湾 Reuters",
            "en": "Taiwan Reuters AP Bloomberg Financial Times",
        },
    },
]
DEFAULT_QUERIES = [topic["gdelt"] for topic in DEFAULT_SEARCH_TOPICS]
NEWS_SEARCH_LOCALES = [
    {"language": "繁体中文", "code": "zh-Hant", "hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"},
    {"language": "简体中文", "code": "zh-Hans", "hl": "zh-CN", "gl": "US", "ceid": "US:zh-Hans"},
    {"language": "日文", "code": "ja", "hl": "ja", "gl": "JP", "ceid": "JP:ja"},
    {"language": "英文", "code": "en", "hl": "en-US", "gl": "US", "ceid": "US:en"},
]
DEFAULT_RSS_FEEDS = [
    {"name": "中央社政治", "url": "https://feeds.feedburner.com/rsscna/politics", "source_type": "news_agency"},
    {"name": "中央社两岸", "url": "https://feeds.feedburner.com/rsscna/mainland", "source_type": "news_agency"},
    {"name": "Focus Taiwan", "url": "https://feeds.feedburner.com/rsscna/engnews", "source_type": "news_agency"},
    {"name": "自由时报政治", "url": "https://news.ltn.com.tw/rss/politics.xml", "source_type": "local_media"},
    {"name": "自由时报国防", "url": "https://news.ltn.com.tw/rss/def.xml", "source_type": "local_media"},
    {"name": "Taipei Times", "url": "https://www.taipeitimes.com/xml/index.rss", "source_type": "international_media"},
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


def is_relevant_taiwan_public_affairs(title: str, text: str = "") -> bool:
    haystack = f"{title}\n{text}".lower()
    taiwan_markers = [
        "taiwan",
        "taiwan strait",
        "cross-strait",
        "台灣",
        "臺灣",
        "台湾",
        "台海",
        "兩岸",
        "两岸",
        "台湾海峡",
    ]
    public_affairs_markers = [
        "politic",
        "government",
        "legislature",
        "legislative yuan",
        "defense",
        "military",
        "diplomacy",
        "election",
        "poll",
        "dpp",
        "kmt",
        "kuomintang",
        "president lai",
        "china",
        "pla",
        "行政院",
        "總統府",
        "总统府",
        "立法院",
        "國防",
        "国防",
        "外交",
        "選舉",
        "选举",
        "民調",
        "民调",
        "民進黨",
        "民进党",
        "國民黨",
        "国民党",
        "民眾黨",
        "民众党",
        "陸委會",
        "陆委会",
        "予算",
        "防衛",
        "外交",
        "選挙",
        "政党",
    ]
    noisy_markers = ["movie", "film", "trailer", "box office", "celebrity", "horoscope", "sports betting"]
    if any(marker in haystack for marker in noisy_markers) and not any(marker in haystack for marker in public_affairs_markers):
        return False
    return any(marker in haystack for marker in taiwan_markers) and any(
        marker in haystack for marker in public_affairs_markers
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
        if normalized and is_relevant_taiwan_public_affairs(normalized["title"], normalized.get("raw_text", "")):
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
        if not is_relevant_taiwan_public_affairs(title, summary):
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
        if not title or not url or not is_relevant_taiwan_public_affairs(title, summary):
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


async def fetch_gdelt_taiwan_politics(start_date: date, end_date: date, max_records: int = 50) -> list[dict[str, Any]]:
    return await fetch_gdelt_articles(
        start_date=start_date,
        end_date=end_date,
        queries=DEFAULT_QUERIES,
        max_records_per_query=max_records,
    )
