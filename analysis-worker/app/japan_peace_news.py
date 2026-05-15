from __future__ import annotations

import asyncio
import hashlib
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import feedparser
import httpx
from dateutil import parser as date_parser

from .clean import clean_article_text, detect_language
from .db import fetch_all, get_conn

JAPAN_PEACE_RSS_FEEDS: list[dict[str, str]] = [
    {"name": "NHK政治", "url": "https://www.nhk.or.jp/rss/news/cat6.xml", "source_type": "media", "language": "ja"},
    {"name": "朝日新聞政治", "url": "https://www.asahi.com/rss/politics.rdf", "source_type": "media", "language": "ja"},
    {"name": "毎日新聞政治", "url": "https://mainichi.jp/rss/etc/politics.rss", "source_type": "media", "language": "ja"},
    {"name": "沖縄タイムス", "url": "https://www.okinawatimes.co.jp/rss/news.xml", "source_type": "media", "language": "ja"},
    {"name": "琉球新報", "url": "https://ryukyushimpo.jp/rss/news_index.xml", "source_type": "media", "language": "ja"},
    {"name": "Japan Times Politics", "url": "https://www.japantimes.co.jp/feed/tag/politics", "source_type": "media", "language": "en"},
    {"name": "Kyodo News", "url": "https://english.kyodonews.net/rss/news.xml", "source_type": "news_agency", "language": "en"},
    {"name": "Peace Boat News", "url": "https://peaceboat.org/feed/", "source_type": "citizen_group", "language": "ja"},
]

JAPAN_PEACE_GDELT_QUERY = (
    'Japan AND (peace OR anti-war OR antiwar OR "Article 9" OR Okinawa OR '
    '"US base" OR "military base" OR Futenma OR Henoko OR hibakusha OR '
    '"nuclear abolition" OR "nuclear weapons" OR disarmament OR '
    '"constitutional revision" OR "defense budget" OR SDF OR '
    '"Self-Defense Forces" OR "peace movement" OR "citizen group" OR '
    '"US-Japan alliance" OR "security treaty")'
)

JAPAN_PEACE_SEARCH_TOPICS: list[dict[str, Any]] = [
    {
        "topic": "憲法九条/平和主義",
        "gdelt": 'Japan AND ("Article 9" OR "peace constitution" OR "constitutional revision" OR pacifism OR "constitutional amendment")',
        "queries": {
            "ja": "憲法九条 OR 平和憲法 OR 改憲 OR 護憲 OR 憲法改正",
            "en": "Japan Article 9 peace constitution constitutional revision",
        },
    },
    {
        "topic": "沖縄/米軍基地",
        "gdelt": 'Japan AND (Okinawa OR "US base" OR "military base" OR Futenma OR Henoko OR Kadena)',
        "queries": {
            "ja": "沖縄 米軍基地 OR 普天間 OR 辺野古 OR 基地負担",
            "en": "Okinawa US military base Futenma Henoko",
        },
    },
    {
        "topic": "反戦運動/市民団体",
        "gdelt": 'Japan AND (anti-war OR antiwar OR "peace movement" OR "citizen group" OR "peace march")',
        "queries": {
            "ja": "反戦運動 OR 平和デモ OR 市民団体 OR 平和行進 OR 戦争反対",
            "en": "Japan anti-war peace movement citizen protest",
        },
    },
    {
        "topic": "自衛隊/防衛政策",
        "gdelt": 'Japan AND ("Self-Defense Forces" OR SDF OR "defense budget" OR "defense policy" OR "counterstrike capability")',
        "queries": {
            "ja": "自衛隊 OR 防衛費 OR 防衛増税 OR 敵基地攻撃能力 OR 安保三文書",
            "en": "Japan defense budget SDF military policy",
        },
    },
    {
        "topic": "核兵器廃絶/被爆",
        "gdelt": 'Japan AND (hibakusha OR Hiroshima OR Nagasaki OR "nuclear weapons" OR "nuclear abolition" OR TPNW OR NPT)',
        "queries": {
            "ja": "核兵器廃絶 OR 被爆者 OR 広島長崎 OR 核禁止条約 OR 非核三原則",
            "en": "Japan hibakusha nuclear abolition Hiroshima Nagasaki TPNW",
        },
    },
    {
        "topic": "日米同盟/安全保障",
        "gdelt": 'Japan AND ("US-Japan alliance" OR SOFA OR "security treaty" OR "defense guidelines")',
        "queries": {
            "ja": "日米同盟 OR 日米安保 OR 地位協定 OR 在日米軍",
            "en": "US Japan alliance security treaty SOFA",
        },
    },
    {
        "topic": "東アジア外交",
        "gdelt": 'Japan AND ("Japan-China" OR "Japan-Korea" OR Senkaku OR "Taiwan Strait" OR "North Korea")',
        "queries": {
            "ja": "日中関係 OR 日韓関係 OR 台湾海峡 OR 尖閣 OR 北朝鮮",
            "en": "Japan China relations Korea Taiwan Strait Senkaku North Korea",
        },
    },
    {
        "topic": "平和教育/歴史認識",
        "gdelt": 'Japan AND ("peace education" OR "historical recognition" OR Yasukuni OR "comfort women" OR "war responsibility")',
        "queries": {
            "ja": "平和教育 OR 歴史認識 OR 靖国 OR 従軍慰安婦 OR 戦争責任",
            "en": "Japan peace education history Yasukuni comfort women",
        },
    },
]

