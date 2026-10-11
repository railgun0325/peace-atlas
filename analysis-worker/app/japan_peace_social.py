from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import random
import re as _re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

import feedparser
import httpx
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from .db import fetch_all, get_conn

# ---------------------------------------------------------------------------
# Headers & helpers
# ---------------------------------------------------------------------------

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
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

MOBILE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
        "Mobile/15E148 Safari/604.1"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ja-JP,en-US;q=0.9,ja;q=0.8",
}

INSTAGRAM_APP_ID = "936619743392459"

DIAGNOSTIC_UNAVAILABLE = {
    "ok": False,
    "available": False,
    "message": "Public fetch is unavailable behind login walls or authentication requirements; this system will not bypass login, captcha, or private access controls.",
}

JAPAN_PEACE_SUBREDDITS = [
    "japanpolitics", "JapanPeace", "japan", "japannews",
    "okinawa", "japanlife", "newsokur",
]

MASTODON_INSTANCES = [
    "https://mstdn.jp",
    "https://fedibird.com",
    "https://mastodon.social",
    "https://pawoo.net",
    "https://misskey.io",
]

# Japanese peace keywords for cross-platform search
JAPAN_PEACE_KEYWORDS = [
    "憲法9条", "憲法九条", "改憲反対", "護憲", "平和憲法", "反戦", "戦争反対", "平和運動",
    "米軍基地", "沖縄基地", "沖縄", "辺野古", "普天間", "安保法制反対", "軍拡反対",
    "自衛隊海外派遣反対", "防衛費", "集団的自衛権", "敵基地攻撃", "武器輸出反対",
    "核兵器廃絶", "核廃絶", "被爆者", "広島", "長崎", "核禁止条約",
    "日米安保", "地位協定", "在日米軍",
    "平和デモ", "平和集会", "市民団体", "Peace Boat", "Article 9", "Japan peace movement",
]

DEFAULT_SOCIAL_SEARCH_KEYWORDS = [
    "反戦",
    "戦争反対",
    "平和運動",
    "憲法九条",
    "憲法9条",
    "九条の会",
    "護憲",
    "改憲反対",
    "安保法制反対",
    "軍拡反対",
    "武器輸出反対",
    "自衛隊 海外派遣 反対",
    "沖縄 米軍基地",
    "辺野古 抗議",
    "普天間 基地",
    "日米安保",
    "地位協定",
    "核兵器廃絶",
    "原水禁",
    "原水協",
    "被爆者",
    "広島 平和",
    "長崎 平和",
    "Peace Boat",
    "Article 9",
    "antiwar Japan",
    "Japan peace movement",
    "Okinawa base protest",
]

DEFAULT_KEYWORD_SAMPLE_SIZE = 8

