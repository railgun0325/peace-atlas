"""Bounded public article capture; an aggregator page is never article content."""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from . import google_news_resolver, pipeline


def _bigrams(text):
    text = re.sub(r"[^\w]", "", str(text).casefold())
    return {text[index:index+2] for index in range(max(0, len(text)-1))}


def _same_title(expected, observed):
    left, right = _bigrams(expected), _bigrams(observed)
    return bool(left and right and len(left & right) / min(len(left), len(right)) >= .55)


def public_full_article_link(body, url, expected_title):
    soup = BeautifulSoup(body, "html.parser")
    title = (soup.select_one('meta[property="og:title"]') or {}).get("content", "") or (soup.title.get_text(" ", strip=True) if soup.title else "")
    if not _same_title(expected_title, title):
        return ""
    current, links = urlsplit(url), set()
    for anchor in soup.select('a[href]'):
        if not re.fullmatch(r"続きを読む|記事全文(?:を読む|はこちら)?|全文を読む|Read (?:the )?(?:full (?:story|article)|more)", anchor.get_text(" ", strip=True), re.I):
            continue
        try:
            target = pipeline._url(urljoin(url, anchor["href"]))
        except pipeline.PipelineError:
            continue
        parsed, query = urlsplit(target), parse_qs(urlsplit(target).query)
        # Follow only a visible full-article variant of this same public page,
        # once. Login links, another story and arbitrary destinations cannot
        # inherit this material's identity or trigger further requests.
        full_variant = query.get("display") == ["1"] or query.get("p") == ["all"] or query.get("page") == ["all"]
        if parsed.scheme == "https" and parsed.netloc == current.netloc and parsed.path == current.path and full_variant and target != url:
            links.add(target)
    return next(iter(links)) if len(links) == 1 else ""


def extract_public_body(body, expected_title, details=None):
    soup = BeautifulSoup(body, "html.parser")
    observed_title = (soup.select_one('meta[property="og:title"]') or {}).get("content", "")
    if not observed_title and soup.title:
        observed_title = soup.title.get_text(" ", strip=True)
    if not _same_title(expected_title, observed_title):
        raise pipeline.PipelineError("公开页标题不能对应选中资料，未替换内容。")
    candidates = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        pending = data if isinstance(data, list) else [data]
        for row in pending:
            if not isinstance(row, dict):
                continue
            if isinstance(row.get("@graph"), list):
                pending.extend(row["@graph"][:30])
            if isinstance(row.get("articleBody"), str):
                candidates.append((row["articleBody"].strip(), "jsonld_article_body", row.get("datePublished")))
            if len(pending) > 100:
                break
    for element in soup.select('script,style,noscript,nav,footer,aside,form,iframe,.related,.ranking,[class*="recommend" i],[class*="related" i],.social'):
        element.decompose()
    for selector in ('[itemprop="articleBody"]', '.article-body', '.articleBody', '.article_body', '.entry-content', '.post-content', 'article', 'main'):
        elements = soup.select(selector)[:8]
        explicit = selector not in {"main", "article"}
        for element in elements:
            paragraphs = [node.get_text(" ", strip=True) for node in element.select('p,h2,h3,li,blockquote' if explicit else 'p,blockquote') if not node.find_parent("a")]
            text = "\n\n".join(dict.fromkeys(line for line in paragraphs if line))
            if not text and explicit:
                text = element.get_text("\n", strip=True)
            if len(text) >= 180:
                candidates.append((text, "public_dom:" + selector, None))
        # Prefer explicit article-body markup to a broad page container.
        if candidates:
            break
        if explicit and elements:
            raise pipeline.PipelineError("对应新闻仅显示短预览，未把推荐文章或整页内容当作正文；保留检索摘要。")
    if not candidates:
        raise pipeline.PipelineError("没有取得足够的公开正文，保留检索摘要。")
    text, method, published = max(candidates, key=lambda row: len(row[0]))
    if any(marker in text for marker in ("この続きを読むには", "ログインして続きを読む", "購読会員限定", "有料会員限定記事")):
        raise pipeline.PipelineError("公开页提示订阅或登录限制，保留检索摘要。")
    if len(text) < 180 or len(text) > 80000:
        raise pipeline.PipelineError("公开正文长度不足或超出保存预算，未替换内容。")
    if details is not None and published:
        try:
            date_fields = pipeline._date_fields(published)
            details.update(body_published_at=date_fields["published_at"], body_publication_day=date_fields["publication_day"], body_publication_precision=date_fields["publication_date_precision"])
        except pipeline.PipelineError:
            details["body_publication_date_unreadable"] = True
    return text, observed_title, method


