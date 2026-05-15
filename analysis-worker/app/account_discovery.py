from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import feedparser
import httpx
from bs4 import BeautifulSoup


DEFAULT_PUBLIC_DISCOVERY_KEYWORDS = [
    "Japan anti-war organization x.com",
    "Japan peace group twitter",
    "Japan Article 9 movement instagram",
    "Japan Okinawa base protest youtube",
    "日本 反戦 団体 X",
    "日本 平和 団体 Twitter",
    "日本 憲法9条 運動 Instagram",
    "沖縄 基地 反対 団体 YouTube",
]

SEARCH_SOURCE_TEMPLATES = [
    ("bing_web", "https://www.bing.com/search?q={q}"),
    ("google_news_rss", "https://news.google.com/rss/search?q={q}&hl=ja&gl=JP&ceid=JP:ja"),
]

SOCIAL_HOST_TO_PLATFORM = {
    "x.com": "twitter",
    "www.x.com": "twitter",
    "twitter.com": "twitter",
    "www.twitter.com": "twitter",
    "instagram.com": "instagram",
    "www.instagram.com": "instagram",
    "youtube.com": "youtube",
    "www.youtube.com": "youtube",
    "youtu.be": "youtube",
}

ORG_SIGNALS = ("organization", "official", "party", "association", "news", "media", "office", "議員", "党", "連盟", "協会", "公式", "新聞")


@dataclass
class DiscoveryCandidate:
    platform: str
    handle: str
    profile_url: str
    evidence_url: str
    source_type: str
    source_detail: str
    category: str = "other"
    verification_status: str = "candidate_unverified"
    note: str = ""


def _clean_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path}".rstrip("/")


def _extract_social_from_url(raw_url: str) -> tuple[str, str, str] | None:
    cleaned = _clean_url(raw_url)
    if not cleaned:
        return None
    parsed = urlparse(cleaned)
    platform = SOCIAL_HOST_TO_PLATFORM.get(parsed.netloc.lower())
    if not platform:
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if platform == "twitter":
        if not parts or parts[0].lower() in {"home", "search", "i", "intent", "share"}:
            return None
        handle = "@" + parts[0].lstrip("@")
        profile_url = f"https://x.com/{handle.lstrip('@')}"
        return platform, handle.lower(), profile_url
    if platform == "instagram":
        if not parts or parts[0].lower() in {"explore", "accounts", "p", "reel", "stories"}:
            return None
        handle = "@" + parts[0].lstrip("@")
        profile_url = f"https://www.instagram.com/{handle.lstrip('@')}"
        return platform, handle.lower(), profile_url
    if platform == "youtube":
        if not parts:
            return None
        first = parts[0].lower()
        if first in {"watch", "shorts", "playlist", "results"}:
            return None
        if first in {"@", "channel", "c", "user"} and len(parts) > 1:
            handle = parts[1]
        elif first.startswith("@"):
            handle = first
        else:
            handle = parts[0]
        handle = handle if handle.startswith("@") else handle
        profile_url = f"https://www.youtube.com/{parts[0] if parts[0].startswith('@') else '/'.join(parts[:2])}".rstrip("/")
        return platform, handle.lower(), profile_url
    return None


def _decode_bing_redirect(url: str) -> str:
    parsed = urlparse(url)
    if "bing.com" not in parsed.netloc:
        return url
    qs = parse_qs(parsed.query)
    target = (qs.get("u") or qs.get("url") or [""])[0]
    if target:
        return unquote(target)
    return url


def extract_links_from_html(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html or "", "html.parser")
    links: list[str] = []
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "").strip()
        if not href:
            continue
        absolute = urljoin(base_url, href)
        absolute = _decode_bing_redirect(absolute)
        links.append(absolute)
    return links


def extract_links_from_rss(content: str) -> list[str]:
    parsed = feedparser.parse(content or "")
    links: list[str] = []
    for entry in parsed.entries:
        link = str(entry.get("link") or "").strip()
        if link:
            links.append(link)
    return links


def classify_candidate_context(text: str) -> tuple[str, str]:
    lowered = (text or "").lower()
    if any(signal in lowered for signal in ORG_SIGNALS):
        return "other", "candidate_public_figure_possible"
    return "other", "candidate_unverified"


def normalize_candidates(
    links: list[str],
    *,
    evidence_url: str,
    source_type: str,
    source_detail: str,
    context_text: str = "",
) -> list[DiscoveryCandidate]:
    out: dict[tuple[str, str], DiscoveryCandidate] = {}
    category, verification_status = classify_candidate_context(context_text)
    for link in links:
        parsed = _extract_social_from_url(link)
        if not parsed:
            continue
        platform, handle, profile_url = parsed
        key = (platform, handle)
        if key not in out:
            out[key] = DiscoveryCandidate(
                platform=platform,
                handle=handle,
                profile_url=profile_url,
                evidence_url=evidence_url,
                source_type=source_type,
                source_detail=source_detail,
                category=category,
                verification_status=verification_status,
            )
    return list(out.values())


async def fetch_text(client: httpx.AsyncClient, url: str) -> str:
    resp = await client.get(url, follow_redirects=True, timeout=10.0)
    if resp.status_code >= 400:
        raise ValueError(f"http_{resp.status_code}: {url}")
    return resp.text


async def discover_public_candidates(
    keywords: list[str],
    *,
    max_requests: int = 10,
    fetcher: Any = None,
) -> tuple[list[DiscoveryCandidate], list[dict[str, Any]]]:
    diagnostics: list[dict[str, Any]] = []
    found: dict[tuple[str, str], DiscoveryCandidate] = {}
    fetch = fetcher
    if fetch is None:
        async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}) as client:
            async def _fetch(u: str) -> str:
                return await fetch_text(client, u)
            fetch = _fetch
            return await discover_public_candidates(keywords, max_requests=max_requests, fetcher=fetch)

    request_count = 0
    for keyword in keywords:
        for source_type, template in SEARCH_SOURCE_TEMPLATES:
            if request_count >= max_requests:
                diagnostics.append({"ok": True, "stage": "public_discovery", "message": "request_limit_reached", "max_requests": max_requests})
                return list(found.values()), diagnostics
            query = keyword.replace(" ", "+")
            url = template.format(q=query)
            request_count += 1
            try:
                body = await fetch(url)
                links = extract_links_from_rss(body) if source_type.endswith("rss") else extract_links_from_html(body, url)
                diagnostics.append({"ok": True, "stage": "query", "source_type": source_type, "keyword": keyword, "query_url": url, "links_found": len(links)})
                candidates = normalize_candidates(
                    links,
                    evidence_url=url,
                    source_type=source_type,
                    source_detail=keyword,
                    context_text=body[:1200],
                )
                for c in candidates:
                    found[(c.platform, c.handle)] = c
            except Exception as exc:
                diagnostics.append({"ok": False, "stage": "query", "source_type": source_type, "keyword": keyword, "query_url": url, "reason": str(exc)})
    return list(found.values()), diagnostics