SOCIAL_RELEVANCE_MARKERS = [
    "反戦",
    "戦争反対",
    "平和運動",
    "憲法九条",
    "憲法9条",
    "九条",
    "護憲",
    "改憲反対",
    "安保法制",
    "軍拡",
    "武器輸出",
    "沖縄",
    "辺野古",
    "普天間",
    "米軍基地",
    "核兵器",
    "核廃絶",
    "被爆",
    "広島",
    "長崎",
    "peace boat",
    "article 9",
    "okinawa base",
    "antiwar japan",
    "anti-war japan",
    "japan peace movement",
    "japanese peace movement",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_date(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)
    try:
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


def _diagnostic(platform: str, ok: bool, message: str = "", **extra: Any) -> dict[str, Any]:
    return {"platform": platform, "ok": ok, "message": message, **extra}


def _clean_search_url(url: str) -> str:
    """Normalize direct links and common search redirect wrappers."""
    text = str(url or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.netloc.endswith("bing.com") and parsed.path.startswith("/ck/"):
        qs = parse_qs(parsed.query)
        for key in ("u", "url"):
            if qs.get(key):
                candidate = qs[key][0]
                if candidate.startswith("a1"):
                    try:
                        payload = candidate[2:]
                        payload += "=" * (-len(payload) % 4)
                        return base64.urlsafe_b64decode(payload).decode("utf-8", "replace")
                    except Exception:
                        candidate = candidate[2:]
                try:
                    return unquote(candidate)
                except Exception:
                    return candidate
    return text


def _is_twitter_post_url(url: str) -> bool:
    parsed = urlparse(_clean_search_url(url))
    host = parsed.netloc.lower().removeprefix("www.")
    if host not in {"x.com", "twitter.com", "mobile.twitter.com"}:
        return False
    path = parsed.path.lower()
    blocked = ("/home", "/search", "/login", "/intent", "/share", "/hashtag", "/i/")
    return "/status/" in path and not any(item in path for item in blocked)


def _is_instagram_post_url(url: str) -> bool:
    parsed = urlparse(_clean_search_url(url))
    host = parsed.netloc.lower().removeprefix("www.")
    if host != "instagram.com":
        return False
    path = parsed.path.lower()
    return any(path.startswith(prefix) for prefix in ("/p/", "/reel/", "/tv/"))


def _is_youtube_video_url(url: str) -> bool:
    parsed = urlparse(_clean_search_url(url))
    host = parsed.netloc.lower().removeprefix("www.")
    if host == "youtu.be":
        return bool(parsed.path.strip("/"))
    if host not in {"youtube.com", "m.youtube.com"}:
        return False
    return parsed.path.lower() == "/watch" and bool(parse_qs(parsed.query).get("v"))


def _keyword_sample(keywords: list[str] | None, sample_size: int = DEFAULT_KEYWORD_SAMPLE_SIZE) -> list[str]:
    cleaned = [item.strip() for item in (keywords or []) if item and item.strip()]
    pool = cleaned or DEFAULT_SOCIAL_SEARCH_KEYWORDS
    if len(pool) <= sample_size:
        result = list(pool)
        random.shuffle(result)
        return result
    return random.sample(pool, sample_size)


def is_relevant_social_post(post: dict[str, Any], keywords: list[str] | None = None) -> bool:
    text = " ".join(
        str(post.get(key) or "")
        for key in ["title", "raw_text", "content_text", "handle", "community", "url"]
    ).lower()
    if not text.strip():
        return False
    markers = [marker.lower() for marker in SOCIAL_RELEVANCE_MARKERS]
    if any(marker in text for marker in markers):
        if "article 9" in text and not any(term in text for term in ["japan", "constitution", "憲法", "九条"]):
            return False
        return True
    for keyword in keywords or []:
        keyword_text = keyword.strip().lower()
        if len(keyword_text) >= 4 and keyword_text in text:
            return True
    return False


async def fetch_x_public_diagnostic(handle: str) -> dict[str, Any]:
    """Twitter/X is available via Nitter RSS (no login, no API key)."""
    return {
        "platform": "x",
        "handle": handle,
        "ok": True,
        "available": True,
        "message": "Twitter/X scraping available via Nitter RSS (no login, no API key, no rate limit).",
        "detail": "Uses Nitter RSS feeds + FxTwitter metadata API. Falls back to xcancel.com HTML parsing.",
    }


async def fetch_instagram_public_diagnostic(handle: str) -> dict[str, Any]:
    """Instagram private API path is disabled by default (legacy/manual-risk)."""
    return {
        "platform": "instagram",
        "handle": handle,
        "ok": False,
        "available": False,
        "message": "Instagram private API (instagrapi) is disabled by default due to account risk.",
        "detail": "Default path is low-frequency Playwright with persisted login state. Legacy private API requires IG_ALLOW_LEGACY_INSTAGRAPI=1 and is manual-risk.",
    }


# ======================================================================
# Reddit – native RSS (primary) + HTML scraping (fallback)
# ======================================================================

REDDIT_RSS_BASE = "https://www.reddit.com"


async def _fetch_reddit_rss(
    url: str, start_date: date, end_date: date, max_posts: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch Reddit native RSS/Atom feed. No auth, no API key."""
    diagnostic = _diagnostic("reddit_rss", ok=False, url=url)
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers=BROWSER_HEADERS)
            diagnostic["status_code"] = resp.status_code
            if resp.status_code in {403, 429}:
                diagnostic["message"] = "Reddit RSS blocked or rate limited."
                return [], diagnostic
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
    except Exception as exc:
        diagnostic["message"] = f"Reddit RSS fetch failed: {exc}"
        return [], diagnostic

    posts: list[dict[str, Any]] = []
    for entry in feed.entries:
        published_at = _parse_date(entry.get("published") or entry.get("updated"))
        if not _date_in_range(published_at, start_date, end_date):
            continue
        title = str(entry.get("title") or "").strip()
        link = str(entry.get("link") or "").strip()
        if not title or not link:
            continue
        author = str(entry.get("author") or "")
        summary = str(entry.get("summary") or "")
        posts.append({
            "platform": "reddit",
            "title": title,
            "url": link,
            "published_at": published_at,
            "raw_text": " ".join(v for v in [title, summary] if v),
            "author": author,
            "community": entry.get("tags", [{}])[0].get("term", "") if entry.get("tags") else "",
            "fetched_at": _now(),
        })
        if len(posts) >= max_posts:
            break

    diagnostic["ok"] = True
    diagnostic["post_count"] = len(posts)
    return posts, diagnostic


async def fetch_reddit_subreddit_posts(
    subreddit: str,
    start_date: date,
    end_date: date,
    max_posts: int = 20,
    sort: str = "new",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch posts from a subreddit via native Reddit RSS. No auth."""
    sub = subreddit.removeprefix("r/").removeprefix("/r/")
    # Use native RSS which actually works
    url = f"{REDDIT_RSS_BASE}/r/{sub}/{sort}/.rss?limit={max_posts}"
    posts, diag = await _fetch_reddit_rss(url, start_date, end_date, max_posts)
    diag["subreddit"] = sub
    if not diag["ok"]:
        # Fallback: try old.reddit.com HTML scraping
        posts, diag = await _fetch_reddit_html_fallback(sub, start_date, end_date, max_posts, sort)
    return posts, diag


async def _fetch_reddit_html_fallback(
    subreddit: str, start_date: date, end_date: date, max_posts: int, sort: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """HTML scraping fallback for Reddit."""
    diagnostic = _diagnostic("reddit_html", ok=False, subreddit=subreddit)
    url = f"https://old.reddit.com/r/{subreddit}/{sort}/"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers=BROWSER_HEADERS)
            diagnostic["status_code"] = resp.status_code
            if resp.status_code in {403, 429}:
                diagnostic["message"] = "Reddit blocked."
                return [], diagnostic
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:
        diagnostic["message"] = f"Reddit HTML fetch failed: {exc}"
        return [], diagnostic

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
        time_el = (thing.select_one(".tagline") or thing).select_one("time")
        published_at = _parse_date(time_el.get("datetime") if time_el else None)
        if not _date_in_range(published_at, start_date, end_date):
            continue
        score_el = thing.select_one(".score.unvoted")
        score_text = score_el.get("title") or score_el.get_text(strip=True) if score_el else "0"
        try:
            score = int(_re.sub(r"[^0-9]", "", score_text) or "0")
        except ValueError:
            score = 0
        if not title:
            continue
        posts.append({
            "platform": "reddit",
            "community": subreddit,
            "title": title,
            "url": href,
            "published_at": published_at,
            "raw_text": title,
            "score": score,
            "fetched_at": _now(),
        })
        if len(posts) >= max_posts:
            break

    diagnostic["ok"] = True
    diagnostic["post_count"] = len(posts)
    return posts, diagnostic


async def search_reddit_keywords(
    keywords: list[str],
    start_date: date,
    end_date: date,
    max_posts: int = 30,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Search Reddit by keywords via native search RSS. No auth."""
    query = "+".join(quote(k) for k in keywords[:5])
    url = f"{REDDIT_RSS_BASE}/r/all/search.rss?q={query}&sort=new&restrict_sr=off&limit={max_posts}"
    posts, diag = await _fetch_reddit_rss(url, start_date, end_date, max_posts)
    diag["query"] = query
    return posts, diag


# ======================================================================
# Public web-search fallback for social post URLs
# ======================================================================

async def search_social_posts_via_bing_rss(
    platform: str,
    keywords: list[str],
    start_date: date,
    end_date: date,
    max_posts: int = 20,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Find public social post URLs through Bing RSS search snippets.

    This does not bypass platform login walls. It only stores public search
    result snippets for post URLs that are already visible to a search engine.
    """
    keyword_terms = [k.strip() for k in keywords if k and k.strip()]
    if not keyword_terms:
        keyword_terms = DEFAULT_SOCIAL_SEARCH_KEYWORDS[:8]

    if platform == "twitter":
        site_queries = ["site:x.com", "site:twitter.com"]
        url_ok = _is_twitter_post_url
    elif platform == "instagram":
        site_queries = ["site:instagram.com/p", "site:instagram.com/reel"]
        url_ok = _is_instagram_post_url
    elif platform == "youtube":
        site_queries = ["site:youtube.com/watch", "site:youtu.be"]
        url_ok = _is_youtube_video_url
    else:
        return [], _diagnostic(platform, ok=False, message="No Bing RSS fallback for platform.")

    diagnostic = _diagnostic(
        f"{platform}_bing_rss",
        ok=False,
        message="Using public search snippets only; no login wall bypass.",
        keywords=keyword_terms[:8],
    )
    posts: list[dict[str, Any]] = []
    seen: set[str] = set()
    queries: list[str] = []
    for site_query in site_queries:
        for i in range(0, min(len(keyword_terms), 9), 3):
            chunk = keyword_terms[i:i + 3]
            quoted = " OR ".join(f'"{item}"' for item in chunk)
            queries.append(f"{site_query} ({quoted})")

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for query in queries:
            if len(posts) >= max_posts:
                break
            url = f"https://www.bing.com/search?q={quote(query)}&format=rss&count={min(max_posts, 50)}"
            try:
                resp = await client.get(url, headers=BROWSER_HEADERS)
                diagnostic.setdefault("statuses", []).append(resp.status_code)
                if resp.status_code in {403, 429}:
                    diagnostic["message"] = f"Bing RSS returned {resp.status_code}."
                    continue
                resp.raise_for_status()
                feed = feedparser.parse(resp.content)
            except Exception as exc:
                diagnostic.setdefault("errors", []).append(str(exc)[:200])
                continue

            for entry in feed.entries:
                link = _clean_search_url(str(entry.get("link") or ""))
                if not link or link in seen or not url_ok(link):
                    continue
                title = str(entry.get("title") or "").strip()
                summary = BeautifulSoup(str(entry.get("summary") or ""), "html.parser").get_text(" ", strip=True)
                published_at = _parse_date(entry.get("published") or entry.get("updated"))
                if not _date_in_range(published_at, start_date, end_date):
                    continue
                posts.append({
                    "platform": platform,
                    "handle": "",
                    "title": title[:120] if title else link,
                    "url": link,
                    "published_at": published_at,
                    "raw_text": " ".join(part for part in [title, summary] if part).strip() or link,
                    "fetched_at": _now(),
                    "source_method": "bing_rss_public_snippet",
                })
                seen.add(link)
                if len(posts) >= max_posts:
                    break

    diagnostic["ok"] = True
    diagnostic["post_count"] = len(posts)
    diagnostic["queries"] = queries[:6]
    if not posts and not diagnostic.get("errors"):
        diagnostic["message"] = "No public post URLs found in Bing RSS snippets."
    return posts, diagnostic


# ======================================================================
# Instagram – Instagrapi (login required, proxy supported)
# ======================================================================
# Uses instagrapi private API wrapper with login credentials.
# Instagram now requires login for ALL access (public profiles, oEmbed, GraphQL).
# Session is persisted to data/instagram_session.json to avoid re-auth rate limits.
# Proxy is read from PROXY_URL / OUTBOUND_HTTP_PROXY / HTTP_PROXY env vars.
# Rate-limit delay: 3-8 seconds between requests (cl.delay_range).

import os as _os
from pathlib import Path as _Path

INSTAGRAM_API = "https://i.instagram.com/api/v1/users/web_profile_info/"

_ig_client = None
_ig_client_lock = asyncio.Lock()
_ig_available = None  # None=unchecked, True=available, False=unavailable


async def _ensure_ig_client():
    """Get or initialize instagrapi Client singleton (async-safe).

    Runs synchronous instagrapi init in a thread pool to avoid blocking the
    asyncio event loop. Session is cached to data/instagram_session.json.
    """
    global _ig_client, _ig_available
    if _os.environ.get("IG_ALLOW_LEGACY_INSTAGRAPI", "0") != "1":
        _ig_available = False
        return None, "instagrapi disabled (legacy/manual-risk); set IG_ALLOW_LEGACY_INSTAGRAPI=1 to override"

    if _ig_available is False:
        return None, "instagrapi unavailable (previous init failed)"
    if _ig_client is not None:
        return _ig_client, None

    async with _ig_client_lock:
        if _ig_client is not None:
            return _ig_client, None
        if _ig_available is False:
            return None, "instagrapi unavailable"

        def _init_sync():
            try:
                from instagrapi import Client  # noqa: F811
            except ImportError:
                return None, "instagrapi not installed (pip install instagrapi)"

            username = _os.environ.get("INSTAGRAM_USERNAME", "")
            password = _os.environ.get("INSTAGRAM_PASSWORD", "")
            if not username or not password:
                return None, "INSTAGRAM_USERNAME / INSTAGRAM_PASSWORD not set"

            cl = Client()
            proxy = (
                _os.environ.get("PROXY_URL")
                or _os.environ.get("OUTBOUND_HTTP_PROXY")
                or _os.environ.get("HTTP_PROXY")
                or _os.environ.get("HTTPS_PROXY")
                or ""
            )
            if proxy:
                cl.set_proxy(proxy)

            cl.delay_range = [3, 8]  # Anti rate-limit

            # Session persistence
            session_file = (
                _Path(__file__).resolve().parent.parent / "data" / "instagram_session.json"
            )
            try:
                session_file.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

            # Try loading saved session first
            try:
                if session_file.exists():
                    cl.load_settings(str(session_file))
                    cl.login(username, password)
                    return cl, None
            except Exception:
                pass

            # Fresh login
            try:
                cl.login(username, password)
                cl.dump_settings(str(session_file))
                return cl, None
            except Exception as e:
                return None, f"Instagram login failed: {e}"

        cl, error = await asyncio.to_thread(_init_sync)
        if cl is not None:
            _ig_client = cl
            _ig_available = True
            return cl, None
        _ig_available = False
        return None, error


def _outgram_available() -> bool:
    """Legacy check — returns False since we use instagrapi now."""
    return False


async def fetch_instagram_profile(
    handle: str,
    start_date: date,
    end_date: date,
    max_posts: int = 20,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch Instagram profile + recent posts. Playwright first, Instagrapi fallback.

    Playwright 模拟真实浏览器 → 封号风险低，推荐。
    Instagrapi 走私有 API → 封号风险高，仅作回退。

    Requires INSTAGRAM_USERNAME/INSTAGRAM_PASSWORD in environment for Instagrapi.
    Playwright 需要首次人工登录后保存浏览器状态。
    """
    diagnostic = _diagnostic("instagram", ok=False, handle=handle)
    username = handle.strip().lstrip("@")

    # ── 方案1: Playwright 浏览器模拟（推荐，封号风险低）──
    try:
        from .ig_playwright import collect_profile_streaming
    except ImportError:
        collect_profile_streaming = None

    if collect_profile_streaming is not None:
        posts: list[dict[str, Any]] = []
        pw_ok = False
        pw_errors: list[str] = []
        pw_statuses: list[str] = []
        try:
            async for event in collect_profile_streaming(username, max_posts):
                if event["type"] == "ig_post":
                    posts.append({
                        "platform": "instagram",
                        "handle": handle,
                        "title": (event.get("caption", "") or "")[:120] or "(no caption)",
                        "url": event.get("url", ""),
                        "published_at": event.get("taken_at"),
                        "raw_text": event.get("caption", ""),
                        "engagement_count": event.get("likes", 0),
                        "fetched_at": _now(),
                        "source_method": "playwright",
                        "shortcode": event.get("shortcode"),
                        "is_video": event.get("is_video", False),
                    })
                elif event["type"] == "ig_error":
                    diagnostic["playwright_error"] = event["message"]
                    pw_errors.append(str(event.get("message", "")))
                    event_diag = event.get("diagnostic") or {}
                    if event_diag.get("status"):
                        pw_statuses.append(str(event_diag.get("status")))
                elif event["type"] == "ig_progress":
                    pw_ok = True  # Progress means browser is working

            if posts:
                diagnostic["ok"] = True
                diagnostic["post_count"] = len(posts)
                diagnostic["source"] = "playwright"
                return posts, diagnostic

            # Do not hide Playwright login/risk stops behind the legacy private API fallback.
            hard_stop_statuses = {
                "no_login_state",
                "challenge_or_rate_limited",
                "session_expired",
                "rate_guard_blocked",
                "playwright_missing",
                "collector_error",
            }
            if hard_stop_statuses.intersection(pw_statuses):
                diagnostic["message"] = diagnostic.get("playwright_error", "Instagram Playwright 采集已停止。")
                diagnostic["playwright_statuses"] = pw_statuses
                diagnostic["source"] = "playwright"
                return [], diagnostic

            # Playwright ran but got 0 posts; report the real state instead of falling through silently.
            if pw_ok:
                diagnostic["message"] = diagnostic.get("playwright_error", "Playwright 已运行，但未找到帖子链接。")
                diagnostic["ok"] = True
                diagnostic["source"] = "playwright"
                diagnostic["playwright_statuses"] = pw_statuses
                return [], diagnostic

        except Exception as exc:
            diagnostic["playwright_error"] = str(exc)[:200]
            # Fall through to Instagrapi

    # ── 方案2: Instagrapi 私有 API 回退 ──

    cl, err = await _ensure_ig_client()
    if cl is None:
        diagnostic["message"] = err or "Instagram client not available"
        return [], diagnostic

    def _fetch():
        try:
            user_id = cl.user_id_from_username(username)
        except Exception as e:
            return None, None, [], f"User lookup failed: {e}"

        try:
            user = cl.user_info(user_id)
        except Exception as e:
            return user_id, None, [], f"User info failed: {e}"

        if user.is_private:
            return user_id, user, [], "Account is private"

        try:
            medias = cl.user_medias(user_id, amount=max_posts)
        except Exception as e:
            return user_id, user, [], f"Media fetch failed: {e}"

        return user_id, user, medias, None

    user_id, user, medias, error = await asyncio.to_thread(_fetch)
    if error:
        diagnostic["message"] = error
        return [], diagnostic

    posts: list[dict[str, Any]] = []
    for m in medias or []:
        caption = (m.caption_text or "")[:500] if hasattr(m, 'caption_text') else ""
        shortcode = m.code if hasattr(m, 'code') else ""
        taken_at = str(m.taken_at) if hasattr(m, 'taken_at') else None
        likes = m.like_count if hasattr(m, 'like_count') else 0
        comments = m.comment_count if hasattr(m, 'comment_count') else 0
        media_type = m.media_type if hasattr(m, 'media_type') else 1

        if not _date_in_range(taken_at, start_date, end_date):
            continue

        posts.append({
            "platform": "instagram",
            "handle": handle,
            "title": caption[:120] if caption else "(no caption)",
            "url": f"https://www.instagram.com/p/{shortcode}/" if shortcode else "",
            "published_at": taken_at,
            "raw_text": caption,
            "engagement_count": int(likes) + int(comments),
            "likes": int(likes),
            "comments": int(comments),
            "media_type": media_type,
            "fetched_at": _now(),
            "source_method": "instagrapi",
        })
        if len(posts) >= max_posts:
            break

    diagnostic["ok"] = True
    diagnostic["post_count"] = len(posts)
    diagnostic["profile"] = {
        "id": str(getattr(user, 'pk', '')) if user else str(user_id or ''),
        "full_name": getattr(user, 'full_name', '') if user else '',
        "followers": getattr(user, 'follower_count', 0) if user else 0,
        "is_verified": getattr(user, 'is_verified', False) if user else False,
        "biography": (getattr(user, 'biography', '') or '')[:200] if user else '',
    }
    return posts, diagnostic


async def search_instagram_keywords(
    keywords: list[str],
    start_date: date,
    end_date: date,
    max_posts: int = 20,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Search Instagram by keywords via Instagrapi hashtag search.

    Uses instagrapi's hashtag_medias_recent() for each keyword.
    Falls back to Bing RSS if instagrapi is unavailable.
    """
    diagnostic = _diagnostic("instagram_search", ok=False, keywords=keywords)

    cl, err = await _ensure_ig_client()
    if cl is None:
        # Fallback to Bing RSS
        diagnostic["message"] = f"Instagrapi unavailable: {err}; using Bing RSS fallback"
        fallback_posts, fallback_diag = await search_social_posts_via_bing_rss(
            "instagram", keywords, start_date, end_date, max_posts
        )
        diagnostic["fallback"] = fallback_diag
        diagnostic["ok"] = True
        diagnostic["post_count"] = len(fallback_posts)
        return fallback_posts, diagnostic

    # Instagram hashtags don't use # prefix in API
    hashtags = [kw.strip().lstrip("#") for kw in keywords[:5] if kw.strip()]

    async def _search_one_hashtag(hashtag: str):
        def _fetch():
            try:
                medias = cl.hashtag_medias_recent(hashtag, amount=min(max_posts, 10))
                return medias, None
            except Exception as e:
                return [], str(e)

        return await asyncio.to_thread(_fetch)

    all_posts: list[dict[str, Any]] = []
    for hashtag in hashtags:
        medias, error = await _search_one_hashtag(hashtag)
        if error:
            diagnostic.setdefault("hashtag_errors", {})[hashtag] = error
            continue
        for m in medias:
            caption = (m.caption_text or "")[:500] if hasattr(m, 'caption_text') else ""
            shortcode = m.code if hasattr(m, 'code') else ""
            taken_at = str(m.taken_at) if hasattr(m, 'taken_at') else None
            likes = m.like_count if hasattr(m, 'like_count') else 0
            comments = m.comment_count if hasattr(m, 'comment_count') else 0

            if not shortcode:
                continue
            if not _date_in_range(taken_at, start_date, end_date):
                continue

            post_url = f"https://www.instagram.com/p/{shortcode}/"
            all_posts.append({
                "platform": "instagram",
                "handle": f"#{hashtag}",
                "title": caption[:120] if caption else f"#{hashtag}",
                "url": post_url,
                "published_at": taken_at,
                "raw_text": caption,
                "engagement_count": int(likes) + int(comments),
                "fetched_at": _now(),
                "source_method": "instagrapi_hashtag",
            })
            if len(all_posts) >= max_posts:
                break
        if len(all_posts) >= max_posts:
            break

    diagnostic["ok"] = True
    diagnostic["post_count"] = len(all_posts)
    diagnostic["hashtags_searched"] = hashtags
    if not all_posts:
        diagnostic["message"] = "No Instagram posts found via hashtag search"
    return all_posts, diagnostic


# ======================================================================
# Twitter/X – Nitter RSS (primary, no login) + FxTwitter metadata
# ======================================================================

# ---------------------------------------------------------------------------
# Twitter/X – FxTwitter API (free, no auth, no account risk)
# ---------------------------------------------------------------------------

FXTWITTER_API = "https://api.fxtwitter.com"


async def fetch_twitter_profile_fxtwitter(
    screen_name: str,
) -> dict[str, Any] | None:
    """Fetch Twitter/X user profile via FxTwitter public API. No auth, zero risk."""
    username = screen_name.strip().lstrip("@")
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                f"{FXTWITTER_API}/{username}",
                headers={"User-Agent": "japan-antiwar-intel/1.0"},
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            if data.get("code") != 200:
                return None
            return data.get("user", data)
    except Exception:
        return None


async def _fetch_twitter_timeline_legacy(
    handle: str,
    start_date: date,
    end_date: date,
    max_posts: int = 20,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch Twitter/X timeline via Nitter RSS (primary, no login) + FxTwitter metadata.

    Nitter RSS is the most reliable free method — no API key, no auth, no rate limit.
    FxTwitter provides profile metadata for free with zero account risk.
    xcancel.com HTML parsing is kept as last-resort fallback.
    """
    import xml.etree.ElementTree as ET

    diagnostic = _diagnostic("twitter", ok=False, handle=handle)
    username = handle.strip().lstrip("@")
    posts: list[dict[str, Any]] = []

    # Step 1: Get profile info from FxTwitter (free, safe)
    profile = await fetch_twitter_profile_fxtwitter(username)
    if profile:
        diagnostic["profile"] = {
            "name": profile.get("name", ""),
            "screen_name": profile.get("screen_name", username),
            "followers": profile.get("followers", 0),
            "following": profile.get("following", 0),
            "description": (profile.get("description", "") or "")[:200],
            "joined": profile.get("joined", ""),
            "location": profile.get("location", ""),
        }

    # Step 2: Primary — Nitter RSS (proven working, no login needed)
    all_nitter_instances = list(NITTER_INSTANCES) + list(NITTER_FALLBACK_INSTANCES)
    for nitter in all_nitter_instances:
        try:
            rss_url = f"{nitter}/{username}/rss"
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(rss_url, headers=BROWSER_HEADERS)
                if resp.status_code != 200:
                    continue

                # Parse RSS with namespace stripping (robust against Atom/RSS variants)
                text = resp.text
                text = _re.sub(r'\s+xmlns[^=]*="[^"]*"', "", text)
                text = _re.sub(r'\s+xmlns:[^=]*="[^"]*"', "", text)
                text = _re.sub(r'<(/)?[\w-]+:', r'<\1', text)

                root = ET.fromstring(text)
                for entry in root.findall(".//item"):
                    title_el = entry.find("title")
                    link_el = entry.find("link")
                    desc_el = entry.find("description")
                    pubdate_el = entry.find("pubDate")

                    title = title_el.text.strip() if title_el is not None and title_el.text else ""
                    link = link_el.text.strip() if link_el is not None and link_el.text else ""
                    content = desc_el.text.strip() if desc_el is not None and desc_el.text else ""
                    content = _re.sub(r"<[^>]+>", "", content)
                    content = _re.sub(r"&amp;", "&", content)
                    content = _re.sub(r"&lt;", "<", content)
                    content = _re.sub(r"&gt;", ">", content)

                    published_at = _parse_date(pubdate_el.text if pubdate_el is not None else None)
                    if not _date_in_range(published_at, start_date, end_date):
                        continue
                    if not title and not content:
                        continue

                    posts.append({
                        "platform": "twitter",
                        "handle": handle,
                        "title": title[:200],
                        "url": link,
                        "published_at": published_at,
                        "raw_text": content[:2000] if content else title[:2000],
                        "fetched_at": _now(),
                        "source_method": f"nitter_rss({nitter.split('//')[1].split('/')[0]})",
                    })
                    if len(posts) >= max_posts:
                        break
                if posts:
                    diagnostic["nitter_instance"] = nitter
                    break
        except Exception:
            continue

    # Step 3: Fallback — xcancel.com Nitter HTML (only if RSS failed)
    if not posts:
        xcancel_diag = await _try_xcancel_timeline(username, start_date, end_date, max_posts)
        posts.extend(xcancel_diag.pop("posts", []))
        diagnostic.update(xcancel_diag)

    diagnostic["ok"] = True
    diagnostic["post_count"] = len(posts)
    return posts, diagnostic


NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.cz",
]

# Nitter RSS backoff instances (tried after primary instances)
NITTER_FALLBACK_INSTANCES = [
    "https://nitter.poast.org",
    "https://nitter.privacyredirect.com",
]


async def _try_xcancel_timeline(
    username: str, start_date: date, end_date: date, max_posts: int
) -> dict[str, Any]:
    """Try to get timeline from xcancel.com (Nitter). Returns partial diagnostic + posts."""
    result: dict[str, Any] = {"xcancel_ok": False, "posts": []}
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                f"https://xcancel.com/{username}",
                headers=BROWSER_HEADERS,
            )
            result["xcancel_status"] = resp.status_code
            if resp.status_code != 200:
                return result

            soup = BeautifulSoup(resp.text, "html.parser")
            for item in soup.select(".timeline-item"):
                text_el = item.select_one(".tweet-content")
                text = text_el.get_text(" ", strip=True) if text_el else ""
                link_el = item.select_one("a.tweet-link")
                href = link_el.get("href") if link_el else ""
                if href.startswith("/"):
                    href = f"https://xcancel.com{href}"
                date_el = item.select_one(".tweet-date a")
                published_at = _parse_date(date_el.get("title") if date_el else None)
                if not _date_in_range(published_at, start_date, end_date):
                    continue
                if text or href:
                    result["posts"].append({
                        "platform": "twitter",
                        "handle": username,
                        "title": text[:120] if text else "(no text)",
                        "url": href,
                        "published_at": published_at,
                        "raw_text": text,
                        "fetched_at": _now(),
                    })
                    if len(result["posts"]) >= max_posts:
                        break
            result["xcancel_ok"] = True
    except Exception:
        pass
    return result


def _normalize_twitter_handle(handle: str) -> str:
    text = str(handle or "").strip()
    if not text:
        return ""
    parsed = urlparse(text if "://" in text else f"https://x.com/{text.lstrip('@')}")
    if parsed.netloc:
        candidate = parsed.path.strip("/").split("/", 1)[0]
        if candidate and candidate.lower() not in {"home", "search", "i", "intent"}:
            text = candidate
    return text.strip().lstrip("@")


def _split_instance_env(name: str) -> list[str]:
    raw = os.getenv(name, "")
    if not raw.strip():
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in _re.split(r"[,\s]+", raw.strip()):
        item = value.strip().rstrip("/")
        if not item.startswith("http"):
            continue
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _get_nitter_instances() -> list[str]:
    primary = _split_instance_env("NITTER_INSTANCES") or list(NITTER_INSTANCES)
    fallback = _split_instance_env("NITTER_FALLBACK_INSTANCES") or list(NITTER_FALLBACK_INSTANCES)
    result: list[str] = []
    seen: set[str] = set()
    for item in primary + fallback:
        norm = item.rstrip("/")
        if norm and norm not in seen:
            seen.add(norm)
            result.append(norm)
    return result


def _validate_nitter_rss_response(resp: httpx.Response) -> dict[str, Any]:
    content_type = (resp.headers.get("content-type", "") or "").lower()
    text = resp.text or ""
    trimmed = text.lstrip()[:1000].lower()
    if resp.status_code == 404:
        return {"rss_valid": False, "reason": "account_not_found"}
    if resp.status_code in {403, 429, 503}:
        return {"rss_valid": False, "reason": f"http_{resp.status_code}"}
    if "html" in content_type or "<html" in trimmed:
        if any(token in trimmed for token in ("cloudflare", "captcha", "just a moment", "attention required")):
            return {"rss_valid": False, "reason": "bot_challenge_html"}
        return {"rss_valid": False, "reason": "unexpected_html"}
    if not any(tag in trimmed for tag in ("<rss", "<feed", "<?xml")):
        return {"rss_valid": False, "reason": "not_rss_xml"}
    return {"rss_valid": True, "reason": "ok"}


def _classify_nitter_timeline_outcome(
    instance_results: list[dict[str, Any]],
    *,
    had_any_feed_items: bool,
    had_any_in_range: bool,
    post_count: int,
) -> dict[str, Any]:
    if post_count > 0:
        return {"outcome": "ok", "retryable": False, "message": "Fetched timeline posts successfully."}
    reasons = [str(item.get("reason", "")) for item in instance_results]
    statuses = {item.get("status") for item in instance_results if item.get("status") is not None}
    all_404 = bool(instance_results) and statuses == {404}
    blocked = any(reason in {"bot_challenge_html", "http_403", "http_429", "http_503"} for reason in reasons)
    had_valid_feed = any(item.get("rss_valid") for item in instance_results)
    if all_404:
        return {"outcome": "account_not_found", "retryable": False, "message": "Account does not exist on checked instances."}
    if blocked and not had_valid_feed:
        return {"outcome": "instances_blocked", "retryable": True, "message": "Checked instances were blocked or challenged."}
    if had_any_feed_items and not had_any_in_range:
        return {"outcome": "date_filtered_out", "retryable": False, "message": "Timeline exists but date filter removed all posts."}
    if had_valid_feed and not had_any_feed_items:
        return {"outcome": "no_new_posts", "retryable": False, "message": "Valid feed but no posts are available."}
    return {"outcome": "rss_unavailable", "retryable": True, "message": "Could not get a valid RSS feed from checked instances."}


async def fetch_twitter_timeline(
    handle: str,
    start_date: date,
    end_date: date,
    max_posts: int = 20,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import xml.etree.ElementTree as ET

    username = _normalize_twitter_handle(handle)
    diagnostic = _diagnostic(
        "twitter", ok=False, handle=handle, normalized_handle=username,
        source="nitter_rss_timeline", instance_results=[], retryable=False,
    )
    posts: list[dict[str, Any]] = []
    had_any_feed_items = False
    had_any_in_range = False
    parse_errors: list[str] = []

    profile = await fetch_twitter_profile_fxtwitter(username)
    if profile:
        diagnostic["profile"] = {
            "name": profile.get("name", ""),
            "screen_name": profile.get("screen_name", username),
        }

    for nitter in _get_nitter_instances():
        inst: dict[str, Any] = {"instance": nitter, "url": f"{nitter}/{username}/rss"}
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(inst["url"], headers=BROWSER_HEADERS)
            inst["status"] = resp.status_code
            inst["content_type"] = resp.headers.get("content-type", "")
            inst.update(_validate_nitter_rss_response(resp))
            if not inst.get("rss_valid"):
                diagnostic["instance_results"].append(inst)
                continue
            text = _re.sub(r'\s+xmlns[^=]*="[^"]*"', "", resp.text)
            text = _re.sub(r'\s+xmlns:[^=]*="[^"]*"', "", text)
            text = _re.sub(r'<(/)?[\w-]+:', r'<\1', text)
            root = ET.fromstring(text)
            feed_count = 0
            in_range_count = 0
            for entry in root.findall(".//item"):
                feed_count += 1
                title = (entry.findtext("title") or "").strip()
                link = (entry.findtext("link") or "").strip()
                content = _re.sub(r"<[^>]+>", "", (entry.findtext("description") or "").strip())
                published_at = _parse_date(entry.findtext("pubDate"))
                if not _date_in_range(published_at, start_date, end_date):
                    continue
                in_range_count += 1
                if not title and not content:
                    continue
                posts.append({
                    "platform": "twitter",
                    "handle": f"@{username}",
                    "title": title[:200],
                    "url": link,
                    "published_at": published_at,
                    "raw_text": (content or title)[:2000],
                    "fetched_at": _now(),
                    "source_method": f"nitter_rss({nitter.split('//')[1].split('/')[0]})",
                })
                if len(posts) >= max_posts:
                    break
            inst["feed_item_count"] = feed_count
            inst["in_range_item_count"] = in_range_count
            diagnostic["instance_results"].append(inst)
            had_any_feed_items = had_any_feed_items or (feed_count > 0)
            had_any_in_range = had_any_in_range or (in_range_count > 0)
            if len(posts) >= max_posts:
                break
        except Exception as exc:
            inst["reason"] = "request_or_parse_error"
            inst["error"] = type(exc).__name__
            parse_errors.append(f"{nitter}: {type(exc).__name__}")
            diagnostic["instance_results"].append(inst)

    diagnostic.update(_classify_nitter_timeline_outcome(
        diagnostic["instance_results"],
        had_any_feed_items=had_any_feed_items,
        had_any_in_range=had_any_in_range,
        post_count=len(posts),
    ))
    diagnostic["errors"] = parse_errors
    diagnostic["post_count"] = len(posts)
    diagnostic["date_range"] = {"start": str(start_date), "end": str(end_date)}
    diagnostic["available"] = diagnostic.get("outcome") != "account_not_found"
    diagnostic["ok"] = True
    return posts, diagnostic


async def search_twitter_keywords(
    keywords: list[str],
    start_date: date,
    end_date: date,
    max_posts: int = 20,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Search Twitter/X by keywords — tries Nitter HTML search, falls back to Bing.

    Twitter keyword search is inherently limited without an API key.
    Nitter instances provide user-timeline RSS but NOT keyword-search RSS.
    This function tries available workarounds in order:
      1. Nitter HTML search page (nitter.net/search?q=...)
      2. Bing RSS with site:x.com
    """
    diagnostic = _diagnostic(
        "twitter_search",
        ok=False,
        keywords=keywords,
        guarantee="not_guaranteed",
        note="Keyword hits are best-effort. Nitter RSS is for account timeline only.",
    )
    keyword_str = " ".join(keywords[:5])
    posts: list[dict[str, Any]] = []

    # ── Strategy 1: Nitter HTML search ──
    # Nitter instances DO have a /search page; parse the HTML for tweet links.
    for nitter in NITTER_INSTANCES:
        try:
            search_url = f"{nitter}/search?q={quote(keyword_str)}&f=tweets"
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(search_url, headers=BROWSER_HEADERS)
                if resp.status_code != 200:
                    continue

                soup = BeautifulSoup(resp.text, "html.parser")
                for item in soup.select(".timeline-item, .tweet-body"):
                    text_el = item.select_one(".tweet-content, .tweet-text")
                    text = text_el.get_text(" ", strip=True) if text_el else ""
                    link_el = item.select_one("a.tweet-link, a[href*='/status/']")
                    href = link_el.get("href") if link_el else ""
                    if href.startswith("/"):
                        href = f"{nitter}{href}"
                    date_el = item.select_one(".tweet-date a, .tweet-published")
                    published_at = _parse_date(date_el.get("title") or date_el.get_text(strip=True) if date_el else None)
                    if not _date_in_range(published_at, start_date, end_date):
                        continue
                    author_el = item.select_one(".username, .fullname")
                    author = author_el.get_text(strip=True) if author_el else ""
                    if text or href:
                        posts.append({
                            "platform": "twitter",
                            "handle": author,
                            "title": text[:120] if text else "(no text)",
                            "url": href,
                            "published_at": published_at,
                            "raw_text": text,
                            "fetched_at": _now(),
                            "source_method": f"nitter_search({nitter})",
                        })
                        if len(posts) >= max_posts:
                            break
                if posts:
                    diagnostic["nitter_search_instance"] = nitter
                    break
        except Exception:
            continue

    # ── Strategy 2: Bing RSS ──
    if not posts:
        fallback_posts, fallback_diag = await search_social_posts_via_bing_rss(
            "twitter", keywords, start_date, end_date, max_posts
        )
        if fallback_posts:
            posts = fallback_posts
        diagnostic["fallback"] = fallback_diag

    diagnostic["ok"] = True
    diagnostic["post_count"] = len(posts)
    if not posts:
        diagnostic["message"] = (
            "Twitter 关键词搜索受限（无官方 API Key）。"
            "Nitter 实例不提供搜索 RSS，Bing 对日文推文索引有限。"
            "建议使用「已核验账号」抓取代替关键词搜索。"
        )
    return posts, diagnostic


# ======================================================================
# Mastodon – ActivityPub public API (no auth)
# ======================================================================

async def fetch_mastodon_account(
    instance_url: str,
    username: str,
    start_date: date,
    end_date: date,
    max_posts: int = 20,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch Mastodon account statuses via public REST API. No auth required."""
    diagnostic = _diagnostic("mastodon", ok=False, handle=f"@{username}@{instance_url}")
    base = instance_url.rstrip("/")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Step 1: lookup account
            lookup_resp = await client.get(
                f"{base}/api/v1/accounts/lookup",
                params={"acct": username},
                headers=BROWSER_HEADERS,
            )
            lookup_resp.raise_for_status()
            account = lookup_resp.json()
            account_id = account["id"]

            # Step 2: get statuses
            statuses_resp = await client.get(
                f"{base}/api/v1/accounts/{account_id}/statuses",
                params={
                    "limit": min(max_posts, 40),
                    "exclude_replies": False,
                    "exclude_reblogs": True,
                },
                headers=BROWSER_HEADERS,
            )
            statuses_resp.raise_for_status()
            statuses = statuses_resp.json()
    except Exception as exc:
        # Fallback: try RSS
        rss_url = f"{base}/@{username}.rss"
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                r = await client.get(rss_url, headers=BROWSER_HEADERS)
                r.raise_for_status()
                feed = feedparser.parse(r.content)
                posts = []
                for entry in feed.entries:
                    published_at = _parse_date(entry.get("published") or entry.get("updated"))
                    if not _date_in_range(published_at, start_date, end_date):
                        continue
                    title = str(entry.get("title") or "").strip()
                    link = str(entry.get("link") or "").strip()
                    if not title or not link:
                        continue
                    posts.append({
                        "platform": "mastodon",
                        "handle": username,
                        "title": title[:120],
                        "url": link,
                        "published_at": published_at,
                        "raw_text": str(entry.get("summary") or ""),
                        "fetched_at": _now(),
                    })
                    if len(posts) >= max_posts:
                        break
                diagnostic["ok"] = True
                diagnostic["post_count"] = len(posts)
                return posts, diagnostic
        except Exception as e2:
            diagnostic["message"] = f"Mastodon API + RSS both failed: {exc} | {e2}"
            return [], diagnostic

    posts: list[dict[str, Any]] = []
    for s in statuses:
        created_at = s.get("created_at", "")
        if not _date_in_range(created_at, start_date, end_date):
            continue
        content = s.get("content", "")
        soup = BeautifulSoup(content, "html.parser")
        plain_text = soup.get_text(" ", strip=True)
        posts.append({
            "platform": "mastodon",
            "instance": instance_url,
            "handle": f"@{account.get('acct', username)}",
            "title": plain_text[:120] if plain_text else "(no text)",
            "url": s.get("url", ""),
            "published_at": created_at,
            "raw_text": plain_text,
            "engagement_count": (
                s.get("favourites_count", 0) + s.get("reblogs_count", 0)
            ),
            "reply_count": s.get("replies_count", 0),
            "tags": [tag["name"] for tag in s.get("tags", [])],
            "fetched_at": _now(),
        })
        if len(posts) >= max_posts:
            break

    diagnostic["ok"] = True
    diagnostic["post_count"] = len(posts)
    diagnostic["profile"] = {
        "id": account_id,
        "display_name": account.get("display_name", ""),
        "followers": account.get("followers_count", 0),
    }
    return posts, diagnostic


async def search_mastodon_keywords(
    keywords: list[str],
    max_posts: int = 20,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Search Mastodon public timeline by hashtag. No auth."""
    diagnostic = _diagnostic("mastodon_search", ok=False, keywords=keywords)
    tag = keywords[0].lstrip("#")
    posts: list[dict[str, Any]] = []

    for instance in MASTODON_INSTANCES[:2]:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    f"{instance}/api/v1/timelines/tag/{quote(tag)}",
                    params={"limit": min(max_posts, 40)},
                    headers=BROWSER_HEADERS,
                )
                r.raise_for_status()
                for s in r.json():
                    content = s.get("content", "")
                    soup = BeautifulSoup(content, "html.parser")
                    plain_text = soup.get_text(" ", strip=True)
                    posts.append({
                        "platform": "mastodon",
                        "instance": instance,
                        "handle": s.get("account", {}).get("acct", ""),
                        "title": plain_text[:120] if plain_text else "(no text)",
                        "url": s.get("url", ""),
                        "published_at": s.get("created_at"),
                        "raw_text": plain_text,
                        "engagement_count": s.get("favourites_count", 0),
                        "fetched_at": _now(),
                    })
                if len(posts) >= max_posts:
                    break
        except Exception:
            continue

    diagnostic["ok"] = True
    diagnostic["post_count"] = len(posts)
    return posts, diagnostic


# ======================================================================
# YouTube – public RSS feed
# ======================================================================

YOUTUBE_CHANNEL_RSS = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


async def fetch_youtube_channel_rss(
    channel_id: str,
    start_date: date,
    end_date: date,
    max_videos: int = 20,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch public videos from a YouTube channel via its public RSS feed.

    Accepts both UC... channel IDs and @handle usernames.
    """
    diagnostic = _diagnostic("youtube", ok=False)
    ch_id = channel_id.strip()
    # Resolve @handle to channel ID if needed
    if not ch_id.startswith("UC"):
        ch_id = await _resolve_youtube_handle(ch_id, diagnostic)
        if not ch_id:
            return [], diagnostic

    url = YOUTUBE_CHANNEL_RSS.format(channel_id=ch_id)
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url, headers=BROWSER_HEADERS)
            diagnostic["status_code"] = resp.status_code
            resp.raise_for_status()
    except Exception as exc:
        diagnostic["message"] = f"YouTube RSS fetch failed: {exc}"
        return [], diagnostic

    parsed = feedparser.parse(resp.content)
    posts: list[dict[str, Any]] = []
    for entry in parsed.entries:
        published_at = _parse_date(entry.get("published") or entry.get("updated"))
        if not _date_in_range(published_at, start_date, end_date):
            continue
        title = str(entry.get("title") or "").strip()
        link = str(entry.get("link") or "").strip()
        if not title or not link:
            continue
        summary = str(entry.get("summary") or "")
        posts.append({
            "platform": "youtube",
            "channel_id": ch_id,
            "title": title,
            "url": link,
            "published_at": published_at,
            "raw_text": " ".join(str(v) for v in [title, summary] if v),
            "fetched_at": _now(),
        })
        if len(posts) >= max_videos:
            break

    diagnostic["ok"] = True
    diagnostic["post_count"] = len(posts)
    return posts, diagnostic


async def _resolve_youtube_handle(handle: str, diagnostic: dict) -> str | None:
    """Resolve a YouTube @handle to a channel ID by scraping the channel page."""
    username = handle.lstrip("@")
    url = f"https://www.youtube.com/@{username}"
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url, headers=BROWSER_HEADERS)
            if resp.status_code != 200:
                diagnostic["message"] = f"YouTube channel page returned {resp.status_code} for @{username}"
                return None
            html = resp.text
    except Exception as exc:
        diagnostic["message"] = f"YouTube handle resolve failed: {exc}"
        return None

    import re
    # Look for channel ID in meta tags: "externalId":"UC..."
    match = re.search(r'"externalId"\s*:\s*"(UC[^"]+)"', html)
    if match:
        return match.group(1)
    # Fallback: look for channel ID in browse_id
    match = re.search(r'"browseId"\s*:\s*"(UC[^"]+)"', html)
    if match:
        return match.group(1)
    # Fallback: look for /channel/UC... URL
    match = re.search(r'/channel/(UC[^"]+)', html)
    if match:
        return match.group(1)

    diagnostic["message"] = f"Could not resolve @{username} to channel ID"
    return None


async def search_youtube_keywords(
    keywords: list[str],
    start_date: date,
    end_date: date,
    max_posts: int = 20,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Search YouTube by keywords via public search snippets."""
    diagnostic = _diagnostic("youtube_search", ok=False, keywords=keywords)
    posts, fallback_diag = await search_social_posts_via_bing_rss(
        "youtube", keywords, start_date, end_date, max_posts
    )
    diagnostic["ok"] = True
    diagnostic["post_count"] = len(posts)
    diagnostic["fallback"] = fallback_diag
    diagnostic["message"] = "YouTube has no generic public RSS keyword endpoint; used public search snippets."
    return posts, diagnostic


# ======================================================================
# RSSHub – public instance proxy
# ======================================================================

RSSHUB_PUBLIC_INSTANCE = "https://rsshub.app"


async def fetch_rsshub_route(
    route: str,
    start_date: date,
    end_date: date,
    max_items: int = 20,
    instance: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch posts from an RSSHub route."""
    base = (instance or RSSHUB_PUBLIC_INSTANCE).rstrip("/")
    url = f"{base}/{route.lstrip('/')}"
    diagnostic = _diagnostic("rsshub", ok=False, route=route)

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers=BROWSER_HEADERS)
            diagnostic["status_code"] = resp.status_code
            resp.raise_for_status()
    except Exception as exc:
        diagnostic["message"] = f"RSSHub fetch failed: {exc}"
        return [], diagnostic

    parsed = feedparser.parse(resp.content)
    posts: list[dict[str, Any]] = []
    for entry in parsed.entries:
        published_at = _parse_date(entry.get("published") or entry.get("updated"))
        if not _date_in_range(published_at, start_date, end_date):
            continue
        title = str(entry.get("title") or "").strip()
        link = str(entry.get("link") or "").strip()
        if not title or not link:
            continue
        summary = str(entry.get("summary") or "")
        posts.append({
            "platform": "rsshub",
            "route": route,
            "title": title,
            "url": link,
            "published_at": published_at,
            "raw_text": " ".join(str(v) for v in [title, summary] if v),
            "fetched_at": _now(),
        })
        if len(posts) >= max_items:
            break

    diagnostic["ok"] = True
    diagnostic["post_count"] = len(posts)
    return posts, diagnostic


# ======================================================================
# Fetch posts for tracked accounts (dispatch)
# ======================================================================

async def fetch_posts_for_account(
    account: dict[str, Any],
    start_date: date,
    end_date: date,
    max_posts: int = 20,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Dispatch to the right fetcher based on account platform."""
    platform = str(account.get("platform", "")).strip().lower()
    handle = str(account.get("handle", "")).strip()

    if platform == "youtube":
        return await fetch_youtube_channel_rss(handle, start_date, end_date, max_posts)

    if platform == "reddit":
        subreddit = handle.removeprefix("r/").removeprefix("/r/")
        return await fetch_reddit_subreddit_posts(subreddit, start_date, end_date, max_posts)

    if platform == "twitter":
        return await fetch_twitter_timeline(handle, start_date, end_date, max_posts)

    if platform == "instagram":
        return await fetch_instagram_profile(handle, start_date, end_date, max_posts)

    if platform == "mastodon":
        # handle format: "username@instance" or "username@instance.url"
        if "@" in handle:
            parts = handle.rsplit("@", 1)
            username, instance = parts[0], parts[1]
            if not instance.startswith("http"):
                instance = f"https://{instance}"
        else:
            username = handle
            instance = MASTODON_INSTANCES[0]
        return await fetch_mastodon_account(instance, username, start_date, end_date, max_posts)

    if platform == "rsshub":
        return await fetch_rsshub_route(handle, start_date, end_date, max_posts)

    # Generic RSS attempt for other platforms
    url = str(account.get("url", "")).strip()
    if url:
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(url, headers=BROWSER_HEADERS)
                resp.raise_for_status()
                parsed = feedparser.parse(resp.content)
                posts = []
                for entry in parsed.entries:
                    published_at = _parse_date(entry.get("published") or entry.get("updated"))
                    if not _date_in_range(published_at, start_date, end_date):
                        continue
                    title = str(entry.get("title") or "").strip()
                    link = str(entry.get("link") or "").strip()
                    if not title or not link:
                        continue
                    posts.append({
                        "platform": platform,
                        "handle": handle,
                        "title": title,
                        "url": link,
                        "published_at": published_at,
                        "raw_text": str(entry.get("summary") or ""),
                        "fetched_at": _now(),
                    })
                    if len(posts) >= max_posts:
                        break
                return posts, _diagnostic(platform, ok=True, post_count=len(posts))
        except Exception as exc:
            return [], _diagnostic(platform, ok=False, message=str(exc))

    return [], _diagnostic(platform, ok=False, message=f"No URL or handler for platform {platform}")


# ======================================================================
# Cross-platform keyword search (NO account database needed)
# ======================================================================

SEARCHABLE_PLATFORMS = ["reddit", "twitter", "instagram", "mastodon", "youtube", "rsshub"]


async def search_social_by_keywords(
    keywords: list[str],
    start_date: date,
    end_date: date,
    platforms: list[str] | None = None,
    max_posts_per_platform: int = 20,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Search across social platforms by keywords. No account database needed.

    This is the primary social discovery method - searches directly by keywords
    rather than relying on pre-registered accounts.
    """
    targets = [p for p in (platforms or SEARCHABLE_PLATFORMS) if p in SEARCHABLE_PLATFORMS]

    all_posts: list[dict[str, Any]] = []
    all_diagnostics: list[dict[str, Any]] = []

    async def search_platform(platform: str):
        try:
            if platform == "reddit":
                posts, diag = await search_reddit_keywords(keywords, start_date, end_date, max_posts_per_platform)
            elif platform == "twitter":
                posts, diag = await search_twitter_keywords(keywords, start_date, end_date, max_posts_per_platform)
            elif platform == "instagram":
                posts, diag = await search_instagram_keywords(keywords, start_date, end_date, max_posts_per_platform)
            elif platform == "mastodon":
                posts, diag = await search_mastodon_keywords(keywords, max_posts_per_platform)
            elif platform == "youtube":
                posts, diag = await search_youtube_keywords(keywords, start_date, end_date, max_posts_per_platform)
            elif platform == "rsshub":
                posts, diag = [], _diagnostic(
                    "rsshub",
                    ok=False,
                    message="RSSHub keyword search needs a concrete route or tracked account; generic keyword search is not available.",
                    post_count=0,
                )
            else:
                return [], _diagnostic(platform, ok=False, message="Unknown platform")
            return posts, diag
        except Exception as exc:
            return [], _diagnostic(platform, ok=False, message=str(exc))

    tasks = [search_platform(p) for p in targets]
    results = await asyncio.gather(*tasks)
    for posts, diag in results:
        filtered_posts = [post for post in posts if is_relevant_social_post(post, keywords)]
        if len(filtered_posts) != len(posts):
            diag["filtered_out"] = len(posts) - len(filtered_posts)
            diag["filtered_post_count"] = len(filtered_posts)
        all_posts.extend(filtered_posts)
        all_diagnostics.append(diag)

    return all_posts, all_diagnostics


# ======================================================================
# Fetch all tracked accounts
# ======================================================================

async def fetch_all_tracked_account_posts(
    start_date: date,
    end_date: date,
    max_posts_per_account: int = 20,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch posts from all tracked (verified/seed) Japan peace social accounts."""
    accounts = fetch_all(
        "SELECT * FROM japan_social_accounts WHERE status IN ('verified', 'seed') ORDER BY platform, handle"
    )
    all_posts: list[dict[str, Any]] = []
    all_diagnostics: list[dict[str, Any]] = []

    semaphore = asyncio.Semaphore(8)

    async def fetch_one(account: dict[str, Any]):
        async with semaphore:
            posts, diagnostic = await fetch_posts_for_account(account, start_date, end_date, max_posts_per_account)
            for post in posts:
                post["account_id"] = account["id"]
                post["account_handle"] = account["handle"]
                post["account_platform"] = account["platform"]
            return posts, diagnostic

    tasks = [fetch_one(account) for account in accounts]
    results = await asyncio.gather(*tasks)
    for posts, diagnostic in results:
        all_posts.extend(posts)
        all_diagnostics.append(diagnostic)

    return all_posts, all_diagnostics


# ======================================================================
# Storage helpers
# ======================================================================

def _post_hash(url: str, text: str) -> str:
    return hashlib.sha256(f"{url}\n{text}".encode("utf-8")).hexdigest()


def _store_social_posts(posts: list[dict[str, Any]]) -> dict[str, int]:
    inserted = 0
    skipped = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for post in posts:
                url = str(post.get("url") or "").strip()
                text = str(post.get("raw_text") or post.get("title") or "").strip()
                platform = str(post.get("platform") or post.get("account_platform") or "unknown").strip()
                if not url or not text:
                    skipped += 1
                    continue
                content_hash = _post_hash(url, text)
                cur.execute(
                    "SELECT id FROM japan_social_posts WHERE url = %s OR fetched_content_hash = %s LIMIT 1",
                    (url, content_hash),
                )
                if cur.fetchone():
                    skipped += 1
                    continue
                cur.execute(
                    """
                    INSERT INTO japan_social_posts
                      (account_id, platform, url, content_text, published_at, fetched_at,
                       language, fetched_content_hash, engagement_count)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        post.get("account_id"),
                        platform,
                        url,
                        text,
                        post.get("published_at"),
                        _now(),
                        post.get("language") or "ja",
                        content_hash,
                        int(post.get("engagement_count") or post.get("score") or 0),
                    ),
                )
                inserted += 1
    return {"inserted": inserted, "skipped_duplicates": skipped}


# ======================================================================
# Stream fetch (used by API endpoint /japan/social/fetch/stream)
# ======================================================================

async def fetch_japan_social_stream(
    start_date: date,
    end_date: date,
    platform: str | None = None,
    platforms: list[str] | None = None,
    account_ids: list[int] | None = None,
    include_verified_accounts: bool = False,
    include_candidate_accounts: bool = False,
    max_posts: int = 50,
    keywords: list[str] | None = None,
):
    """Stream-fetch Japan social posts with real-time per-platform progress.

    Yields events:
      stage           — phase start (keyword_search / accounts)
      platform_start  — starting a platform keyword search
      platform_done   — platform search complete (includes diagnostic)
      post            — individual post found (yielded immediately)
      progress        — account fetch loop progress
      rate_limited    — platform blocked or rate-limited
      done            — final summary
    """
    requested_platforms = [p for p in (platforms or ([platform] if platform else [])) if p]
    selected_platforms = [p for p in (requested_platforms or SEARCHABLE_PLATFORMS) if p in SEARCHABLE_PLATFORMS]
    if not selected_platforms:
        selected_platforms = SEARCHABLE_PLATFORMS
    account_platforms = requested_platforms or selected_platforms

    search_keywords = _keyword_sample(keywords)
    all_posts: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []

    # ═══════════════════════════════════════════════
    # Phase 1: Keyword search — SEQUENTIAL (one platform at a time)
    #           so the UI shows per-platform progress in real time.
    # ═══════════════════════════════════════════════
    yield {
        "type": "stage",
        "stage": "keyword_search",
        "message": f"关键词检索开始：{', '.join(search_keywords)}",
        "keywords": search_keywords,
        "platforms": selected_platforms,
        "total_platforms": len(selected_platforms),
    }

    for idx, plat in enumerate(selected_platforms, start=1):
        yield {
            "type": "platform_start",
            "platform": plat,
            "index": idx,
            "total": len(selected_platforms),
            "message": f"[{idx}/{len(selected_platforms)}] 正在搜索 {plat}…",
        }

        try:
            if plat == "reddit":
                posts, diag = await search_reddit_keywords(
                    search_keywords, start_date, end_date, max_posts
                )
            elif plat == "twitter":
                posts, diag = await search_twitter_keywords(
                    search_keywords, start_date, end_date, max_posts
                )
            elif plat == "instagram":
                posts, diag = await search_instagram_keywords(
                    search_keywords, start_date, end_date, max_posts
                )
            elif plat == "mastodon":
                posts, diag = await search_mastodon_keywords(
                    search_keywords, max_posts
                )
            elif plat == "youtube":
                posts, diag = await search_youtube_keywords(
                    search_keywords, start_date, end_date, max_posts
                )
            elif plat == "rsshub":
                posts, diag = [], _diagnostic(
                    "rsshub", ok=False,
                    message="RSSHub 关键词搜索需要具体路由；已跳过。",
                    post_count=0,
                )
            else:
                posts, diag = [], _diagnostic(plat, ok=False, message="未知平台")
        except Exception as exc:
            posts, diag = [], _diagnostic(plat, ok=False, message=str(exc))

        # Filter for relevance
        filtered_posts = [p for p in posts if is_relevant_social_post(p, search_keywords)]
        if len(filtered_posts) != len(posts):
            diag["filtered_out"] = len(posts) - len(filtered_posts)
        diag["filtered_post_count"] = len(filtered_posts)
        diagnostics.append(diag)

        # ── Rate-limit / blocked feedback ──
        status_code = diag.get("status_code") or diag.get("xcancel_status")
        blocked_codes = {429, 403, 401}
        if status_code in blocked_codes:
            yield {
                "type": "rate_limited",
                "platform": plat,
                "status_code": status_code,
                "message": diag.get("message", f"HTTP {status_code} — 被限流或阻拦"),
            }
        elif not diag.get("ok") and not filtered_posts:
            yield {
                "type": "rate_limited",
                "platform": plat,
                "message": diag.get("message", "平台不可用"),
            }

        # ── Platform done ──
        yield {
            "type": "platform_done",
            "platform": plat,
            "index": idx,
            "total": len(selected_platforms),
            "post_count": len(filtered_posts),
            "ok": diag.get("ok", False),
            "diagnostic": diag,
        }

        # ── Yield each post immediately ──
        for post in filtered_posts:
            post["source"] = "keyword"
            all_posts.append(post)
            yield {
                "type": "post",
                "source": "keyword",
                "platform": post.get("platform"),
                "url": post.get("url"),
                "content_text": (post.get("raw_text") or post.get("title") or "")[:120],
                "engagement_count": post.get("engagement_count", 0),
            }

    # ═══════════════════════════════════════════════
    # Phase 2: Account-based fetch
    # ═══════════════════════════════════════════════
    should_fetch_accounts = bool(account_ids) or include_verified_accounts or include_candidate_accounts
    if should_fetch_accounts:
        yield {
            "type": "stage",
            "stage": "accounts",
            "message": "开始抓取已追踪账号…",
        }
        try:
            if account_ids:
                placeholders = ",".join(["%s"] * len(account_ids))
                accounts = fetch_all(
                    f"SELECT * FROM japan_social_accounts WHERE id IN ({placeholders})",
                    tuple(account_ids),
                )
            else:
                statuses = ["verified"]
                if include_candidate_accounts:
                    statuses.append("candidate")
                placeholders = ",".join(["%s"] * len(statuses))
                accounts = fetch_all(
                    f"SELECT * FROM japan_social_accounts WHERE status IN ({placeholders}) ORDER BY platform, handle",
                    tuple(statuses),
                )
            accounts = [a for a in accounts if str(a.get("platform", "")).lower() in account_platforms]
        except Exception as exc:
            accounts = []
            diagnostics.append(_diagnostic("accounts", ok=False, message=f"账号查询失败: {exc}"))
            yield {"type": "rate_limited", "platform": "database", "message": str(exc)}

        total = len(accounts)
        if total == 0:
            message = "没有找到符合条件的已追踪账号。请先导入/核验账号，或填写具体账号 ID。"
            diagnostics.append(_diagnostic("accounts", ok=True, post_count=0, message=message))
            yield {
                "type": "progress",
                "current": 0,
                "total": 0,
                "platform": "accounts",
                "message": message,
            }
        for index, account in enumerate(accounts, start=1):
            plat = str(account.get("platform", "")).lower()
            handle = str(account.get("handle", ""))
            yield {
                "type": "progress",
                "current": index,
                "total": total,
                "platform": plat,
                "handle": handle,
                "message": f"[{index}/{total}] {plat} {handle}",
            }

            try:
                # ── Instagram: 优先 Playwright 流式 ──
                if plat == "instagram":
                    _pw = None
                    try:
                        from .ig_playwright import collect_profile_streaming as _pw_cps
                        _pw = _pw_cps
                    except ImportError:
                        pass

                    if _pw is not None:
                        # Playwright 流式采集 — 逐条推送进度和帖子
                        ig_posts: list[dict[str, Any]] = []
                        ig_errors: list[str] = []
                        async for ig_event in _pw_cps(handle, max_posts):
                            if ig_event["type"] == "ig_progress":
                                yield {
                                    "type": "progress",
                                    "current": index,
                                    "total": total,
                                    "platform": plat,
                                    "handle": handle,
                                    "message": ig_event["message"],
                                }
                            elif ig_event["type"] == "ig_post":
                                post = {
                                    "platform": "instagram",
                                    "handle": handle,
                                    "title": (ig_event.get("caption", "") or "")[:120] or "(no caption)",
                                    "url": ig_event.get("url", ""),
                                    "published_at": ig_event.get("taken_at"),
                                    "raw_text": ig_event.get("caption", ""),
                                    "engagement_count": ig_event.get("likes", 0),
                                    "fetched_at": _now(),
                                    "source_method": "playwright",
                                    "shortcode": ig_event.get("shortcode"),
                                }
                                ig_posts.append(post)
                                all_posts.append(post)
                                yield {
                                    "type": "post",
                                    "source": "account",
                                    "platform": "instagram",
                                    "handle": handle,
                                    "url": post["url"],
                                    "content_text": post["raw_text"][:120] or post["url"],
                                    "engagement_count": post.get("engagement_count", 0),
                                }
                            elif ig_event["type"] == "ig_error":
                                ig_errors.append(ig_event["message"])
                                yield {
                                    "type": "rate_limited",
                                    "platform": "instagram",
                                    "handle": handle,
                                    "message": ig_event["message"],
                                }

                        diagnostic = _diagnostic(
                            "instagram_playwright",
                            ok=len(ig_posts) > 0,
                            handle=handle,
                            post_count=len(ig_posts),
                            source="playwright",
                        )
                        if ig_errors:
                            diagnostic["message"] = "; ".join(ig_errors[-2:])
                    else:
                        # Playwright 不可用，走 Instagrapi
                        posts, diagnostic = await fetch_posts_for_account(
                            account, start_date, end_date, max_posts
                        )
                        for post in posts:
                            post["account_id"] = account["id"]
                            post["account_name"] = account.get("account_name") or account.get("handle")
                            post["source"] = "account"
                            all_posts.append(post)
                            yield {
                                "type": "post",
                                "source": "account",
                                "platform": post.get("platform"),
                                "handle": handle,
                                "url": post.get("url"),
                                "content_text": (post.get("raw_text") or post.get("title") or "")[:120],
                                "engagement_count": post.get("engagement_count", 0),
                            }
                else:
                    posts, diagnostic = await fetch_posts_for_account(
                        account, start_date, end_date, max_posts
                    )
                    for post in posts:
                        post["account_id"] = account["id"]
                        post["account_name"] = account.get("account_name") or account.get("handle")
                        post["source"] = "account"
                        all_posts.append(post)
                        yield {
                            "type": "post",
                            "source": "account",
                            "platform": post.get("platform"),
                            "handle": handle,
                            "url": post.get("url"),
                            "content_text": (post.get("raw_text") or post.get("title") or "")[:120],
                            "engagement_count": post.get("engagement_count", 0),
                        }
            except Exception as exc:
                diagnostic = _diagnostic(plat, ok=False, message=str(exc))
                yield {
                    "type": "rate_limited",
                    "platform": plat,
                    "handle": handle,
                    "message": f"抓取异常: {exc}",
                }

            diagnostics.append(diagnostic)

            # Rate-limit event (except-case already yielded above;
            # success branches may also yield rate_limited inside the try)
            if not diagnostic.get("ok"):
                already_yielded = False  # success branches yield inline
                if diagnostic.get("message", "").startswith("抓取异常"):
                    already_yielded = True  # except already yielded
                if not already_yielded:
                    yield {
                        "type": "rate_limited",
                        "platform": plat,
                        "handle": handle,
                        "message": diagnostic.get("message", "抓取失败"),
                        "status_code": diagnostic.get("status_code"),
                    }

    # ═══════════════════════════════════════════════
    # Store results
    # ═══════════════════════════════════════════════
    try:
        stored = _store_social_posts(all_posts) if all_posts else {"inserted": 0, "skipped_duplicates": 0}
    except Exception as exc:
        stored = {"inserted": 0, "skipped_duplicates": 0}
        diagnostics.append(_diagnostic("postgres", ok=False, message=f"入库失败: {exc}"))

    yield {
        "type": "done",
        "ok": True,
        "fetched": len(all_posts),
        **stored,
        "keywords": search_keywords,
        "diagnostics": diagnostics,
    }


# ======================================================================
# List stored social posts
# ======================================================================

def list_japan_social_posts(
    start_date: date | None = None,
    end_date: date | None = None,
    platform: str | None = None,
    account_id: int | None = None,
    keyword: str | None = None,
    verified_only: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    clauses = ["1=1"]
    params: list[Any] = []
    if verified_only:
        clauses.append("account_id IN (SELECT id FROM japan_social_accounts WHERE status = 'verified')")
    if start_date:
        clauses.append("COALESCE(published_at, fetched_at) >= %s")
        params.append(start_date)
    if end_date:
        clauses.append("COALESCE(published_at, fetched_at) < %s")
        params.append(end_date + timedelta(days=1))
    if platform:
        clauses.append("platform = %s")
        params.append(platform)
    if account_id:
        clauses.append("account_id = %s")
        params.append(account_id)
    if keyword:
        clauses.append("content_text ILIKE %s")
        params.append(f"%{keyword}%")
    params.append(limit)
    return fetch_all(
        f"""
        SELECT p.*, a.account_name, a.handle, a.organization, a.status AS account_status
        FROM japan_social_posts p
        LEFT JOIN japan_social_accounts a ON p.account_id = a.id
        WHERE {' AND '.join(clauses)}
        ORDER BY COALESCE(p.published_at, p.fetched_at) DESC
        LIMIT %s
        """,
        tuple(params),
    )