async def capture_article(item, original_url=""):
    result = copy.deepcopy(item)
    metadata = result.setdefault("metadata", {})
    target = pipeline._url(original_url or metadata.get("body_source_url") or item["url"])
    diagnostic = {"source": "public_article", "item_id": item.get("id"), "url": target, "ok": False}
    aggregator = urlsplit(target).hostname in {"news.google.com", "www.news.google.com"}
    if aggregator and not google_news_resolver.article_id(target):
        diagnostic.update(outcome="aggregator_only", message="当前是聚合器地址；需出版方原文地址，聚合页不能当作新闻正文。")
        return result, diagnostic
    try:
        async with asyncio.timeout(18):
            if aggregator:
                target = await google_news_resolver.resolve_original_url(target)
            body, final_url, status = await pipeline._fetch_public_feed(target, accept="text/html, application/xhtml+xml")
            continuation = public_full_article_link(body, final_url, pipeline._without_publisher_suffix(item["title"], item.get("source_name", "")))
            if continuation:
                first_page = final_url
                body, final_url, status = await pipeline._fetch_public_feed(continuation, accept="text/html, application/xhtml+xml")
            else:
                first_page = ""
        publication = {}
        text, title, method = extract_public_body(body, pipeline._without_publisher_suffix(item["title"], item.get("source_name", "")), publication)
        metadata.update(publication)
        if publication.get("body_published_at") and item.get("published_at"):
            original = pipeline._date_fields(item["published_at"])
            metadata["body_publication_differs_from_feed"] = (publication["body_published_at"] != original["published_at"]
                if publication["body_publication_precision"] == "timestamp" else publication["body_publication_day"] != original["publication_day"])
        metadata.update(content_kind="public_article_body", body_source_url=final_url,
                        body_captured_at=datetime.now(timezone.utc).isoformat(), body_capture_method=method,
                        body_document_sha256=hashlib.sha256(body).hexdigest(), body_page_title=title,
                        body_completeness="public_extract_not_guaranteed_full_article")
        if aggregator:
            metadata["body_address_resolution"] = "google_news_public_article_rpc"
        if first_page:
            metadata["body_public_continuation_from"] = first_page
        result["content"] = text
        result = pipeline.enrich_item(result)
        diagnostic.update(ok=True, outcome="public_body_captured", status_code=status, final_url=final_url, characters=len(text),
                          message="已读取对应标题的公开正文；分页、付费内容和未呈现部分不声称已完整读取。")
    except asyncio.CancelledError:
        raise
    except pipeline.PipelineError as exc:
        diagnostic.update(outcome="unavailable", message=str(exc))
    except Exception:
        diagnostic.update(outcome="source_error", message="原文读取未完成，保留已有摘要；请检查出版方地址和可访问性。")
    return result, diagnostic


async def capture_sources(items, *, limit=12):
    output = copy.deepcopy(items)
    candidates = [(i, row) for i, row in enumerate(output) if row.get("type") == "news"
                  and (row.get("metadata") or {}).get("content_kind") in {"rss_summary", "headline_only"}]
    diagnostics = []
    semaphore = asyncio.Semaphore(3)

    async def capture(index, item):
        async with semaphore:
            output[index], diagnostic = await capture_article(item)
            diagnostics.append(diagnostic)

    attempted = candidates[:limit]
    tasks = [asyncio.create_task(capture(index, item)) for index, item in attempted]
    timed_out = False
    try:
        async with asyncio.timeout(24):
            # One source may terminate its own async operation. Keep that
            # failure local, so it cannot cancel the entire report/queue lane.
            await asyncio.gather(*tasks, return_exceptions=True)
    except TimeoutError:
        timed_out = True
        diagnostics.append({"source": "public_article", "ok": False, "outcome": "time_budget", "message": "公开正文读取达到24秒预算；已读正文保留，其余仍是摘要。"})
    finally:
        for task in tasks:
            if not task.done(): task.cancel()
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    for (_, item), outcome in zip(attempted, outcomes):
        if isinstance(outcome, BaseException):
            diagnostics.append({"source": "public_article", "item_id": item.get("id"), "url": item["url"], "ok": False,
                "outcome": "time_budget" if timed_out and isinstance(outcome, asyncio.CancelledError) else "source_cancelled" if isinstance(outcome, asyncio.CancelledError) else "source_error",
                "message": "此来源读取未完成，保留已有摘要；其他已取得正文继续分析。"})
    if len(candidates) > limit:
        diagnostics.append({"source": "public_article", "ok": False, "outcome": "sample_limit", "message": f"本次仅尝试前{limit}条公开原文，另有{len(candidates)-limit}条摘要未尝试。"})
    return output, diagnostics