JAPAN_PEACE_NEWS_SEARCH_LOCALES = [
    {"language": "日文", "code": "ja", "hl": "ja", "gl": "JP", "ceid": "JP:ja"},
    {"language": "英文", "code": "en", "hl": "en-US", "gl": "US", "ceid": "US:en"},
]

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"


def _dt_start(day: date) -> str:
    return datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).strftime("%Y%m%d%H%M%S")


def _dt_end(day: date) -> str:
    return datetime.combine(day, datetime.max.time(), tzinfo=timezone.utc).strftime("%Y%m%d%H%M%S")


def _parse_date(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)
    try:
        if text.isdigit() and len(text) == 14:
            return datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).isoformat()
        return date_parser.parse(text).isoformat()
    except Exception:
        return None


def _date_in_range(value: str | None, start_date: date, end_date: date) -> bool:
    if not value:
        return True
    try:
        parsed = date_parser.parse(value)
        return start_date <= parsed.date() <= end_date
    except Exception:
        return True


def is_relevant_japan_peace(title: str, text: str = "") -> bool:
    """Filter for Japan anti-war / peace content. Must mention Japan and a peace/anti-war marker."""
    haystack = f"{title}\n{text}".lower()

    japan_markers = [
        "japan", "日本の", "日本", "にっぽん", "にほん",
        "tokyo", "東京", "okinawa", "沖縄", "hiroshima", "広島",
        "nagasaki", "長崎", "osaka", "大阪",
    ]

    peace_markers = [
        "peace", "anti-war", "antiwar", "平和", "反戦",
        "constitution", "憲法", "article 9", "九条", "九條",
        "military base", "基地", "futenma", "普天間", "henoko", "辺野古",
        "nuclear", "核", "hibakusha", "被爆", "disarmament", "軍縮",
        "defense", "防衛", "security", "安保", "alliance", "同盟",
        "sofa", "地位協定", "sdf", "自衛隊",
        "comfort women", "慰安婦", "yasukuni", "靖国",
        "revision", "改正", "改憲",
        "senkaku", "尖閣", "taiwan strait", "台湾海峡",
        "north korea", "北朝鮮", "missile", "ミサイル",
        "protest", "抗議", "demonstration", "デモ",
        "humanitarian", "人道", "refugee", "難民",
        "pacifism", "pacifist",
        "arms", "武器", "arms trade", "武器貿易",
    ]

    noise_markers = [
        "movie", "film", "trailer", "box office",
        "celebrity", "horoscope", "sports", "game", "anime",
    ]

    is_japan = any(marker in haystack for marker in japan_markers)
    is_peace = any(marker in haystack for marker in peace_markers)
    is_noise = any(marker in haystack for marker in noise_markers) and not is_peace
    japan_specific_peace = any(
        marker in haystack
        for marker in [
            "憲法九条",
            "憲法9条",
            "九条改正",
            "九條",
            "戦争反対",
            "反戦運動",
            "平和憲法",
            "護憲",
        ]
    )

    if is_noise:
        return False
    if japan_specific_peace:
        return True
    if not is_japan or not is_peace:
        return False

    return True


def _normalize_gdelt_item(item: dict[str, Any], query: str) -> dict[str, Any] | None:
    url = item.get("url")
    title = item.get("title")
    if not url or not title:
        return None
    source_name = item.get("domain") or item.get("sourcecountry") or "GDELT"
    raw_text = " ".join(str(v) for v in [title, item.get("description"), item.get("snippet")] if v)
    published_at = _parse_date(item.get("seendate"))
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


async def fetch_japan_gdelt_query(
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
                diagnostic["message"] = "GDELT rate limited; retrying after wait."
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
        normalized = _normalize_gdelt_item(item, query)
        if normalized and is_relevant_japan_peace(normalized["title"], normalized.get("raw_text", "")):
            articles.append(normalized)

    diagnostic["ok"] = True
    diagnostic["article_count"] = len(articles)
    if not articles:
        diagnostic["message"] = "GDELT returned no relevant Japan peace articles."
    return articles, diagnostic


async def fetch_japan_google_news_query(
    query: str,
    start_date: date,
    end_date: date,
    max_records: int,
    locale: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
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
        published_at = _parse_date(entry.get("published") or entry.get("updated"))
        if not _date_in_range(published_at, start_date, end_date):
            continue
        title = str(entry.get("title") or "").strip()
        url = str(entry.get("link") or "").strip()
        if not title or not url:
            continue
        summary = str(entry.get("summary") or "")
        if not is_relevant_japan_peace(title, summary):
            continue
        source = entry.get("source") or {}
        source_name = source.get("title") if isinstance(source, dict) else None
        articles.append({
            "title": title,
            "url": url,
            "source_name": source_name or "Google News",
            "source_type": "international_media",
            "published_at": published_at,
            "raw_text": " ".join(str(v) for v in [title, summary] if v),
            "language": locale["code"],
            "gdelt_query": f"google_news:{locale['code']}:{query}",
            "source_country": None,
        })
        if len(articles) >= max_records:
            break

    diagnostic["ok"] = True
    diagnostic["article_count"] = len(articles)
    if not articles:
        diagnostic["message"] = "Google News RSS returned no relevant Japan peace articles."
    return articles, diagnostic


async def fetch_japan_rss_feed(
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
        published_at = _parse_date(entry.get("published") or entry.get("updated"))
        if not _date_in_range(published_at, start_date, end_date):
            continue
        title = str(entry.get("title") or "").strip()
        url = str(entry.get("link") or "").strip()
        summary = str(entry.get("summary") or "")
        if not title or not url or not is_relevant_japan_peace(title, summary):
            continue
        articles.append({
            "title": title,
            "url": url,
            "source_name": feed["name"],
            "source_type": feed.get("source_type", "media"),
            "published_at": published_at,
            "raw_text": " ".join(str(v) for v in [title, summary] if v),
            "language": feed.get("language"),
            "gdelt_query": f"rss:{feed['name']}",
            "source_country": None,
        })
        if len(articles) >= max_records:
            break

    diagnostic["ok"] = True
    diagnostic["article_count"] = len(articles)
    if not articles:
        diagnostic["message"] = "RSS feed returned no relevant Japan peace articles."
    return articles, diagnostic


async def fetch_japan_peace_news(
    start_date: date,
    end_date: date,
    *,
    queries: list[str] | None = None,
    max_records_per_query: int = 25,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch Japan anti-war/peace news from GDELT, Google News, and RSS feeds.

    Returns (articles, diagnostics).
    """
    selected_queries = queries or [topic["gdelt"] for topic in JAPAN_PEACE_SEARCH_TOPICS]
    results: dict[str, dict[str, Any]] = {}
    diagnostics: list[dict[str, Any]] = []

    # Layer 1: GDELT
    for index, query in enumerate(selected_queries):
        if index:
            await asyncio.sleep(5.5)
        batch, diagnostic = await fetch_japan_gdelt_query(query, start_date, end_date, max_records_per_query)
        diagnostics.append(diagnostic)
        for article in batch:
            results.setdefault(str(article["url"]), article)

    # Layer 2: Google News RSS
    news_semaphore = asyncio.Semaphore(6)
    news_tasks = [
        {"query": topic["queries"][locale["code"]], **locale}
        for topic in JAPAN_PEACE_SEARCH_TOPICS
        for locale in JAPAN_PEACE_NEWS_SEARCH_LOCALES
    ]

    async def run_news(task: dict[str, str]):
        async with news_semaphore:
            return await fetch_japan_google_news_query(
                task["query"], start_date, end_date, max_records_per_query, task,
            )

    news_jobs = [run_news(task) for task in news_tasks]
    for job in asyncio.as_completed(news_jobs):
        batch, diagnostic = await job
        diagnostics.append(diagnostic)
        for article in batch:
            results.setdefault(str(article["url"]), article)

    # Layer 3: RSS feeds
    rss_semaphore = asyncio.Semaphore(6)

    async def run_rss(feed: dict[str, str]):
        async with rss_semaphore:
            return await fetch_japan_rss_feed(feed, start_date, end_date, max_records_per_query)

    rss_jobs = [run_rss(feed) for feed in JAPAN_PEACE_RSS_FEEDS]
    for job in asyncio.as_completed(rss_jobs):
        batch, diagnostic = await job
        diagnostics.append(diagnostic)
        for article in batch:
            results.setdefault(str(article["url"]), article)

    return list(results.values()), diagnostics


def get_japan_peace_gdelt_queries(extra: list[str] | None = None) -> list[str]:
    return [topic["gdelt"] for topic in JAPAN_PEACE_SEARCH_TOPICS] + (extra or [])


def get_japan_peace_news_search_tasks(extra_queries: list[str] | None = None) -> list[dict[str, str]]:
    tasks: list[dict[str, str]] = []
    for locale in JAPAN_PEACE_NEWS_SEARCH_LOCALES:
        for topic in JAPAN_PEACE_SEARCH_TOPICS:
            tasks.append({**locale, "query": topic["queries"][locale["code"]]})
    for extra in (extra_queries or []):
        for locale in JAPAN_PEACE_NEWS_SEARCH_LOCALES:
            tasks.append({**locale, "query": extra})
    return tasks


def _fingerprint(title: str, text: str) -> str:
    return hashlib.sha256(f"{title}\n{text}".encode("utf-8")).hexdigest()


def _store_japan_news(articles: list[dict[str, Any]]) -> dict[str, int]:
    inserted = 0
    skipped = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for article in articles:
                title = str(article.get("title") or "").strip()
                url = str(article.get("url") or "").strip()
                if not title or not url:
                    skipped += 1
                    continue
                raw_text = str(article.get("raw_text") or title)
                clean_text = clean_article_text(title, raw_text)
                content_hash = _fingerprint(title, clean_text)
                published_at = article.get("published_at")
                fetched_at = datetime.now(timezone.utc)
                cur.execute("SELECT id FROM japan_news WHERE url = %s OR content_hash = %s LIMIT 1", (url, content_hash))
                if cur.fetchone():
                    skipped += 1
                    continue
                cur.execute(
                    """
                    INSERT INTO japan_news
                      (title, url, source_name, source_type, published_at, fetched_at, language,
                       raw_text, clean_text, content_hash, category, relevance_score, antiwar_relevance)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        title,
                        url,
                        article.get("source_name") or "unknown",
                        article.get("source_type") or "japan_media",
                        published_at,
                        fetched_at,
                        article.get("language") or detect_language(clean_text),
                        raw_text,
                        clean_text,
                        content_hash,
                        "antiwar" if is_relevant_japan_peace(title, clean_text) else "general",
                        0.8 if is_relevant_japan_peace(title, clean_text) else 0.2,
                        is_relevant_japan_peace(title, clean_text),
                    ),
                )
                inserted += 1
    return {"inserted": inserted, "skipped_duplicates": skipped}


async def fetch_japan_news_stream(
    start_date: date,
    end_date: date,
    keywords: list[str] | None = None,
    categories: list[str] | None = None,
    max_records: int = 50,
    ingest: bool = True,
):
    yield {"type": "stage", "stage": "fetch", "message": "公開ニュース検索を開始します。"}
    articles, diagnostics = await fetch_japan_peace_news(
        start_date=start_date,
        end_date=end_date,
        queries=keywords,
        max_records_per_query=max_records,
    )
    for article in articles[:100]:
        yield {
            "type": "article",
            "title": article.get("title"),
            "url": article.get("url"),
            "source_name": article.get("source_name"),
            "language": article.get("language"),
        }
    inserted = 0
    skipped = 0
    if ingest and articles:
        stored = _store_japan_news(articles)
        inserted = stored["inserted"]
        skipped = stored["skipped_duplicates"]
    yield {
        "type": "done",
        "ok": True,
        "fetched": len(articles),
        "inserted": inserted,
        "skipped_duplicates": skipped,
        "diagnostics": diagnostics,
    }


def list_japan_news(
    start_date: date | None = None,
    end_date: date | None = None,
    keyword: str | None = None,
    category: str | None = None,
    antiwar_only: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    clauses = ["1=1"]
    params: list[Any] = []
    if start_date:
        clauses.append("COALESCE(published_at, fetched_at) >= %s")
        params.append(start_date)
    if end_date:
        clauses.append("COALESCE(published_at, fetched_at) < %s")
        params.append(end_date + timedelta(days=1))
    if keyword:
        clauses.append("(title ILIKE %s OR clean_text ILIKE %s)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])
    if category:
        clauses.append("category = %s")
        params.append(category)
    if antiwar_only:
        clauses.append("antiwar_relevance = TRUE")
    params.append(limit)
    return fetch_all(
        f"""
        SELECT *
        FROM japan_news
        WHERE {' AND '.join(clauses)}
        ORDER BY COALESCE(published_at, fetched_at) DESC
        LIMIT %s
        """,
        tuple(params),
    )
